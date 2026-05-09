import { Controller, Get, Post, Param, Body, HttpCode } from '@nestjs/common';
import { PostulaFacilService } from '../../application/postula-facil.service';

@Controller('postula-facil')
export class PostulaFacilController {
  constructor(private readonly service: PostulaFacilService) {}

  @Get(':id')
  get(@Param('id') id: string) {
    return this.service.get(id);
  }

  @Get('estado/:id')
  estado(@Param('id') id: string) {
    return this.service.estado(id);
  }

  @Post()
  @HttpCode(200)
  save(@Body() body: any) {
    return this.service.save(body);
  }
}
