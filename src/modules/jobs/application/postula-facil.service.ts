import { Injectable } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import env from '../../shared/infrastructure/environment';

@Injectable()
export class PostulaFacilService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
  ) {}

  async save(body: {
    id_usuario: string;
    plan: string;
    profesion: string;
    resumen: string;
    cv_url: string;
    cargos: string[];
    experiencia: string;
    ubicaciones: string[];
    pretension_general: string;
    rut?: string;
    fecha_nacimiento?: string;
    empresa?: string;
    anio_inicio?: string;
    actualmente_trabajando?: boolean;
    anio_fin?: string;
  }) {
    await this.bq.query(`
      MERGE ${this.bq.t('POSTULA_FACIL')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN UPDATE SET
        PLAN = @plan, PROFESION = @prof, RESUMEN = @resumen,
        CV_URL = @cv, CARGOS = @cargos, EXPERIENCIA = @exp,
        UBICACIONES = @ubic, PRETENSION_GENERAL = @pretension,
        RUT = COALESCE(NULLIF(@rut, ''), T.RUT),
        FECHA_NACIMIENTO = COALESCE(NULLIF(@fn, ''), T.FECHA_NACIMIENTO),
        EMPRESA = COALESCE(NULLIF(@empresa, ''), T.EMPRESA),
        ANIO_INICIO = IF(@anio_inicio != '', SAFE_CAST(@anio_inicio AS INT64), T.ANIO_INICIO),
        ACTUALMENTE_TRABAJANDO = @actualmente,
        ANIO_FIN = IF(@actualmente, NULL, SAFE_CAST(NULLIF(@anio_fin, '') AS INT64)),
        FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT
        (ID_USUARIO, PLAN, PROFESION, RESUMEN, CV_URL, CARGOS, EXPERIENCIA, UBICACIONES, PRETENSION_GENERAL, RUT, FECHA_NACIMIENTO, EMPRESA, ANIO_INICIO, ACTUALMENTE_TRABAJANDO, ANIO_FIN, FECHA_ACTUALIZACION)
      VALUES
        (@id, @plan, @prof, @resumen, @cv, @cargos, @exp, @ubic, @pretension, @rut, @fn, NULLIF(@empresa, ''), SAFE_CAST(NULLIF(@anio_inicio, '') AS INT64), @actualmente, IF(@actualmente, NULL, SAFE_CAST(NULLIF(@anio_fin, '') AS INT64)), CURRENT_TIMESTAMP())
    `, {
      id: body.id_usuario,
      plan: body.plan,
      prof: body.profesion,
      resumen: body.resumen,
      cv: body.cv_url,
      cargos: body.cargos,
      exp: body.experiencia,
      ubic: body.ubicaciones,
      pretension: body.pretension_general,
      rut: body.rut || '',
      fn: body.fecha_nacimiento || '',
      empresa: body.empresa || '',
      anio_inicio: body.anio_inicio || '',
      actualmente: body.actualmente_trabajando ?? true,
      anio_fin: body.anio_fin || '',
    });

    // Send confirmation email once per user (track via correo_guardar_info flag)
    const sent = await this.bq.query<any>(`
      SELECT correo_guardar_info FROM ${this.bq.t('CORREOS_ENVIADOS')}
      WHERE id_usuario = @id LIMIT 1
    `, { id: body.id_usuario }).catch(() => []);

    const alreadySent = sent.length && sent[0].correo_guardar_info;

    if (!alreadySent) {
      const user = await this.bq.query<any>(`
        SELECT NOMBRE, EMAIL FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
      `, { id: body.id_usuario }).catch(() => []);

      if (user.length) {
        await this.email.send(
          user[0].EMAIL,
          '¡Tu perfil de Postula Fácil está listo!',
          this.email.postulaFacilHtml(user[0].NOMBRE, body.cargos),
        ).catch(() => null);

        await this.bq.query(`
          MERGE ${this.bq.t('CORREOS_ENVIADOS')} T
          USING (SELECT @id AS id_usuario) S ON T.id_usuario = S.id_usuario
          WHEN MATCHED THEN UPDATE SET correo_guardar_info = 1
          WHEN NOT MATCHED THEN INSERT (id_usuario, correo_bienvenida, correo_guardar_info)
          VALUES (@id, 0, 1)
        `, { id: body.id_usuario }).catch(() => null);
      }
    }

    // Disparar registro en portales en segundo plano (fire-and-forget)
    this.triggerRegisterJob(body.id_usuario).catch((e) =>
      console.error('[postula-facil] register job error:', e.message),
    );

    return { success: true };
  }

  private async triggerRegisterJob(userId: string): Promise<void> {
    const project = env.gcpProjectId;
    const region  = env.gcpRegion;
    const job     = env.autoJobName;

    const tokenRes = await fetch(
      'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
      { headers: { 'Metadata-Flavor': 'Google' } },
    ).catch(() => null);

    if (!tokenRes?.ok) return; // local — no hacer nada

    const { access_token } = await tokenRes.json();

    const res = await fetch(
      `https://run.googleapis.com/v2/projects/${project}/locations/${region}/jobs/${job}:run`,
      {
        method: 'POST',
        headers: { Authorization: `Bearer ${access_token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
          overrides: {
            containerOverrides: [{
              args: ['python', 'register.py'],
              env: [{ name: 'SINGLE_USER_ID', value: userId }],
            }],
            taskCount: 1,
          },
        }),
      },
    );

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`register job ${res.status}: ${err.slice(0, 200)}`);
    }
    console.log(`[postula-facil] register job disparado para ${userId}`);
  }

  async get(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT * FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    if (!rows.length) return null;

    const r = rows[0];
    return {
      profesion: r.PROFESION || '',
      resumen: r.RESUMEN || '',
      cv_url: r.CV_URL || '',
      cargos: this.parseJson(r.CARGOS),
      experiencia: r.EXPERIENCIA || '',
      ubicaciones: this.parseJson(r.UBICACIONES),
      pretension_general: r.PRETENSION_GENERAL || '',
      rut: r.RUT || '',
      fecha_nacimiento: r.FECHA_NACIMIENTO || '',
      empresa: r.EMPRESA || '',
      anio_inicio: r.ANIO_INICIO ? String(r.ANIO_INICIO) : '',
      actualmente_trabajando: r.ACTUALMENTE_TRABAJANDO ?? true,
      anio_fin: r.ANIO_FIN ? String(r.ANIO_FIN) : '',
    };
  }

  async estado(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT PROFESION, CV_URL, CARGOS, EXPERIENCIA, UBICACIONES, PRETENSION_GENERAL
      FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    if (!rows.length) return { completo: false };

    const r = rows[0];
    const cargos = this.parseJson(r.CARGOS);
    const ubicaciones = this.parseJson(r.UBICACIONES);

    const completo = !!(
      r.PROFESION && r.CV_URL && cargos.length &&
      r.EXPERIENCIA && ubicaciones.length && r.PRETENSION_GENERAL
    );

    return { completo };
  }

  private parseJson(val: any): any[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
