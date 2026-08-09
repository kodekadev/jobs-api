import { Controller, Get, Post, Put, Delete, Param, Body, Query, HttpCode } from '@nestjs/common';
import { AdminService } from '../../application/admin.service';

@Controller('admin')
export class AdminController {
  constructor(private readonly adminService: AdminService) {}

  @Post('verify-pin')
  @HttpCode(200)
  verifyPin(@Body('pin') pin: string) {
    return this.adminService.verifyPin(pin);
  }

  @Get('users')
  getUsers() {
    return this.adminService.getUsers();
  }

  @Get('users/:id/diagnostics')
  getDiagnostics(@Param('id') id: string) {
    return this.adminService.getDiagnostics(id);
  }

  @Get('users/:id/postulaciones')
  getPostulaciones(@Param('id') id: string, @Query('limit') limit?: string) {
    return this.adminService.getPostulaciones(id, Math.min(Number(limit) || 200, 500));
  }

  @Post('users/:id/plan')
  @HttpCode(200)
  updatePlan(@Param('id') id: string, @Body() body: { plan: string; fecha_fin?: string }) {
    return this.adminService.updatePlan(id, body.plan, body.fecha_fin);
  }

  @Put('users/:id/cargos')
  updateCargos(@Param('id') id: string, @Body() body: { cargos: string[] }) {
    return this.adminService.updateCargos(id, body.cargos);
  }

  @Delete('users/:uid/portal/:portal')
  deletePortal(@Param('uid') uid: string, @Param('portal') portal: string) {
    return this.adminService.deletePortal(uid, portal);
  }

  @Get('analytics')
  getAnalytics() {
    return this.adminService.getAnalytics();
  }
}
