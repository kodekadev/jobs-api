import { Module } from '@nestjs/common';
import { AuthService } from './application/auth.service';
import { ProfileService } from './application/profile.service';
import { PostulaFacilService } from './application/postula-facil.service';
import { PostulacionesService } from './application/postulaciones.service';
import { PlanService } from './application/plan.service';
import { AiService } from './application/ai.service';
import { AdminService } from './application/admin.service';
import { AuthController } from './infrastructure/controllers/auth.controller';
import { ProfileController } from './infrastructure/controllers/profile.controller';
import { PostulaFacilController } from './infrastructure/controllers/postula-facil.controller';
import { PostulacionesController } from './infrastructure/controllers/postulaciones.controller';
import { PlanController } from './infrastructure/controllers/plan.controller';
import { CronController } from './infrastructure/controllers/cron.controller';
import { AiController } from './infrastructure/controllers/ai.controller';
import { AdminController } from './infrastructure/controllers/admin.controller';

@Module({
  providers: [AuthService, ProfileService, PostulaFacilService, PostulacionesService, PlanService, AiService, AdminService],
  controllers: [AuthController, ProfileController, PostulaFacilController, PostulacionesController, PlanController, CronController, AiController, AdminController],
})
export class JobsModule {}
