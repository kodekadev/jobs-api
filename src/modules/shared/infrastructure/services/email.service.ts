import { Injectable } from '@nestjs/common';
import { Resend } from 'resend';
import env from '../environment';

const TEAL = '#2A8FA5';
const DARK = '#1a1a2e';

function base(content: string): string {
  return `
    <div style="font-family:Arial,sans-serif;max-width:520px;margin:0 auto;padding:32px;background:#ffffff">
      <div style="margin-bottom:28px">
        <span style="font-size:22px;font-weight:800;color:${DARK}">Aplic</span><span style="font-size:22px;font-weight:800;color:${TEAL}">AI</span>
      </div>
      ${content}
      <hr style="border:none;border-top:1px solid #E2E8F0;margin:32px 0"/>
      <p style="color:#94A3B8;font-size:12px;margin:0">
        AplicAI · Nexon SpA · Santiago, Chile<br/>
        <a href="${env.frontendUrl}" style="color:${TEAL}">aplicai.cl</a> ·
        <a href="mailto:soporte@aplicai.cl" style="color:${TEAL}">soporte@aplicai.cl</a>
      </p>
    </div>
  `;
}

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

  welcomeHtml(nombre: string): string {
    return base(`
      <h2 style="color:${DARK};margin:0 0 8px">¡Bienvenido/a a AplicAI, ${nombre || 'usuario'}!</h2>
      <p style="color:#555;margin:0 0 16px">
        Ya tienes acceso a <strong>14 días de prueba gratis</strong> del Plan PRO.
        Configura tu perfil y activa las postulaciones automáticas para que empieces a recibir oportunidades de empleo.
      </p>
      <a href="${env.frontendUrl}/perfil"
         style="display:inline-block;background:${TEAL};color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;margin-bottom:16px">
        Completar mi perfil
      </a>
      <p style="color:#888;font-size:13px;margin:0">
        Si tienes alguna duda escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:${TEAL}">soporte@aplicai.cl</a>.
      </p>
    `);
  }

  resetPasswordHtml(nombre: string, link: string): string {
    return base(`
      <h2 style="color:${DARK};margin:0 0 8px">Recupera tu contraseña</h2>
      <p style="color:#555;margin:0 0 24px">
        Hola ${nombre || 'usuario'}, haz clic en el botón para crear una nueva contraseña.
      </p>
      <a href="${link}"
         style="display:inline-block;background:${TEAL};color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700">
        Cambiar contraseña
      </a>
      <p style="color:#999;font-size:12px;margin-top:24px">Este link expira en 1 hora.</p>
    `);
  }

  planExpiryWarningHtml(nombre: string, plan: string, dias: number): string {
    const urgente = dias <= 1;
    return base(`
      <h2 style="color:${urgente ? '#DC2626' : DARK};margin:0 0 8px">
        ${urgente ? '⚠️ Tu plan vence mañana' : `Tu plan ${plan} vence en ${dias} días`}
      </h2>
      <p style="color:#555;margin:0 0 16px">
        Hola ${nombre || 'usuario'}, tu plan <strong>${plan}</strong> expira
        ${urgente ? 'mañana' : `en ${dias} días`}. Al vencer, bajarás a Plan Gratis con
        5 postulaciones por día y 1 cargo buscado.
      </p>
      <a href="${env.frontendUrl}/planes"
         style="display:inline-block;background:${TEAL};color:white;padding:14px 32px;border-radius:8px;text-decoration:none;font-weight:700;margin-bottom:16px">
        Renovar mi plan
      </a>
      <p style="color:#888;font-size:13px;margin:0">
        Si tienes preguntas escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:${TEAL}">soporte@aplicai.cl</a>.
      </p>
    `);
  }

  postulaFacilHtml(nombre: string, cargos: string[]): string {
    return base(`
      <h2 style="color:${DARK};margin:0 0 8px">¡Todo listo!</h2>
      <p style="color:#555;margin:0 0 8px">
        Hola ${nombre || 'usuario'}, ya estamos buscando empleos para ti como:
      </p>
      <ul style="color:${TEAL};font-weight:700;margin:0 0 16px;padding-left:20px">
        ${cargos.map((c) => `<li style="margin-bottom:4px">${c}</li>`).join('')}
      </ul>
      <p style="color:#555;margin:0">Te avisaremos cuando tengamos novedades.</p>
    `);
  }
}
