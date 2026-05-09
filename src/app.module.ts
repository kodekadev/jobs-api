import { Module } from '@nestjs/common';
import { JobsModule } from './modules/jobs/jobs.module';
import { SharedModule } from './modules/shared/shared.module';

@Module({
  imports: [SharedModule, JobsModule],
})
export class AppModule {}
