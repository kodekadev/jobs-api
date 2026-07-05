import { Injectable } from '@nestjs/common';
import { Resend } from 'resend';
import env from '../environment';

@Injectable()
export class EmailService {
  private readonly resend: Resend;

  constructor() {
    this.resend = new Resend(env.resendApiKey);
  }

  async send(to: string, subject: string, html: string): Promise<void> {
    await this.resend.emails.send({ from: env.fromEmail, to, subject, html });
  }

  // ─── TEMPLATES ────────────────────────────────────────────────────────────

  private base(headerBg: string, headerContent: string, body: string): string {
    return `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:Arial,Helvetica,sans-serif">
  <div style="max-width:520px;margin:32px auto;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.1)">
    <div style="background:${headerBg};padding:28px 32px">
      <img src="${env.frontendUrl}/JOBS.png" alt="AplicAI" style="height:32px"/>
      ${headerContent}
    </div>
    <div style="background:#ffffff;padding:32px">
      ${body}
    </div>
    <div style="background:#F8FAFC;padding:20px 32px;border-top:1px solid #E2E8F0">
      <p style="color:#94A3B8;font-size:12px;margin:0;line-height:1.6">
        AplicAI — Nexon SpA, RUT 78.193.017-2, Ñuñoa, Santiago, Chile.<br>
        <a href="${env.frontendUrl}/terminos" style="color:#2A8FA5">Términos</a> ·
        <a href="${env.frontendUrl}/privacidad" style="color:#2A8FA5">Privacidad</a> ·
        soporte@aplicai.cl
      </p>
    </div>
  </div>
</body>
</html>`;
  }

  welcomeHtml(nombre: string): string {
    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">¡Bienvenido a AplicAI!</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">Tu cuenta está activa y lista para usar</p>`;

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 24px">
        Hola <strong>${nombre}</strong>, ya puedes empezar a postular automáticamente a empleos mientras tú descansas.
      </p>

      <div style="background:#F0F9FF;border-radius:12px;padding:20px;margin-bottom:24px">
        <p style="color:#0F172A;font-weight:700;font-size:14px;margin:0 0 12px">Para comenzar en 3 pasos:</p>
        ${[
          ['1', 'Completa tu perfil', 'Agrega tus cargos, CV y experiencia para que podamos postular por ti.'],
          ['2', 'Instala la extensión Chrome', 'La extensión postula automáticamente en LinkedIn mientras navegas.'],
          ['3', 'Activa el Autopilot', 'Una vez activo, postulamos por ti todos los días de forma automática.'],
        ].map(([n, t, d]) => `
          <div style="display:flex;gap:12px;margin-bottom:12px;align-items:flex-start">
            <div style="width:24px;height:24px;border-radius:50%;background:#2A8FA5;color:white;font-weight:700;font-size:12px;display:flex;align-items:center;justify-content:center;flex-shrink:0;text-align:center;line-height:24px">${n}</div>
            <div>
              <p style="color:#0F172A;font-weight:700;font-size:14px;margin:0 0 2px">${t}</p>
              <p style="color:#64748B;font-size:13px;margin:0">${d}</p>
            </div>
          </div>`).join('')}
      </div>

      <a href="${env.frontendUrl}/dashboard"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Ir al dashboard →
      </a>

      <p style="color:#94A3B8;font-size:13px;margin-top:24px;line-height:1.5">
        ¿Tienes dudas? Escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>`;

    return this.base(
      'linear-gradient(135deg, #090F1E 0%, #0D2140 100%)',
      headerContent,
      body,
    );
  }

