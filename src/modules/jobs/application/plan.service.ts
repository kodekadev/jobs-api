import { ForbiddenException, Injectable } from '@nestjs/common';
import * as crypto from 'crypto';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import { TelegramService } from '../../shared/infrastructure/services/telegram.service';
import env from '../../shared/infrastructure/environment';

const PLAN_PRICES: Record<string, number> = {
  PRO: 9990,
  TURBO: 14990,
  PREMIUM: 19990,
};

// Condición SQL de vigencia. Si FECHA_FIN está guardada se usa directamente;
// si no, se calcula desde FECHA_INICIO (30 días pagados, 14 días TRIAL).
export const PLAN_VIGENTE_SQL = `(
  pc.PLAN = 'FREE'
  OR ((pc.PLAN = 'TRIAL' OR pc.ESTADO = 'TRIAL') AND (
    (pc.FECHA_FIN IS NOT NULL AND DATE(pc.FECHA_FIN) >= CURRENT_DATE())
    OR (pc.FECHA_FIN IS NULL AND DATE(pc.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 7 DAY))
  ))
  OR (pc.PLAN NOT IN ('FREE', 'TRIAL') AND pc.ESTADO != 'TRIAL' AND (
    (pc.FECHA_FIN IS NOT NULL AND DATE(pc.FECHA_FIN) >= CURRENT_DATE())
    OR (pc.FECHA_FIN IS NULL AND DATE(pc.FECHA_INICIO) >= DATE_SUB(CURRENT_DATE(), INTERVAL 30 DAY))
  ))
)`;

const CV_OPT_LIMITS: Record<string, number> = {
  FREE: 0, PRO: 2, TURBO: 3, PREMIUM: 5, TRIAL: 1,
};

