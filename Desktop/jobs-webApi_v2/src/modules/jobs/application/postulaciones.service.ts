import { Injectable } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

@Injectable()
export class PostulacionesService {
  constructor(private readonly bq: BigQueryService) {}

  async getTodayCount(userId: string): Promise<number> {
    const rows = await this.bq.query<any>(`
      SELECT COUNT(*) AS total
      FROM ${this.bq.t('EMPLEOS')}
      WHERE id_usuario = @id
        AND DATE(Fecha_Postulacion) = CURRENT_DATE()
    `, { id: userId });
    return Number(rows[0]?.total ?? 0);
  }

  async getByUser(userId: string) {
    const pfRows = await this.bq.query<any>(`
      SELECT CARGOS FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    const cargosUsuario: string[] = pfRows.length ? this.parseJson(pfRows[0].CARGOS) : [];

    const [countRows, rows] = await Promise.all([
      this.bq.query<any>(`
        SELECT COUNT(*) as total FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT
          cargo, Empresa, Fecha_Postulacion, titulo_empleo
        FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id
        ORDER BY Fecha_Postulacion DESC
        LIMIT 5000
      `, { id: userId }),
    ]);

    const total = Number(countRows[0]?.total ?? rows.length);

    const grouped: Record<string, any[]> = {};

    for (const r of rows) {
      const cargo = cargosUsuario.find(
        (c) => c.toLowerCase() === (r.CARGO || '').toLowerCase(),
      ) || 'Otros';

      if (!grouped[cargo]) grouped[cargo] = [];

      grouped[cargo].push({
        empresa: r.Empresa || '',
        fecha: r.Fecha_Postulacion,
        estado: 'Enviada',
        link: '',
        titulo: r.titulo_empleo || '',
        tiempo: this.relTime(r.Fecha_Postulacion),
      });
    }

    return { postulaciones: grouped, total };
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