  planExpiryHtml(nombre: string, plan: string, diasRestantes: number, fechaFin: string): string {
    const isUrgent = diasRestantes <= 1;
    const headerBg = isUrgent
      ? 'linear-gradient(135deg, #431407 0%, #7C2D12 100%)'
      : 'linear-gradient(135deg, #1c1917 0%, #292524 100%)';

    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">
        ${isUrgent ? '⚠️ ' : ''}Tu plan ${plan} vence ${isUrgent ? 'mañana' : `en ${diasRestantes} días`}
      </h1>
      <p style="color:rgba(255,255,255,0.6);margin:0;font-size:14px">Renueva para no perder tus postulaciones automáticas</p>`;

    const planFeatures: Record<string, string[]> = {
      PRO:     ['25 postulaciones automáticas/día', '4 cargos buscados', '✨ Optimización de CV con IA (2/mes)', 'Soporte prioritario'],
      PREMIUM: ['50 postulaciones automáticas/día', '10 cargos buscados', '✨ Optimización de CV con IA (5/mes)', 'Soporte 24/7'],
    };
    const features = planFeatures[plan] || [];

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 20px">
        Hola <strong>${nombre}</strong>, tu plan <strong>${plan}</strong> vence el <strong>${fechaFin}</strong>.
        Si no renuevas, pasarás automáticamente al plan gratuito (10 postulaciones/día).
      </p>

      ${features.length ? `
      <div style="background:#F8FAFC;border-radius:12px;padding:20px;margin-bottom:24px;border-left:4px solid ${isUrgent ? '#EF4444' : '#7C3AED'}">
        <p style="color:#0F172A;font-weight:700;font-size:14px;margin:0 0 10px">Lo que perderías con el plan gratuito:</p>
        ${features.map(f => `
          <div style="display:flex;gap:8px;margin-bottom:8px;align-items:flex-start">
            <span style="color:#EF4444;font-size:16px;line-height:1">×</span>
            <span style="color:#64748B;font-size:14px">${f}</span>
          </div>`).join('')}
      </div>` : ''}

      <a href="${env.frontendUrl}/planes"
        style="display:inline-block;background:linear-gradient(135deg,#6D28D9,#7C3AED);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Renovar plan ${plan} →
      </a>

      <p style="color:#94A3B8;font-size:13px;margin-top:24px;line-height:1.5">
        También puedes gestionar tu suscripción desde <a href="${env.frontendUrl}/cuenta" style="color:#2A8FA5">Mi cuenta</a>.
        Si tienes dudas escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>`;

    return this.base(headerBg, headerContent, body);
  }

  resetPasswordHtml(nombre: string, link: string): string {
    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">Recupera tu contraseña</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">Haz clic en el botón para crear una nueva</p>`;

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 24px">
        Hola <strong>${nombre || 'usuario'}</strong>, recibimos una solicitud para restablecer la contraseña de tu cuenta.
      </p>
      <a href="${link}"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Cambiar contraseña →
      </a>
      <p style="color:#94A3B8;font-size:13px;margin-top:24px">
        Este link expira en 1 hora. Si no solicitaste esto, ignora este correo.
      </p>`;

    return this.base(
      'linear-gradient(135deg, #090F1E 0%, #0D2140 100%)',
      headerContent,
      body,
    );
  }

  postulaFacilHtml(nombre: string, cargos: string[]): string {
    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">¡Todo listo, estamos en marcha!</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">Autopilot activado</p>`;

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 16px">
        Hola <strong>${nombre || 'usuario'}</strong>, ya estamos buscando empleos para ti como:
      </p>
      <div style="background:#F0F9FF;border-radius:12px;padding:16px;margin-bottom:24px">
        ${cargos.map(c => `
          <div style="display:flex;gap:8px;margin-bottom:6px;align-items:center">
            <span style="color:#2A8FA5;font-size:16px">→</span>
            <span style="color:#0F172A;font-weight:600;font-size:14px">${c}</span>
          </div>`).join('')}
      </div>
      <p style="color:#64748B;font-size:14px;margin:0 0 24px">Te avisaremos cuando tengamos novedades de postulaciones.</p>
      <a href="${env.frontendUrl}/mis-postulaciones"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Ver mis postulaciones →
      </a>`;

    return this.base(
      'linear-gradient(135deg, #052e16 0%, #064e3b 100%)',
      headerContent,
      body,
    );
  }
}
