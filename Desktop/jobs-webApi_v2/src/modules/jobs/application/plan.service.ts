import { Injectable } from '@nestjs/common';
import * as crypto from 'crypto';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import env from '../../shared/infrastructure/environment';

const PLAN_PRICES: Record<string, number> = {
  PRO: 9990,
  PREMIUM: 19990,
};

@Injectable()
export class PlanService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
  ) {}

  async getPlan(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT PLAN, ESTADO, FECHA_INICIO FROM ${this.bq.t('PLAN_CONTRATADO')}
      WHERE ID_USUARIO = @id AND ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
      ORDER BY FECHA_INICIO DESC LIMIT 1
    `, { id: userId });

    return { plan: rows[0]?.PLAN || 'FREE', estado: rows[0]?.ESTADO || null };
  }

  async cancelPlan(userId: string) {
    await this.bq.query(`
      UPDATE ${this.bq.t('PLAN_CONTRATADO')}
      SET ESTADO = 'CANCELADO_PENDIENTE'
      WHERE ID_USUARIO = @id AND ESTADO = 'ACTIVO'
    `, { id: userId });

    return { success: true };
  }

  async savePlan(userId: string, plan: string) {
    const now = new Date().toISOString();

    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET PLAN = @plan, ESTADO = 'ACTIVO', FECHA_INICIO = @now, METODO_PAGO = 'APP'
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, METODO_PAGO)
        VALUES (@id, @plan, 'ACTIVO', @now, 'APP')
    `, { id: userId, plan, now });

    return { success: true };
  }

  // ─── CRON: NOTIFICAR PLANES POR VENCER ───────────────────────────────────
  async notifyExpiringPlans(diasAntes: number): Promise<{ enviados: number }> {
    const rows = await this.bq.query<any>(`
      SELECT
        u.NOMBRE, u.EMAIL, pc.PLAN,
        DATE_ADD(DATE(pc.FECHA_INICIO), INTERVAL 30 DAY) AS FECHA_FIN
      FROM ${this.bq.t('USUARIOS')} u
      JOIN ${this.bq.t('PLAN_CONTRATADO')} pc ON u.ID_USUARIO = pc.ID_USUARIO
      WHERE pc.ESTADO = 'ACTIVO'
        AND pc.PLAN NOT IN ('FREE', 'TRIAL')
        AND DATE_ADD(DATE(pc.FECHA_INICIO), INTERVAL 30 DAY)
            = DATE_ADD(CURRENT_DATE(), INTERVAL @dias DAY)
    `, { dias: diasAntes });

    await Promise.all(
      rows.map((r: any) => {
        const fechaFin = new Date(r.FECHA_FIN).toLocaleDateString('es-CL', {
          day: 'numeric', month: 'long', year: 'numeric',
        });
        const subject = diasAntes <= 1
          ? `⚠️ Tu plan ${r.PLAN} vence mañana`
          : `Tu plan ${r.PLAN} vence en ${diasAntes} días`;
        return this.email.send(
          r.EMAIL,
          subject,
          this.email.planExpiryHtml(r.NOMBRE, r.PLAN, diasAntes, fechaFin),
        ).catch(() => null);
      }),
    );

    return { enviados: rows.length };
  }

  // ─── FLOW.CL: CREATE PAYMENT ORDER ────────────────────────────────────────
  async createCheckout(userId: string, plan: string, userEmail: string) {
    const amount = PLAN_PRICES[plan];
    if (!amount) throw new Error('Plan inválido');

    const orderId = `jobs-${userId}-${Date.now()}`;
    const urlConfirmation = `${env.frontendUrl}/api/plan/notificacion`;
    const urlReturn = `${env.frontendUrl}/pago/retorno`;

    const params: Record<string, string> = {
      apiKey: env.flowApiKey,
      amount: String(amount),
      currency: 'CLP',
      email: userEmail,
      orderId,
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

    // Store pending payment
    await this.bq.query(`
      INSERT INTO ${this.bq.t('PAGOS_PENDIENTES')} (ORDER_ID, ID_USUARIO, PLAN, TOKEN, FECHA)
      VALUES (@orderId, @userId, @plan, @token, CURRENT_TIMESTAMP())
    `, { orderId, userId, plan, token: data.token }).catch(() => null);

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
      SELECT ID_USUARIO, PLAN FROM ${this.bq.t('PAGOS_PENDIENTES')}
      WHERE TOKEN = @token LIMIT 1
    `, { token }).catch(() => []);

    if (!rows.length) return;

    await this.savePlan(rows[0].ID_USUARIO, rows[0].PLAN);

    await this.bq.query(`
      DELETE FROM ${this.bq.t('PAGOS_PENDIENTES')} WHERE TOKEN = @token
    `, { token }).catch(() => null);
  }

  private sign(params: Record<string, string>): string {
    const sorted = Object.keys(params)
      .filter((k) => k !== 's')
      .sort()
      .map((k) => `${k}${params[k]}`)
      .join('');

    return crypto.createHmac('sha256', env.flowSecretKey).update(sorted).digest('hex');
  }
}
