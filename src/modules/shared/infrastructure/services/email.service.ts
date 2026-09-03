import { Injectable } from '@nestjs/common';
import { Resend } from 'resend';
import * as crypto from 'crypto';
import env from '../environment';
import { BigQueryService } from './bigquery.service';

@Injectable()
export class EmailService {
  private readonly resend: Resend;

  constructor(private readonly bq: BigQueryService) {
    this.resend = new Resend(env.resendApiKey);
  }

  generateUnsubToken(email: string): string {
    return crypto.createHmac('sha256', env.jwtSecret).update(email.toLowerCase().trim()).digest('hex');
  }

  private unsubUrl(email: string): string {
    return `${env.frontendUrl}/desuscribirse?token=${this.generateUnsubToken(email)}&email=${encodeURIComponent(email)}`;
  }

  private async isUnsubscribed(email: string): Promise<boolean> {
    try {
      const rows = await this.bq.query<any>(
        `SELECT 1 FROM \`${process.env.BQ_PROJECT || 'jobs-425301'}.${process.env.BQ_DATASET || 'DWH'}.EMAIL_UNSUBSCRIBED\`
         WHERE LOWER(email) = LOWER(@email) LIMIT 1`,
        { email },
      );
      return rows.length > 0;
    } catch {
      return false; // si la tabla no existe aún, no bloquear
    }
  }

  async send(to: string, subject: string, html: string): Promise<void> {
    if (await this.isUnsubscribed(to)) return;

    const footer = `
      <div style="text-align:center;padding:8px 32px 20px;background:#F8FAFC">
        <p style="color:#CBD5E1;font-size:11px;margin:0;line-height:1.8">
          Recibiste este correo porque tienes una cuenta en AplicAI.<br>
          <a href="${this.unsubUrl(to)}" style="color:#CBD5E1;text-decoration:underline">Cancelar suscripción a correos</a>
        </p>
      </div>`;

    const htmlFinal = html.includes('</body>')
      ? html.replace('</body>', `${footer}</body>`)
      : html + footer;

    await this.resend.emails.send({ from: env.fromEmail, to, subject, html: htmlFinal });
  }

  // ─── TEMPLATES ────────────────────────────────────────────────────────────

