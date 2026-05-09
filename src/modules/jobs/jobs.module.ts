import { Module } from '@nestjs/common';
import { AuthService } from './application/auth.service';
import { ProfileService } from './application/profile.service';
import { PostulaFacilService } from './application/postula-facil.service';
import { PostulacionesService } from './application/postulaciones.service';
import { PlanService } from './application/plan.service';
import { AuthController } from './infrastructure/controllers/auth.controller';
import { ProfileController } from './infrastructure/controllers/profile.controller';
import { PostulaFacilController } from './infrastructure/controllers/postula-facil.controller';
import { PostulacionesController } from './infrastructure/controllers/postulaciones.controller';
import { PlanController } from './infrastructure/controllers/plan.controller';

@Module({
  providers: [AuthService, ProfileService, PostulaFacilService, PostulacionesService, PlanService],
  controllers: [AuthController, ProfileController, PostulaFacilController, PostulacionesController, PlanController],
})
export class JobsModule {}
