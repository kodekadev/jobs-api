import { Module } from '@nestjs/common';
import { APP_GUARD } from '@nestjs/core';
import { JobsModule } from './modules/jobs/jobs.module';
import { SharedModule } from './modules/shared/shared.module';
import { AppController } from './app.controller';
import { AppService } from './app.service';
import { JwtAuthGuard } from './modules/shared/infrastructure/guards/jwt-auth.guard';

@Module({
  imports: [SharedModule, JobsModule],
  controllers: [AppController],
  providers: [
    AppService,
    // Auth global: TODAS las rutas exigen Bearer JWT salvo las marcadas @Public()
    { provide: APP_GUARD, useClass: JwtAuthGuard },
  ],
})
export class AppModule {}
