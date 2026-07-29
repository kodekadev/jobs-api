import { Injectable } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import { CloudRunService } from '../../shared/infrastructure/services/cloud-run.service';

@Injectable()
export class PostulaFacilService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
    private readonly cloudRun: CloudRunService,
  ) {}

  async save(body: any) {
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
        NIVEL_EDUCATIVO = COALESCE(NULLIF(@nivel_educativo, ''), T.NIVEL_EDUCATIVO),
        INSTITUCION = COALESCE(NULLIF(@institucion, ''), T.INSTITUCION),
        CARRERA = COALESCE(NULLIF(@carrera, ''), T.CARRERA),
        SITUACION_ESTUDIOS = COALESCE(NULLIF(@situacion_estudios, ''), T.SITUACION_ESTUDIOS),
        ANIO_INICIO_ESTUDIOS = IF(@anio_inicio_estudios != '', SAFE_CAST(@anio_inicio_estudios AS INT64), T.ANIO_INICIO_ESTUDIOS),
        FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT
        (ID_USUARIO, PLAN, PROFESION, RESUMEN, CV_URL, CARGOS, EXPERIENCIA, UBICACIONES, PRETENSION_GENERAL, RUT, FECHA_NACIMIENTO, EMPRESA, ANIO_INICIO, ACTUALMENTE_TRABAJANDO, ANIO_FIN, NIVEL_EDUCATIVO, INSTITUCION, CARRERA, SITUACION_ESTUDIOS, ANIO_INICIO_ESTUDIOS, FECHA_ACTUALIZACION)
      VALUES
        (@id, @plan, @prof, @resumen, @cv, @cargos, @exp, @ubic, @pretension, @rut, @fn, NULLIF(@empresa, ''), SAFE_CAST(NULLIF(@anio_inicio, '') AS INT64), @actualmente, IF(@actualmente, NULL, SAFE_CAST(NULLIF(@anio_fin, '') AS INT64)), NULLIF(@nivel_educativo, ''), NULLIF(@institucion, ''), NULLIF(@carrera, ''), NULLIF(@situacion_estudios, ''), SAFE_CAST(NULLIF(@anio_inicio_estudios, '') AS INT64), CURRENT_TIMESTAMP())
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
      rut: body.rut || '',
      fn: body.fecha_nacimiento || '',
      empresa: body.empresa || '',
      anio_inicio: body.anio_inicio || '',
      actualmente: body.actualmente_trabajando ?? true,
      anio_fin: body.anio_fin || '',
      nivel_educativo: body.nivel_educativo || '',
      institucion: body.institucion || '',
      carrera: body.carrera || '',
      situacion_estudios: body.situacion_estudios || '',
      anio_inicio_estudios: body.anio_inicio_estudios || '',
    });

    const sent = await this.bq.query(`
      SELECT ID FROM ${this.bq.t('CORREOS_ENVIADOS')}
      WHERE ID_USUARIO = @id AND TIPO = 'postula_facil' LIMIT 1
    `, { id: body.id_usuario }).catch(() => []);

    if (!sent.length) {
      const user = await this.bq.query(`
        SELECT NOMBRE, EMAIL FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
      `, { id: body.id_usuario });
      if (user.length) {
        await (this.email as any).send(user[0].EMAIL, '¡Tu perfil de Postula Fácil está listo!', (this.email as any).postulaFacilHtml(user[0].NOMBRE, body.cargos)).catch(() => null);
        await this.bq.query(`
          INSERT INTO ${this.bq.t('CORREOS_ENVIADOS')} (ID_USUARIO, TIPO, FECHA)
          VALUES (@id, 'postula_facil', CURRENT_TIMESTAMP())
        `, { id: body.id_usuario }).catch(() => null);
      }
    }

    const existing = await this.bq.query(`
      SELECT id_usuario FROM ${this.bq.t('CUENTAS_PORTALES')}
      WHERE id_usuario = @id AND portal = 'trabajando' LIMIT 1
    `, { id: body.id_usuario }).catch(() => []);

    if (!existing.length) {
      this.cloudRun.triggerRegisterJob(body.id_usuario);
    }

    // Auto-activar autopilot si el usuario ya tiene cargos configurados
    const cargos = Array.isArray(body.cargos) ? body.cargos : [];
    if (cargos.length > 0) {
      await this.bq.query(`
        MERGE ${this.bq.t('POSTULACIONES_AUTO')} T
        USING (SELECT @id AS id_usuario) S
        ON T.id_usuario = S.id_usuario
        WHEN MATCHED THEN UPDATE SET activo = 1
        WHEN NOT MATCHED THEN INSERT (id_usuario, activo) VALUES (@id, 1)
      `, { id: body.id_usuario }).catch((e: any) => {
        console.error('[PostulaFacil] autopilot merge error:', e.message);
      });
    }

    return { success: true };
  }

  async get(userId: string) {
    const rows = await this.bq.query(`
      SELECT * FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });
    if (!rows.length) return null;
    const r = rows[0];
    return {
      profesion:             r.PROFESION || '',
      resumen:               r.RESUMEN || '',
      cv_url:                r.CV_URL || '',
      cargos:                this.parseJson(r.CARGOS),
      experiencia:           r.EXPERIENCIA || '',
      ubicaciones:           this.parseJson(r.UBICACIONES),
      pretension_general:    r.PRETENSION_GENERAL || '',
      rut:                   r.RUT || '',
      fecha_nacimiento:      r.FECHA_NACIMIENTO || '',
      empresa:               r.EMPRESA || '',
      anio_inicio:           r.ANIO_INICIO ? String(r.ANIO_INICIO) : '',
      actualmente_trabajando: r.ACTUALMENTE_TRABAJANDO ?? true,
      anio_fin:              r.ANIO_FIN ? String(r.ANIO_FIN) : '',
      nivel_educativo:       r.NIVEL_EDUCATIVO || '',
      institucion:           r.INSTITUCION || '',
      carrera:               r.CARRERA || '',
      situacion_estudios:    r.SITUACION_ESTUDIOS || '',
      anio_inicio_estudios:  r.ANIO_INICIO_ESTUDIOS ? String(r.ANIO_INICIO_ESTUDIOS) : '',
    };
  }

  async estado(userId: string) {
    const rows = await this.bq.query(`
      SELECT PROFESION, CV_URL, CARGOS, EXPERIENCIA, UBICACIONES, PRETENSION_GENERAL
      FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });
    if (!rows.length) return { completo: false };
    const r = rows[0];
    const cargos = this.parseJson(r.CARGOS);
    const ubicaciones = this.parseJson(r.UBICACIONES);
    const completo = !!(r.PROFESION && r.CV_URL && cargos.length &&
      r.EXPERIENCIA && ubicaciones.length && r.PRETENSION_GENERAL);
    return { completo };
  }

  private parseJson(val: any): any[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
