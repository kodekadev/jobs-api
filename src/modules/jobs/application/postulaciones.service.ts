import { Injectable } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

@Injectable()
export class PostulacionesService {
  constructor(private readonly bq: BigQueryService) {}

  async getByUser(userId: string) {
    const pfRows = await this.bq.query<any>(`
      SELECT CARGOS FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    const cargosUsuario: string[] = pfRows.length ? this.parseJson(pfRows[0].CARGOS) : [];

    const [countRows, rows] = await Promise.all([
      this.bq.query<any>(`
        SELECT COUNT(*) AS total FROM ${this.bq.t('EMPLEOS')}
        WHERE ID_USUARIO = @id
          AND FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT
          CARGO, TITULO_EMPLEO, EMPRESA, DESCRIPCION, LINK, FECHA_POSTULACION
        FROM ${this.bq.t('EMPLEOS')}
        WHERE ID_USUARIO = @id
          AND FECHA_POSTULACION >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        ORDER BY FECHA_POSTULACION DESC
        LIMIT 200
      `, { id: userId }),
    ]);

    const total = Number(countRows[0]?.total ?? 0);
    const grouped: Record<string, any[]> = {};

    for (const r of rows) {
      const cargo = cargosUsuario.find(
        (c) => c.toLowerCase() === (r.CARGO || '').toLowerCase(),
      ) || 'Otros';

      if (!grouped[cargo]) grouped[cargo] = [];

      grouped[cargo].push({
        titulo: r.TITULO_EMPLEO || '',
        empresa: r.EMPRESA || '',
        descripcion: r.DESCRIPCION || '',
        link: r.LINK || '',
        fecha: r.FECHA_POSTULACION,
        estado: 'Enviada',
        tiempo: this.relTime(r.FECHA_POSTULACION),
      });
    }

    return { postulaciones: grouped, total };
  }

  async getNotificaciones(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT id, titulo, empresa, link, portal, leida, fecha
      FROM ${this.bq.t('NOTIFICACIONES')}
      WHERE id_usuario = @id
      ORDER BY fecha DESC
      LIMIT 50
    `, { id: userId });

    const noLeidas = rows.filter((r) => !r.leida).length;
    return {
      notificaciones: rows.map((r) => ({
        id: r.id,
        titulo: r.titulo || '',
        empresa: r.empresa || '',
        link: r.link || '',
        portal: r.portal || '',
        leida: r.leida ?? false,
        fecha: r.fecha,
        tiempo: this.relTime(r.fecha),
      })),
      no_leidas: noLeidas,
    };
  }

  async marcarLeidas(userId: string) {
    await this.bq.query(`
      UPDATE ${this.bq.t('NOTIFICACIONES')}
      SET leida = TRUE
      WHERE id_usuario = @id AND leida = FALSE
    `, { id: userId });
    return { ok: true };
  }

  private relTime(fecha: any): string {
    if (!fecha) return '';
    const d = new Date(fecha.value ?? fecha);
    const diff = Date.now() - d.getTime();
    const mins = Math.floor(diff / 60000);
    if (mins < 1) return 'Ahora';
    if (mins < 60) return `hace ${mins} min`;
    const hrs = Math.floor(mins / 60);
    if (hrs < 24) return `hace ${hrs}h`;
    const days = Math.floor(hrs / 24);
    return `hace ${days} día${days > 1 ? 's' : ''}`;
  }

  private parseJson(val: any): string[] {
    if (!val) return [];
    if (Array.isArray(val)) return val;
    try { return JSON.parse(val); } catch { return []; }
  }
}
