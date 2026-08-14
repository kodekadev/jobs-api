import { Injectable, BadRequestException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { GcsService } from '../../shared/infrastructure/services/gcs.service';
import env from '../../shared/infrastructure/environment';

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

  async saveAutopilotFeedback(userId: string, ratingServicio: number, ratingPostulaciones: number, comentario: string, tipo: string) {
    await this.bq.query(`
      INSERT INTO ${this.bq.t('AUTOPILOT_FEEDBACK')}
        (ID_USUARIO, RATING_SERVICIO, RATING_POSTULACIONES, COMENTARIO, TIPO, FECHA)
      VALUES (@id, @rs, @rp, @comentario, @tipo, CURRENT_TIMESTAMP())
    `, { id: userId, rs: ratingServicio || 0, rp: ratingPostulaciones || 0, comentario: comentario || '', tipo: tipo || 'desconocido' });

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
