import { Injectable, UnauthorizedException, NotFoundException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import env from '../../shared/infrastructure/environment';

const PLAN_LIMITE: Record<string, number> = {
  TRIAL: 5, PRO: 10, TURBO: 15, PREMIUM: 20, SPRINT: 25, OWNER: 50, FREE: 0,
};

@Injectable()
export class AdminService {
  constructor(private readonly bq: BigQueryService) {}

  verifyPin(pin: string) {
    if (!env.adminSecret || pin !== env.adminSecret) {
      throw new UnauthorizedException('PIN incorrecto');
    }
    return { ok: true };
  }

  async getUsers() {
    const rows = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO                                                     AS id,
        u.NOMBRE                                                         AS nombre,
        u.EMAIL                                                          AS email,
        COALESCE(pc.PLAN, 'FREE')                                        AS plan,
        COALESCE(
          pc.ESTADO = 'ACTIVO' AND
          (pc.FECHA_FIN IS NULL OR pc.FECHA_FIN > CURRENT_TIMESTAMP()),
          FALSE
        )                                                                AS plan_vigente,
        CAST(pc.FECHA_FIN AS STRING)                                     AS fecha_fin,
        COALESCE(pa.activo, 0)                                           AS autopilot_activo,
        pf.CARGOS                                                        AS cargos_raw,
        pf.UBICACIONES                                                   AS ubicaciones_raw,
        (pf.CARGOS IS NOT NULL AND pf.CARGOS NOT IN ('', '[]', 'null')) AS tiene_cargos,
        (pf.UBICACIONES IS NOT NULL AND pf.UBICACIONES NOT IN ('', '[]', 'null')) AS tiene_ubicaciones,
        COUNTIF(cp.PORTAL = 'trabajando')    > 0                         AS tiene_trabajando,
        COUNTIF(cp.PORTAL = 'chiletrabajos') > 0                         AS tiene_chiletrabajos,
        COUNTIF(cp.PORTAL = 'computrabajo')  > 0                         AS tiene_computrabajo,
        COUNTIF(cp.PORTAL = 'laborum')       > 0                         AS tiene_laborum,
        MAX(CASE WHEN cp.PORTAL = 'trabajando'    THEN COALESCE(cp.CV_COMPLETO, FALSE) ELSE FALSE END) AS cv_trabajando,
        MAX(CASE WHEN cp.PORTAL = 'chiletrabajos' THEN COALESCE(cp.CV_COMPLETO, FALSE) ELSE FALSE END) AS cv_chiletrabajos,
        MAX(CASE WHEN cp.PORTAL = 'computrabajo'  THEN COALESCE(cp.CV_COMPLETO, FALSE) ELSE FALSE END) AS cv_computrabajo,
        MAX(CASE WHEN cp.PORTAL = 'laborum'       THEN COALESCE(cp.CV_COMPLETO, FALSE) ELSE FALSE END) AS cv_laborum,
        COUNTIF(EXTRACT(DATE FROM e.FECHA_POSTULACION AT TIME ZONE 'America/Santiago') = CURRENT_DATE('America/Santiago')) AS postulaciones_hoy,
        COUNTIF(e.FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY))             AS postulaciones_7dias,
        COUNTIF(e.FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) AND LOWER(COALESCE(e.PORTAL,'')) = 'trabajando')    AS postulaciones_7dias_tbj,
        COUNTIF(e.FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) AND LOWER(COALESCE(e.PORTAL,'')) = 'chiletrabajos') AS postulaciones_7dias_cht,
        COUNTIF(e.FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) AND LOWER(COALESCE(e.PORTAL,'')) = 'computrabajo')  AS postulaciones_7dias_cpt,
        COUNTIF(e.FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY) AND LOWER(COALESCE(e.PORTAL,'')) = 'laborum')       AS postulaciones_7dias_lab,
        COUNT(e.LINK)                                                    AS total_postulaciones
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN (
        SELECT ID_USUARIO, PLAN, ESTADO, FECHA_FIN
        FROM (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY ID_USUARIO ORDER BY FECHA_INICIO DESC) AS rn
          FROM ${this.bq.t('PLAN_CONTRATADO')} WHERE ESTADO = 'ACTIVO'
        ) WHERE rn = 1
      ) pc ON u.ID_USUARIO = pc.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON LOWER(u.ID_USUARIO) = LOWER(pa.id_usuario)
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')}      pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN ${this.bq.t('CUENTAS_PORTALES')}   cp ON LOWER(u.ID_USUARIO) = LOWER(cp.ID_USUARIO)
      LEFT JOIN ${this.bq.t('EMPLEOS')}             e  ON u.ID_USUARIO = e.ID_USUARIO
      GROUP BY u.ID_USUARIO, u.NOMBRE, u.EMAIL, pc.PLAN, pc.ESTADO, pc.FECHA_FIN,
               pa.activo, pf.CARGOS, pf.UBICACIONES
      ORDER BY u.ID_USUARIO
    `);

    return rows.map((r) => {
      const plan = r.plan || 'FREE';
      return {
        id:                     r.id,
        nombre:                 r.nombre || '',
        email:                  r.email  || '',
        plan,
        plan_vigente:           Boolean(r.plan_vigente),
        fecha_fin:              r.fecha_fin || null,
        autopilot_activo:       Boolean(r.autopilot_activo),
        cargos:                 this.parseJson(r.cargos_raw),
        tiene_cargos:           Boolean(r.tiene_cargos),
        tiene_ubicaciones:      Boolean(r.tiene_ubicaciones),
        tiene_trabajando:       Boolean(r.tiene_trabajando),
        tiene_chiletrabajos:    Boolean(r.tiene_chiletrabajos),
        tiene_computrabajo:     Boolean(r.tiene_computrabajo),
        tiene_laborum:          Boolean(r.tiene_laborum),
        cv_trabajando:          Boolean(r.cv_trabajando),
        cv_chiletrabajos:       Boolean(r.cv_chiletrabajos),
        cv_computrabajo:        Boolean(r.cv_computrabajo),
        cv_laborum:             Boolean(r.cv_laborum),
        postulaciones_hoy:      Number(r.postulaciones_hoy)      || 0,
        postulaciones_7dias:    Number(r.postulaciones_7dias)    || 0,
        postulaciones_7dias_tbj:Number(r.postulaciones_7dias_tbj)|| 0,
        postulaciones_7dias_cht:Number(r.postulaciones_7dias_cht)|| 0,
        postulaciones_7dias_cpt:Number(r.postulaciones_7dias_cpt)|| 0,
        postulaciones_7dias_lab:Number(r.postulaciones_7dias_lab)|| 0,
        total_postulaciones:    Number(r.total_postulaciones)    || 0,
        limite_dia:             PLAN_LIMITE[plan] ?? 0,
      };
    });
  }

  async getDiagnostics(userId: string) {
    const [pfRows, planRows, paRows, cpRows, statsRows] = await Promise.all([
      this.bq.query<any>(`
        SELECT CARGOS, UBICACIONES, CV_URL, RESUMEN FROM ${this.bq.t('POSTULA_FACIL')}
        WHERE ID_USUARIO = @id LIMIT 1
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT PLAN, ESTADO, FECHA_FIN FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ID_USUARIO = @id AND ESTADO = 'ACTIVO' ORDER BY FECHA_INICIO DESC LIMIT 1
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT activo FROM ${this.bq.t('POSTULACIONES_AUTO')}
        WHERE LOWER(id_usuario) = LOWER(@id) LIMIT 1
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT PORTAL, CV_COMPLETO FROM ${this.bq.t('CUENTAS_PORTALES')}
        WHERE LOWER(ID_USUARIO) = LOWER(@id)
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT
          COUNTIF(EXTRACT(DATE FROM FECHA_POSTULACION AT TIME ZONE 'America/Santiago') = CURRENT_DATE('America/Santiago')) AS hoy,
          COUNTIF(FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)) AS semana,
          COUNTIF(LOWER(COALESCE(PORTAL,'')) = 'linkedin' AND EXTRACT(DATE FROM FECHA_POSTULACION AT TIME ZONE 'America/Santiago') = CURRENT_DATE('America/Santiago')) AS hoy_lkd
        FROM ${this.bq.t('EMPLEOS')} WHERE ID_USUARIO = @id
      `, { id: userId }),
    ]);

    const pf   = pfRows[0]   || {};
    const plan = planRows[0] || {};
    const pa   = paRows[0]   || {};
    const stats = statsRows[0] || {};

    const cargos    = this.parseJson(pf.CARGOS);
    const ubicaciones = this.parseJson(pf.UBICACIONES);

    const portales: Record<string, boolean> = {};
    const cvPortal: Record<string, boolean> = {};
    for (const cp of cpRows) {
      portales[cp.PORTAL] = true;
      cvPortal[cp.PORTAL] = Boolean(cp.CV_COMPLETO);
    }

    const planStr   = plan.PLAN   || 'FREE';
    const limite    = PLAN_LIMITE[planStr] ?? 0;
    const fechaFin  = plan.FECHA_FIN ? (plan.FECHA_FIN.value ?? plan.FECHA_FIN) : null;
    const vigente   = plan.ESTADO === 'ACTIVO' && (!fechaFin || new Date(fechaFin) > new Date());
    const autopilot = Boolean(pa.activo);

    const checks = [
      { key: 'plan',       label: 'Plan vigente',         ok: vigente,              detalle: vigente ? `${planStr} activo` : 'Sin plan activo' },
      { key: 'autopilot',  label: 'Autopilot activo',     ok: autopilot,            detalle: autopilot ? 'Encendido' : 'Apagado' },
      { key: 'cargos',     label: 'Cargos configurados',  ok: cargos.length > 0,    detalle: cargos.length > 0 ? cargos.join(', ') : 'Sin cargos — el autopilot no sabe qué buscar' },
      { key: 'ubicaciones',label: 'Ubicación configurada',ok: ubicaciones.length > 0,detalle: ubicaciones.length > 0 ? ubicaciones.join(', ') : 'Sin ubicación' },
      { key: 'cv',         label: 'CV subido',            ok: Boolean(pf.CV_URL),   detalle: pf.CV_URL ? 'CV disponible' : 'Sin CV subido' },
      { key: 'cuenta_tbj', label: 'Cuenta Trabajando.cl', ok: Boolean(portales['trabajando']),    detalle: portales['trabajando'] ? `CV: ${cvPortal['trabajando'] ? '✓ completo' : '✗ incompleto'}` : 'Sin cuenta' },
      { key: 'cuenta_cht', label: 'Cuenta ChileTrabajos', ok: Boolean(portales['chiletrabajos']), detalle: portales['chiletrabajos'] ? `CV: ${cvPortal['chiletrabajos'] ? '✓ completo' : '✗ incompleto'}` : 'Sin cuenta' },
      { key: 'cuenta_cpt', label: 'Cuenta Computrabajo',  ok: Boolean(portales['computrabajo']),  detalle: portales['computrabajo'] ? `CV: ${cvPortal['computrabajo'] ? '✓ completo' : '✗ incompleto'}` : 'Sin cuenta' },
      { key: 'cuenta_lab', label: 'Cuenta Laborum',       ok: Boolean(portales['laborum']),       detalle: portales['laborum'] ? `CV: ${cvPortal['laborum'] ? '✓ completo' : '✗ incompleto'}` : 'Sin cuenta' },
    ];

    return {
      hoy:      Number(stats.hoy)     || 0,
      limite,
      semana:   Number(stats.semana)  || 0,
      hoyLkd:   Number(stats.hoy_lkd) || 0,
      fecha_fin: fechaFin,
      checks,
    };
  }

  async getPostulaciones(userId: string, limit: number) {
    const rows = await this.bq.query<any>(`
      SELECT
        LPAD(CAST(ROW_NUMBER() OVER (ORDER BY FECHA_POSTULACION DESC) AS STRING), 6, '0') AS id,
        TITULO_EMPLEO, EMPRESA, LINK, CARGO, PORTAL, FECHA_POSTULACION
      FROM ${this.bq.t('EMPLEOS')}
      WHERE ID_USUARIO = @id
      ORDER BY FECHA_POSTULACION DESC
      LIMIT @lim
    `, { id: userId, lim: limit });

    return rows.map((r: any) => ({
      id:     r.id,
      titulo: r.TITULO_EMPLEO || '',
      empresa:r.EMPRESA       || '',
      link:   r.LINK          || '',
      cargo:  r.CARGO         || '',
      portal: r.PORTAL        || '',
      fecha:  r.FECHA_POSTULACION,
    }));
  }

  async updatePlan(userId: string, plan: string, fechaFin?: string) {
    const fechaSql = fechaFin
      ? `TIMESTAMP('${fechaFin.slice(0, 10)} 23:59:59')`
      : 'TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)';

    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S ON T.ID_USUARIO = S.ID_USUARIO AND T.ESTADO = 'ACTIVO'
      WHEN MATCHED THEN
        UPDATE SET PLAN = @plan, FECHA_INICIO = CURRENT_TIMESTAMP(), FECHA_FIN = ${fechaSql}, MEDIO_PAGO = 'ADMIN'
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN, MEDIO_PAGO)
        VALUES (@id, @plan, 'ACTIVO', CURRENT_TIMESTAMP(), ${fechaSql}, 'ADMIN')
    `, { id: userId, plan });

    return { ok: true };
  }

  async updateCargos(userId: string, cargos: string[]) {
    const exists = await this.bq.query<any>(`
      SELECT 1 FROM ${this.bq.t('POSTULA_FACIL')} WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    if (!exists.length) throw new NotFoundException('Usuario sin perfil Postula Fácil');

    await this.bq.query(`
      UPDATE ${this.bq.t('POSTULA_FACIL')}
      SET CARGOS = @cargos, FECHA_ACTUALIZACION = CURRENT_TIMESTAMP()
      WHERE ID_USUARIO = @id
    `, { id: userId, cargos: JSON.stringify(cargos) });

    return { ok: true };
  }

  async deletePortal(userId: string, portal: string) {
    await this.bq.query(`
      DELETE FROM ${this.bq.t('CUENTAS_PORTALES')}
      WHERE LOWER(ID_USUARIO) = LOWER(@id) AND LOWER(PORTAL) = LOWER(@portal)
    `, { id: userId, portal });

    return { ok: true };
  }

  private parseJson(val: any): any[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
