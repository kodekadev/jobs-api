import { CanActivate, ExecutionContext, Injectable, UnauthorizedException } from '@nestjs/common';
import * as jwt from 'jsonwebtoken';
import env from '../environment';
import { BigQueryService } from '../services/bigquery.service';

// Cache de estado activo para no consultar BQ en cada request
// Estructura: userId -> { active: boolean, cachedAt: number }
const _activeCache = new Map<string, { active: boolean; cachedAt: number }>();
const CACHE_TTL_MS = 5 * 60 * 1000; // 5 minutos

@Injectable()
export class JwtAuthGuard implements CanActivate {
  constructor(private readonly bq: BigQueryService) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    const request = context.switchToHttp().getRequest();
    const auth = request.headers['authorization'];
    if (!auth || !auth.startsWith('Bearer ')) throw new UnauthorizedException('Token requerido');
    const token = auth.slice(7);

    let payload: any;
    try {
      payload = jwt.verify(token, env.jwtSecret);
    } catch {
      throw new UnauthorizedException('Token inválido o expirado');
    }

    const userId: string = payload.id;
    const now = Date.now();
    const cached = _activeCache.get(userId);

    let active: boolean;
    if (cached && now - cached.cachedAt < CACHE_TTL_MS) {
      active = cached.active;
    } else {
      const rows = await this.bq.query<any>(
        `SELECT ACTIVO FROM ${this.bq.t('USUARIOS')} WHERE ID_USUARIO = @id LIMIT 1`,
        { id: userId },
      );
      active = rows.length > 0 && rows[0].ACTIVO !== false;
      _activeCache.set(userId, { active, cachedAt: now });
    }

    if (!active) throw new UnauthorizedException('Cuenta suspendida');

    request.user = payload;
    return true;
  }

  // Llama esto tras desactivar un usuario para forzar re-check inmediato
  static invalidateCache(userId: string) {
    _activeCache.delete(userId);
  }
}
