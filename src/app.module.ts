import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { JobsModule } from './modules/jobs/jobs.module';
import { SharedModule } from './modules/shared/shared.module';

@Module({
  imports: [
    JobsModule,
    SharedModule,
  ],  
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
