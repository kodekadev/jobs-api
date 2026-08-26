import { Controller, Post, Headers, UnauthorizedException, Req } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';
import { AuthService } from '../../application/auth.service';
import { BigQueryService } from '../../../shared/infrastructure/services/bigquery.service';
import env from '../../../shared/infrastructure/environment';
import { Public } from '../../../shared/infrastructure/guards/jwt-auth.guard';
import * as crypto from 'crypto';
import { Request } from 'express';

@Public() // valida su propio CRON_SECRET (no es un JWT de usuario)
@Controller('cron')
export class CronController {
  constructor(
    private readonly planService: PlanService,
    private readonly authService: AuthService,
    private readonly bq: BigQueryService,
  ) {}

  // Cloud Scheduler llama este endpoint diariamente.
  // Header: Authorization: Bearer <CRON_SECRET>
  @Post('plan-expiry')
  async planExpiry(@Headers('authorization') auth: string) {
    if (!env.cronSecret || auth !== `Bearer ${env.cronSecret}`) {
      throw new UnauthorizedException('Forbidden');
    }

    const [r7, r1] = await Promise.all([
      this.planService.notifyExpiringPlans(7),
      this.planService.notifyExpiringPlans(1),
    ]);

    return {
      ok: true,
      enviados_7_dias: r7.enviados,
      enviados_1_dia: r1.enviados,
    };
  }

  // Marca planes vencidos como VENCIDO y envía email post-trial.
  // Llamar tras el plan-expiry para que el email no llegue antes de que expire.
  @Post('expire-plans')
  async expirePlans(@Headers('authorization') auth: string) {
    if (!env.cronSecret || auth !== `Bearer ${env.cronSecret}`) {
      throw new UnauthorizedException('Forbidden');
    }

    const [expired, notified] = await Promise.all([
      this.planService.expireOutdatedPlans(),
      this.planService.notifyPostExpiry(),
    ]);

    return { ok: true, ...expired, enviados_post_expiry: notified.enviados };
  }

  @Post('cleanup-unverified')
  async cleanupUnverified(@Headers('authorization') auth: string) {
    if (!env.cronSecret || auth !== `Bearer ${env.cronSecret}`) {
      throw new UnauthorizedException('Forbidden');
    }
    return this.authService.cleanupUnverifiedUsers();
  }

  @Post('email-events')
  async emailEvents(
    @Req() req: Request,
    @Headers('svix-id') svixId: string,
    @Headers('svix-timestamp') svixTs: string,
    @Headers('svix-signature') svixSig: string,
  ) {
    // Verificar firma de Resend (Svix)
    const secret = env.resendWebhookSecret;
    if (secret) {
      const rawBody = (req as any).rawBody as Buffer;
      const signedContent = `${svixId}.${svixTs}.${rawBody?.toString() ?? JSON.stringify(req.body)}`;
      const secretBytes = Buffer.from(secret.replace(/^whsec_/, ''), 'base64');
      const computed = crypto.createHmac('sha256', secretBytes).update(signedContent).digest('base64');
      const valid = (svixSig || '').split(' ').some(s => s.replace(/^v1,/, '') === computed);
      if (!valid) throw new UnauthorizedException('Invalid signature');
    }

    const { type, data } = req.body as any;
    const tipo = type === 'email.opened' ? 'email_open' : type === 'email.clicked' ? 'email_click' : null;
    if (!tipo) return { ok: true, skipped: true };

    const id = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const email = data?.to?.[0] ?? data?.email ?? null;
    const emailTipo = (data?.tags as any[])?.find((t: any) => t.name === 'tipo')?.value ?? null;

    await this.bq.query(`
      INSERT INTO ${this.bq.t('EVENTOS_ANALYTICS')} (id, tipo, uid, email_tipo, ip, created_at)
      SELECT @id, @tipo, u.ID_USUARIO, @emailTipo, NULL, CURRENT_TIMESTAMP()
      FROM ${this.bq.t('USUARIOS')} u
      WHERE LOWER(u.EMAIL) = LOWER(@email)
      LIMIT 1
    `, { id, tipo, email, emailTipo }).catch(() => null);

    return { ok: true };
  }
}
