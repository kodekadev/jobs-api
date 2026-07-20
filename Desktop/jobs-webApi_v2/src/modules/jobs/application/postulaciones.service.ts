import { Injectable, BadRequestException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

@Injectable()
export class PostulacionesService {
  constructor(private readonly bq: BigQueryService) {}

  // Guarda una postulación hecha por la extensión (LinkedIn o portales).
  // Mismo esquema que usa el scraper: tabla EMPLEOS.
  async save(body: {
    user_id: string; id_empleo?: string; titulo_empleo?: string; empresa?: string;
    link?: string; cargo?: string; portal?: string; descripcion?: string;
  }) {
    if (!body?.user_id) throw new BadRequestException('user_id requerido');

    // Dedupe: no registrar dos veces el mismo empleo para el mismo usuario
    if (body.id_empleo) {
      const dup = await this.bq.query<any>(`
        SELECT 1 FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id AND id_empleo = @empleo LIMIT 1
      `, { id: body.user_id, empleo: body.id_empleo });
      if (dup.length) return { success: true, duplicated: true };
    }

    const portal = (body.portal || 'linkedin').toLowerCase();
    const desc   = `${body.link || ''}\n\n${(body.descripcion || '').slice(0, 4000)}`.trim();

    await this.bq.query(`
      INSERT INTO ${this.bq.t('EMPLEOS')}
        (id_empleo, id_usuario, titulo_empleo, Fecha_Postulacion, cargo, Empresa, Descripcion, portal)
      VALUES (@empleo, @id, @titulo, @fecha, @cargo, @empresa, @desc, @portal)
    `, {
      empleo:  body.id_empleo || '',
      id:      body.user_id,
      titulo:  body.titulo_empleo || '',
      fecha:   new Date().toISOString(),
      cargo:   body.cargo || '',
      empresa: body.empresa || '',
      desc,
      portal,
    });

    return { success: true };
  }

  // IDs de empleos ya postulados — la extensión los usa para deduplicar
  async getAppliedIds(userId: string): Promise<string[]> {
    const rows = await this.bq.query<any>(`
      SELECT DISTINCT id_empleo FROM ${this.bq.t('EMPLEOS')}
      WHERE id_usuario = @id AND id_empleo IS NOT NULL AND id_empleo != ''
    `, { id: userId });
    return rows.map((r: any) => String(r.id_empleo));
  }

  async getTodayCount(userId: string): Promise<{ linkedin: number; emails: number; portales: number; total: number }> {
    const rows = await this.bq.query<any>(`
      SELECT
        COUNTIF(
          portal = 'linkedin'
          OR (portal IS NULL AND (STARTS_WITH(Descripcion, '[linkedin]') OR STARTS_WITH(Descripcion, '[extension]')))
        ) AS linkedin,
        COUNTIF(
          portal = 'email_directo'
          OR (portal IS NULL AND STARTS_WITH(Descripcion, '[email_directo]'))
        ) AS emails,
        COUNTIF(portal IN ('trabajando', 'chiletrabajos', 'indeed')
          OR (portal IS NULL AND (
            STARTS_WITH(Descripcion, '[trabajando]') OR
            STARTS_WITH(Descripcion, '[chiletrabajos]')
          ))
        ) AS portales
      FROM ${this.bq.t('EMPLEOS')}
      WHERE id_usuario = @id
        AND DATE(Fecha_Postulacion) = CURRENT_DATE()
    `, { id: userId });
    const linkedin = Number(rows[0]?.linkedin ?? 0);
    const emails   = Number(rows[0]?.emails   ?? 0);
    const portales = Number(rows[0]?.portales  ?? 0);
    return { linkedin, emails, portales, total: linkedin + emails + portales };
  }

  async getByUser(userId: string) {
    const pfRows = await this.bq.query<any>(`
      SELECT CARGOS FROM ${this.bq.t('POSTULA_FACIL')}
      WHERE ID_USUARIO = @id LIMIT 1
    `, { id: userId });

    const cargosUsuario: string[] = pfRows.length ? this.parseJson(pfRows[0].CARGOS) : [];

    const [countRows, rows] = await Promise.all([
      this.bq.query<any>(`
        SELECT COUNT(*) as total, MIN(Fecha_Postulacion) as primera
        FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id
      `, { id: userId }),
      this.bq.query<any>(`
        SELECT
          cargo, Empresa, Fecha_Postulacion, titulo_empleo, Descripcion, link,
          COALESCE(portal,
            CASE
              WHEN STARTS_WITH(Descripcion, '[linkedin]') OR STARTS_WITH(Descripcion, '[extension]') THEN 'linkedin'
              WHEN STARTS_WITH(Descripcion, '[email_directo]') THEN 'email_directo'
              ELSE 'otro'
            END
          ) AS portal
        FROM ${this.bq.t('EMPLEOS')}
        WHERE id_usuario = @id
        ORDER BY Fecha_Postulacion DESC
        LIMIT 5000
      `, { id: userId }),
    ]);

    const total   = Number(countRows[0]?.total ?? rows.length);
    const primera = countRows[0]?.primera?.value ?? countRows[0]?.primera ?? null;

    const grouped: Record<string, any[]> = {};

    for (const r of rows) {
      const rCargo = (r.cargo || r.titulo_empleo || '').toLowerCase();
      const cargo = cargosUsuario.find((c) => {
        const cLow = c.toLowerCase();
        return cLow === rCargo || rCargo.includes(cLow) || cLow.includes(rCargo);
      }) || 'Otros';

      if (!grouped[cargo]) grouped[cargo] = [];

      grouped[cargo].push({
        empresa:     r.Empresa || '',
        fecha:       r.Fecha_Postulacion,
        estado:      'Enviada',
        link:        r.link || '',
        titulo:      r.titulo_empleo || '',
        tiempo:      this.relTime(r.Fecha_Postulacion),
        descripcion: r.Descripcion || '',
        portal:      r.portal || 'otro',
      });
    }

    return { postulaciones: grouped, total, primera_postulacion: primera };
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
