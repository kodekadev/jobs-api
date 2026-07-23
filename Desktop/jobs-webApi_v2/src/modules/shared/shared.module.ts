import { Module, Global } from '@nestjs/common';
import { BigQueryService } from './infrastructure/services/bigquery.service';
import { GcsService } from './infrastructure/services/gcs.service';
import { EmailService } from './infrastructure/services/email.service';
import { CloudRunService } from './infrastructure/services/cloud-run.service';
import { TelegramService } from './infrastructure/services/telegram.service';

@Global()
@Module({
  providers: [BigQueryService, GcsService, EmailService, CloudRunService, TelegramService],
  exports: [BigQueryService, GcsService, EmailService, CloudRunService, TelegramService],
})
export class SharedModule {}
