import { Inject, Logger } from '@nestjs/common';
import { ITraeAcceso } from '../domain/interface/itrae.acceso';
import { ArgumentoInvalido } from '../domain/dto/argumento-invalido';
import { ITraeAccesoRepository } from '../domain/interface/itrae.acceso.repository';

export class TraeAcceso implements ITraeAcceso {
  constructor(
   @Inject(ITraeAccesoRepository) private readonly traeAccesoRepository: ITraeAccesoRepository,
   
  ) { }

  async obtenerAcceso(usuarioId:string, passId: string, id: string): Promise<any> {
        
    //Logger.log(`obtenerAcceso | params usuario=${usuarioId}`, this.constructor.name, id);
    if (!usuarioId || !passId) {
      throw new ArgumentoInvalido('El usuario y la contraseña son obligatorios.');
    }  
    const acceso = await this.traeAccesoRepository.traeAcceso(usuarioId,passId, 1);
    return acceso;
  }

}