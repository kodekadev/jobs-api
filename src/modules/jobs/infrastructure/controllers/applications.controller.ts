import { Controller, Get, Post, Param, Body, UseGuards } from '@nestjs/common';
import { ApplicationsService } from '../../application/applications.service';
import { JwtAuthGuard } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Controller('applications')
@UseGuards(JwtAuthGuard)
export class ApplicationsController {
  constructor(private readonly service: ApplicationsService) {}

  @Post()
  save(@Body() body: any) {
    return this.service.saveApplication(body);
  }

  @Get(':id/applied')
  getApplied(@Param('id') id: string) {
    return this.service.getAppliedIds(id);
  }
}
