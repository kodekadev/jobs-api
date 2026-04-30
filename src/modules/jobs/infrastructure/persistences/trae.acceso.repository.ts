import { BigQuery } from '@google-cloud/bigquery';
import * as bcrypt from "bcrypt";
import { Injectable, ServiceUnavailableException } from '@nestjs/common';
import { ITraeAccesoRepository } from '../../domain/interface/itrae.acceso.repository';
import environment from '@/modules/shared/infrastructure/environment';


interface Usuario {
  id?: string;
  nombre?: string;
  email?: string;
  [key: string]: any;
}


@Injectable()
export class TraeAccesoRepository implements ITraeAccesoRepository {
  private bigquery: BigQuery;
  constructor() {

    /*
    const projectId = environment.gcpProjectId;
    const keyFilename = environment.googleApplicationCredentials;

    this.bigquery = new BigQuery({
      projectId: projectId,
      keyFilename: keyFilename,
    });
    */


    const { Storage } = require('@google-cloud/storage');
    const storage = new Storage();

     this.bigquery = new BigQuery();

    console.log("Auth OK");


  }

  async traeAcceso(
    usuario: string,
    pass: any,
  ): Promise<any> {
    try {

      const usuarioId = usuario;
      const query = `SELECT id_usuario, nombre, email, password
                     FROM \`${environment.gcpProjectId}.${environment.bigquery_dataset}.USUARIOS\` 
                     WHERE email = @usuarioId`;

      const [rows] = await this.bigquery.query({
        query: query,
        location: 'US',
        params: {
          usuarioId: usuarioId,
        },
      });


      if (rows.length === 0) {
        return {
          success: false,
          message: "Usuario no existe",
        };

      }

      const { id_usuario, nombre, email, password } = rows[0];
      const passwordCorrecta = await bcrypt.compare(pass, password);

      if (!passwordCorrecta) {
        return {
          success: false,
          message: "Clave no Corresponde",
        }
      }
      else {
        return {
          success: true,
          message: "Acceso concedido",
          NOMBRE: nombre,
          EMAIL: email,
          ID_USUARIO: id_usuario
        }
      };

    } catch (error) {
      console.error('Error al obtener acceso:', error);
      throw new ServiceUnavailableException(`No se pudo filtrar usuarios en GCP BigQuery: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
}