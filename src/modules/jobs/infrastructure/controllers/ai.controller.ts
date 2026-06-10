import { Controller, Post, Body, UseGuards } from '@nestjs/common';
import { AiService } from '../../application/ai.service';
import { JwtAuthGuard } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Controller('api/ai')
@UseGuards(JwtAuthGuard)
export class AiController {
  constructor(private readonly ai: AiService) {}

  @Post('responder-pregunta')
  async responder(@Body() body: { pregunta: string; perfil: any }) {
    const respuesta = await this.ai.responderPregunta(body.pregunta || '', body.perfil || {});
    return { respuesta };
  }
}
