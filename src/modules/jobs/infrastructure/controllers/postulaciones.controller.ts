import { Controller, Get, Patch, Param, UseGuards } from '@nestjs/common';
import { PostulacionesService } from '../../application/postulaciones.service';
import { JwtAuthGuard } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Controller('postulaciones')
@UseGuards(JwtAuthGuard)
export class PostulacionesController {
  constructor(private readonly service: PostulacionesService) {}

  @Get(':id')
  getByUser(@Param('id') id: string) {
    return this.service.getByUser(id);
  }

  @Get(':id/notificaciones')
  getNotificaciones(@Param('id') id: string) {
    return this.service.getNotificaciones(id);
  }

  @Patch(':id/notificaciones/leidas')
  marcarLeidas(@Param('id') id: string) {
    return this.service.marcarLeidas(id);
  }
}
