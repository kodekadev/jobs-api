import { Module, Global } from '@nestjs/common';
import { BigQueryService } from './infrastructure/services/bigquery.service';
import { GcsService } from './infrastructure/services/gcs.service';
import { EmailService } from './infrastructure/services/email.service';

@Global()
@Module({
  providers: [BigQueryService, GcsService, EmailService],
  exports: [BigQueryService, GcsService, EmailService],
})
export class SharedModule {}
