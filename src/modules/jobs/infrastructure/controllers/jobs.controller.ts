import { Controller, Query, Res, Post, Logger, Inject, Body } from '@nestjs/common';
import { Response } from 'express';
import { Respuesta } from '../../domain/dto/respuesta';
import { ArgumentoInvalido } from '../../domain/dto/argumento-invalido';
import { ITraeAcceso } from '../../domain/interface/itrae.acceso';

@Controller('jobs')
export class JobsController {
constructor(@Inject(ITraeAcceso) private readonly traeAcceso: ITraeAcceso) { }  
  
@Post('obtenerAcceso')
  async obtenerAcceso(
    @Body() { usuario, pass }: { usuario: string; pass: string },       
    @Res({ passthrough: true }) res: Response,
  ): Promise<Respuesta> {
    
    const id = res.locals.transaccionId;

    try {
      //Logger.log(`Inicio proceso....`, this.constructor.name, id);
      const respuesta = await this.traeAcceso.obtenerAcceso(usuario, pass,id);
      
      res
        .status(200)
        .send(new Respuesta('consulta realizada exitosamente', {respuesta} , [], 200));
      return;

    } catch (error) {
      if (error instanceof ArgumentoInvalido) {
        Logger.error(`error=${error.message}`, this.constructor.name, id);
        res
          .status(400)
          .send(new Respuesta('Error al procesar', {}, [error.message], 400));
        return;
      }

      Logger.error(`error=${error}`, this.constructor.name, id);
      res
        .status(500)
        .send(
          new Respuesta('Error al procesar', {}, [error], 500),
        );
    }
  }
}