  private base(headerBg: string, headerContent: string, body: string): string {
    return `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#F0F4F8;font-family:Arial,Helvetica,sans-serif">
  <div style="max-width:520px;margin:32px auto;border-radius:16px;overflow:hidden;box-shadow:0 4px 24px rgba(0,0,0,0.1)">
    <div style="background:${headerBg};padding:28px 32px;text-align:center">
      <div style="margin-bottom:8px"><span style="font-size:26px;font-weight:800;color:#ffffff;letter-spacing:-0.5px;font-family:Arial,sans-serif">Aplic<span style="color:#4ECDC4">AI</span></span></div>
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
        <p style="color:#0F172A;font-weight:700;font-size:14px;margin:0 0 12px">Para comenzar en 2 pasos:</p>
        ${[
          ['1', 'Completa tu perfil', 'Agrega tus cargos, CV, ubicación y pretensión de renta. Sin esto no podemos postular.'],
          ['2', 'Activa el Autopilot', 'Desde ese momento buscamos y postulamos empleos por ti en portales de empleo todos los días hábiles.'],
        ].map(([n, t, d]) => `
          <div style="display:flex;gap:12px;margin-bottom:12px;align-items:flex-start">
            <div style="width:24px;height:24px;border-radius:50%;background:#2A8FA5;color:white;font-weight:700;font-size:12px;flex-shrink:0;text-align:center;line-height:24px">${n}</div>
            <div>
              <p style="color:#0F172A;font-weight:700;font-size:14px;margin:0 0 2px">${t}</p>
              <p style="color:#64748B;font-size:13px;margin:0">${d}</p>
            </div>
          </div>`).join('')}
      </div>

<a href="${env.frontendUrl}/postula-facil"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Completar mi perfil →
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
      SPRINT:  ['750 postulaciones en 30 días', '4 cargos buscados', '✨ Optimización de CV con IA (3)'],
      TURBO:   ['750 postulaciones en 30 días', '4 cargos buscados', '✨ Optimización de CV con IA (3)'],
      PREMIUM: ['50 postulaciones automáticas/día', '10 cargos buscados', '✨ Optimización de CV con IA (5/mes)', 'Soporte 24/7'],
    };
    const features = planFeatures[plan] || [];

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 20px">
        Hola <strong>${nombre}</strong>, tu plan <strong>${plan}</strong> vence el <strong>${fechaFin}</strong>.
        Si no renuevas, pasarás automáticamente al plan gratuito (5 postulaciones/día).
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

  verifyEmailHtml(nombre: string, code: string): string {
    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">Código de verificación</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">Ingresa este código para activar tu cuenta</p>`;

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 24px">
        Hola <strong>${nombre}</strong>, ingresa el siguiente código en AplicAI para verificar tu cuenta:
      </p>
      <div style="background:#F0F9FF;border-radius:16px;padding:28px;text-align:center;margin-bottom:24px">
        <span style="font-size:42px;font-weight:800;letter-spacing:10px;color:#1E6E82;font-family:monospace">${code}</span>
      </div>
      <p style="color:#94A3B8;font-size:13px;margin:0;line-height:1.5">
        Este código expira en 24 horas. Si no creaste esta cuenta, ignora este correo.
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

  cargoQualityHtml(nombre: string, cargos: string[]): string {
    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">Mejora tus cargos y postula mejor</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">Un pequeño ajuste puede hacer una gran diferencia</p>`;

    const cargosList = cargos.map(c => `
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:6px">
        <span style="color:#F59E0B;font-size:14px">⚠</span>
        <span style="color:#64748B;font-size:14px">${c}</span>
      </div>`).join('');

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 16px">
        Hola <strong>${nombre}</strong>, notamos que tus cargos actuales son muy genéricos:
      </p>

      <div style="background:#FFFBEB;border-radius:12px;padding:16px;margin-bottom:20px;border-left:4px solid #F59E0B">
        ${cargosList}
        <p style="color:#92400E;font-size:13px;margin:12px 0 0;line-height:1.5">
          Cargos como <strong>"Consultor"</strong> o <strong>"Director"</strong> solos hacen que el sistema postule a empleos de rubros muy distintos (ingeniería, salud, tecnología) que quizás no te interesan.
        </p>
      </div>

      <p style="color:#334155;font-size:14px;line-height:1.6;margin:0 0 8px"><strong>¿Cómo mejorarlo?</strong> Agrega el rubro o área a tu cargo:</p>

      <div style="background:#F0FDF4;border-radius:12px;padding:16px;margin-bottom:24px">
        ${[
          ['Consultor → ', 'Consultor de Gestión, Consultor Organizacional, Consultor Financiero'],
          ['Director → ', 'Director Comercial, Director de Operaciones, Director de Proyectos'],
          ['Vendedor → ', 'Ejecutivo de Ventas B2B, Vendedor Industrial, Ejecutivo Comercial'],
        ].map(([antes, despues]) => `
          <div style="margin-bottom:8px;font-size:13px">
            <span style="color:#EF4444">${antes}</span>
            <span style="color:#16A34A">${despues}</span>
          </div>`).join('')}
      </div>

      <a href="${env.frontendUrl}/postula-facil"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Actualizar mis cargos →
      </a>

      <p style="color:#94A3B8;font-size:13px;margin-top:24px;line-height:1.5">
        ¿Tienes dudas? Escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>`;

    return this.base('linear-gradient(135deg, #78350F 0%, #D97706 100%)', headerContent, body);
  }

  campaignHtml(nombre: string, descuento: number, vigencia: string, plan: string): string {
    const PRICES: Record<string, number> = { PRO: 9990, TURBO: 14990, PREMIUM: 19990 };
    const plansToShow = plan && PRICES[plan] ? [plan] : ['PRO', 'TURBO', 'PREMIUM'];
    const precio = (p: string) => Math.round(PRICES[p] * (1 - descuento / 100));
    const fmtClp = (n: number) => `$${n.toLocaleString('es-CL')}`;

    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">Oferta especial para ti 🎉</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">${descuento}% de descuento — válido hasta el ${vigencia}</p>`;

    const planCards = plansToShow.map(p => `
      <div style="background:#F8FAFC;border-radius:12px;padding:16px;text-align:center;flex:1;min-width:120px">
        <div style="font-weight:800;font-size:13px;color:#64748B;letter-spacing:0.08em;text-transform:uppercase;margin-bottom:8px">${p}</div>
        <div style="font-size:12px;color:#94A3B8;text-decoration:line-through;margin-bottom:2px">${fmtClp(PRICES[p])}</div>
        <div style="font-size:24px;font-weight:800;color:#0F172A">${fmtClp(precio(p))}</div>
        <div style="font-size:11px;color:#10B981;font-weight:700;margin-top:2px">-${descuento}%</div>
      </div>`).join('');

    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 20px">
        Hola <strong>${nombre}</strong>, tenemos una oferta especial para ti. Durante un tiempo limitado, puedes activar tu plan con un <strong>${descuento}% de descuento</strong>.
      </p>
      <div style="display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap">${planCards}</div>
      <p style="color:#64748B;font-size:13px;margin:0 0 24px;line-height:1.5">
        La oferta es válida hasta el <strong>${vigencia}</strong>. Una vez activado el plan, este se renueva al precio normal al siguiente período.
      </p>
      <a href="${env.frontendUrl}/planes"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Ver oferta y activar plan →
      </a>
      <p style="color:#94A3B8;font-size:13px;margin-top:24px;line-height:1.5">
        ¿Preguntas? Escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>`;

    return this.base('linear-gradient(135deg, #1E3A5F 0%, #2A8FA5 100%)', headerContent, body);
  }

  postExpiryHtml(nombre: string, postulaciones: number): string {
    const headerContent = `
      <h1 style="color:white;margin:16px 0 4px;font-size:22px">Tu período de prueba terminó</h1>
      <p style="color:rgba(255,255,255,0.65);margin:0;font-size:14px">Pero lo que lograste sigue siendo tuyo</p>`;

    const statColor = '#4ECDC4';
    const body = `
      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 20px">
        Hola <strong>${nombre}</strong>, tu período de prueba de AplicAI ha terminado.
        Esto es lo que logramos juntos mientras estuvo activo:
      </p>

      <div style="background:#F0F9FF;border-radius:16px;padding:24px;margin-bottom:24px;text-align:center">
        <div style="font-size:48px;font-weight:800;color:${statColor};line-height:1;margin-bottom:4px">${postulaciones}</div>
        <div style="color:#334155;font-size:15px;font-weight:600">postulaciones enviadas automáticamente</div>
        <div style="color:#64748B;font-size:13px;margin-top:4px">mientras tú hacías otras cosas</div>
      </div>

      <p style="color:#334155;font-size:15px;line-height:1.6;margin:0 0 24px">
        Para seguir recibiendo entrevistas sin esfuerzo, activa un plan pagado y el autopilot retoma inmediatamente.
      </p>

      <a href="${env.frontendUrl}/planes"
        style="display:inline-block;background:linear-gradient(135deg,#1E6E82,#2A8FA5);color:white;padding:14px 28px;border-radius:10px;text-decoration:none;font-weight:700;font-size:15px">
        Ver planes y continuar →
      </a>

      <p style="color:#94A3B8;font-size:13px;margin-top:24px;line-height:1.5">
        ¿Tienes preguntas? Escríbenos a <a href="mailto:soporte@aplicai.cl" style="color:#2A8FA5">soporte@aplicai.cl</a>
      </p>`;

    return this.base(
      'linear-gradient(135deg, #090F1E 0%, #0D2140 100%)',
      headerContent,
      body,
    );
  }
}
