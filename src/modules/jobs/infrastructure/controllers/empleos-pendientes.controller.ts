import { Controller, Get, Post, Patch, Param, Body, HttpCode, Request } from '@nestjs/common';
import { EmpleosPendientesService } from '../../application/empleos-pendientes.service';

@Controller('empleos-pendientes')
export class EmpleosPendientesController {
  constructor(private readonly service: EmpleosPendientesService) {}

  @Get()
  getPendientes(@Request() req: any) {
    return this.service.getPendientes(req.user.id);
  }

  @Post(':id/aprobar')
  @HttpCode(200)
  aprobar(@Request() req: any, @Param('id') id: string) {
    return this.service.aprobar(req.user.id, id);
  }

  @Post(':id/rechazar')
  @HttpCode(200)
  rechazar(@Request() req: any, @Param('id') id: string) {
    return this.service.rechazar(req.user.id, id);
  }

  @Post('aprobar-todos')
  @HttpCode(200)
  aprobarTodos(@Request() req: any) {
    return this.service.aprobarTodos(req.user.id);
  }

  @Get('modo-revision')
  getModoRevision(@Request() req: any) {
    return this.service.getModoRevision(req.user.id);
  }

  @Patch('modo-revision')
  @HttpCode(200)
  setModoRevision(@Request() req: any, @Body() body: { activo: boolean }) {
    return this.service.setModoRevision(req.user.id, Boolean(body.activo));
  }
}
