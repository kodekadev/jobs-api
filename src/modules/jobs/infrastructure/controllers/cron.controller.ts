import { Controller, Post, Headers, UnauthorizedException } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';
import { AuthService } from '../../application/auth.service';
import env from '../../../shared/infrastructure/environment';
import { Public } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Public() // valida su propio CRON_SECRET (no es un JWT de usuario)
@Controller('cron')
export class CronController {
  constructor(
    private readonly planService: PlanService,
    private readonly authService: AuthService,
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
}
