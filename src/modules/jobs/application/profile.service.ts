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
        COALESCE(pa.activo, 0) as AUTO_ACTIVO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
      WHERE u.ID_USUARIO = @id
      LIMIT 1
    `, { id: userId });

    if (!rows.length) return null;

    const p = rows[0];

    // FOTO_URL: bucket is public — raw URL works directly in browser
    // CV_URL: private bucket — generate signed URL for download
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

  async toggleAutoPostulaciones(userId: string, activo: boolean) {
    const now = new Date().toISOString();
    const activoInt = activo ? 1 : 0;

    await this.bq.query(`
      MERGE ${this.bq.t('POSTULACIONES_AUTO')} T
      USING (SELECT @id AS id_usuario, @activo AS activo) S
      ON T.id_usuario = S.id_usuario
      WHEN MATCHED THEN
        UPDATE SET activo = S.activo, fecha_actualizacion = @now
      WHEN NOT MATCHED THEN
        INSERT (id_usuario, activo, fecha_creacion, fecha_actualizacion)
        VALUES (S.id_usuario, S.activo, @now, @now)
    `, { id: userId, activo: activoInt, now });

    if (activo) {
      this.triggerAutoJob(userId).catch((e) =>
        console.error('trigger auto job error:', e.message),
      );
    }

    return { success: true, activo };
  }

  private async triggerAutoJob(userId: string): Promise<void> {
    const project = env.gcpProjectId;
    const region  = env.gcpRegion;
    const job     = env.autoJobName;

    // Obtener token desde metadata server (disponible en Cloud Run)
    const tokenRes = await fetch(
      'http://metadata.google.internal/computeMetadata/v1/instance/service-accounts/default/token',
      { headers: { 'Metadata-Flavor': 'Google' } },
    ).catch(() => null);

    if (!tokenRes?.ok) {
      console.warn('No se pudo obtener token de metadata server (¿estás en local?)');
      return;
    }

    const { access_token } = await tokenRes.json();

    const res = await fetch(
      `https://run.googleapis.com/v2/projects/${project}/locations/${region}/jobs/${job}:run`,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${access_token}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          overrides: {
            containerOverrides: [{
              env: [{ name: 'SINGLE_USER_ID', value: userId }],
            }],
            taskCount: 1,
          },
        }),
      },
    );

    if (!res.ok) {
      const err = await res.text();
      throw new Error(`Cloud Run Job trigger ${res.status}: ${err.slice(0, 200)}`);
    }

    console.log(`✓ Auto-postulaciones disparado para usuario ${userId}`);
  }
}
