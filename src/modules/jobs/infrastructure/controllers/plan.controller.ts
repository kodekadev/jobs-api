import { Controller, Get, Post, Param, Body, HttpCode, Query, UseGuards } from '@nestjs/common';
import { PlanService } from '../../application/plan.service';
import { JwtAuthGuard } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Controller('plan')
export class PlanController {
  constructor(private readonly planService: PlanService) {}

  @Get(':id')
  @UseGuards(JwtAuthGuard)
  getPlan(@Param('id') id: string) {
    return this.planService.getPlan(id);
  }

  @Post()
  @HttpCode(200)
  @UseGuards(JwtAuthGuard)
  savePlan(@Body() body: { id: string; plan: string }) {
    return this.planService.savePlan(body.id, body.plan);
  }

  @Post('checkout')
  @HttpCode(200)
  @UseGuards(JwtAuthGuard)
  createCheckout(@Body() body: { id: string; plan: string; email: string }) {
    return this.planService.createCheckout(body.id, body.plan, body.email);
  }

  @Post('notificacion')
  @HttpCode(200)
  notification(@Body('token') bodyToken: string, @Query('token') queryToken: string) {
    return this.planService.handleNotification(bodyToken || queryToken);
  }

  @Get('retorno/:token')
  getReturnStatus(@Param('token') token: string) {
    return this.planService.getReturnStatus(token);
  }
}
