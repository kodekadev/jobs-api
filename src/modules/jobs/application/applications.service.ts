import { Injectable, BadRequestException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

@Injectable()
export class ApplicationsService {
  constructor(private readonly bq: BigQueryService) {}

  async saveApplication(body: {
    user_id: string;
    id_empleo?: string;
    titulo_empleo?: string;
    empresa?: string;
    link?: string;
    cargo?: string;
    descripcion?: string;
    portal?: string;
    fecha_postulacion?: string;
  }) {
    if (!body.user_id) throw new BadRequestException('user_id requerido');

    const link = body.link || '';
    const portal = body.portal || 'linkedin';
    const fecha = body.fecha_postulacion
      ? `TIMESTAMP('${body.fecha_postulacion.replace('T', ' ').slice(0, 19)}')`
      : 'CURRENT_TIMESTAMP()';

    // Evitar duplicados por link
    if (link) {
      const dup = await this.bq.query<any>(`
        SELECT 1 FROM ${this.bq.t('EMPLEOS')}
        WHERE ID_USUARIO = @uid AND LINK = @link LIMIT 1
      `, { uid: body.user_id, link });
      if (dup.length) return { ok: true, duplicate: true };
    }

    await this.bq.query(`
      INSERT INTO ${this.bq.t('EMPLEOS')}
        (ID_USUARIO, TITULO_EMPLEO, EMPRESA, LINK, CARGO, DESCRIPCION, PORTAL, FECHA_POSTULACION)
      VALUES
        (@uid, @titulo, @empresa, @link, @cargo, @desc, @portal, ${fecha})
    `, {
      uid:     body.user_id,
      titulo:  body.titulo_empleo  || '',
      empresa: body.empresa        || '',
      link:    link,
      cargo:   body.cargo          || '',
      desc:    body.descripcion    || '',
      portal:  portal,
    });

    return { ok: true, duplicate: false };
  }

  async getAppliedIds(userId: string): Promise<string[]> {
    const rows = await this.bq.query<any>(`
      SELECT LINK FROM ${this.bq.t('EMPLEOS')}
      WHERE ID_USUARIO = @uid AND PORTAL = 'linkedin' AND LINK IS NOT NULL AND LINK != ''
      ORDER BY FECHA_POSTULACION DESC
      LIMIT 1000
    `, { uid: userId });

    return rows.map((r: any) => r.LINK as string);
  }
}
