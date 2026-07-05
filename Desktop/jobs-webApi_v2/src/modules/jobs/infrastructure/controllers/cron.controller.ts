import { Controller, Post, Headers, UnauthorizedException } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';
import env from '../../../shared/infrastructure/environment';

@Controller('api/cron')
export class CronController {
  constructor(private readonly planService: PlanService) {}

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
}
