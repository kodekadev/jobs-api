import { Injectable, UnauthorizedException, BadRequestException } from '@nestjs/common';
import * as bcrypt from 'bcrypt';
import * as jwt from 'jsonwebtoken';
import * as crypto from 'crypto';
import { OAuth2Client } from 'google-auth-library';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import { TelegramService } from '../../shared/infrastructure/services/telegram.service';
import env from '../../shared/infrastructure/environment';
import { PLAN_VIGENTE_SQL } from './plan.service';

@Injectable()
export class AuthService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
    private readonly telegram: TelegramService,
  ) {}

  // ─── ID SECUENCIAL: jobs1, jobs2, jobs3… ─────────────────────────────────
  private async nextUserId(): Promise<string> {
    const rows = await this.bq.query<any>(`
      SELECT MAX(SAFE_CAST(REGEXP_EXTRACT(ID_USUARIO, r'^jobs(\\d+)$') AS INT64)) AS max_num
      FROM ${this.bq.t('USUARIOS')}
    `);
    const max = rows[0]?.max_num ?? 0;
    return `jobs${max + 1}`;
  }

  // ─── LOGIN: single BigQuery JOIN that returns ALL user data ────────────────
  async login(emailRaw: string, password: string) {
    const emailNorm = emailRaw.trim().toLowerCase();

    const rows = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO, u.NOMBRE, u.EMAIL, u.CELULAR, u.ASIGNADO_LKD,
        u.FECHA_REGISTRO, u.PASSWORD, u.EMAIL_VERIFICADO,
        ic.PROFESION, ic.EXPERIENCIA,
        ic.FOTO_URL, ic.CV_URL as INFO_CV_URL,
        pf.PROFESION as PF_PROFESION, pf.CARGOS, pf.UBICACIONES, pf.RESUMEN,
        pf.CV_URL as PF_CV_URL,
        pf.EXPERIENCIA as PF_EXPERIENCIA,
        pf.PRETENSION_GENERAL,
        COALESCE(pa.ACTIVO, 0) as AUTO_ACTIVO,
        COALESCE(pc.PLAN, 'FREE') as PLAN,
        pc.ESTADO as PLAN_ESTADO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')} pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      LEFT JOIN ${this.bq.t('PLAN_CONTRATADO')} pc
        ON u.ID_USUARIO = pc.ID_USUARIO AND pc.ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
        AND ${PLAN_VIGENTE_SQL}
      WHERE LOWER(u.EMAIL) = @email
      LIMIT 1
    `, { email: emailNorm });

    if (!rows.length) throw new UnauthorizedException('No existe una cuenta con ese email');

    const u = rows[0];

    if (!u.PASSWORD) throw new UnauthorizedException('Cuenta Google — usa ese método');

    const ok = await bcrypt.compare(password, u.PASSWORD);
    if (!ok) throw new UnauthorizedException('Contraseña incorrecta');

    // NULL means existing user (before verification feature) → allow through
    if (u.EMAIL_VERIFICADO === false) {
      throw new UnauthorizedException('email_no_verificado');
    }

    this.trackLogin(u.ID_USUARIO);
    return this.buildResponse(u);
  }

  // ─── GOOGLE LOGIN ─────────────────────────────────────────────────────────
  async loginGoogle(credential?: string, emailLegacy?: string, nombreLegacy?: string) {
    let emailNorm: string;
    let nombre: string;

    if (credential) {
      const client = new OAuth2Client('994947687832-oqbi6lnmdv96m5jcsqcajr0d7u9f22th.apps.googleusercontent.com');
      let payload: any;
      try {
        const ticket = await client.verifyIdToken({
          idToken: credential,
          audience: '994947687832-oqbi6lnmdv96m5jcsqcajr0d7u9f22th.apps.googleusercontent.com',
        });
        payload = ticket.getPayload();
      } catch {
        throw new UnauthorizedException('Token de Google inválido');
      }
      if (!payload?.email) throw new UnauthorizedException('Token de Google inválido');
      emailNorm = payload.email.trim().toLowerCase();
      nombre    = payload.name || emailNorm;
    } else if (emailLegacy) {
      emailNorm = emailLegacy.trim().toLowerCase();
      nombre    = nombreLegacy || emailNorm;
    } else {
      throw new UnauthorizedException('Token de Google inválido');
    }

    const existing = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO, u.NOMBRE, u.EMAIL, u.CELULAR, u.ASIGNADO_LKD, u.FECHA_REGISTRO,
        ic.PROFESION, ic.EXPERIENCIA, ic.FOTO_URL, ic.CV_URL as INFO_CV_URL,
        pf.PROFESION as PF_PROFESION, pf.CARGOS, pf.UBICACIONES, pf.RESUMEN, pf.CV_URL as PF_CV_URL,
        pf.EXPERIENCIA as PF_EXPERIENCIA, pf.PRETENSION_GENERAL,
        COALESCE(pa.ACTIVO, 0) as AUTO_ACTIVO,
        COALESCE(pc.PLAN, 'FREE') as PLAN,
        pc.ESTADO as PLAN_ESTADO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')} pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      LEFT JOIN ${this.bq.t('PLAN_CONTRATADO')} pc
        ON u.ID_USUARIO = pc.ID_USUARIO AND pc.ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
        AND ${PLAN_VIGENTE_SQL}
      WHERE LOWER(u.EMAIL) = @email
      LIMIT 1
    `, { email: emailNorm });

    if (existing.length) {
      this.trackLogin(existing[0].ID_USUARIO);
      return this.buildResponse(existing[0]);
    }

    const id = await this.nextUserId();
    await this.bq.query(`
      INSERT INTO ${this.bq.t('USUARIOS')}
        (ID_USUARIO, NOMBRE, EMAIL, CELULAR, PASSWORD, TERMINOS, ASIGNADO_LKD, FECHA_REGISTRO)
      VALUES
        (@id, @nombre, @email, NULL, NULL, 1, 0, CURRENT_TIMESTAMP())
    `, { id, nombre, email: emailNorm });

    this.email.send(
      emailNorm,
      '¡Bienvenido a AplicAI! Tu cuenta está activa',
      this.email.welcomeHtml(nombre),
      'welcome',
    ).catch(() => null);

    this.telegram.newUser(nombre, emailNorm, 'google');
    this.assignTrial(id).catch(() => null);

    const newUser = { ID_USUARIO: id, NOMBRE: nombre, EMAIL: emailNorm, PLAN: 'TRIAL', AUTO_ACTIVO: false };
    return this.buildResponse(newUser);
  }

  // ─── REGISTER ─────────────────────────────────────────────────────────────
  async register(body: {
    nombre: string;
    email: string;
    celular: string;
    password: string;
    terminos: boolean;
  }) {
    const emailNorm = body.email.trim().toLowerCase();
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const celularRegex = /^\+56[0-9]{9}$/;
    const passRegex = /^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*?&]{6,30}$/;

    if (!emailRegex.test(emailNorm)) throw new BadRequestException('Email inválido');
    if (!celularRegex.test(body.celular)) throw new BadRequestException('Celular inválido (+56XXXXXXXXX)');
    if (!passRegex.test(body.password)) throw new BadRequestException('Contraseña inválida (6-30 chars, letras y números)');
    if (!body.terminos) throw new BadRequestException('Debes aceptar los términos');

    const dup = await this.bq.query<any>(`
      SELECT ID_USUARIO FROM ${this.bq.t('USUARIOS')}
      WHERE LOWER(EMAIL) = @email OR CELULAR = @celular
      LIMIT 1
    `, { email: emailNorm, celular: body.celular });

    if (dup.length) throw new BadRequestException('Email o celular ya registrado');

    const hash = await bcrypt.hash(body.password, 10);
    const id = await this.nextUserId();

    await this.bq.query(`
      INSERT INTO ${this.bq.t('USUARIOS')}
        (ID_USUARIO, NOMBRE, EMAIL, CELULAR, PASSWORD, TERMINOS, ASIGNADO_LKD, FECHA_REGISTRO, EMAIL_VERIFICADO)
      VALUES
        (@id, @nombre, @email, @celular, @password, @terminos, 0, CURRENT_TIMESTAMP(), false)
    `, { id, nombre: body.nombre, email: emailNorm, celular: body.celular, password: hash, terminos: body.terminos ? 1 : 0 });

    const code    = String(Math.floor(100000 + Math.random() * 900000));
    const hashed  = crypto.createHash('sha256').update(code).digest('hex');
    const expires = new Date(Date.now() + 24 * 3600 * 1000).toISOString();

    await this.bq.query(`
      INSERT INTO ${this.bq.t('EMAIL_VERIFICATIONS')} (ID_USUARIO, TOKEN, EXPIRES_AT, CREATED_AT)
      VALUES (@id, @token, @expires, CURRENT_TIMESTAMP())
    `, { id, token: hashed, expires }).catch(() => null);

    this.email.send(
      emailNorm,
      `${code} es tu código de verificación de AplicAI`,
      this.email.verifyEmailHtml(body.nombre, code),
      'verify_email',
    ).catch(() => null);

    return { success: true, pendingVerification: true };
  }

  // ─── VERIFY EMAIL (código de 6 dígitos) ──────────────────────────────────
  async verifyEmail(emailRaw: string, code: string) {
    const emailNorm = emailRaw.trim().toLowerCase();
    const hashed    = crypto.createHash('sha256').update(code.trim()).digest('hex');

    const userRows = await this.bq.query<any>(`
      SELECT ID_USUARIO, NOMBRE FROM ${this.bq.t('USUARIOS')}
      WHERE LOWER(EMAIL) = @email LIMIT 1
    `, { email: emailNorm });

    if (!userRows.length) throw new BadRequestException('Código inválido');

    const userId = userRows[0].ID_USUARIO;
    const nombre = userRows[0].NOMBRE;

    const rows = await this.bq.query<any>(`
      SELECT EXPIRES_AT FROM ${this.bq.t('EMAIL_VERIFICATIONS')}
      WHERE ID_USUARIO = @id AND TOKEN = @token LIMIT 1
    `, { id: userId, token: hashed });

    if (!rows.length) throw new BadRequestException('Código inválido');
    if (new Date(rows[0].EXPIRES_AT) < new Date()) throw new BadRequestException('Código expirado — solicita uno nuevo');

    await Promise.all([
      this.bq.query(`
        UPDATE ${this.bq.t('USUARIOS')} SET EMAIL_VERIFICADO = true WHERE ID_USUARIO = @id
      `, { id: userId }),
      this.bq.query(`
        DELETE FROM ${this.bq.t('EMAIL_VERIFICATIONS')} WHERE ID_USUARIO = @id
      `, { id: userId }),
    ]);

    await this.assignTrial(userId);

    this.email.send(
      emailNorm,
      '¡Bienvenido a AplicAI! Tu cuenta está activa',
      this.email.welcomeHtml(nombre),
      'welcome',
    ).catch(() => null);

    this.telegram.newUser(nombre, emailNorm, 'email');

    // Retornar sesión completa para auto-login al dashboard
    const fullUser = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO, u.NOMBRE, u.EMAIL, u.CELULAR, u.ASIGNADO_LKD, u.FECHA_REGISTRO,
        ic.PROFESION, ic.EXPERIENCIA,
        ic.FOTO_URL, ic.CV_URL as INFO_CV_URL,
        pf.PROFESION as PF_PROFESION, pf.CARGOS, pf.UBICACIONES, pf.RESUMEN,
        pf.CV_URL as PF_CV_URL,
        pf.EXPERIENCIA as PF_EXPERIENCIA,
        pf.PRETENSION_GENERAL,
        COALESCE(pa.ACTIVO, 0) as AUTO_ACTIVO,
        COALESCE(pc.PLAN, 'FREE') as PLAN, pc.ESTADO as PLAN_ESTADO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')} pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      LEFT JOIN ${this.bq.t('PLAN_CONTRATADO')} pc
        ON u.ID_USUARIO = pc.ID_USUARIO AND pc.ESTADO IN ('ACTIVO', 'CANCELADO_PENDIENTE')
        AND ${PLAN_VIGENTE_SQL}
      WHERE u.ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    return this.buildResponse(fullUser[0]);
  }

  // ─── RESEND VERIFICATION ──────────────────────────────────────────────────
  async resendVerification(emailRaw: string) {
    const emailNorm = emailRaw.trim().toLowerCase();

    const rows = await this.bq.query<any>(`
      SELECT ID_USUARIO, NOMBRE, EMAIL_VERIFICADO FROM ${this.bq.t('USUARIOS')}
      WHERE LOWER(EMAIL) = @email LIMIT 1
    `, { email: emailNorm });

    if (!rows.length) return { success: true };
    if (rows[0].EMAIL_VERIFICADO === true) return { success: true };

    const userId = rows[0].ID_USUARIO;
    const nombre = rows[0].NOMBRE;

    await this.bq.query(`
      DELETE FROM ${this.bq.t('EMAIL_VERIFICATIONS')} WHERE ID_USUARIO = @id
    `, { id: userId }).catch(() => null);

    const code    = String(Math.floor(100000 + Math.random() * 900000));
    const hashed  = crypto.createHash('sha256').update(code).digest('hex');
    const expires = new Date(Date.now() + 24 * 3600 * 1000).toISOString();

    await this.bq.query(`
      INSERT INTO ${this.bq.t('EMAIL_VERIFICATIONS')} (ID_USUARIO, TOKEN, EXPIRES_AT, CREATED_AT)
      VALUES (@id, @token, @expires, CURRENT_TIMESTAMP())
    `, { id: userId, token: hashed, expires }).catch(() => null);

    await this.email.send(
      emailNorm,
      `${code} es tu código de verificación de AplicAI`,
      this.email.verifyEmailHtml(nombre, code),
    ).catch(() => null);

    return { success: true };
  }

  // ─── FORGOT PASSWORD ───────────────────────────────────────────────────────
  async forgotPassword(emailRaw: string) {
    const emailNorm = emailRaw.trim().toLowerCase();

    const rows = await this.bq.query<any>(`
      SELECT ID_USUARIO, NOMBRE FROM ${this.bq.t('USUARIOS')}
      WHERE LOWER(EMAIL) = @email LIMIT 1
    `, { email: emailNorm });

    if (!rows.length) return { success: true };

    const user = rows[0];

    await this.bq.query(`
      DELETE FROM ${this.bq.t('PASSWORD_RESETS')} WHERE ID_USUARIO = @id
    `, { id: user.ID_USUARIO });

    const rawToken = crypto.randomBytes(32).toString('hex');
    const hashed = crypto.createHash('sha256').update(rawToken).digest('hex');
    const expires = new Date(Date.now() + 3600 * 1000).toISOString();

    await this.bq.query(`
      INSERT INTO ${this.bq.t('PASSWORD_RESETS')} (ID_USUARIO, TOKEN, EXPIRES_AT)
      VALUES (@id, @token, @expires)
    `, { id: user.ID_USUARIO, token: hashed, expires });

    const link = `${env.frontendUrl}/reset-password?token=${rawToken}`;
    await this.email.send(
      emailNorm,
      'Recupera tu contraseña — Jobs',
      this.email.resetPasswordHtml(user.NOMBRE, link),
      'reset_password',
    );

    return { success: true };
  }

  // ─── RESET PASSWORD ────────────────────────────────────────────────────────
  async resetPassword(rawToken: string, newPassword: string) {
    const passRegex = /^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*?&]{6,30}$/;
    if (!passRegex.test(newPassword)) throw new BadRequestException('Contraseña inválida');

    const hashed = crypto.createHash('sha256').update(rawToken).digest('hex');

    const rows = await this.bq.query<any>(`
      SELECT ID_USUARIO, EXPIRES_AT FROM ${this.bq.t('PASSWORD_RESETS')}
      WHERE TOKEN = @token LIMIT 1
    `, { token: hashed });

    if (!rows.length) throw new BadRequestException('Token inválido o expirado');

    const record = rows[0];
    if (new Date(record.EXPIRES_AT) < new Date()) throw new BadRequestException('Token expirado');

    const hash = await bcrypt.hash(newPassword, 10);

    await Promise.all([
      this.bq.query(`
        UPDATE ${this.bq.t('USUARIOS')} SET PASSWORD = @pass WHERE ID_USUARIO = @id
      `, { pass: hash, id: record.ID_USUARIO }),
      this.bq.query(`
        DELETE FROM ${this.bq.t('PASSWORD_RESETS')} WHERE ID_USUARIO = @id
      `, { id: record.ID_USUARIO }),
    ]);

    return { success: true };
  }

  // ─── CHANGE PASSWORD ──────────────────────────────────────────────────────
  async changePassword(userId: string, currentPassword: string, newPassword: string) {
    const passRegex = /^(?=.*[A-Za-z])(?=.*\d)[A-Za-z\d@$!%*?&]{6,30}$/;
    if (!passRegex.test(newPassword)) throw new BadRequestException('Contraseña inválida (6-30 chars, letras y números)');

    const rows = await this.bq.query<any>(`
      SELECT PASSWORD FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    if (!rows.length) throw new UnauthorizedException('Usuario no encontrado');
    if (!rows[0].PASSWORD) throw new BadRequestException('Cuenta Google — usa ese método');

    const ok = await bcrypt.compare(currentPassword, rows[0].PASSWORD);
    if (!ok) throw new UnauthorizedException('Contraseña actual incorrecta');

    const hash = await bcrypt.hash(newPassword, 10);
    await this.bq.query(`
      UPDATE ${this.bq.t('USUARIOS')} SET PASSWORD = @pass WHERE ID_USUARIO = @id
    `, { pass: hash, id: userId });

    return { success: true };
  }

  // ─── DEACTIVATE ACCOUNT ───────────────────────────────────────────────────
  async deactivateAccount(userId: string) {
    await this.bq.query(`
      UPDATE ${this.bq.t('POSTULACIONES_AUTO')} SET ACTIVO = 0 WHERE ID_USUARIO = @id
    `, { id: userId }).catch(() => null);

    return { success: true };
  }

  // ─── SET CELULAR ──────────────────────────────────────────────────────────
  async setCelular(userId: string, celular: string) {
    const celularRegex = /^\+56[0-9]{9}$/;
    if (!celularRegex.test(celular)) throw new BadRequestException('Celular inválido (+56XXXXXXXXX)');

    await this.bq.query(`
      UPDATE ${this.bq.t('USUARIOS')} SET CELULAR = @celular WHERE ID_USUARIO = @id
    `, { celular, id: userId });

    return { success: true, celular };
  }

  // ─── DELETE ACCOUNT ───────────────────────────────────────────────────────
  async deleteAccount(userId: string, password: string) {
    const rows = await this.bq.query<any>(`
      SELECT PASSWORD FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    if (!rows.length) throw new UnauthorizedException('Usuario no encontrado');

    if (rows[0].PASSWORD) {
      const ok = await bcrypt.compare(password, rows[0].PASSWORD);
      if (!ok) throw new UnauthorizedException('Contraseña incorrecta');
    }

    // Soft-delete: eliminar datos sensibles y marcar como eliminado
    await Promise.all([
      this.bq.query(`DELETE FROM ${this.bq.t('POSTULACIONES_AUTO')} WHERE ID_USUARIO = @id`, { id: userId }).catch(() => null),
      this.bq.query(`DELETE FROM ${this.bq.t('POSTULA_FACIL')} WHERE ID_USUARIO = @id`, { id: userId }).catch(() => null),
      this.bq.query(`DELETE FROM ${this.bq.t('PLAN_CONTRATADO')} WHERE ID_USUARIO = @id`, { id: userId }).catch(() => null),
      this.bq.query(`
        UPDATE ${this.bq.t('USUARIOS')}
        SET NOMBRE = 'CUENTA_ELIMINADA', EMAIL = CONCAT('deleted_', ID_USUARIO, '@aplicai.cl'),
            PASSWORD = NULL, CELULAR = NULL
        WHERE ID_USUARIO = @id
      `, { id: userId }),
    ]);

    return { success: true };
  }

  // ─── CLEANUP USUARIOS NO VERIFICADOS ─────────────────────────────────────
  async cleanupUnverifiedUsers() {
    // Solo borra EMAIL_VERIFICADO = false (no NULL — esos son usuarios anteriores al feature).
    // Espera 48h desde FECHA_REGISTRO antes de borrar.
    const rows = await this.bq.query<any>(`
      SELECT ID_USUARIO FROM ${this.bq.t('USUARIOS')}
      WHERE EMAIL_VERIFICADO = false
        AND TIMESTAMP_DIFF(CURRENT_TIMESTAMP(), FECHA_REGISTRO, HOUR) >= 48
    `);

    if (!rows.length) return { ok: true, eliminados: 0 };

    const ids = rows.map((r: any) => r.ID_USUARIO);

    await Promise.all([
      this.bq.query(`
        DELETE FROM ${this.bq.t('EMAIL_VERIFICATIONS')}
        WHERE ID_USUARIO IN UNNEST(@ids)
      `, { ids }),
      this.bq.query(`
        DELETE FROM ${this.bq.t('USUARIOS')}
        WHERE ID_USUARIO IN UNNEST(@ids)
          AND EMAIL_VERIFICADO = false
      `, { ids }),
    ]);

    console.log(`[cron] cleanup-unverified: ${ids.length} usuarios eliminados`);
    return { ok: true, eliminados: ids.length };
  }

  // ─── UNSUBSCRIBE ──────────────────────────────────────────────────────────
  async unsubscribe(email: string, token: string): Promise<{ ok: boolean }> {
    const expected = this.email.generateUnsubToken(email);
    if (token !== expected) throw new BadRequestException('Token inválido');

    await this.bq.query(`
      CREATE TABLE IF NOT EXISTS \`jobs-425301.DWH.EMAIL_UNSUBSCRIBED\`
      (email STRING, fecha_baja TIMESTAMP)
    `).catch(() => null);

    await this.bq.query(`
      INSERT INTO \`jobs-425301.DWH.EMAIL_UNSUBSCRIBED\` (email, fecha_baja)
      SELECT @email, CURRENT_TIMESTAMP()
      WHERE NOT EXISTS (
        SELECT 1 FROM \`jobs-425301.DWH.EMAIL_UNSUBSCRIBED\` WHERE LOWER(email) = LOWER(@email)
      )
    `, { email });

    return { ok: true };
  }

  // ─── HELPERS ──────────────────────────────────────────────────────────────

  // Asigna TRIAL de 14 días al registrarse. Si ya tiene un plan activo no lo pisa.
  private async assignTrial(userId: string): Promise<void> {
    const now = new Date().toISOString();
    const fin = new Date();
    fin.setDate(fin.getDate() + 14);
    const fechaFin = fin.toISOString();
    await this.bq.query(`
      MERGE ${this.bq.t('PLAN_CONTRATADO')} T
      USING (SELECT @id AS ID_USUARIO) S
      ON T.ID_USUARIO = S.ID_USUARIO
      WHEN NOT MATCHED THEN
        INSERT (ID_USUARIO, PLAN, ESTADO, FECHA_INICIO, FECHA_FIN, METODO_PAGO)
        VALUES (@id, 'TRIAL', 'ACTIVO', @now, @fechaFin, 'REGISTRO')
    `, { id: userId, now, fechaFin })
      .catch((e: any) => console.warn('[auth] No se pudo asignar TRIAL:', e.message));
  }

  private buildResponse(u: any) {
    const token = jwt.sign(
      { id: u.ID_USUARIO, email: u.EMAIL, nombre: u.NOMBRE },
      env.jwtSecret,
      { expiresIn: '7d' },
    );

    const cargos = this.parseJson(u.CARGOS);
    const ubicaciones = this.parseJson(u.UBICACIONES);

    const postulaCompleto = !!(
      u.PF_PROFESION && u.PF_CV_URL && cargos.length &&
      u.PF_EXPERIENCIA && ubicaciones.length && u.PRETENSION_GENERAL
    );

    return {
      token,
      usuario: {
        id: u.ID_USUARIO,
        nombre: u.NOMBRE,
        email: u.EMAIL,
        celular: u.CELULAR || '',
        plan: u.PLAN || 'FREE',
        plan_estado: u.PLAN_ESTADO || null,
        asignado_lkd: u.ASIGNADO_LKD || false,
      },
      perfil: {
        profesion: u.PROFESION || '',
        experiencia: u.EXPERIENCIA || '',
        foto_url: u.FOTO_URL || '',
        cv_url: u.INFO_CV_URL || '',
      },
      postula_facil: {
        completo: postulaCompleto,
        profesion: u.PF_PROFESION || '',
        cargos,
        ubicaciones,
        resumen: u.RESUMEN || '',
        cv_url: u.PF_CV_URL || '',
        experiencia: u.PF_EXPERIENCIA || '',
        pretension_general: u.PRETENSION_GENERAL || '',
      },
      postulaciones_auto: {
        activo: Boolean(u.AUTO_ACTIVO),
      },
    };
  }

  private trackLogin(userId: string): void {
    const id = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    this.bq.query(`
      INSERT INTO ${this.bq.t('EVENTOS_ANALYTICS')} (id, tipo, uid, email_tipo, ip, created_at)
      VALUES (@id, 'login', @uid, NULL, NULL, CURRENT_TIMESTAMP())
    `, { id, uid: userId }).catch(() => null);
  }

  private parseJson(val: any): any[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
