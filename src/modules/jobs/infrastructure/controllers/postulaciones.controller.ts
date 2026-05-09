import { Controller, Get, Param } from '@nestjs/common';
import { PostulacionesService } from '../../application/postulaciones.service';

@Controller('postulaciones')
export class PostulacionesController {
  constructor(private readonly service: PostulacionesService) {}

  @Get(':id')
  getByUser(@Param('id') id: string) {
    return this.service.getByUser(id);
  }
}
