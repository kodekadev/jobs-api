import { Injectable } from '@nestjs/common';
import { Resend } from 'resend';
import env from '../environment';

@Injectable()
export class EmailService {
  private get resend(): Resend {
    if (!env.resendApiKey) throw new Error('RESEND_API_KEY no configurado');
    return new Resend(env.resendApiKey);
  }

  async send(to: string, subject: string, html: string): Promise<void> {
    const { error } = await this.resend.emails.send({ from: env.fromEmail, to, subject, html });
    if (error) {
      console.error('[EmailService] Resend error:', JSON.stringify(error));
      throw new Error(error.message || 'Error enviando email');
    }
  }

  resetPasswordHtml(nombre: string, link: string): string {
    return `
      <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px">
        <img src="${env.frontendUrl}/JOBS.png" alt="Jobs" style="height:40px;margin-bottom:24px"/>
        <h2 style="color:#0A2463;margin:0 0 8px">Recupera tu contraseña</h2>
        <p style="color:#555;margin:0 0 24px">Hola ${nombre || 'usuario'}, haz clic en el botón para crear una nueva contraseña.</p>
        <a href="${link}" style="display:inline-block;background:#0A2463;color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700">
          Cambiar contraseña
        </a>
        <p style="color:#999;font-size:12px;margin-top:24px">Este link expira en 1 hora.</p>
      </div>
    `;
  }

  postulaFacilHtml(nombre: string, cargos: string[]): string {
    return `
      <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px">
        <img src="${env.frontendUrl}/JOBS.png" alt="Jobs" style="height:40px;margin-bottom:24px"/>
        <h2 style="color:#0A2463;margin:0 0 8px">¡Todo listo!</h2>
        <p style="color:#555">Hola ${nombre || 'usuario'}, ya estamos buscando empleos para ti como:</p>
        <ul style="color:#0A2463;font-weight:600">
          ${cargos.map((c) => `<li>${c}</li>`).join('')}
        </ul>
        <p style="color:#555">Te avisaremos cuando tengamos novedades.</p>
      </div>
    `;
  }
}
