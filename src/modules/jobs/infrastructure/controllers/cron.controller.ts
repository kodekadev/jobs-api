import { Controller, Post, HttpCode, Headers, UnauthorizedException } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';
import env from '../../../shared/infrastructure/environment';

@Controller('cron')
export class CronController {
  constructor(private readonly planService: PlanService) {}

  @Post('plan-expiry')
  @HttpCode(200)
  async planExpiry(@Headers('authorization') auth: string) {
    const token = (auth || '').replace(/^Bearer\s+/i, '').trim();
    if (!env.cronSecret || token !== env.cronSecret) {
      throw new UnauthorizedException('CRON_SECRET inválido');
    }
    return this.planService.runPlanExpiryCron();
  }
}
