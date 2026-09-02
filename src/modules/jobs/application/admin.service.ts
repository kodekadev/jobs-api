import { Injectable, ForbiddenException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';

const PLAN_LIMITS: Record<string, number> = {
  FREE: 5, PRO: 25, PREMIUM: 50, TRIAL: 10, TURBO: 40,
};

// Cuentas internas excluidas de métricas comerciales
const INTERNAL_IDS = new Set([
  'jobs5', 'jobs9', 'jobs15', 'jobs27',
]);
const INTERNAL_EMAIL_PATTERN = /^(bastian\.alfaro@gmail\.com|.*@kodekadev\.com)$/i;

const ADMIN_EMAILS = ['bastian.alfaro@gmail.com'];

@Injectable()
export class AdminService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
  ) {}

  checkAdmin(email: string) {
    if (!ADMIN_EMAILS.includes((email || '').toLowerCase())) {
      throw new ForbiddenException('Acceso restringido a administradores');
    }
  }

  async getUsers() {
    const [rows, loginRows] = await Promise.all([
      this.bq.query<any>(`
      WITH plan_latest AS (
        SELECT ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN,
          ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) AS rn
        FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE', 'TRIAL')
      ),
      post_stats AS (
        SELECT
          id_usuario,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') = CURRENT_DATE('America/Santiago')
            AND portal NOT IN ('email_directo', '')
          ) AS hoy,
          COUNT(*) AS total,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
            AND portal NOT IN ('email_directo', '')
          ) AS semana,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
            AND portal = 'trabajando'
          ) AS sem_tbj,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
            AND portal = 'chiletrabajos'
          ) AS sem_cht,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
            AND portal = 'computrabajo'
          ) AS sem_cpt,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
            AND portal = 'laborum'
          ) AS sem_lab,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
            AND portal = 'empleaxchile'
          ) AS sem_exc
        FROM ${this.bq.t('EMPLEOS')}
        GROUP BY id_usuario
      ),
      portal_stats AS (
        SELECT
          id_usuario,
          MAX(CASE WHEN portal = 'trabajando'    THEN 1 ELSE 0 END)                        AS tiene_tbj,
          MAX(CASE WHEN portal = 'trabajando'    AND cv_completo = TRUE THEN 1 ELSE 0 END) AS cv_tbj,
          MAX(CASE WHEN portal = 'chiletrabajos' THEN 1 ELSE 0 END)                        AS tiene_cht,
          MAX(CASE WHEN portal = 'chiletrabajos' AND cv_completo = TRUE THEN 1 ELSE 0 END) AS cv_cht,
          MAX(CASE WHEN portal = 'computrabajo'  THEN 1 ELSE 0 END)                        AS tiene_cpt,
          MAX(CASE WHEN portal = 'computrabajo'  AND cv_completo = TRUE THEN 1 ELSE 0 END) AS cv_cpt,
          MAX(CASE WHEN portal = 'laborum'       THEN 1 ELSE 0 END)                        AS tiene_lab,
          MAX(CASE WHEN portal = 'laborum'       AND cv_completo = TRUE THEN 1 ELSE 0 END) AS cv_lab,
          MAX(CASE WHEN portal = 'empleaxchile'  THEN 1 ELSE 0 END)                        AS tiene_exc,
          MAX(CASE WHEN portal = 'empleaxchile'  AND cv_completo = TRUE THEN 1 ELSE 0 END) AS cv_exc
        FROM ${this.bq.t('CUENTAS_PORTALES')}
        GROUP BY id_usuario
      )
      SELECT
        u.ID_USUARIO,
        u.NOMBRE,
        u.EMAIL,
        u.FECHA_REGISTRO,
        CASE WHEN pl.ESTADO = 'TRIAL' THEN 'TRIAL' ELSE COALESCE(pl.PLAN, 'FREE') END AS plan,
        pl.ESTADO AS plan_estado,
        pl.FECHA_FIN,
        COALESCE(pa.ACTIVO, 0) AS autopilot_activo,
        (pf.ID_USUARIO IS NOT NULL) AS tiene_postulafacil,
        pf.CARGOS,
        pf.UBICACIONES,
        COALESCE(ps.hoy, 0)      AS postulaciones_hoy,
        COALESCE(ps.total, 0)    AS total_postulaciones,
        COALESCE(ps.semana, 0)   AS postulaciones_7dias,
        COALESCE(ps.sem_tbj, 0)  AS postulaciones_7dias_tbj,
        COALESCE(ps.sem_cht, 0)  AS postulaciones_7dias_cht,
        COALESCE(ps.sem_cpt, 0)  AS postulaciones_7dias_cpt,
        COALESCE(ps.sem_lab, 0)  AS postulaciones_7dias_lab,
        COALESCE(ps.sem_exc, 0)  AS postulaciones_7dias_exc,
        COALESCE(por.tiene_tbj, 0) AS tiene_trabajando,
        COALESCE(por.cv_tbj, 0)    AS cv_trabajando,
        COALESCE(por.tiene_cht, 0) AS tiene_chiletrabajos,
        COALESCE(por.cv_cht, 0)    AS cv_chiletrabajos,
        COALESCE(por.tiene_cpt, 0) AS tiene_computrabajo,
        COALESCE(por.cv_cpt, 0)    AS cv_computrabajo,
        COALESCE(por.tiene_lab, 0) AS tiene_laborum,
        COALESCE(por.cv_lab, 0)    AS cv_laborum,
        COALESCE(por.tiene_exc, 0) AS tiene_empleaxchile,
        COALESCE(por.cv_exc, 0)    AS cv_empleaxchile,
        CASE
          WHEN COALESCE(pl.PLAN, 'FREE') = 'FREE' THEN TRUE
          WHEN (pl.PLAN = 'TRIAL' OR pl.ESTADO = 'TRIAL')
            AND (
              (pl.FECHA_FIN IS NOT NULL AND DATE(pl.FECHA_FIN) >= CURRENT_DATE())
              OR (pl.FECHA_FIN IS NULL AND DATE(pl.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            ) THEN TRUE
          WHEN pl.PLAN NOT IN ('FREE', 'TRIAL') AND pl.ESTADO != 'TRIAL' AND (
            (pl.FECHA_FIN IS NOT NULL AND DATE(pl.FECHA_FIN) >= CURRENT_DATE())
            OR (pl.FECHA_FIN IS NULL
              AND DATE(pl.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
          ) THEN TRUE
          ELSE FALSE
        END AS plan_vigente
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN plan_latest pl ON u.ID_USUARIO = pl.ID_USUARIO AND pl.rn = 1
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')} pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN post_stats ps ON u.ID_USUARIO = ps.id_usuario
      LEFT JOIN portal_stats por ON u.ID_USUARIO = por.id_usuario
      WHERE u.NOMBRE != 'CUENTA_ELIMINADA'
        AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
      ORDER BY postulaciones_hoy DESC, total_postulaciones DESC
    `),
      this.bq.query<any>(`
        SELECT uid, MAX(created_at) AS ultima_conexion
        FROM ${this.bq.t('EVENTOS_ANALYTICS')}
        WHERE tipo = 'login' AND uid IS NOT NULL
        GROUP BY uid
      `).catch(() => [] as any[]),
    ]);

    const loginMap = new Map<string, any>(loginRows.map((r: any) => [r.uid, r.ultima_conexion]));

    return rows.map((r: any) => ({
      id:                      r.ID_USUARIO,
      nombre:                  r.NOMBRE || '',
      email:                   r.EMAIL || '',
      fecha_registro:          r.FECHA_REGISTRO?.value ?? r.FECHA_REGISTRO ?? null,
      plan:                    Boolean(r.plan_vigente) ? (r.plan || 'FREE') : 'FREE',
      plan_estado:             r.plan_estado || null,
      plan_vigente:            Boolean(r.plan_vigente),
      plan_original:           r.plan || 'FREE',
      fecha_fin:               r.FECHA_FIN?.value ?? r.FECHA_FIN ?? null,
      autopilot_activo:        Boolean(r.autopilot_activo),
      tiene_postulafacil:      Boolean(r.tiene_postulafacil),
      cargos:                  this.parseJson(r.CARGOS),
      tiene_cargos:            this.parseJson(r.CARGOS).length > 0,
      tiene_ubicaciones:       this.parseJson(r.UBICACIONES).length > 0,
      tiene_trabajando:        Boolean(r.tiene_trabajando),
      cv_trabajando:           Boolean(r.cv_trabajando),
      tiene_chiletrabajos:     Boolean(r.tiene_chiletrabajos),
      cv_chiletrabajos:        Boolean(r.cv_chiletrabajos),
      tiene_computrabajo:      Boolean(r.tiene_computrabajo),
      cv_computrabajo:         Boolean(r.cv_computrabajo),
      tiene_laborum:           Boolean(r.tiene_laborum),
      cv_laborum:              Boolean(r.cv_laborum),
      tiene_empleaxchile:      Boolean(r.tiene_empleaxchile),
      cv_empleaxchile:         Boolean(r.cv_empleaxchile),
      postulaciones_hoy:       Number(r.postulaciones_hoy ?? 0),
      total_postulaciones:     Number(r.total_postulaciones ?? 0),
      postulaciones_7dias:     Number(r.postulaciones_7dias ?? 0),
      postulaciones_7dias_tbj: Number(r.postulaciones_7dias_tbj ?? 0),
      postulaciones_7dias_cht: Number(r.postulaciones_7dias_cht ?? 0),
      postulaciones_7dias_cpt: Number(r.postulaciones_7dias_cpt ?? 0),
      postulaciones_7dias_lab: Number(r.postulaciones_7dias_lab ?? 0),
      postulaciones_7dias_exc: Number(r.postulaciones_7dias_exc ?? 0),
      limite_dia:              Boolean(r.plan_vigente) ? (PLAN_LIMITS[r.plan] ?? PLAN_LIMITS['FREE']) : PLAN_LIMITS['FREE'],
      ultima_conexion:         (() => { const v = loginMap.get(r.ID_USUARIO); return v?.value ?? v ?? null; })(),
    }));
  }

  async getDiagnostics(userId: string) {
    const [pfRows, planRows, portalRows, stats] = await Promise.all([
      this.bq.query<any>(`
        SELECT CARGOS, UBICACIONES FROM ${this.bq.t('POSTULA_FACIL')}
        WHERE ID_USUARIO = @id LIMIT 1
      `, { id: userId }),

      this.bq.query<any>(`
        SELECT PLAN, ESTADO, FECHA_INICIO, FECHA_FIN,
          CASE
            WHEN PLAN = 'FREE' THEN TRUE
            WHEN PLAN = 'TRIAL'
              AND (
                (FECHA_FIN IS NOT NULL AND DATE(FECHA_FIN) >= CURRENT_DATE())
                OR (FECHA_FIN IS NULL AND DATE(FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
              ) THEN TRUE
            WHEN PLAN NOT IN ('FREE', 'TRIAL') AND (
              (FECHA_FIN IS NOT NULL AND DATE(FECHA_FIN) >= CURRENT_DATE())
              OR (FECHA_FIN IS NULL
                AND DATE(FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
            ) THEN TRUE
            ELSE FALSE
          END AS vigente
        FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ID_USUARIO = @id AND ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
        ORDER BY FECHA_INICIO DESC LIMIT 1
      `, { id: userId }),

      this.bq.query<any>(`
        SELECT portal, cv_completo, email FROM ${this.bq.t('CUENTAS_PORTALES')}
        WHERE id_usuario = @id
      `, { id: userId }),

      this.bq.query<any>(`
        SELECT
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') = CURRENT_DATE('America/Santiago')
            AND portal NOT IN ('email_directo', '')
          ) AS hoy,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') = CURRENT_DATE('America/Santiago')
            AND portal = 'linkedin'
          ) AS hoy_linkedin,
          COUNTIF(
            DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
          ) AS semana
        FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id
      `, { id: userId }),
    ]);

    const pf    = pfRows[0];
    const plan  = planRows[0];
    const stat  = stats[0];
    const cargos     = this.parseJson(pf?.CARGOS);
    const ubicaciones = this.parseJson(pf?.UBICACIONES);
    const planStr    = plan?.PLAN || 'FREE';
    const limite     = PLAN_LIMITS[planStr] ?? 5;
    const hoy        = Number(stat?.hoy ?? 0);
    const hoyLkd     = Number(stat?.hoy_linkedin ?? 0);
    const semana     = Number(stat?.semana ?? 0);
    const tbj = (portalRows as any[]).find(p => p.portal === 'trabajando');
    const cht = (portalRows as any[]).find(p => p.portal === 'chiletrabajos');
    const cpt = (portalRows as any[]).find(p => p.portal === 'computrabajo');
    const lab = (portalRows as any[]).find(p => p.portal === 'laborum');

    return {
      plan: planStr,
      plan_vigente: Boolean(plan?.vigente),
      fecha_fin: plan?.FECHA_FIN?.value ?? plan?.FECHA_FIN ?? null,
      hoy, hoyLkd, semana, limite,
      checks: [
        {
          key: 'plan',
          label: 'Plan vigente',
          ok: Boolean(plan?.vigente),
          detalle: plan ? `${planStr} — vence ${this.fmtDate(plan.FECHA_FIN)}` : 'Sin plan activo',
        },
        {
          key: 'cargos',
          label: 'Cargos configurados',
          ok: cargos.length > 0,
          detalle: cargos.length ? cargos.join(', ') : 'Sin cargos — el autopilot no sabe qué buscar',
        },
        {
          key: 'ubicaciones',
          label: 'Ubicaciones configuradas',
          ok: ubicaciones.length > 0,
          detalle: ubicaciones.length ? ubicaciones.join(', ') : 'Sin ubicaciones',
        },
        {
          key: 'limite',
          label: 'Cupo diario disponible',
          ok: hoy < limite,
          detalle: `${hoy} / ${limite} hoy`,
        },
        {
          key: 'cuenta_tbj',
          label: 'Cuenta Trabajando.cl',
          ok: Boolean(tbj),
          detalle: tbj?.email || 'Sin cuenta — ejecutar crear_cuentas_portales.py',
        },
        {
          key: 'cv_tbj',
          label: 'CV completo en Trabajando',
          ok: Boolean(tbj?.cv_completo),
          detalle: tbj
            ? tbj.cv_completo ? 'Completo ✓' : 'Incompleto — ejecutar completar_cvs_portales.py'
            : 'N/A (sin cuenta)',
        },
        {
          key: 'cuenta_cht',
          label: 'Cuenta ChileTrabajos',
          ok: Boolean(cht),
          detalle: cht?.email || 'Sin cuenta',
        },
        {
          key: 'cuenta_cpt',
          label: 'Cuenta Computrabajo',
          ok: Boolean(cpt),
          detalle: cpt?.email || 'Sin cuenta',
        },
        {
          key: 'cuenta_lab',
          label: 'Cuenta Laborum',
          ok: Boolean(lab),
          detalle: lab?.email || 'Sin cuenta',
        },
        {
          key: 'linkedin',
          label: 'LinkedIn extensión hoy',
          ok: hoyLkd > 0,
          detalle: hoyLkd > 0 ? `${hoyLkd} postulaciones LinkedIn hoy` : 'Sin postulaciones LinkedIn hoy',
        },
      ],
    };
  }

  async setPlan(userId: string, plan: string, fechaFin?: string) {
    const fin = fechaFin
      ? new Date(fechaFin).toISOString()
      : (() => {
          const d = new Date();
          d.setDate(d.getDate() + (plan === 'TRIAL' ? 7 : 30));
          return d.toISOString();
        })();

    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET PLAN = @plan, ESTADO = 'ACTIVO', FECHA_INICIO = @now,
          FECHA_FIN = @fechaFin, MEDIO_PAGO = 'ADMIN'
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN, MEDIO_PAGO)
        VALUES (@id, @plan, 'ACTIVO', @now, @fechaFin, 'ADMIN')
    `, { id: userId, plan, now: new Date().toISOString(), fechaFin: fin });

    return { success: true };
  }

  private fmtDate(val: any): string {
    if (!val) return 'sin fecha';
    const d = new Date(val?.value ?? val);
    return isNaN(d.getTime()) ? 'sin fecha' : d.toLocaleDateString('es-CL');
  }

  async setCargos(userId: string, cargos: string[]) {
    const json = JSON.stringify(cargos);
    await this.bq.query(`
      UPDATE ${this.bq.t('POSTULA_FACIL')}
      SET CARGOS = @cargos
      WHERE ID_USUARIO = @id
    `, { id: userId, cargos: json });
    return { success: true };
  }

  async deletePortal(userId: string, portal: string) {
    await this.bq.query(`
      DELETE FROM ${this.bq.t('CUENTAS_PORTALES')}
      WHERE LOWER(id_usuario) = LOWER(@id) AND LOWER(portal) = LOWER(@portal)
    `, { id: userId, portal });
    return { success: true };
  }

  async deletePostulacion(userId: string, idEmpleo: string) {
    await this.bq.query(`
      DELETE FROM ${this.bq.t('EMPLEOS')}
      WHERE id_usuario = @uid AND (id_empleo = @emp OR link = @emp)
    `, { uid: userId, emp: idEmpleo });
    return { success: true };
  }

  async trackEvent(tipo: string, uid?: string, emailTipo?: string, ip?: string): Promise<void> {
    const id = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const doInsert = () => this.bq.dml(`
      INSERT INTO ${this.bq.t('EVENTOS_ANALYTICS')} (id, tipo, uid, email_tipo, ip, created_at)
      VALUES (@id, @tipo, @uid, @emailTipo, @ip, CURRENT_TIMESTAMP())
    `, { id, tipo, uid: uid || null, emailTipo: emailTipo || null, ip: ip || null });

    try {
      await doInsert();
    } catch {
      await this.bq.dml(`
        CREATE TABLE IF NOT EXISTS ${this.bq.t('EVENTOS_ANALYTICS')} (
          id STRING NOT NULL,
          tipo STRING NOT NULL,
          uid STRING,
          email_tipo STRING,
          ip STRING,
          created_at TIMESTAMP NOT NULL
        )
        PARTITION BY DATE(created_at)
      `);
      await doInsert();
    }
  }

  async getAnalytics() {
    const [rows, dauRows, convRows, emailRows, funnelRows, retentionRows] = await Promise.all([
      this.bq.query<any>(`
        SELECT
          pf.PROFESION, pf.CARGOS, pf.UBICACIONES, pf.ACTUALMENTE_TRABAJANDO, pf.PRETENSION_GENERAL,
          EXTRACT(YEAR FROM CURRENT_DATE())
            - EXTRACT(YEAR FROM SAFE_CAST(pf.FECHA_NACIMIENTO AS DATE)) AS edad_aprox,
          u.NOMBRE, u.EMAIL,
          CASE WHEN pc.ESTADO = 'TRIAL' THEN 'TRIAL' ELSE COALESCE(pc.PLAN, 'FREE') END AS plan,
          pc.FECHA_FIN,
          DATE_DIFF(DATE(pc.FECHA_FIN), CURRENT_DATE(), DAY) AS dias_para_vencer
        FROM ${this.bq.t('POSTULA_FACIL')} pf
        LEFT JOIN ${this.bq.t('USUARIOS')} u
          ON LOWER(u.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        LEFT JOIN (
          SELECT ID_USUARIO, PLAN, ESTADO, FECHA_FIN, FECHA_INICIO
          FROM ${this.bq.t('PLAN_CONTRATADO')}
          WHERE ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE', 'TRIAL')
          QUALIFY ROW_NUMBER() OVER (
            PARTITION BY LOWER(ID_USUARIO) ORDER BY FECHA_INICIO DESC
          ) = 1
        ) pc ON LOWER(pc.ID_USUARIO) = LOWER(pf.ID_USUARIO)
        WHERE COALESCE(u.NOMBRE, '') != 'CUENTA_ELIMINADA'
          AND NOT STARTS_WITH(COALESCE(u.EMAIL, ''), 'deleted_')
      `),

      // DAU últimos 7 días (desde EMPLEOS)
      this.bq.query<any>(`
        SELECT
          FORMAT_DATE('%Y-%m-%d', DATE(Fecha_Postulacion, 'America/Santiago')) AS fecha,
          COUNT(DISTINCT id_usuario) AS activos,
          COUNT(*) AS postulaciones
        FROM ${this.bq.t('EMPLEOS')}
        WHERE DATE(Fecha_Postulacion, 'America/Santiago')
          >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
          AND portal NOT IN ('email_directo', '')
        GROUP BY 1 ORDER BY 1
      `),

      // Conversiones reales (pagos) últimos 30 días
      // Excluye legacy TRIAL (PLAN='PRO' + ESTADO='TRIAL') y ADMIN
      this.bq.query<any>(`
        SELECT
          FORMAT_DATE('%Y-%m-%d', DATE(FECHA_INICIO, 'America/Santiago')) AS fecha,
          PLAN AS plan,
          COUNT(*) AS conversiones
        FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ESTADO = 'ACTIVO'
          AND PLAN NOT IN ('FREE', 'TRIAL')
          AND COALESCE(MEDIO_PAGO, '') NOT IN ('', 'ADMIN')
          AND DATE(FECHA_INICIO, 'America/Santiago')
            >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 30 DAY)
        GROUP BY 1, 2 ORDER BY 1
      `),

      // Email events (tracking pixel / click) — tabla puede no existir aún
      this.bq.query<any>(`
        SELECT tipo, email_tipo,
          FORMAT_DATE('%Y-%m-%d', DATE(created_at, 'America/Santiago')) AS dia,
          COUNT(*) AS total, COUNT(DISTINCT uid) AS unicos
        FROM ${this.bq.t('EVENTOS_ANALYTICS')}
        WHERE DATE(created_at, 'America/Santiago')
          >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
        GROUP BY 1, 2, 3
        ORDER BY 3
      `).catch(() => [] as any[]),

      // Embudo de conversión: trials activos → pagos totales
      this.bq.query<any>(`
        SELECT
          COUNTIF((ESTADO = 'TRIAL' OR (PLAN = 'TRIAL' AND ESTADO = 'ACTIVO'))
            AND (FECHA_FIN IS NULL OR DATE(FECHA_FIN) >= CURRENT_DATE())
          ) AS trials_activos,
          COUNTIF(
            ESTADO = 'ACTIVO'
            AND PLAN NOT IN ('FREE', 'TRIAL')
            AND COALESCE(MEDIO_PAGO, '') NOT IN ('', 'ADMIN')
            AND DATE(FECHA_FIN) >= CURRENT_DATE()
          ) AS pagadores_activos,
          COUNTIF(
            ESTADO = 'ACTIVO'
            AND PLAN NOT IN ('FREE', 'TRIAL')
            AND COALESCE(MEDIO_PAGO, '') NOT IN ('', 'ADMIN')
          ) AS pagadores_historico,
          COUNTIF(ESTADO = 'CANCELADO_PENDIENTE') AS cancelados_pendientes
        FROM (
          SELECT ID_USUARIO, PLAN, ESTADO, FECHA_FIN, MEDIO_PAGO,
            ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) AS rn
          FROM ${this.bq.t('PLAN_CONTRATADO')}
        )
        WHERE rn = 1
      `),

      // Retención: usuarios que siguen activos de los primeros 30 registrados
      this.bq.query<any>(`
        WITH primeros AS (
          SELECT ID_USUARIO, DATE(FECHA_REGISTRO) AS fecha_registro
          FROM ${this.bq.t('USUARIOS')}
          WHERE NOMBRE != 'CUENTA_ELIMINADA'
            AND NOT STARTS_WITH(COALESCE(EMAIL, ''), 'deleted_')
          ORDER BY FECHA_REGISTRO ASC
          LIMIT 50
        ),
        actividad AS (
          SELECT id_usuario,
            MAX(DATE(Fecha_Postulacion, 'America/Santiago')) AS ultima_postulacion
          FROM ${this.bq.t('EMPLEOS')}
          GROUP BY id_usuario
        )
        SELECT
          COUNT(*) AS total,
          COUNTIF(DATE_DIFF(CURRENT_DATE(), a.ultima_postulacion, DAY) <= 7)  AS activos_7d,
          COUNTIF(DATE_DIFF(CURRENT_DATE(), a.ultima_postulacion, DAY) <= 30) AS activos_30d,
          COUNTIF(a.ultima_postulacion IS NULL) AS nunca_postularon
        FROM primeros p
        LEFT JOIN actividad a ON LOWER(a.id_usuario) = LOWER(p.ID_USUARIO)
      `),
    ]);

    const total = rows.length;
    const porPlan: Record<string, number> = {};
    const edades: Record<string, number> = { '18-25': 0, '26-35': 0, '36-45': 0, '46-55': 0, '55+': 0, 'sin_dato': 0 };
    const profesionesCnt: Record<string, number> = {};
    const ubicacionesCnt: Record<string, number> = {};
    const cargosCnt: Record<string, number> = {};
    const pretensiones: Record<string, number> = { '<500k': 0, '500k-800k': 0, '800k-1.2M': 0, '1.2M-2M': 0, '>2M': 0, 'sin_dato': 0 };
    let conEmpleo = 0, sinEmpleo = 0;
    const porVencer: any[] = [];

    for (const r of rows) {
      const plan = r.plan || 'FREE';
      porPlan[plan] = (porPlan[plan] || 0) + 1;

      // Edad
      const edad = Number(r.edad_aprox);
      if (!r.edad_aprox || isNaN(edad) || edad < 15 || edad > 90) edades['sin_dato']++;
      else if (edad <= 25) edades['18-25']++;
      else if (edad <= 35) edades['26-35']++;
      else if (edad <= 45) edades['36-45']++;
      else if (edad <= 55) edades['46-55']++;
      else edades['55+']++;

      // Profesión
      const prof = (r.PROFESION || '').trim();
      if (prof) profesionesCnt[prof] = (profesionesCnt[prof] || 0) + 1;

      // Empleo actual
      if (r.ACTUALMENTE_TRABAJANDO === true || r.ACTUALMENTE_TRABAJANDO === 1) conEmpleo++;
      else if (r.ACTUALMENTE_TRABAJANDO === false || r.ACTUALMENTE_TRABAJANDO === 0) sinEmpleo++;

      // Pretensión
      const raw = String(r.PRETENSION_GENERAL || '')
        .replace(/[$ \.]/g, '').replace(/,/g, '').replace(/k$/i, '000');
      const val = parseInt(raw, 10);
      if (!val || isNaN(val)) pretensiones['sin_dato']++;
      else if (val < 500_000)   pretensiones['<500k']++;
      else if (val < 800_000)   pretensiones['500k-800k']++;
      else if (val < 1_200_000) pretensiones['800k-1.2M']++;
      else if (val < 2_000_000) pretensiones['1.2M-2M']++;
      else                      pretensiones['>2M']++;

      // Ubicaciones
      for (const ub of this.parseJson(r.UBICACIONES)) {
        const u = ub.trim();
        if (u) ubicacionesCnt[u] = (ubicacionesCnt[u] || 0) + 1;
      }

      // Cargos
      for (const c of this.parseJson(r.CARGOS)) {
        const c2 = c.trim();
        if (c2) cargosCnt[c2] = (cargosCnt[c2] || 0) + 1;
      }

      // Planes por vencer (≤7 días, excluye FREE y ya vencidos)
      const dias = Number(r.dias_para_vencer);
      if (plan !== 'FREE' && r.FECHA_FIN && !isNaN(dias) && dias >= 0 && dias <= 7) {
        porVencer.push({
          nombre: r.NOMBRE || '',
          email: r.EMAIL || '',
          plan,
          fecha_fin: r.FECHA_FIN?.value ?? r.FECHA_FIN,
          dias,
        });
      }
    }

    const topN = (obj: Record<string, number>, n = 10) =>
      Object.entries(obj)
        .sort((a, b) => b[1] - a[1])
        .slice(0, n)
        .map(([nombre, count]) => ({ nombre, count }));

    // DAU 7d
    const dau_7d = dauRows.map((r: any) => ({
      fecha:         String(r.fecha),
      activos:       Number(r.activos),
      postulaciones: Number(r.postulaciones),
    }));

    // Conversiones 7d — agrupar por fecha sumando planes
    const convByFecha: Record<string, { fecha: string; total: number; breakdown: Record<string, number> }> = {};
    for (const r of convRows) {
      const f = String(r.fecha);
      if (!convByFecha[f]) convByFecha[f] = { fecha: f, total: 0, breakdown: {} };
      convByFecha[f].total += Number(r.conversiones);
      convByFecha[f].breakdown[r.plan] = (convByFecha[f].breakdown[r.plan] || 0) + Number(r.conversiones);
    }
    const conv_7d = Object.values(convByFecha).sort((a, b) => a.fecha.localeCompare(b.fecha));

    // Email metrics
    let email_opens = 0, email_clicks = 0;
    const email_by_tipo: Record<string, { opens: number; clicks: number }> = {};
    const email_by_day:  Record<string, { opens: number; clicks: number }> = {};
    for (const r of emailRows) {
      const tipo = String(r.tipo || '');
      const et   = String(r.email_tipo || 'desconocido');
      const dia  = String(r.dia || '');
      const cnt  = Number(r.total);
      if (!email_by_tipo[et]) email_by_tipo[et] = { opens: 0, clicks: 0 };
      if (!email_by_day[dia]) email_by_day[dia]  = { opens: 0, clicks: 0 };
      if (tipo === 'email_open') {
        email_opens += cnt;
        email_by_tipo[et].opens += cnt;
        email_by_day[dia].opens += cnt;
      } else if (tipo.startsWith('email_click')) {
        email_clicks += cnt;
        email_by_tipo[et].clicks += cnt;
        email_by_day[dia].clicks += cnt;
      }
    }
    const email_daily = Object.entries(email_by_day)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([dia, s]) => ({ dia, opens: s.opens, clicks: s.clicks }));

    return {
      total,
      porPlan,
      edades,
      profesiones: topN(profesionesCnt),
      ubicaciones: topN(ubicacionesCnt),
      cargos:      topN(cargosCnt),
      pretensiones,
      empleo: {
        con:      conEmpleo,
        sin:      sinEmpleo,
        sin_dato: total - conEmpleo - sinEmpleo,
      },
      porVencer: porVencer.sort((a, b) => a.dias - b.dias),
      dau_7d,
      conv_7d,
      email_stats: {
        opens:    email_opens,
        clicks:   email_clicks,
        by_tipo:  email_by_tipo,
        daily:    email_daily,
      },
      funnel: funnelRows[0] ? {
        trials_activos:       Number(funnelRows[0].trials_activos   ?? 0),
        pagadores_activos:    Number(funnelRows[0].pagadores_activos ?? 0),
        pagadores_historico:  Number(funnelRows[0].pagadores_historico ?? 0),
        cancelados_pendientes: Number(funnelRows[0].cancelados_pendientes ?? 0),
      } : null,
      retencion_primeros_50: retentionRows[0] ? {
        total:            Number(retentionRows[0].total         ?? 0),
        activos_7d:       Number(retentionRows[0].activos_7d    ?? 0),
        activos_30d:      Number(retentionRows[0].activos_30d   ?? 0),
        nunca_postularon: Number(retentionRows[0].nunca_postularon ?? 0),
      } : null,
    };
  }

  async getBilling() {
    const rows = await this.bq.query<any>(`
      WITH plan_latest AS (
        SELECT ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN,
          ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) AS rn
        FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE', 'TRIAL')
      )
      SELECT
        u.ID_USUARIO,
        u.NOMBRE,
        u.EMAIL,
        u.FECHA_REGISTRO,
        CASE WHEN pl.ESTADO = 'TRIAL' THEN 'TRIAL' ELSE COALESCE(pl.PLAN, 'FREE') END AS plan,
        pl.ESTADO AS plan_estado,
        pl.FECHA_INICIO,
        pl.FECHA_FIN,
        CASE
          WHEN COALESCE(pl.PLAN, 'FREE') = 'FREE' THEN TRUE
          WHEN (pl.PLAN = 'TRIAL' OR pl.ESTADO = 'TRIAL')
            AND (
              (pl.FECHA_FIN IS NOT NULL AND DATE(pl.FECHA_FIN) >= CURRENT_DATE())
              OR (pl.FECHA_FIN IS NULL AND DATE(pl.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
            ) THEN TRUE
          WHEN pl.PLAN NOT IN ('FREE', 'TRIAL') AND pl.ESTADO != 'TRIAL' AND (
            (pl.FECHA_FIN IS NOT NULL AND DATE(pl.FECHA_FIN) >= CURRENT_DATE())
            OR (pl.FECHA_FIN IS NULL
              AND DATE(pl.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
          ) THEN TRUE
          ELSE FALSE
        END AS plan_vigente
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN plan_latest pl ON u.ID_USUARIO = pl.ID_USUARIO AND pl.rn = 1
      WHERE u.NOMBRE != 'CUENTA_ELIMINADA'
        AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
        AND u.ACTIVO IS NOT FALSE
      ORDER BY pl.FECHA_INICIO DESC
    `);

    const PLAN_PRICES: Record<string, number> = { PRO: 9990, TURBO: 14990, PREMIUM: 19990 };

    let totalUsuarios = 0, pagadores = 0, trial = 0, free = 0, vencidos = 0, cancelados = 0;
    let mrr = 0;
    const pagadoresList: any[] = [];
    const trialList: any[] = [];

    for (const r of rows) {
      const uid = String(r.ID_USUARIO || '');
      const email = String(r.EMAIL || '');
      if (INTERNAL_IDS.has(uid.toLowerCase()) || INTERNAL_EMAIL_PATTERN.test(email)) continue;

      totalUsuarios++;
      const plan = r.plan || 'FREE';
      const vigente = Boolean(r.plan_vigente);
      const estado = r.plan_estado || null;

      if (plan === 'TRIAL' && vigente) {
        trial++;
        trialList.push({
          id: uid, nombre: r.NOMBRE || '', email,
          fecha_inicio: r.FECHA_INICIO?.value ?? r.FECHA_INICIO ?? null,
          fecha_fin: r.FECHA_FIN?.value ?? r.FECHA_FIN ?? null,
        });
      } else if (plan !== 'FREE' && plan !== 'TRIAL' && vigente) {
        pagadores++;
        const precio = PLAN_PRICES[plan] ?? 0;
        if (estado !== 'CANCELADO_PENDIENTE') mrr += precio;
        if (estado === 'CANCELADO_PENDIENTE') cancelados++;
        pagadoresList.push({
          id: uid, nombre: r.NOMBRE || '', email, plan, estado,
          fecha_fin: r.FECHA_FIN?.value ?? r.FECHA_FIN ?? null,
          precio,
        });
      } else if (!vigente && plan !== 'FREE') {
        vencidos++;
      } else {
        free++;
      }
    }

    // Total histórico: suma de precios de todos los planes pagados activados
    const totalCobradoHistorico = pagadoresList.reduce((s: number, p: any) => s + (p.precio ?? 0), 0)
      + vencidos * 9990; // estimación conservadora planes vencidos (todos como PRO)

    return {
      total_pagadores: pagadores,
      total_trials: trial,
      free,
      vencidos,
      cancelados,
      mrr,
      total_cobrado_historico: totalCobradoHistorico,
      pagadores: pagadoresList
        .sort((a: any, b: any) => b.precio - a.precio)
        .map((p: any) => ({
          nombre: p.nombre,
          email:  p.email,
          plan:   p.plan,
          estado: p.estado,
          monto:  p.precio,
          fecha_inicio: null,
          fecha_fin: p.fecha_fin,
        })),
      trials: trialList,
    };
  }

  private parseJson(val: any): string[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }

  // ─── Cargo quality ────────────────────────────────────────────────────────

  private readonly _SENIORITY = new Set([
    'jefe', 'gerente', 'director', 'coordinador', 'analista', 'asistente',
    'auxiliar', 'ejecutivo', 'operario', 'supervisor', 'encargado', 'lider',
    'especialista', 'tecnico', 'practicante', 'junior', 'senior', 'trainee',
    'subgerente', 'responsable', 'ayudante', 'agente', 'profesional',
    'consultor', 'consultora', 'vendedor', 'vendedora', 'comercial',
    'subdirector', 'subdirectora', 'representante', 'promotor', 'promotora',
  ]);

  private _normCargo(text: string): string {
    return text.normalize('NFKD').replace(/[̀-ͯ]/g, '')
      .toLowerCase().replace(/\/[ao]s?\b/gi, '').trim();
  }

  private _isBadCargo(cargo: string): boolean {
    const words = this._normCargo(cargo).split(/\s+/).filter(w => w.length > 2);
    return words.length > 0 && words.every(w => this._SENIORITY.has(w));
  }

  async getCargoQuality() {
    const rows = await this.bq.query<any>(`
      SELECT u.ID_USUARIO, u.NOMBRE, u.EMAIL, pf.CARGOS
      FROM ${this.bq.t('USUARIOS')} u
      JOIN ${this.bq.t('POSTULA_FACIL')} pf ON pf.ID_USUARIO = u.ID_USUARIO
      WHERE pf.CARGOS IS NOT NULL AND pf.CARGOS != '[]' AND pf.CARGOS != ''
    `);

    const flagged: any[] = [];
    for (const r of rows) {
      if (INTERNAL_EMAIL_PATTERN.test(r.EMAIL || '')) continue;
      const cargos: string[] = this.parseJson(r.CARGOS);
      if (!cargos.length) continue;
      const badOnes = cargos.filter(c => this._isBadCargo(c));
      if (badOnes.length > 0 && badOnes.length === cargos.length) {
        flagged.push({
          uid:    r.ID_USUARIO,
          nombre: r.NOMBRE || '',
          email:  r.EMAIL  || '',
          cargos,
          bad:    badOnes,
        });
      }
    }
    return flagged;
  }

  async notifyCargoQuality(uids: string[]) {
    const all = await this.getCargoQuality();
    const targets = uids.length ? all.filter(u => uids.includes(u.uid)) : all;
    const sent: string[] = []; const failed: string[] = [];
    for (const u of targets) {
      try {
        const html = this.email.cargoQualityHtml(u.nombre, u.cargos);
        await this.email.send(u.email, 'Mejora tus cargos en AplicAI — postula mejor', html);
        sent.push(u.email);
      } catch { failed.push(u.email); }
    }
    return { sent: sent.length, failed };
  }

  async sendCampaign(uids: string[], descuento_pct: number, vigencia_hasta: string) {
    if (!uids?.length) return { sent: 0, failed: [] };

    const placeholders = uids.map((_, i) => `@uid${i}`).join(', ');
    const params = Object.fromEntries(uids.map((uid, i) => [`uid${i}`, uid]));
    const rows = await this.bq.query<any>(`
      SELECT u.ID_USUARIO, u.NOMBRE, u.EMAIL,
             pc.PLAN
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN (
        SELECT ID_USUARIO, PLAN
        FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ESTADO IN ('ACTIVO','TRIAL')
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) = 1
      ) pc ON pc.ID_USUARIO = u.ID_USUARIO
      WHERE u.ID_USUARIO IN (${placeholders})
    `, params);

    const fmtFecha = (s: string) => {
      const [y, m, d] = s.split('-');
      return `${d}/${m}/${y}`;
    };
    const vigFmt = vigencia_hasta ? fmtFecha(vigencia_hasta) : '';
    const sent: string[] = [];
    const failed: string[] = [];

    for (const r of rows) {
      const to = r.EMAIL || r.email;
      const nombre = r.NOMBRE || r.nombre || 'usuario';
      const plan = r.PLAN || r.plan || '';
      if (!to) continue;
      try {
        const html = this.email.campaignHtml(nombre, descuento_pct, vigFmt, plan);
        await this.email.send(to, `Oferta especial: ${descuento_pct}% de descuento en AplicAI`, html);
        sent.push(to);
      } catch {
        failed.push(to);
      }
    }
    return { sent: sent.length, failed };
  }

  async getPortalStats(days: number = 14) {
    const rows = await this.bq.query<any>(`
      SELECT
        DATE(Fecha_Postulacion, 'America/Santiago') AS fecha,
        portal,
        COUNT(*) AS total
      FROM ${this.bq.t('EMPLEOS')}
      WHERE DATE(Fecha_Postulacion, 'America/Santiago') >= DATE_SUB(CURRENT_DATE('America/Santiago'), INTERVAL @days DAY)
        AND portal IS NOT NULL
      GROUP BY fecha, portal
      ORDER BY fecha ASC
    `, { days });

    // Pivotear: [{ fecha, computrabajo, laborum, chiletrabajos, empleaxchile }]
    const byDate: Record<string, Record<string, number>> = {};
    for (const r of rows) {
      const d = r.fecha?.value ?? r.fecha;
      if (!d) continue;
      if (!byDate[d]) byDate[d] = {};
      byDate[d][r.portal] = Number(r.total ?? 0);
    }
    return Object.entries(byDate)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([fecha, portales]) => ({ fecha, ...portales }));
  }

  async getUserFeedback(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT RATING_SERVICIO, RATING_POSTULACIONES, COMENTARIO, TIPO, FECHA
      FROM ${this.bq.t('AUTOPILOT_FEEDBACK')}
      WHERE ID_USUARIO = @id
      ORDER BY FECHA DESC
      LIMIT 20
    `, { id: userId });
    return rows.map((r: any) => ({
      rating_servicio:      r.RATING_SERVICIO ?? null,
      rating_postulaciones: r.RATING_POSTULACIONES ?? null,
      comentario:           r.COMENTARIO || '',
      tipo:                 r.TIPO || '',
      fecha:                r.FECHA?.value ?? r.FECHA ?? null,
    }));
  }
}
