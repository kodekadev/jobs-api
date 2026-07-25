import { Controller, Get, Post, Param, Body, HttpCode } from '@nestjs/common';
import { PostulacionesService } from '../../application/postulaciones.service';

@Controller('postulaciones')
export class PostulacionesController {
  constructor(private readonly service: PostulacionesService) {}

  // La extensión Chrome registra aquí cada postulación enviada
  @Post()
  @HttpCode(200)
  save(@Body() body: any) {
    return this.service.save(body);
  }

  // IDs ya postulados — deduplicación de la extensión
  @Get(':id/applied')
  getApplied(@Param('id') id: string) {
    return this.service.getAppliedIds(id);
  }

  @Get(':id/today-count')
  getTodayCount(@Param('id') id: string) {
    return this.service.getTodayCount(id);
  }

  @Get(':id')
  getByUser(@Param('id') id: string) {
    return this.service.getByUser(id);
  }
}
