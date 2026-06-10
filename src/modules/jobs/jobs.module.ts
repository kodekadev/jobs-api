import { Module } from '@nestjs/common';
import { AuthService } from './application/auth.service';
import { ProfileService } from './application/profile.service';
import { PostulaFacilService } from './application/postula-facil.service';
import { PostulacionesService } from './application/postulaciones.service';
import { PlanService } from './application/plan.service';
import { ApplicationsService } from './application/applications.service';
import { IndeedService } from './application/indeed.service';
import { AiService } from './application/ai.service';
import { AuthController } from './infrastructure/controllers/auth.controller';
import { ProfileController } from './infrastructure/controllers/profile.controller';
import { PostulaFacilController } from './infrastructure/controllers/postula-facil.controller';
import { PostulacionesController } from './infrastructure/controllers/postulaciones.controller';
import { PlanController } from './infrastructure/controllers/plan.controller';
import { ApplicationsController } from './infrastructure/controllers/applications.controller';
import { IndeedController } from './infrastructure/controllers/indeed.controller';
import { AiController } from './infrastructure/controllers/ai.controller';

@Module({
  providers: [AuthService, ProfileService, PostulaFacilService, PostulacionesService, PlanService, ApplicationsService, IndeedService, AiService],
  controllers: [AuthController, ProfileController, PostulaFacilController, PostulacionesController, PlanController, ApplicationsController, IndeedController, AiController],
})
export class JobsModule {}
