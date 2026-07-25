import {
  CanActivate,
  ExecutionContext,
  ForbiddenException,
  Injectable,
  SetMetadata,
  UnauthorizedException,
} from '@nestjs/common';
import { Reflector } from '@nestjs/core';
import * as jwt from 'jsonwebtoken';
import env from '../environment';

/** Marca una ruta (o controller completo) como pública — sin token. */
export const IS_PUBLIC_KEY = 'isPublic';
export const Public = () => SetMetadata(IS_PUBLIC_KEY, true);

export interface JwtPayload {
  id: string;
  email: string;
  nombre: string;
}

/**
 * Guard global de autenticación + autorización.
 *
 * 1. Autenticación: exige `Authorization: Bearer <jwt>` firmado con JWT_SECRET
 *    (el mismo que firma auth.service en el login).
 * 2. Autorización (anti-IDOR): en esta API, el param `:id` y el `body.id`
 *    son SIEMPRE el id del usuario dueño del recurso. Si no coincide con el
 *    id del token → 403. Ojo si algún día agregas una ruta donde `:id` NO sea
 *    un id de usuario: usa otro nombre de parámetro (p.ej. `:token`, `:slug`).
 */
@Injectable()
export class JwtAuthGuard implements CanActivate {
  constructor(private readonly reflector: Reflector) {}

  canActivate(context: ExecutionContext): boolean {
    const isPublic = this.reflector.getAllAndOverride<boolean>(IS_PUBLIC_KEY, [
      context.getHandler(),
      context.getClass(),
    ]);
    if (isPublic) return true;

    const req = context.switchToHttp().getRequest();

    const auth: string = req.headers?.authorization || '';
    if (!auth.startsWith('Bearer ')) {
      throw new UnauthorizedException('Token requerido');
    }

    let payload: JwtPayload;
    try {
      payload = jwt.verify(auth.slice(7), env.jwtSecret) as JwtPayload;
    } catch {
      throw new UnauthorizedException('Token inválido o expirado');
    }
    req.user = payload;

    // ── Anti-IDOR centralizado ────────────────────────────────────────────
    const targetId: unknown = req.params?.id ?? req.body?.id;
    if (
      typeof targetId === 'string' &&
      targetId.length > 0 &&
      payload.id &&
      targetId !== payload.id
    ) {
      throw new ForbiddenException('No autorizado para operar sobre este usuario');
    }

    return true;
  }
}
