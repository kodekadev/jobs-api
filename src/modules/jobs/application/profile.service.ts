import { Injectable, BadRequestException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { GcsService } from '../../shared/infrastructure/services/gcs.service';
import env from '../../shared/infrastructure/environment';
import { isValidRating } from './rating.utils';
import { normalizarOpcion } from './feedback.utils';
import { getDailyLimit } from './plan.limits';

export interface AutopilotFeedbackInput {
  id: string;
  rating_servicio?: number | null;
  rating_postulaciones?: number | null;
  comentario?: string;
  tipo?: string;
  ofertas_relevantes?: string | null;
  llamadas?: string | null;
  consiguio_trabajo?: string | null;
  que_falta?: string;
  rating_general?: number | null;
}

@Injectable()
export class ProfileService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly gcs: GcsService,
  ) {}

  async getProfile(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO, u.NOMBRE, u.EMAIL, u.CELULAR,
        ic.PROFESION, ic.EXPERIENCIA, ic.FOTO_URL, ic.CV_URL,
        COALESCE(pa.ACTIVO, 0) as AUTO_ACTIVO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      WHERE u.ID_USUARIO = @id
      LIMIT 1
    `, { id: userId });

    if (!rows.length) return null;

    const p = rows[0];

    // Generate signed URLs for private files
    if (p.FOTO_URL) {
      const fileName = this.gcs.extractFileName(p.FOTO_URL);
      if (fileName) {
        const signed = await this.gcs.getSignedUrl(env.gcsBucketImages, fileName).catch(() => p.FOTO_URL);
        if (signed) p.FOTO_URL = signed;
      }
    }

    if (p.CV_URL) {
      const fileName = this.gcs.extractFileName(p.CV_URL);
      if (fileName) {
        const signed = await this.gcs.getSignedUrl(env.gcsBucketCv, fileName).catch(() => p.CV_URL);
        if (signed) p.CV_URL = signed;
      }
    }

    return p;
  }

  async updateProfile(userId: string, profesion: string, experiencia: string) {
    await this.bq.query(`
      MERGE ${this.bq.t('INFO_CLIENTE')} T
      USING (SELECT @id AS ID_USUARIO, @prof AS PROFESION, @exp AS EXPERIENCIA) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET PROFESION = S.PROFESION, EXPERIENCIA = S.EXPERIENCIA, FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PROFESION, EXPERIENCIA, FECHA_ACTUALIZACION)
        VALUES (S.ID_USUARIO, S.PROFESION, S.EXPERIENCIA, CURRENT_TIMESTAMP())
    `, { id: userId, prof: profesion, exp: experiencia });

    return { success: true };
  }

  async uploadImage(userId: string, buffer: Buffer, mimeType: string, originalName: string) {
    const allowed = ['image/jpeg', 'image/png'];
    if (!allowed.includes(mimeType)) throw new BadRequestException('Solo JPG o PNG');

    const ext = originalName.split('.').pop();
    const fileName = `avatar-${userId}.${ext}`;
    const fotoUrl = await this.gcs.uploadBuffer(env.gcsBucketImages, fileName, buffer, mimeType);

    await this.bq.query(`
      MERGE ${this.bq.t('INFO_CLIENTE')} T
      USING (SELECT @id AS ID_USUARIO, @foto AS FOTO_URL) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET FOTO_URL = S.FOTO_URL, FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, FOTO_URL, FECHA_ACTUALIZACION)
        VALUES (S.ID_USUARIO, S.FOTO_URL, CURRENT_TIMESTAMP())
    `, { id: userId, foto: fotoUrl });

    return { success: true, foto_url: fotoUrl };
  }

  async uploadCv(userId: string, buffer: Buffer, mimeType: string, originalName: string) {
    if (mimeType !== 'application/pdf') throw new BadRequestException('Solo PDF');
    if (buffer.length > 5 * 1024 * 1024) throw new BadRequestException('Máximo 5MB');

    const safeName = originalName.replace(/\s+/g, '_');
    const fileName = `cv-${userId}-${safeName}`;
    const cvUrl = await this.gcs.uploadBuffer(env.gcsBucketCv, fileName, buffer, mimeType);

    await this.bq.query(`
      MERGE ${this.bq.t('INFO_CLIENTE')} T
      USING (SELECT @id AS ID_USUARIO, @cv AS CV_URL) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET CV_URL = S.CV_URL, FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, CV_URL, FECHA_ACTUALIZACION)
        VALUES (S.ID_USUARIO, S.CV_URL, CURRENT_TIMESTAMP())
    `, { id: userId, cv: cvUrl });

    return { success: true, cv_url: cvUrl };
  }

  /**
   * Postulaciones de hoy contra el límite del usuario, más las ofertas que
   * quedaron sin postular por el tope. Alimenta el contador del dashboard.
   */
  async getMetricasHoy(userId: string) {
    const [planRows, metricaRows, hoyRows] = await Promise.all([
      this.bq.query<any>(`
        SELECT pc.PLAN, pc.ESTADO, pc.FECHA_INICIO, pc.FECHA_FIN, pf.LIMITE_DIARIO
        FROM ${this.bq.t('POSTULA_FACIL')} pf
        LEFT JOIN ${this.bq.t('PLAN_CONTRATADO')} pc
          ON pc.ID_USUARIO = pf.ID_USUARIO
         AND pc.ESTADO IN ('ACTIVO','CANCELADO_PENDIENTE','TRIAL')
        WHERE pf.ID_USUARIO = @id
        ORDER BY pc.FECHA_INICIO DESC LIMIT 1
      `, { id: userId }).catch(() => []),

      this.bq.query<any>(`
        SELECT IFNULL(SUM(OFERTAS_COMPATIBLES), 0)  AS compatibles,
               IFNULL(SUM(NO_POSTULADAS_LIMITE), 0) AS perdidas
        FROM ${this.bq.t('METRICAS_DIARIAS')}
        WHERE FECHA = CURRENT_DATE('America/Santiago') AND ID_USUARIO = @id
      `, { id: userId }).catch(() => []),

      this.bq.query<any>(`
        SELECT COUNT(*) AS n FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id
          AND DATE(Fecha_Postulacion, 'America/Santiago') = CURRENT_DATE('America/Santiago')
          AND portal NOT IN ('email_directo', '')
      `, { id: userId }).catch(() => []),
    ]);

    const p = planRows[0] ?? {};
    const vencido = p.FECHA_FIN ? new Date(p.FECHA_FIN.value ?? p.FECHA_FIN) < new Date() : false;

    const limite = getDailyLimit({
      plan: p.PLAN,
      plan_vigente: !vencido,
      limite_diario: p.LIMITE_DIARIO,
    });

    const postuladas = Number(hoyRows[0]?.n ?? 0);
    const perdidas   = Number(metricaRows[0]?.perdidas ?? 0);

    return {
      postuladas,
      limite,
      compatibles: Number(metricaRows[0]?.compatibles ?? 0),
      perdidas,
      // Solo sugerir subir de plan si de verdad se perdieron ofertas hoy
      mostrar_upsell: perdidas > 0 && postuladas >= limite,
    };
  }

  async saveAutopilotFeedback(input: AutopilotFeedbackInput) {
    await this.bq.query(`
      INSERT INTO ${this.bq.t('AUTOPILOT_FEEDBACK')}
        (ID_USUARIO, RATING_SERVICIO, RATING_POSTULACIONES, COMENTARIO, TIPO, FECHA,
         OFERTAS_RELEVANTES, LLAMADAS, CONSIGUIO_TRABAJO, QUE_FALTA, RATING_GENERAL)
      VALUES (@id, @rs, @rp, @comentario, @tipo, CURRENT_TIMESTAMP(),
              @ofertas, @llamadas, @trabajo, @queFalta, @ratingGeneral)
    `, {
      id: input.id,
      rs: isValidRating(input.rating_servicio) ? Number(input.rating_servicio) : null,
      rp: isValidRating(input.rating_postulaciones) ? Number(input.rating_postulaciones) : null,
      comentario: input.comentario || '',
      tipo: input.tipo || 'desconocido',
      ofertas: normalizarOpcion(input.ofertas_relevantes, 'ofertas_relevantes'),
      llamadas: normalizarOpcion(input.llamadas, 'llamadas'),
      trabajo: normalizarOpcion(input.consiguio_trabajo, 'consiguio_trabajo'),
      queFalta: input.que_falta || '',
      ratingGeneral: isValidRating(input.rating_general) ? Number(input.rating_general) : null,
    }, {
      rs: 'INT64', rp: 'INT64', ratingGeneral: 'INT64',
      ofertas: 'STRING', llamadas: 'STRING', trabajo: 'STRING',
    });

    return { success: true };
  }

  async toggleAutoPostulaciones(userId: string, activo: number) {
    const now = new Date().toISOString();

    await this.bq.query(`
      MERGE ${this.bq.t('POSTULACIONES_AUTO')} T
      USING (SELECT @id AS ID_USUARIO, @activo AS ACTIVO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET ACTIVO = S.ACTIVO, FECHA_ACTUALIZACION = @now
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, ACTIVO, FECHA_CREACION, FECHA_ACTUALIZACION)
        VALUES (S.ID_USUARIO, S.ACTIVO, @now, @now)
    `, { id: userId, activo, now });

    return { success: true, activo };
  }
}
