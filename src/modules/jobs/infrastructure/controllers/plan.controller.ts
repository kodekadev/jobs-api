import { Controller, Get, Post, Param, Body, HttpCode, Query } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';

@Controller('plan')
export class PlanController {
  constructor(private readonly planService: PlanService) {}

  @Get(':id')
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

  @Post('notificacion')
  @HttpCode(200)
  notification(@Query('token') token: string) {
    return this.planService.handleNotification(token);
  }

  @Get('retorno/:token')
  getReturnStatus(@Param('token') token: string) {
    return this.planService.getReturnStatus(token);
  }
}
