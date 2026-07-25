import { Controller, Post, Body, HttpCode } from '@nestjs/common';
import { AiService } from '../../application/ai.service';

@Controller('ai')
export class AiController {
  constructor(private readonly ai: AiService) {}

  // La extensión evalúa aquí si una vacante calza con el CV antes de postular
  @Post('evaluar-empleo')
  @HttpCode(200)
  evaluarEmpleo(@Body() body: { user_id: string; titulo?: string; descripcion?: string }) {
    return this.ai.evaluarEmpleo(body);
  }

  // Preguntas desconocidas del wizard — Claude responde como el candidato
  @Post('responder-pregunta')
  @HttpCode(200)
  responderPregunta(@Body() body: { user_id?: string; pregunta: string; opciones?: string[]; perfil?: any }) {
    return this.ai.responderPregunta(body);
  }
}