@Injectable()
export class PlanService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
    private readonly telegram: TelegramService,
  ) {}

  async getCvOptimizaciones(userId: string): Promise<{ usadas: number; limite: number; restantes: number }> {
    const planRows = await this.bq.query<any>(`
      SELECT PLAN, FECHA_INICIO FROM ${this.bq.t('PLAN_CONTRATADO')} pc
      WHERE pc.ID_USUARIO = @id AND pc.ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
        AND ${PLAN_VIGENTE_SQL}
      ORDER BY FECHA_INICIO DESC LIMIT 1
    `, { id: userId });

    const plan = planRows[0]?.PLAN || 'FREE';
    const limite = CV_OPT_LIMITS[plan] ?? 0;
    if (limite === 0) return { usadas: 0, limite: 0, restantes: 0 };

    const rawFi = planRows[0]?.FECHA_INICIO?.value ?? planRows[0]?.FECHA_INICIO;
    const desde = rawFi ? new Date(rawFi).toISOString() : new Date(Date.now() - 30 * 24 * 60 * 60 * 1000).toISOString();

    // CV_OPTIMIZACIONES ya existe con schema: id, id_usuario, fecha (DATE), tipo, created_at
    const rows = await this.bq.query<any>(`
      SELECT COUNT(*) AS total FROM ${this.bq.t('CV_OPTIMIZACIONES')}
      WHERE id_usuario = @id AND tipo = 'cv_optimizacion' AND created_at >= TIMESTAMP(@desde)
    `, { id: userId, desde }).catch(() => [{ total: 0 }]);

    const usadas = Number(rows[0]?.total ?? 0);
    return { usadas, limite, restantes: Math.max(0, limite - usadas) };
  }

  async registrarCvOptimizacion(userId: string, _plan: string): Promise<void> {
    await this.bq.query(`
      INSERT INTO ${this.bq.t('CV_OPTIMIZACIONES')} (id, id_usuario, fecha, tipo, created_at)
      VALUES (GENERATE_UUID(), @id, CURRENT_DATE(), 'cv_optimizacion', CURRENT_TIMESTAMP())
    `, { id: userId });
  }

  async getPlan(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT PLAN, ESTADO, FECHA_INICIO, FECHA_FIN FROM ${this.bq.t('PLAN_CONTRATADO')} pc
      WHERE pc.ID_USUARIO = @id AND pc.ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE', 'TRIAL')
        AND ${PLAN_VIGENTE_SQL}
      ORDER BY FECHA_INICIO DESC LIMIT 1
    `, { id: userId });

    const row = rows[0];
    // Legacy schema: PLAN='PRO' + ESTADO='TRIAL' → show as TRIAL
    const planDisplay = row?.ESTADO === 'TRIAL' ? 'TRIAL' : (row?.PLAN || 'FREE');
    let fecha_fin: string | null = null;

    if (row && planDisplay !== 'FREE') {
      if (row.FECHA_FIN) {
        const ff = row.FECHA_FIN?.value ? new Date(row.FECHA_FIN.value) : new Date(row.FECHA_FIN);
        fecha_fin = ff.toISOString().split('T')[0];
      } else if (row.FECHA_INICIO) {
        const fi = row.FECHA_INICIO?.value ? new Date(row.FECHA_INICIO.value) : new Date(row.FECHA_INICIO);
        const dias = planDisplay === 'TRIAL' ? 7 : 30;
        fi.setDate(fi.getDate() + dias);
        fecha_fin = fi.toISOString().split('T')[0];
      }
    }

    return { plan: planDisplay, estado: row?.ESTADO || null, fecha_fin };
  }

  async cancelPlan(userId: string) {
    await this.bq.query(`
      UPDATE ${this.bq.t('PLAN_CONTRATADO')}
      SET ESTADO = 'CANCELADO_PENDIENTE'
      WHERE ID_USUARIO = @id AND ESTADO = 'ACTIVO'
    `, { id: userId });

    return { success: true };
  }

  // Endpoint público (autenticado): solo permite activar FREE.
  // Los planes pagados SOLO se activan vía confirmación de pago de Flow
  // (confirmPayment → activatePlan) — si no, cualquiera se da PREMIUM gratis.
  async savePlan(userId: string, plan: string) {
    if ((plan || '').toUpperCase() !== 'FREE') {
      throw new ForbiddenException('Los planes pagados se activan solo tras confirmar el pago');
    }
    return this.activatePlan(userId, 'FREE');
  }

  async activateTrial(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT PLAN FROM ${this.bq.t('PLAN_CONTRATADO')}
      WHERE ID_USUARIO = @id AND PLAN != 'FREE' LIMIT 1
    `, { id: userId }).catch(() => []);

    if (rows.length > 0) {
      throw new ForbiddenException('Ya usaste tu prueba gratuita');
    }
    return this.activatePlan(userId, 'TRIAL');
  }

  // Uso interno — sin restricción de plan (la llama el flujo de pago confirmado).
  private async activatePlan(userId: string, plan: string) {
    const now = new Date().toISOString();
    const fin = new Date();
    fin.setDate(fin.getDate() + (plan === 'TRIAL' ? 7 : 30));
    const fechaFin = fin.toISOString();

    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET PLAN = @plan, ESTADO = 'ACTIVO', FECHA_INICIO = @now, FECHA_FIN = @fechaFin, METODO_PAGO = 'APP'
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN, METODO_PAGO)
        VALUES (@id, @plan, 'ACTIVO', @now, @fechaFin, 'APP')
    `, { id: userId, plan, now, fechaFin });

    return { success: true };
  }

  // ─── CRON: NOTIFICAR PLANES POR VENCER ───────────────────────────────────
  async notifyExpiringPlans(diasAntes: number): Promise<{ enviados: number }> {
    const rows = await this.bq.query<any>(`
      SELECT
        u.NOMBRE, u.EMAIL, pc.PLAN,
        COALESCE(DATE(pc.FECHA_FIN), DATE_ADD(DATE(pc.FECHA_INICIO),
          INTERVAL IF(pc.PLAN = 'TRIAL', 7, 30) DAY)) AS FECHA_FIN
      FROM ${this.bq.t('USUARIOS')} u
      JOIN ${this.bq.t('PLAN_CONTRATADO')} pc ON u.ID_USUARIO = pc.ID_USUARIO
      WHERE pc.ESTADO = 'ACTIVO'
        AND pc.PLAN NOT IN ('FREE')
        AND COALESCE(DATE(pc.FECHA_FIN), DATE_ADD(DATE(pc.FECHA_INICIO),
            INTERVAL IF(pc.PLAN = 'TRIAL', 7, 30) DAY))
            = DATE_ADD(CURRENT_DATE(), INTERVAL @dias DAY)
    `, { dias: diasAntes });

    await Promise.all(
      rows.map((r: any) => {
        const rawFin = r.FECHA_FIN?.value ?? r.FECHA_FIN;
        const fechaFin = new Date(rawFin).toLocaleDateString('es-CL', {
          day: 'numeric', month: 'long', year: 'numeric',
        });
        const subject = diasAntes <= 1
          ? `⚠️ Tu plan ${r.PLAN} vence mañana`
          : `Tu plan ${r.PLAN} vence en ${diasAntes} días`;
        return this.email.send(
          r.EMAIL,
          subject,
          this.email.planExpiryHtml(r.NOMBRE, r.PLAN, diasAntes, fechaFin),
          'plan_expiry',
        ).catch(() => null);
      }),
    );

    return { enviados: rows.length };
  }

  // ─── CRON: MARCAR PLANES VENCIDOS ────────────────────────────────────────
  async expireOutdatedPlans(): Promise<{ ok: boolean }> {
    await this.bq.query(`
      UPDATE ${this.bq.t('PLAN_CONTRATADO')}
      SET ESTADO = 'VENCIDO'
      WHERE ESTADO IN ('ACTIVO', 'TRIAL')
        AND PLAN != 'FREE'
        AND FECHA_FIN IS NOT NULL
        AND DATE(FECHA_FIN) < CURRENT_DATE()
    `).catch(() => null);
    return { ok: true };
  }

  // ─── CRON: EMAIL POST-VENCIMIENTO (día siguiente) ─────────────────────────
  async notifyPostExpiry(): Promise<{ enviados: number }> {
    const rows = await this.bq.query<any>(`
      SELECT
        u.NOMBRE, u.EMAIL, pc.ID_USUARIO,
        COALESCE(ps.total_posts, 0) AS total_posts
      FROM ${this.bq.t('USUARIOS')} u
      JOIN ${this.bq.t('PLAN_CONTRATADO')} pc ON u.ID_USUARIO = pc.ID_USUARIO
      LEFT JOIN (
        SELECT id_usuario, COUNTIF(portal NOT IN ('email_directo', '')) AS total_posts
        FROM ${this.bq.t('EMPLEOS')}
        GROUP BY id_usuario
      ) ps ON u.ID_USUARIO = ps.id_usuario
      WHERE pc.ESTADO IN ('ACTIVO', 'TRIAL')
        AND pc.PLAN != 'FREE'
        AND pc.FECHA_FIN IS NOT NULL
        AND DATE(pc.FECHA_FIN) = DATE_SUB(CURRENT_DATE(), INTERVAL 1 DAY)
        AND u.NOMBRE != 'CUENTA_ELIMINADA'
        AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
    `).catch(() => [] as any[]);

    await Promise.all(
      rows.map((r: any) =>
        this.email.send(
          r.EMAIL,
          '¿Qué lograste con AplicAI esta semana?',
          this.email.postExpiryHtml(r.NOMBRE, Number(r.total_posts ?? 0)),
          'post_expiry',
        ).catch(() => null),
      ),
    );

    return { enviados: rows.length };
  }

  // ─── PROMO CODES ──────────────────────────────────────────────────────────
  async getPromoCode(codigo: string): Promise<{ valido: boolean; descuento_pct: number; vigencia_hasta: string }> {
    const rows = await this.bq.query<any>(`
      SELECT descuento_pct, vigencia_hasta
      FROM ${this.bq.t('CODIGOS_PROMO')}
      WHERE codigo = @codigo
        AND vigencia_hasta >= CURRENT_DATE()
      LIMIT 1
    `, { codigo }).catch(() => [] as any[]);

    if (!rows.length) return { valido: false, descuento_pct: 0, vigencia_hasta: '' };
    const r = rows[0];
    const vh = r.vigencia_hasta?.value ?? r.vigencia_hasta;
    return {
      valido: true,
      descuento_pct: Number(r.descuento_pct),
      vigencia_hasta: typeof vh === 'string' ? vh : new Date(vh).toISOString().split('T')[0],
    };
  }

  // ─── FLOW.CL: CREATE PAYMENT ORDER ────────────────────────────────────────
  async createCheckout(userId: string, plan: string, userEmail: string, promoCode?: string) {
    const baseAmount = PLAN_PRICES[plan];
    if (!baseAmount) throw new Error('Plan inválido');

    let amount = baseAmount;
    if (promoCode) {
      const promo = await this.getPromoCode(promoCode);
      if (promo.valido) {
        amount = Math.round(baseAmount * (1 - promo.descuento_pct / 100));
      }
    }

    const orderId = `jobs-${userId}-${Date.now()}`;
    const urlConfirmation = `${env.backendUrl || 'https://jobs-api-994947687832.us-central1.run.app'}/api/plan/notificacion`;
    const urlReturn = `${env.frontendUrl}/pago/retorno`;

    const params: Record<string, string> = {
      apiKey: env.flowApiKey,
      amount: String(amount),
      currency: 'CLP',
      email: userEmail,
      commerceOrder: orderId, // Flow exige "commerceOrder" (error 104 si va como orderId)
      subject: `Plan ${plan} — AplicAI`,
      urlConfirmation,
      urlReturn,
    };

    params.s = this.sign(params);

    const formData = new URLSearchParams(params);

    const res = await fetch(`${env.flowBaseUrl}/payment/create`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: formData.toString(),
    });

    const data: any = await res.json();
    if (!data.token) {
      console.error('Flow error response:', JSON.stringify(data));
      throw new Error(data?.message || data?.code ? `Flow: ${data.code} - ${data.message}` : 'Error creando pago Flow');
    }

    // Store pending payment — si falla aquí el usuario pagará sin que podamos activar su plan
    await this.bq.query(`
      INSERT INTO ${this.bq.t('PAGOS_PENDIENTES')} (ORDER_ID, ID_USUARIO, PLAN, TOKEN, FECHA)
      VALUES (@orderId, @userId, @plan, @token, CURRENT_TIMESTAMP())
    `, { orderId, userId, plan, token: data.token }).catch((e) => {
      console.error('[plan] CRÍTICO: no se pudo guardar pago pendiente en BQ:', e?.message ?? e);
      throw new Error('Error registrando el pago. Por favor intenta de nuevo.');
    });

    return { url: `${data.url}?token=${data.token}`, token: data.token };
  }

  // ─── FLOW.CL: HANDLE NOTIFICATION (webhook) ───────────────────────────────
  async handleNotification(token: string) {
    const status = await this.getFlowStatus(token);
    if (status?.status === 2) {
      await this.confirmPayment(token);
    }
    return { success: true };
  }

  async getReturnStatus(token: string) {
    const status = await this.getFlowStatus(token);

    if (status?.status === 2) {
      await this.confirmPayment(token);
    }

    return {
      paid: status?.status === 2,
      flowOrder: status?.flowOrder,
      amount: status?.amount,
    };
  }

  // ─── INTERNAL ─────────────────────────────────────────────────────────────
  private async getFlowStatus(token: string) {
    const params: Record<string, string> = {
      apiKey: env.flowApiKey,
      token,
    };
    params.s = this.sign(params);

    const qs = new URLSearchParams(params).toString();
    const res = await fetch(`${env.flowBaseUrl}/payment/getStatus?${qs}`);
    return res.json() as any;
  }

  private async confirmPayment(token: string) {
    const rows = await this.bq.query<any>(`
      SELECT ID_USUARIO, PLAN, ORDER_ID FROM ${this.bq.t('PAGOS_PENDIENTES')}
      WHERE TOKEN = @token LIMIT 1
    `, { token }).catch((e) => {
      console.error('[plan] Error leyendo PAGOS_PENDIENTES para token', token, ':', e?.message ?? e);
      return [];
    });

    if (!rows.length) {
      console.warn('[plan] confirmPayment: token no encontrado en PAGOS_PENDIENTES:', token);
      return;
    }

    const { ID_USUARIO: userId, PLAN: plan, ORDER_ID: orderId } = rows[0];

    await this.activatePlan(userId, plan);

    await this.bq.query(`
      DELETE FROM ${this.bq.t('PAGOS_PENDIENTES')} WHERE TOKEN = @token
    `, { token }).catch(() => null);

    // Notificación Telegram + registro en HISTORIAL_PAGOS
    const userRows = await this.bq.query<any>(`
      SELECT NOMBRE, EMAIL FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId }).catch(() => []);
    const u = userRows[0];
    const montos: Record<string, number> = { PRO: 9990, TURBO: 14990, PREMIUM: 19990 };
    const monto = montos[plan] ?? 0;
    const precioFmt = monto.toLocaleString('es-CL');

    await this.bq.query(`
      INSERT INTO ${this.bq.t('HISTORIAL_PAGOS')} (ORDER_ID, ID_USUARIO, PLAN, MONTO, TOKEN, FECHA_PAGO, NOMBRE, EMAIL)
      VALUES (@orderId, @userId, @plan, @monto, @token, CURRENT_TIMESTAMP(), @nombre, @email)
    `, { orderId, userId, plan, monto, token, nombre: u?.NOMBRE ?? '', email: u?.EMAIL ?? '' }).catch((e) => {
      console.error('[plan] No se pudo registrar en HISTORIAL_PAGOS:', e?.message ?? e);
    });

    this.telegram.send(
      `💰 <b>Nuevo pago recibido</b>\n` +
      `👤 ${u?.NOMBRE || userId}\n` +
      `✉️ ${u?.EMAIL || ''}\n` +
      `📦 Plan: <b>${plan}</b> — $${precioFmt} CLP`
    ).catch(() => null);
  }

  private sign(params: Record<string, string>): string {
    const sorted = Object.keys(params)
      .filter((k) => k !== 's')
      .sort()
      .map((k) => `${k}${params[k]}`)
      .join('');

    return crypto.createHmac('sha256', env.flowSecretKey).update(sorted).digest('hex');
  }

  // ─── EMPLEO FOLLOWUP ──────────────────────────────────────────────────────

  generateEmpleoToken(userId: string): string {
    return crypto.createHmac('sha256', env.jwtSecret).update(`empleo:${userId}`).digest('hex');
  }

  verifyEmpleoToken(userId: string, token: string): boolean {
    try {
      const expected = Buffer.from(this.generateEmpleoToken(userId), 'hex');
      const provided  = Buffer.from(token, 'hex');
      if (expected.length !== provided.length) return false;
      return crypto.timingSafeEqual(expected, provided);
    } catch {
      return false;
    }
  }

  // Cron: envía email de seguimiento a usuarios con ≥60 días de suscripción
  // que aún no hayan respondido.
  async sendEmpleoFollowup(): Promise<{ enviados: number }> {
    const rows = await this.bq.query<any>(`
      SELECT u.ID_USUARIO, u.NOMBRE, u.EMAIL
      FROM ${this.bq.t('USUARIOS')} u
      JOIN ${this.bq.t('PLAN_CONTRATADO')} pc ON u.ID_USUARIO = pc.ID_USUARIO
      WHERE pc.PLAN NOT IN ('FREE', 'TRIAL')
        AND pc.ESTADO NOT IN ('CANCELADO')
        AND DATE(pc.FECHA_INICIO) <= DATE_SUB(CURRENT_DATE(), INTERVAL 60 DAY)
        AND u.NOMBRE != 'CUENTA_ELIMINADA'
        AND NOT STARTS_WITH(u.EMAIL, 'deleted_')
        AND NOT EXISTS (
          SELECT 1 FROM ${this.bq.t('EMPLEO_CONSEGUIDO')}
          WHERE ID_USUARIO = u.ID_USUARIO
        )
    `).catch(() => [] as any[]);

    await Promise.all(rows.map(async (r: any) => {
      const token  = this.generateEmpleoToken(r.ID_USUARIO);
      const base   = `${env.frontendUrl}/consegui-empleo?uid=${r.ID_USUARIO}&token=${token}`;
      const linkSi = `${base}&r=si`;
      const linkNo = `${base}&r=no`;

      // Registrar envío antes de mandar (evita reenvíos si el cron corre dos veces)
      await this.bq.query(`
        INSERT INTO ${this.bq.t('EMPLEO_CONSEGUIDO')}
          (ID, ID_USUARIO, RESPUESTA, EMPRESA, CARGO, FUE_CON_APLICAI, TESTIMONIAL, FECHA_EMAIL, FECHA_RESPUESTA)
        VALUES
          (GENERATE_UUID(), @uid, NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP(), NULL)
      `, { uid: r.ID_USUARIO }).catch(() => null);

      return this.email.send(
        r.EMAIL,
        '¿Conseguiste empleo? Cuéntanos 🎉',
        this.email.empleoFollowupHtml(r.NOMBRE, linkSi, linkNo),
        'empleo_followup',
      ).catch(() => null);
    }));

    return { enviados: rows.length };
  }

  // Registra la respuesta sí/no del usuario (llamado desde la página del frontend)
  async registrarRespuestaEmpleo(
    userId: string,
    token: string,
    respuesta: 'si' | 'no',
    datos?: { empresa?: string; cargo?: string; fueCon?: boolean; testimonial?: string },
  ): Promise<{ ok: boolean }> {
    if (!this.verifyEmpleoToken(userId, token)) {
      throw new ForbiddenException('Token inválido');
    }

    const { empresa = null, cargo = null, fueCon = null, testimonial = null } = datos ?? {};

    // UPDATE si ya existe fila (del envío del email), INSERT si no
    const existing = await this.bq.query<any>(`
      SELECT ID FROM ${this.bq.t('EMPLEO_CONSEGUIDO')}
      WHERE ID_USUARIO = @uid AND RESPUESTA IS NULL
      LIMIT 1
    `, { uid: userId }).catch(() => [] as any[]);

    if (existing.length > 0) {
      await this.bq.query(`
        UPDATE ${this.bq.t('EMPLEO_CONSEGUIDO')}
        SET RESPUESTA = @resp,
            EMPRESA = @empresa,
            CARGO = @cargo,
            FUE_CON_APLICAI = @fueCon,
            TESTIMONIAL = @testimonial,
            FECHA_RESPUESTA = CURRENT_TIMESTAMP()
        WHERE ID_USUARIO = @uid AND RESPUESTA IS NULL
      `, { uid: userId, resp: respuesta, empresa, cargo, fueCon, testimonial });
    } else {
      await this.bq.query(`
        INSERT INTO ${this.bq.t('EMPLEO_CONSEGUIDO')}
          (ID, ID_USUARIO, RESPUESTA, EMPRESA, CARGO, FUE_CON_APLICAI, TESTIMONIAL, FECHA_EMAIL, FECHA_RESPUESTA)
        VALUES
          (GENERATE_UUID(), @uid, @resp, @empresa, @cargo, @fueCon, @testimonial, NULL, CURRENT_TIMESTAMP())
      `, { uid: userId, resp: respuesta, empresa, cargo, fueCon, testimonial });
    }

    if (respuesta === 'si') {
      this.telegram.send(
        `🎉 <b>¡Usuario consiguió empleo!</b>\n` +
        `👤 ${userId}\n` +
        `🏢 ${empresa || '(sin empresa)'} — ${cargo || '(sin cargo)'}\n` +
        `✅ Con AplicAI: ${fueCon ? 'Sí' : fueCon === false ? 'No' : '?'}`
      ).catch(() => null);
    }

    return { ok: true };
  }
}
