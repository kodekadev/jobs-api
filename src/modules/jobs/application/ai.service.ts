import { Injectable } from '@nestjs/common';
import Anthropic from '@anthropic-ai/sdk';
import env from '../../shared/infrastructure/environment';

@Injectable()
export class AiService {
  private readonly anthropic: Anthropic;

  constructor() {
    this.anthropic = new Anthropic({ apiKey: env.anthropicApiKey });
  }

  async responderPregunta(pregunta: string, perfil: any): Promise<string> {
    const contexto = [
      perfil.nombre      ? `Nombre: ${perfil.nombre}` : '',
      perfil.carrera || perfil.profesion ? `Profesión: ${perfil.carrera || perfil.profesion}` : '',
      perfil.experiencia ? `Años de experiencia: ${perfil.experiencia}` : '',
      perfil.pretension_general ? `Pretensión de renta: $${perfil.pretension_general} CLP` : '',
      perfil.celular     ? `Teléfono: ${perfil.celular}` : '',
      perfil.resumen     ? `Resumen profesional: ${perfil.resumen}` : '',
      'Ciudad: Santiago, Chile',
    ].filter(Boolean).join('\n');

    const msg = await this.anthropic.messages.create({
      model: 'claude-haiku-4-5-20251001',
      max_tokens: 120,
      messages: [{
        role: 'user',
        content:
          `Eres un candidato llenando un formulario de postulación en LinkedIn Easy Apply.\n` +
          `Tu perfil:\n${contexto}\n\n` +
          `Pregunta del formulario: "${pregunta}"\n\n` +
          `Responde de forma concisa y directa, en primera persona. ` +
          `Para preguntas numéricas devuelve solo el número. ` +
          `Para preguntas de sí/no devuelve solo "Sí" o "No". ` +
          `Solo devuelve la respuesta, sin explicaciones.`,
      }],
    });

    return ((msg.content[0] as any).text || 'Sí').trim();
  }
}
