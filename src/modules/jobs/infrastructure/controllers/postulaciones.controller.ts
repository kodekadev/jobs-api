import { Controller, Get, Param, UseGuards } from '@nestjs/common';
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
}
