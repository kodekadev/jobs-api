import { Injectable, BadRequestException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import { CloudRunService } from '../../shared/infrastructure/services/cloud-run.service';

const PLAN_LIMITS: Record<string, { cargos: number; ubicaciones: number }> = {
  FREE:    { cargos: 1,  ubicaciones: 1  },
  PRO:     { cargos: 4,  ubicaciones: 4  },
  SPRINT:  { cargos: 4,  ubicaciones: 4  },
  PREMIUM: { cargos: 10, ubicaciones: 10 },
  TRIAL:   { cargos: 4,  ubicaciones: 4  },
};

@Injectable()
export class PostulaFacilService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
    private readonly cloudRun: CloudRunService,
  ) {}

  private extraColumnsReady = false;

  private async ensureExtraColumns() {
    if (this.extraColumnsReady) return;
    try {
      await this.bq.query(`ALTER TABLE ${this.bq.t('POSTULA_FACIL')} ADD COLUMN IF NOT EXISTS EMPRESAS_EXCLUIDAS STRING`);
    } catch { /* ya existe */ }
    try {
      await this.bq.query(`ALTER TABLE ${this.bq.t('POSTULA_FACIL')} ADD COLUMN IF NOT EXISTS BUSQUEDA_ACTIVA BOOL`);
    } catch { /* ya existe */ }
    this.extraColumnsReady = true;
  }

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
    nivel_educativo?: string;
    institucion?: string;
    carrera?: string;
    situacion_estudios?: string;
    anio_inicio_estudios?: string;
    tipo_busqueda?: string;
    jornada?: string;
    empresas_excluidas?: string[];
    busqueda_activa?: boolean;
  }) {
    await this.ensureExtraColumns();
    const limits = PLAN_LIMITS[(body.plan || 'FREE').toUpperCase()] ?? PLAN_LIMITS.FREE;
    if ((body.cargos || []).length > limits.cargos) {
      throw new BadRequestException(`Plan ${body.plan} permite máximo ${limits.cargos} cargo(s)`);
    }
    if ((body.ubicaciones || []).length > limits.ubicaciones) {
      throw new BadRequestException(`Plan ${body.plan} permite máximo ${limits.ubicaciones} ubicación(es)`);
    }

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
        TIPO_BUSQUEDA = COALESCE(NULLIF(@tipo_busqueda, ''), T.TIPO_BUSQUEDA),
        JORNADA = COALESCE(NULLIF(@jornada, ''), T.JORNADA),
        EMPRESAS_EXCLUIDAS = @empresas_excluidas,
        BUSQUEDA_ACTIVA = @busqueda_activa,
        FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHEN NOT MATCHED THEN INSERT
        (ID_USUARIO, PLAN, PROFESION, RESUMEN, CV_URL, CARGOS, EXPERIENCIA, UBICACIONES, PRETENSION_GENERAL, RUT, FECHA_NACIMIENTO, EMPRESA, ANIO_INICIO, ACTUALMENTE_TRABAJANDO, ANIO_FIN, NIVEL_EDUCATIVO, INSTITUCION, CARRERA, SITUACION_ESTUDIOS, ANIO_INICIO_ESTUDIOS, TIPO_BUSQUEDA, JORNADA, EMPRESAS_EXCLUIDAS, BUSQUEDA_ACTIVA, FECHA_ACTUALIZACION)
      VALUES
        (@id, @plan, @prof, @resumen, @cv, @cargos, @exp, @ubic, @pretension, @rut, @fn, NULLIF(@empresa, ''), SAFE_CAST(NULLIF(@anio_inicio, '') AS INT64), @actualmente, IF(@actualmente, NULL, SAFE_CAST(NULLIF(@anio_fin, '') AS INT64)), NULLIF(@nivel_educativo, ''), NULLIF(@institucion, ''), NULLIF(@carrera, ''), NULLIF(@situacion_estudios, ''), SAFE_CAST(NULLIF(@anio_inicio_estudios, '') AS INT64), NULLIF(@tipo_busqueda, ''), NULLIF(@jornada, ''), @empresas_excluidas, @busqueda_activa, CURRENT_TIMESTAMP())
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
      tipo_busqueda: body.tipo_busqueda || '',
      jornada: body.jornada || '',
      empresas_excluidas: JSON.stringify(body.empresas_excluidas || []),
      busqueda_activa: body.busqueda_activa ?? false,
    });

    // Sync profession/experiencia to INFO_CLIENTE so /perfil shows it pre-filled
    if (body.profesion) {
      await this.bq.query(`
        MERGE ${this.bq.t('INFO_CLIENTE')} T
        USING (SELECT @id AS ID_USUARIO, @prof AS PROFESION, @exp AS EXPERIENCIA) S
        ON T.ID_USUARIO = S.ID_USUARIO
        WHEN MATCHED THEN
          UPDATE SET
            PROFESION = IF(NULLIF(S.PROFESION, '') IS NOT NULL, S.PROFESION, T.PROFESION),
            EXPERIENCIA = IF(NULLIF(S.EXPERIENCIA, '') IS NOT NULL, S.EXPERIENCIA, T.EXPERIENCIA),
            FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
        WHEN NOT MATCHED THEN
          INSERT (ID_USUARIO, PROFESION, EXPERIENCIA, FECHA_ACTUALIZACION)
          VALUES (S.ID_USUARIO, S.PROFESION, S.EXPERIENCIA, CURRENT_TIMESTAMP())
      `, { id: body.id_usuario, prof: body.profesion, exp: body.experiencia || '' });
    }

    // INSERT first (only if not yet sent) — BigQuery serializes DML so only one
    // concurrent request gets numDmlAffectedRows=1; the rest get 0 or a serialization
    // error (caught as 0). This prevents duplicate emails when user saves repeatedly.
    const inserted = await this.bq.dml(`
      INSERT INTO ${this.bq.t('CORREOS_ENVIADOS')} (ID_USUARIO, TIPO, FECHA)
      SELECT @id, 'postula_facil', CURRENT_TIMESTAMP()
      WHERE NOT EXISTS (
        SELECT 1 FROM ${this.bq.t('CORREOS_ENVIADOS')}
        WHERE ID_USUARIO = @id AND TIPO = 'postula_facil'
      )
    `, { id: body.id_usuario }).catch(() => 0);

    if (inserted > 0) {
      const user = await this.bq.query<any>(`
        SELECT NOMBRE, EMAIL FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
      `, { id: body.id_usuario });

      if (user.length) {
        await this.email.send(
          user[0].EMAIL,
          '¡Tu perfil de Postula Fácil está listo!',
          this.email.postulaFacilHtml(user[0].NOMBRE, body.cargos),
        ).catch(() => null);
      }
    }

    // Trigger registration job at most once every 2 hours per user.
    // - If account already exists in CUENTAS_PORTALES → skip (success).
    // - If job was triggered in the last 2h (CORREOS_ENVIADOS) → skip (running).
    // - Otherwise → trigger and record the attempt.
    // This prevents parallel Cloud Run executions while still allowing
    // retries if the job failed (record expires after 2 hours).
    const accountExists = await this.bq.query<any>(`
      SELECT id_usuario FROM ${this.bq.t('CUENTAS_PORTALES')}
      WHERE id_usuario = @id AND portal = 'trabajando' LIMIT 1
    `, { id: body.id_usuario }).catch(() => []);

    if (!accountExists.length) {
      const recentlyTriggered = await this.bq.query<any>(`
        SELECT ID FROM ${this.bq.t('CORREOS_ENVIADOS')}
        WHERE ID_USUARIO = @id AND TIPO = 'registro_portales'
          AND FECHA >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 HOUR)
        LIMIT 1
      `, { id: body.id_usuario }).catch(() => []);

      if (!recentlyTriggered.length) {
        // Insert BEFORE triggering so concurrent saves don't race through
        await this.bq.query(`
          INSERT INTO ${this.bq.t('CORREOS_ENVIADOS')} (ID_USUARIO, TIPO, FECHA)
          VALUES (@id, 'registro_portales', CURRENT_TIMESTAMP())
        `, { id: body.id_usuario }).catch(() => null);

        this.cloudRun.triggerRegisterJob(body.id_usuario);
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
      rut: r.RUT || '',
      fecha_nacimiento: r.FECHA_NACIMIENTO || '',
      empresa: r.EMPRESA || '',
      anio_inicio: r.ANIO_INICIO ? String(r.ANIO_INICIO) : '',
      actualmente_trabajando: r.ACTUALMENTE_TRABAJANDO ?? true,
      anio_fin: r.ANIO_FIN ? String(r.ANIO_FIN) : '',
      nivel_educativo: r.NIVEL_EDUCATIVO || '',
      institucion: r.INSTITUCION || '',
      carrera: r.CARRERA || '',
      situacion_estudios: r.SITUACION_ESTUDIOS || '',
      anio_inicio_estudios: r.ANIO_INICIO_ESTUDIOS ? String(r.ANIO_INICIO_ESTUDIOS) : '',
      empresas_excluidas: this.parseJson(r.EMPRESAS_EXCLUIDAS),
      busqueda_activa: r.BUSQUEDA_ACTIVA ?? false,
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
      r.PROFESION && cargos.length &&
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
