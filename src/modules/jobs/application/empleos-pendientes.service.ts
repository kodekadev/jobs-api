import { Injectable, NotFoundException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

@Injectable()
export class EmpleosPendientesService {
  constructor(private readonly bq: BigQueryService) {}

  async getPendientes(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT id, portal, titulo, empresa, url, estado,
             fecha_encontrado, fecha_expira
      FROM ${this.bq.t('EMPLEOS_PENDIENTES')}
      WHERE id_usuario = @uid
        AND estado = 'pendiente'
        AND fecha_expira > CURRENT_TIMESTAMP()
      ORDER BY fecha_encontrado DESC
      LIMIT 200
    `, { uid: userId });

    return rows.map((r: any) => ({
      id:               r.id,
      portal:           r.portal,
      titulo:           r.titulo,
      empresa:          r.empresa,
      url:              r.url,
      estado:           r.estado,
      fecha_encontrado: r.fecha_encontrado?.value ?? r.fecha_encontrado ?? null,
      fecha_expira:     r.fecha_expira?.value ?? r.fecha_expira ?? null,
    }));
  }

  async aprobar(userId: string, jobId: string) {
    await this._assertOwns(userId, jobId);
    await this.bq.query(`
      UPDATE ${this.bq.t('EMPLEOS_PENDIENTES')}
      SET estado = 'aprobado', fecha_accion = CURRENT_TIMESTAMP()
      WHERE id = @id AND id_usuario = @uid
    `, { id: jobId, uid: userId });
    return { ok: true };
  }

  async rechazar(userId: string, jobId: string) {
    await this._assertOwns(userId, jobId);
    await this.bq.query(`
      UPDATE ${this.bq.t('EMPLEOS_PENDIENTES')}
      SET estado = 'rechazado', fecha_accion = CURRENT_TIMESTAMP()
      WHERE id = @id AND id_usuario = @uid
    `, { id: jobId, uid: userId });
    return { ok: true };
  }

  async aprobarTodos(userId: string): Promise<{ aprobados: number }> {
    await this.bq.query(`
      UPDATE ${this.bq.t('EMPLEOS_PENDIENTES')}
      SET estado = 'aprobado', fecha_accion = CURRENT_TIMESTAMP()
      WHERE id_usuario = @uid
        AND estado = 'pendiente'
        AND fecha_expira > CURRENT_TIMESTAMP()
    `, { uid: userId });
    return { aprobados: -1 };
  }

  async getModoRevision(userId: string): Promise<{ activo: boolean }> {
    const rows = await this.bq.query<any>(`
      SELECT COALESCE(modo_revision, FALSE) AS modo_revision
      FROM ${this.bq.t('POSTULACIONES_AUTO')}
      WHERE id_usuario = @uid LIMIT 1
    `, { uid: userId });
    return { activo: rows.length > 0 ? Boolean(rows[0].modo_revision) : false };
  }

  async setModoRevision(userId: string, activo: boolean): Promise<{ activo: boolean }> {
    await this.bq.query(`
      UPDATE ${this.bq.t('POSTULACIONES_AUTO')}
      SET modo_revision = @activo
      WHERE id_usuario = @uid
    `, { uid: userId, activo });
    return { activo };
  }

  private async _assertOwns(userId: string, jobId: string) {
    const rows = await this.bq.query<any>(`
      SELECT id FROM ${this.bq.t('EMPLEOS_PENDIENTES')}
      WHERE id = @id AND id_usuario = @uid LIMIT 1
    `, { id: jobId, uid: userId });
    if (!rows.length) throw new NotFoundException('Empleo no encontrado');
  }
}
