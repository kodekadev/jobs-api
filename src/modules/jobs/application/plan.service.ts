import { BadRequestException, Injectable } from '@nestjs/common';
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
    private readonly emailService: EmailService,
  ) {}

  async getPlan(userId: string) {
    const [rows, trialRows] = await Promise.all([
      this.bq.query<any>(`
        SELECT PLAN, ESTADO, FECHA_FIN FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ID_USUARIO = @id
          AND UPPER(ESTADO) IN ('ACTIVO', 'TRIAL')
          AND DATE(FECHA_FIN) >= CURRENT_DATE()
        ORDER BY CASE UPPER(ESTADO) WHEN 'ACTIVO' THEN 1 ELSE 2 END, FECHA_FIN DESC
        LIMIT 1
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT 1 FROM ${this.bq.t('PLAN_CONTRATADO')}
        WHERE ID_USUARIO = @id AND UPPER(ESTADO) = 'TRIAL' LIMIT 1
      `, { id: userId }),
    ]);

    return {
      plan:      rows[0]?.PLAN      || 'FREE',
      estado:    rows[0]?.ESTADO    || null,
      fecha_fin: rows[0]?.FECHA_FIN || null,
      had_trial: trialRows.length > 0,
    };
  }

  async activateTrial(userId: string) {
    const existing = await this.bq.query<any>(`
      SELECT 1 FROM ${this.bq.t('PLAN_CONTRATADO')}
      WHERE ID_USUARIO = @id AND UPPER(ESTADO) = 'TRIAL' LIMIT 1
    `, { id: userId });

    if (existing.length) {
      throw new BadRequestException('Ya usaste tu período de prueba gratuita');
    }

    await this.bq.query(`
      INSERT INTO ${this.bq.t('PLAN_CONTRATADO')}
        (ID_USUARIO, PLAN, FECHA_INICIO, FECHA_FIN, ESTADO, MEDIO_PAGO)
      VALUES
        (@id, 'PRO', CURRENT_TIMESTAMP(), TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 14 DAY), 'TRIAL', 'TRIAL')
    `, { id: userId });

    return { success: true };
  }

  async savePlan(userId: string, plan: string) {
    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN MATCHED THEN
        UPDATE SET PLAN = @plan, ESTADO = 'ACTIVO', FECHA_INICIO = CURRENT_TIMESTAMP(),
          FECHA_FIN = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 DAY), MEDIO_PAGO = 'APP'
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN, MEDIO_PAGO)
        VALUES (@id, @plan, 'ACTIVO', CURRENT_TIMESTAMP(),
          TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 30 DAY), 'APP')
    `, { id: userId, plan });

    return { success: true };
  }

  // ─── FLOW.CL: CREATE PAYMENT ORDER ────────────────────────────────────────
  async createCheckout(userId: string, plan: string, userEmail: string) {
    const amount = PLAN_PRICES[plan];
    if (!amount) throw new Error('Plan inválido');

    const orderId = `AIC-${Date.now()}`;
    const urlConfirmation = `${env.backendUrl}/api/plan/notificacion`;
    // Flow redirige al usuario con POST: el route handler del front lo convierte en GET
    const urlReturn = `${env.frontendUrl}/api/pago/retorno`;

    const params: Record<string, string> = {
      apiKey: env.flowApiKey,
      amount: String(amount),
      commerceOrder: orderId,
      currency: 'CLP',
      email: userEmail,
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
    console.error('[Flow response]', JSON.stringify(data));
    if (!data.token) throw new Error(`Error creando pago Flow: ${data.message || JSON.stringify(data)}`);

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

    let plan: string | null = null;
    if (status?.status === 2) {
      plan = await this.confirmPayment(token);
    }

    return {
      paid: status?.status === 2,
      plan,
      flowOrder: status?.flowOrder,
      amount: status?.amount,
    };
  }

  // ─── CRON ─────────────────────────────────────────────────────────────────
  async runPlanExpiryCron(): Promise<any> {
    const t = (name: string) => this.bq.t(name);

    // Avisos de vencimiento: usuarios cuyo plan vence en 7 o 1 día
    const [expiring7, expiring1] = await Promise.all([
      this.bq.query<any>(`
        SELECT u.NOMBRE AS nombre, u.EMAIL AS email, pc.PLAN AS plan
        FROM ${t('PLAN_CONTRATADO')} pc
        JOIN ${t('USUARIOS')} u ON LOWER(pc.ID_USUARIO) = LOWER(u.ID_USUARIO)
        WHERE pc.ESTADO IN ('ACTIVO', 'TRIAL')
          AND pc.FECHA_FIN IS NOT NULL
          AND DATE(pc.FECHA_FIN AT TIME ZONE 'America/Santiago') = DATE_ADD(CURRENT_DATE('America/Santiago'), INTERVAL 7 DAY)
      `),
      this.bq.query<any>(`
        SELECT u.NOMBRE AS nombre, u.EMAIL AS email, pc.PLAN AS plan
        FROM ${t('PLAN_CONTRATADO')} pc
        JOIN ${t('USUARIOS')} u ON LOWER(pc.ID_USUARIO) = LOWER(u.ID_USUARIO)
        WHERE pc.ESTADO IN ('ACTIVO', 'TRIAL')
          AND pc.FECHA_FIN IS NOT NULL
          AND DATE(pc.FECHA_FIN AT TIME ZONE 'America/Santiago') = DATE_ADD(CURRENT_DATE('America/Santiago'), INTERVAL 1 DAY)
      `),
    ]);

    // Marcar como VENCIDO todos los planes cuya FECHA_FIN ya pasó
    await this.bq.query(`
      UPDATE ${t('PLAN_CONTRATADO')}
      SET ESTADO = 'VENCIDO'
      WHERE ESTADO IN ('ACTIVO', 'TRIAL')
        AND FECHA_FIN IS NOT NULL
        AND FECHA_FIN < CURRENT_TIMESTAMP()
    `);

    // Enviar emails de aviso (en paralelo, sin bloquear si alguno falla)
    const sendAll = async (users: any[], dias: number) => {
      let ok = 0;
      for (const r of users) {
        try {
          const subject = dias <= 1
            ? `⚠️ Tu plan ${r.plan} vence mañana — renueva ahora`
            : `Tu plan ${r.plan} vence en ${dias} días`;
          await this.emailService.send(r.email, subject, this.emailService.planExpiryWarningHtml(r.nombre, r.plan, dias));
          ok++;
        } catch (e) {
          console.error(`[cron] email fallo ${r.email}:`, e);
        }
      }
      return ok;
    };

    const [ok7, ok1] = await Promise.all([sendAll(expiring7, 7), sendAll(expiring1, 1)]);

    console.log(`[cron/plan-expiry] avisos_7d=${ok7} avisos_1d=${ok1}`);
    return { enviados_7_dias: ok7, enviados_1_dia: ok1 };
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

  private async confirmPayment(token: string): Promise<string | null> {
    const rows = await this.bq.query<any>(`
      SELECT ID_USUARIO, PLAN FROM ${this.bq.t('PAGOS_PENDIENTES')}
      WHERE TOKEN = @token LIMIT 1
    `, { token }).catch(() => []);

    if (!rows.length) return null;

    await this.savePlan(rows[0].ID_USUARIO, rows[0].PLAN);

    await this.bq.query(`
      DELETE FROM ${this.bq.t('PAGOS_PENDIENTES')} WHERE TOKEN = @token
    `, { token }).catch(() => null);

    return rows[0].PLAN;
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
