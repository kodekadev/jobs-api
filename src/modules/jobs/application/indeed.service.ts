import { Injectable, NotFoundException } from '@nestjs/common';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

@Injectable()
export class IndeedService {
  constructor(private readonly bq: BigQueryService) {}

  async submitOtp(userId: string, otp: string) {
    const rows = await this.bq.query<any>(`
      SELECT indeed_otp_pending FROM ${this.bq.t('POSTULACIONES_AUTO')}
      WHERE ID_USUARIO = @uid LIMIT 1
    `, { uid: userId });

    if (!rows.length || !rows[0].indeed_otp_pending) {
      throw new NotFoundException('No hay OTP pendiente para este usuario');
    }

    await this.bq.query(`
      UPDATE ${this.bq.t('POSTULACIONES_AUTO')}
      SET
        indeed_otp_value   = @otp,
        indeed_otp_pending = FALSE,
        indeed_otp_at      = CURRENT_TIMESTAMP()
      WHERE ID_USUARIO = @uid
    `, { uid: userId, otp });

    return { ok: true };
  }

  async getOtpStatus(userId: string) {
    const rows = await this.bq.query<any>(`
      SELECT indeed_otp_pending FROM ${this.bq.t('POSTULACIONES_AUTO')}
      WHERE ID_USUARIO = @uid LIMIT 1
    `, { uid: userId });

    return {
      pending: rows.length ? Boolean(rows[0].indeed_otp_pending) : false,
    };
  }
}
