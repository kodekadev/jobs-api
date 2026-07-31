import { Injectable, ForbiddenException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

const PLAN_LIMITS: Record<string, number> = {
  FREE: 5, PRO: 25, PREMIUM: 50, TRIAL: 25, TURBO: 40,
};

const ADMIN_EMAILS = ['bastian.alfaro@gmail.com'];

@Injectable()
export class AdminService {
  constructor(private readonly bq: BigQueryService) {}

  checkAdmin(email: string) {
    if (!ADMIN_EMAILS.includes((email || '').toLowerCase())) {
      throw new ForbiddenException('Acceso restringido a administradores');
    }
  }

  async getUsers() {
    const rows = await this.bq.query<any>(`
      WITH plan_latest AS (
        SELECT ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN,
          ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) AS rn
        FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
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
          ) AS sem_lab
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
          MAX(CASE WHEN portal = 'laborum'       AND cv_completo = TRUE THEN 1 ELSE 0 END) AS cv_lab
        FROM ${this.bq.t('CUENTAS_PORTALES')}
        GROUP BY id_usuario
      )
      SELECT
        u.ID_USUARIO,
        u.NOMBRE,
        u.EMAIL,
        u.FECHA_REGISTRO,
        COALESCE(pl.PLAN, 'FREE') AS plan,
        pl.ESTADO AS plan_estado,
        pl.FECHA_FIN,
        COALESCE(pa.ACTIVO, 0) AS autopilot_activo,
        pf.CARGOS,
        pf.UBICACIONES,
        COALESCE(ps.hoy, 0)      AS postulaciones_hoy,
        COALESCE(ps.total, 0)    AS total_postulaciones,
        COALESCE(ps.semana, 0)   AS postulaciones_7dias,
        COALESCE(ps.sem_tbj, 0)  AS postulaciones_7dias_tbj,
        COALESCE(ps.sem_cht, 0)  AS postulaciones_7dias_cht,
        COALESCE(ps.sem_cpt, 0)  AS postulaciones_7dias_cpt,
        COALESCE(ps.sem_lab, 0)  AS postulaciones_7dias_lab,
        COALESCE(por.tiene_tbj, 0) AS tiene_trabajando,
        COALESCE(por.cv_tbj, 0)    AS cv_trabajando,
        COALESCE(por.tiene_cht, 0) AS tiene_chiletrabajos,
        COALESCE(por.cv_cht, 0)    AS cv_chiletrabajos,
        COALESCE(por.tiene_cpt, 0) AS tiene_computrabajo,
        COALESCE(por.cv_cpt, 0)    AS cv_computrabajo,
        COALESCE(por.tiene_lab, 0) AS tiene_laborum,
        COALESCE(por.cv_lab, 0)    AS cv_laborum,
        CASE
          WHEN COALESCE(pl.PLAN, 'FREE') = 'FREE' THEN TRUE
          WHEN pl.PLAN = 'TRIAL'
            AND DATE(pl.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) THEN TRUE
          WHEN pl.PLAN NOT IN ('FREE', 'TRIAL') AND (
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
    `);

    return rows.map((r: any) => ({
      id:                      r.ID_USUARIO,
      nombre:                  r.NOMBRE || '',
      email:                   r.EMAIL || '',
      fecha_registro:          r.FECHA_REGISTRO?.value ?? r.FECHA_REGISTRO ?? null,
      plan:                    r.plan || 'FREE',
      plan_estado:             r.plan_estado || null,
      plan_vigente:            Boolean(r.plan_vigente),
      fecha_fin:               r.FECHA_FIN?.value ?? r.FECHA_FIN ?? null,
      autopilot_activo:        Boolean(r.autopilot_activo),
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
      postulaciones_hoy:       Number(r.postulaciones_hoy ?? 0),
      total_postulaciones:     Number(r.total_postulaciones ?? 0),
      postulaciones_7dias:     Number(r.postulaciones_7dias ?? 0),
      postulaciones_7dias_tbj: Number(r.postulaciones_7dias_tbj ?? 0),
      postulaciones_7dias_cht: Number(r.postulaciones_7dias_cht ?? 0),
      postulaciones_7dias_cpt: Number(r.postulaciones_7dias_cpt ?? 0),
      postulaciones_7dias_lab: Number(r.postulaciones_7dias_lab ?? 0),
      limite_dia:              PLAN_LIMITS[r.plan] ?? PLAN_LIMITS['FREE'],
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
              AND DATE(FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 14 DAY) THEN TRUE
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
          d.setDate(d.getDate() + (plan === 'TRIAL' ? 14 : 30));
          return d.toISOString();
        })();

    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET PLAN = @plan, ESTADO = 'ACTIVO', FECHA_INICIO = @now,
          FECHA_FIN = @fechaFin, METODO_PAGO = 'ADMIN'
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN, METODO_PAGO)
        VALUES (@id, @plan, 'ACTIVO', @now, @fechaFin, 'ADMIN')
    `, { id: userId, plan, now: new Date().toISOString(), fechaFin: fin });

    return { success: true };
  }

  private fmtDate(val: any): string {
    if (!val) return 'sin fecha';
    const d = new Date(val?.value ?? val);
    return isNaN(d.getTime()) ? 'sin fecha' : d.toLocaleDateString('es-CL');
  }

  private parseJson(val: any): string[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
