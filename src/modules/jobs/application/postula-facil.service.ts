import { Injectable } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';

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
  }) {
    await this.bq.query(`
      MERGE ${this.bq.t('POSTULA_FACIL')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN UPDATE SET
        PLAN = @plan, PROFESION = @prof, RESUMEN = @resumen,
        CV_URL = @cv, CARGOS = @cargos, EXPERIENCIA = @exp,
        UBICACIONES = @ubic, PRETENSION_GENERAL = @pretension,
        FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT
        (ID_USUARIO, PLAN, PROFESION, RESUMEN, CV_URL, CARGOS, EXPERIENCIA, UBICACIONES, PRETENSION_GENERAL, FECHA_ACTUALIZACION)
      VALUES
        (@id, @plan, @prof, @resumen, @cv, @cargos, @exp, @ubic, @pretension, CURRENT_TIMESTAMP())
    `, {
      id: body.id_usuario,
      plan: body.plan,
      prof: body.profesion,
      resumen: body.resumen,
      cv: body.cv_url,
      cargos: JSON.stringify(body.cargos),
      exp: body.experiencia,
      ubic: JSON.stringify(body.ubicaciones),
      pretension: body.pretension_general,
    });

    // Send confirmation email once per user
    const sent = await this.bq.query<any>(`
      SELECT ID FROM ${this.bq.t('CORREOS_ENVIADOS')}
      WHERE ID_USUARIO = @id AND TIPO = 'postula_facil' LIMIT 1
    `, { id: body.id_usuario }).catch(() => []);

    if (!sent.length) {
      const user = await this.bq.query<any>(`
        SELECT NOMBRE, EMAIL FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
      `, { id: body.id_usuario });

      if (user.length) {
        await this.email.send(
          user[0].EMAIL,
          '¡Tu perfil de Postula Fácil está listo!',
          this.email.postulaFacilHtml(user[0].NOMBRE, body.cargos),
        ).catch(() => null);

        await this.bq.query(`
          INSERT INTO ${this.bq.t('CORREOS_ENVIADOS')} (ID_USUARIO, TIPO, FECHA)
          VALUES (@id, 'postula_facil', CURRENT_TIMESTAMP())
        `, { id: body.id_usuario }).catch(() => null);
      }
    }

    return { success: true };
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
