import { Injectable, BadRequestException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { GcsService } from '../../shared/infrastructure/services/gcs.service';
import env from '../../shared/infrastructure/environment';

const pdfParse = require('pdf-parse') as (buf: Buffer) => Promise<{ text: string }>;

const ANTHROPIC_URL = 'https://api.anthropic.com/v1/messages';
const MODEL = process.env.ANTHROPIC_MODEL || 'claude-haiku-4-5-20251001';
const CV_CACHE_TTL_MS = 6 * 60 * 60 * 1000; // 6 horas

interface Contexto {
  nombre: string;
  cargos: string[];
  experiencia: string;
  profesion: string;
  carrera: string;
  resumen: string;
  empresa: string;
  cvTexto: string;
}

@Injectable()
export class AiService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly gcs: GcsService,
  ) {}

  // Caché en memoria del texto del CV — evita descargar/parsear el PDF por cada pregunta
  private cvCache = new Map<string, { texto: string; ts: number }>();

  // Caché de respuestas: memoria (rápido, por instancia) + BigQuery (persistente).
  // Las preguntas de los formularios se repiten mucho entre vacantes — tras los
  // primeros días casi todo sale del caché y el gasto en IA cae a ~cero.
  // Además, la tabla AI_RESPUESTAS es el dataset para un futuro modelo propio.
  private respuestaCache = new Map<string, string>();
  private tablaLista = false;

  private normPregunta(p: string): string {
    return String(p || '')
      .toLowerCase()
      .normalize('NFD').replace(/[̀-ͯ]/g, '')
      .replace(/[¿?¡!]/g, '')
      .replace(/\s+/g, ' ')
      .trim()
      .slice(0, 300);
  }

  private async ensureTabla() {
    if (this.tablaLista) return;
    try {
      await this.bq.query(`
        CREATE TABLE IF NOT EXISTS ${this.bq.t('AI_RESPUESTAS')} (
          ID_USUARIO    STRING,
          PREGUNTA_NORM STRING,
          PREGUNTA      STRING,
          RESPUESTA     STRING,
          FUENTE        STRING,
          FECHA         TIMESTAMP
        )
      `);
      this.tablaLista = true;
    } catch (e: any) {
      console.warn('[ai] No se pudo crear AI_RESPUESTAS:', e.message);
    }
  }

  private async getRespuestaCacheada(userId: string, norm: string): Promise<string | null> {
    const memKey = `${userId}|${norm}`;
    const mem = this.respuestaCache.get(memKey);
    if (mem) return mem;
    try {
      const rows = await this.bq.query<any>(`
        SELECT RESPUESTA FROM ${this.bq.t('AI_RESPUESTAS')}
        WHERE ID_USUARIO = @id AND PREGUNTA_NORM = @norm
          AND FECHA > TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 60 DAY)
        ORDER BY FECHA DESC LIMIT 1
      `, { id: userId, norm });
      if (rows.length && rows[0].RESPUESTA) {
        this.respuestaCache.set(memKey, rows[0].RESPUESTA);
        return rows[0].RESPUESTA;
      }
    } catch { /* tabla puede no existir aún */ }
    return null;
  }

  private guardarRespuesta(userId: string, norm: string, pregunta: string, respuesta: string) {
    this.respuestaCache.set(`${userId}|${norm}`, respuesta);
    // Fire-and-forget: no bloquear la respuesta al usuario por el INSERT
    this.ensureTabla()
      .then(() => this.bq.query(`
        INSERT INTO ${this.bq.t('AI_RESPUESTAS')}
          (ID_USUARIO, PREGUNTA_NORM, PREGUNTA, RESPUESTA, FUENTE, FECHA)
        VALUES (@id, @norm, @pregunta, @respuesta, 'claude', CURRENT_TIMESTAMP())
      `, { id: userId, norm, pregunta: pregunta.slice(0, 500), respuesta: respuesta.slice(0, 1000) }))
      .catch((e) => console.warn('[ai] No se pudo guardar respuesta:', e.message));
  }

  // ── Llamada a Claude (fetch directo, sin SDK) ─────────────────────────────
  private async claude(system: string, user: string, maxTokens = 300): Promise<string | null> {
    const key = process.env.ANTHROPIC_API_KEY;
    if (!key) return null; // sin API key → los callers hacen fail-open
    try {
      const r = await fetch(ANTHROPIC_URL, {
        method: 'POST',
        headers: {
          'x-api-key': key,
          'anthropic-version': '2023-06-01',
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          model: MODEL,
          max_tokens: maxTokens,
          system,
          messages: [{ role: 'user', content: user }],
        }),
      });
      if (!r.ok) {
        console.warn('[ai] Anthropic respondió', r.status, await r.text().catch(() => ''));
        return null;
      }
      const d: any = await r.json();
      return d?.content?.[0]?.text ?? null;
    } catch (e: any) {
      console.warn('[ai] Error llamando a Anthropic:', e.message);
      return null;
    }
  }

  // ── Contexto del candidato: postula_facil + texto real del CV ─────────────
  private async getContexto(userId: string): Promise<Contexto | null> {
    const rows = await this.bq.query<any>(`
      SELECT pf.PROFESION, pf.RESUMEN, pf.CV_URL, pf.CARGOS, pf.EXPERIENCIA,
             pf.CARRERA, pf.NIVEL_EDUCATIVO, pf.INSTITUCION, pf.EMPRESA, u.NOMBRE
      FROM ${this.bq.t('POSTULA_FACIL')} pf
      JOIN ${this.bq.t('USUARIOS')} u ON u.ID_USUARIO = pf.ID_USUARIO
      WHERE pf.ID_USUARIO = @id LIMIT 1
    `, { id: userId });
    if (!rows.length) return null;
    const r = rows[0];

    let cargos: string[] = [];
    try { cargos = Array.isArray(r.CARGOS) ? r.CARGOS : JSON.parse(r.CARGOS || '[]'); } catch { /* noop */ }

    return {
      nombre: r.NOMBRE || '',
      cargos,
      experiencia: r.EXPERIENCIA || '',
      profesion: r.PROFESION || '',
      carrera: [r.CARRERA, r.NIVEL_EDUCATIVO, r.INSTITUCION].filter(Boolean).join(' — '),
      resumen: r.RESUMEN || '',
      empresa: r.EMPRESA || '',
      cvTexto: await this.getCvTexto(userId, r.CV_URL),
    };
  }

  private async getCvTexto(userId: string, cvUrl: string): Promise<string> {
    if (!cvUrl) return '';
    const cached = this.cvCache.get(userId);
    if (cached && Date.now() - cached.ts < CV_CACHE_TTL_MS) return cached.texto;

    try {
      // Firmar la URL si es un archivo de nuestro bucket privado
      let url = cvUrl;
      const fileName = this.gcs.extractFileName(cvUrl);
      if (fileName) {
        url = await this.gcs.getSignedUrl(env.gcsBucketCv, fileName).catch(() => cvUrl);
      }
      const res = await fetch(url);
      if (!res.ok) return '';
      const buf = Buffer.from(await res.arrayBuffer());
      const { text } = await pdfParse(buf);
      const texto = (text || '').trim().slice(0, 6000);
      this.cvCache.set(userId, { texto, ts: Date.now() });
      return texto;
    } catch (e: any) {
      console.warn('[ai] No se pudo extraer texto del CV:', e.message);
      return '';
    }
  }

  private perfilPrompt(ctx: Contexto): string {
    return `CANDIDATO: ${ctx.nombre}
Cargos que busca: ${ctx.cargos.join(', ') || '(no especificados)'}
Años de experiencia: ${ctx.experiencia || 'no indicado'}
Profesión: ${ctx.profesion || 'no indicada'}
Educación: ${ctx.carrera || 'no indicada'}
${ctx.empresa ? `Última empresa: ${ctx.empresa}` : ''}
${ctx.resumen ? `Resumen profesional: ${ctx.resumen}` : ''}
${ctx.cvTexto ? `\nCV COMPLETO (texto extraído del PDF):\n---\n${ctx.cvTexto}\n---` : '\n(CV no disponible en texto)'}`;
  }

  // ── Evaluar si una vacante calza con el candidato ──────────────────────────
  async evaluarEmpleo(body: { user_id: string; titulo?: string; descripcion?: string }) {
    if (!body?.user_id) throw new BadRequestException('user_id requerido');

    const ctx = await this.getContexto(body.user_id);
    // Fail-open: sin contexto o sin IA, no bloquear la postulación
    if (!ctx) return { apto: true, score: 50, razon: 'sin-perfil' };

    const system = `Eres un evaluador de compatibilidad laboral. Comparas una vacante contra el perfil real de un candidato y decides si vale la pena postular.
Criterios: el cargo debe corresponder al nivel y área del candidato (una práctica/internship NO es apta para alguien con años de experiencia, y viceversa), los requisitos principales deben estar razonablemente cubiertos por su experiencia o herramientas del CV, y el rubro debe ser compatible.
Responde SOLO un JSON válido, sin markdown: {"apto": true|false, "score": 0-100, "razon": "máximo 15 palabras"}`;

    const user = `${this.perfilPrompt(ctx)}

VACANTE:
Título: ${(body.titulo || '').slice(0, 200)}
Descripción:
${(body.descripcion || '').slice(0, 3500)}

¿Debería postular este candidato a esta vacante?`;

    const raw = await this.claude(system, user, 150);
    if (!raw) return { apto: true, score: 50, razon: 'ia-no-disponible' };

    try {
      const clean = raw.replace(/```json|```/g, '').trim();
      const parsed = JSON.parse(clean);
      return {
        apto: parsed.apto !== false,
        score: Number(parsed.score) || 0,
        razon: String(parsed.razon || '').slice(0, 200),
      };
    } catch {
      return { apto: true, score: 50, razon: 'respuesta-no-parseable' };
    }
  }

  // ── Responder una pregunta del formulario COMO el candidato ────────────────
  async responderPregunta(body: {
    user_id?: string; pregunta: string; opciones?: string[]; perfil?: any;
  }) {
    if (!body?.pregunta) throw new BadRequestException('pregunta requerida');

    // Clave de caché: pregunta normalizada + opciones (la misma pregunta con
    // opciones distintas puede requerir otra respuesta exacta)
    const norm = this.normPregunta(
      body.pregunta + (body.opciones?.length ? '|' + body.opciones.join('|') : ''),
    );
    if (body.user_id) {
      const cacheada = await this.getRespuestaCacheada(body.user_id, norm);
      if (cacheada) return { respuesta: cacheada, cache: true };
    }

    let contexto = '';
    if (body.user_id) {
      const ctx = await this.getContexto(body.user_id);
      if (ctx) contexto = this.perfilPrompt(ctx);
    }
    if (!contexto && body.perfil) {
      const p = body.perfil;
      contexto = `CANDIDATO: ${p.nombre || ''}
Cargos que busca: ${(p.cargos || []).join(', ')}
Años de experiencia: ${p.experiencia || ''}
Educación: ${p.carrera || ''}
Resumen: ${p.resumen || ''}`;
    }
    if (!contexto) return { respuesta: null };

    const system = `Respondes preguntas de formularios de postulación de empleo EN NOMBRE del candidato, como si fueras él/ella.
Reglas estrictas:
- Responde SOLO el valor que va en el campo, sin explicaciones ni comillas.
- Si la pregunta pide un número (años, escala, cantidad), responde solo el número.
- Si hay opciones disponibles, responde EXACTAMENTE el texto de una de ellas.
- Basa las respuestas en la experiencia y herramientas que realmente aparecen en el CV y perfil. No inventes certificaciones, títulos ni experiencia que no estén ahí.
- Cuando la respuesta sea ambigua, elige la opción veraz más favorable para el candidato.
- Responde en el idioma de la pregunta. Máximo 30 palabras salvo que pidan un texto largo (carta, motivación).`;

    const user = `${contexto}

PREGUNTA DEL FORMULARIO: ${body.pregunta.slice(0, 500)}
${body.opciones?.length ? `OPCIONES DISPONIBLES: ${body.opciones.slice(0, 20).join(' | ')}` : ''}

Respuesta:`;

    const raw = await this.claude(system, user, 200);
    const respuesta = raw ? raw.trim() : null;
    if (respuesta && body.user_id) {
      this.guardarRespuesta(body.user_id, norm, body.pregunta, respuesta);
    }
    return { respuesta };
  }
}
