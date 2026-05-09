import { Injectable, UnauthorizedException, BadRequestException } from '@nestjs/common';
import * as bcrypt from 'bcrypt';
import * as jwt from 'jsonwebtoken';
import * as crypto from 'crypto';
import { v4 as uuidv4 } from 'uuid';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';
import env from '../../shared/infrastructure/environment';

@Injectable()
export class AuthService {
  constructor(
    private readonly bq: BigQueryService,
    private readonly email: EmailService,
  ) {}

  // ─── LOGIN: single BigQuery JOIN that returns ALL user data ────────────────
  async login(emailRaw: string, password: string) {
    const emailNorm = emailRaw.trim().toLowerCase();

    const rows = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO, u.NOMBRE, u.EMAIL, u.CELULAR, u.ASIGNADO_LKD,
        u.FECHA_REGISTRO, u.PASSWORD,
        ic.PROFESION, ic.EXPERIENCIA,
        ic.FOTO_URL, ic.CV_URL as INFO_CV_URL,
        pf.CARGOS, pf.UBICACIONES, pf.RESUMEN,
        pf.CV_URL as PF_CV_URL,
        pf.EXPERIENCIA as PF_EXPERIENCIA,
        pf.PRETENSION_GENERAL,
        COALESCE(pa.ACTIVO, FALSE) as AUTO_ACTIVO,
        COALESCE(pc.PLAN, 'FREE') as PLAN,
        pc.ESTADO as PLAN_ESTADO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')} pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      LEFT JOIN ${this.bq.t('PLAN_CONTRATADO')} pc
        ON u.ID_USUARIO = pc.ID_USUARIO AND pc.ESTADO = 'ACTIVO'
      WHERE LOWER(u.EMAIL) = @email
      LIMIT 1
    `, { email: emailNorm });

    if (!rows.length) throw new UnauthorizedException('Credenciales inválidas');

    const u = rows[0];

    if (!u.PASSWORD) throw new UnauthorizedException('Cuenta Google — usa ese método');

    const ok = await bcrypt.compare(password, u.PASSWORD);
    if (!ok) throw new UnauthorizedException('Credenciales inválidas');

    return this.buildResponse(u);
  }

  // ─── GOOGLE LOGIN ─────────────────────────────────────────────────────────
  async loginGoogle(emailRaw: string, nombre: string) {
    const emailNorm = emailRaw.trim().toLowerCase();

    const existing = await this.bq.query<any>(`
      SELECT
        u.ID_USUARIO, u.NOMBRE, u.EMAIL, u.CELULAR, u.ASIGNADO_LKD, u.FECHA_REGISTRO,
        ic.PROFESION, ic.EXPERIENCIA, ic.FOTO_URL, ic.CV_URL as INFO_CV_URL,
        pf.CARGOS, pf.UBICACIONES, pf.RESUMEN, pf.CV_URL as PF_CV_URL,
        pf.EXPERIENCIA as PF_EXPERIENCIA, pf.PRETENSION_GENERAL,
        COALESCE(pa.ACTIVO, FALSE) as AUTO_ACTIVO,
        COALESCE(pc.PLAN, 'FREE') as PLAN,
        pc.ESTADO as PLAN_ESTADO
      FROM ${this.bq.t('USUARIOS')} u
      LEFT JOIN ${this.bq.t('INFO_CLIENTE')} ic ON u.ID_USUARIO = ic.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULA_FACIL')} pf ON u.ID_USUARIO = pf.ID_USUARIO
      LEFT JOIN ${this.bq.t('POSTULACIONES_AUTO')} pa ON u.ID_USUARIO = pa.ID_USUARIO
      LEFT JOIN ${this.bq.t('PLAN_CONTRATADO')} pc
        ON u.ID_USUARIO = pc.ID_USUARIO AND pc.ESTADO = 'ACTIVO'
      WHERE LOWER(u.EMAIL) = @email
      LIMIT 1
    `, { email: emailNorm });

    if (existing.length) return this.buildResponse(existing[0]);

    const id = uuidv4();
    await this.bq.query(`
      INSERT INTO ${this.bq.t('USUARIOS')}
        (ID_USUARIO, NOMBRE, EMAIL, CELULAR, PASSWORD, TERMINOS, ASIGNADO_LKD, FECHA_REGISTRO)
      VALUES
        (@id, @nombre, @email, NULL, NULL, 1, 0, CURRENT_TIMESTAMP())
    `, { id, nombre, email: emailNorm });

    const newUser = { ID_USUARIO: id, NOMBRE: nombre, EMAIL: emailNorm, PLAN: 'FREE', AUTO_ACTIVO: false };
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
    const id = `jobs_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;

    await this.bq.query(`
      INSERT INTO ${this.bq.t('USUARIOS')}
        (ID_USUARIO, NOMBRE, EMAIL, CELULAR, PASSWORD, TERMINOS, ASIGNADO_LKD, FECHA_REGISTRO)
      VALUES
        (@id, @nombre, @email, @celular, @password, @terminos, 0, CURRENT_TIMESTAMP())
    `, { id, nombre: body.nombre, email: emailNorm, celular: body.celular, password: hash, terminos: body.terminos ? 1 : 0 });

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

  // ─── HELPERS ──────────────────────────────────────────────────────────────
  private buildResponse(u: any) {
    const token = jwt.sign(
      { id: u.ID_USUARIO, email: u.EMAIL, nombre: u.NOMBRE },
      env.jwtSecret,
      { expiresIn: '7d' },
    );

    const cargos = this.parseJson(u.CARGOS);
    const ubicaciones = this.parseJson(u.UBICACIONES);

    const postulaCompleto = !!(
      u.PROFESION && u.PF_CV_URL && cargos.length &&
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

  private parseJson(val: any): any[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
