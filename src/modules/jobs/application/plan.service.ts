import { Injectable } from '@nestjs/common';
import * as crypto from 'crypto';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import env from '../../shared/infrastructure/environment';

const PLAN_PRICES: Record<string, number> = {
  PRO: 9990,
  PREMIUM: 19990,
};

@Injectable()
export class PlanService {
  constructor(private readonly bq: BigQueryService) {}

  async getPlan(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT PLAN, ESTADO, FECHA_INICIO FROM ${this.bq.t('PLAN_CONTRATADO')}
      WHERE ID_USUARIO = @id AND ESTADO = 'ACTIVO'
      ORDER BY FECHA_INICIO DESC LIMIT 1
    `, { id: userId });

    return { plan: rows[0]?.PLAN || 'FREE', estado: rows[0]?.ESTADO || null };
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
      merchantId: env.flowApiKey,
      orderId,
      subject: `Plan ${plan} — Jobs`,
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
    if (!data.token) throw new Error('Error creando pago Flow');

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
