import { Controller, Get, Post, Param, Body, HttpCode, Query, Header, Headers, UnauthorizedException } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';
import { Public } from '../../../shared/infrastructure/guards/jwt-auth.guard';
import env from '../../../shared/infrastructure/environment';

@Controller('plan')
export class PlanController {
  constructor(private readonly planService: PlanService) {}

  @Get(':id')
  @Header('Cache-Control', 'no-store')
  getPlan(@Param('id') id: string) {
    return this.planService.getPlan(id);
  }

  @Post()
  @HttpCode(200)
  savePlan(@Body() body: { id: string; plan: string }) {
    return this.planService.savePlan(body.id, body.plan);
  }

  @Post('checkout')
  @HttpCode(200)
  createCheckout(@Body() body: { id: string; plan: string; email: string }) {
    return this.planService.createCheckout(body.id, body.plan, body.email);
  }

  @Public() // webhook de Flow — se valida contra la API de Flow con el token
  @Post('notificacion')
  @HttpCode(200)
  notification(@Query('token') token: string) {
    return this.planService.handleNotification(token);
  }

  @Post('trial')
  @HttpCode(200)
  activateTrial(@Body() body: { id: string }) {
    return this.planService.activateTrial(body.id);
  }

  @Post('cancel')
  @HttpCode(200)
  cancelPlan(@Body() body: { id: string }) {
    return this.planService.cancelPlan(body.id);
  }

  @Get(':id/cv-optimizaciones')
  @Header('Cache-Control', 'no-store')
  getCvOptimizaciones(@Param('id') id: string) {
    return this.planService.getCvOptimizaciones(id);
  }

  @Post(':id/cv-optimizaciones')
  @HttpCode(200)
  registrarCvOptimizacion(@Param('id') id: string, @Body() body: { plan: string }) {
    return this.planService.registrarCvOptimizacion(id, body.plan || 'FREE');
  }

  @Public() // retorno de pago — autenticado por el token de Flow (de un solo uso)
  @Get('retorno/:token')
  getReturnStatus(@Param('token') token: string) {
    return this.planService.getReturnStatus(token);
  }

  // ── CRON: Cloud Scheduler llama esto diariamente ───────────────────────────
  // Protegido con CRON_SECRET en Authorization header
  @Public()
  @Post('cron/notificar')
  @HttpCode(200)
  async cronNotificar(@Headers('authorization') auth: string) {
    if (!env.cronSecret || auth !== `Bearer ${env.cronSecret}`) {
      throw new UnauthorizedException();
    }
    // Notifica a usuarios con plan venciendo en 7, 3 y 1 día
    const [r7, r3, r1] = await Promise.all([
      this.planService.notifyExpiringPlans(7),
      this.planService.notifyExpiringPlans(3),
      this.planService.notifyExpiringPlans(1),
    ]);
    return { dias7: r7.enviados, dias3: r3.enviados, dias1: r1.enviados };
  }
}
