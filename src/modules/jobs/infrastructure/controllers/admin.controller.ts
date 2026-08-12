import { Controller, Get, Post, Put, Delete, Param, Body, HttpCode, Request, ForbiddenException, Query } from '@nestjs/common';
import { AdminService } from '../../application/admin.service';
import { PostulacionesService } from '../../application/postulaciones.service';
import env from '../../../shared/infrastructure/environment';

// Nota: usa :userId (no :id) para no disparar el check anti-IDOR del JWT guard,
// que bloquearía al admin operar sobre otros usuarios.
@Controller('admin')
export class AdminController {
  constructor(
    private readonly service: AdminService,
    private readonly postulaciones: PostulacionesService,
  ) {}

  @Post('verify-pin')
  @HttpCode(200)
  verifyPin(@Request() req: any, @Body() body: { pin: string }) {
    this.service.checkAdmin(req.user.email);
    if (!env.adminPin || body.pin !== env.adminPin) {
      throw new ForbiddenException('PIN incorrecto');
    }
    return { ok: true };
  }

  @Get('users')
  getUsers(@Request() req: any) {
    this.service.checkAdmin(req.user.email);
    return this.service.getUsers();
  }

  @Get('users/:userId/diagnostics')
  getDiagnostics(@Request() req: any, @Param('userId') userId: string) {
    this.service.checkAdmin(req.user.email);
    return this.service.getDiagnostics(userId);
  }

  @Post('users/:userId/plan')
  @HttpCode(200)
  setPlan(
    @Request() req: any,
    @Param('userId') userId: string,
    @Body() body: { plan: string; fecha_fin?: string },
  ) {
    this.service.checkAdmin(req.user.email);
    return this.service.setPlan(userId, body.plan, body.fecha_fin);
  }

  @Get('users/:userId/postulaciones')
  getPostulaciones(@Request() req: any, @Param('userId') userId: string) {
    this.service.checkAdmin(req.user.email);
    return this.postulaciones.getByUser(userId);
  }

  @Put('users/:userId/cargos')
  @HttpCode(200)
  setCargos(
    @Request() req: any,
    @Param('userId') userId: string,
    @Body() body: { cargos: string[] },
  ) {
    this.service.checkAdmin(req.user.email);
    return this.service.setCargos(userId, body.cargos);
  }

  @Get('analytics')
  getAnalytics(@Request() req: any) {
    this.service.checkAdmin(req.user.email);
    return this.service.getAnalytics();
  }

  @Get('billing')
  getBilling(@Request() req: any) {
    this.service.checkAdmin(req.user.email);
    return this.service.getBilling();
  }

  @Delete('users/:userId/portal/:portal')
  deletePortal(
    @Request() req: any,
    @Param('userId') userId: string,
    @Param('portal') portal: string,
  ) {
    this.service.checkAdmin(req.user.email);
    return this.service.deletePortal(userId, portal);
  }
}
