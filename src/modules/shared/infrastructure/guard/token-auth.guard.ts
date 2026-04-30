import {
  CanActivate,
  ExecutionContext,
  Injectable,
  Logger,
  UnauthorizedException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

@Injectable()
export class TokenAuthGuard implements CanActivate {
  constructor(private readonly config: ConfigService) {}

  async canActivate(context: ExecutionContext): Promise<boolean> {
    try {
      const request = context.switchToHttp().getRequest();
      const { authorization }: any = request.headers;
      if (!authorization || authorization.trim() === '') {
        throw new UnauthorizedException('Token inválido');
      }
      const authToken = authorization.replace(/token/gim, '').trim();
      if (authToken != this.config.get<string>('API_TOKEN')) {
        throw new UnauthorizedException('Token no coincide');
      }
      return true;
    } catch (err: any) {
      Logger.error(
        `Error al validar Token de autorizacion - ${err.message}`,
        this.constructor.name,
      );
      //Logger.error(`error=${error.message}`, this.constructor.name, id);
      throw err;
    }
  }
}
