import { CanActivate, ExecutionContext, Injectable } from '@nestjs/common';
//import { log } from '../log-service';
import { Request } from 'express';

@Injectable()
export class AutorizacionEnvGuard implements CanActivate {
  canActivate(context: ExecutionContext): boolean {
    const req: Request = context.switchToHttp().getRequest();
    const res = context.switchToHttp().getResponse();

    const [oldWrite, oldEnd] = [res.write, res.end];
    const chunks: Buffer[] = [];
    let message = '';
    (res.write as unknown) = function (chunk: any) {
      chunks.push(Buffer.from(chunk));
      (oldWrite as Function).apply(res, arguments);
    };

    (res.end as unknown) = function (chunk: any) {
      if (chunk) {
        chunks.push(Buffer.from(chunk));
      }
      message = Buffer.concat(chunks).toString('utf8');
      (oldEnd as Function).apply(res, arguments);
    };

    res.locals.startDate = new Date();
    res.locals.transaccionId = new Date().getTime();

    res.on('finish', () => {
      const finishDate: any = new Date();
      const elapsedMilliseconds = Math.abs(res.locals.startDate - finishDate);
      const app = res.locals.acceso ? res.locals.acceso.Nombre : '';
      switch (res.statusCode) {
        case 200:
        case 201:
          //log.info(`${res.locals.transaccionId} | STATUS=${res.statusCode} APP=${app} PATH=${req.method}:${req.path} TIEMPO=${elapsedMilliseconds}`);
          break;
        case 400:
        case 401:
          //log.warn(`${res.locals.transaccionId} | STATUS=${res.statusCode} APP=${app} PATH=${req.method}:${req.path} TIEMPO=${elapsedMilliseconds} MENSAJE=${message}`);
          break;
        default:
          //log.error(`${res.locals.transaccionId} | STATUS=${res.statusCode} APP=${app} PATH=${req.method}:${req.path} TIEMPO=${elapsedMilliseconds} MENSAJE=${message}`);
          break;
      }
    });

    return true;
  }
}
