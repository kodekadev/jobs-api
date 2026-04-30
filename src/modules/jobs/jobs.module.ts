import { Module } from '@nestjs/common';
import { JobsController } from './infrastructure/controllers/jobs.controller';
import { ITraeAcceso } from './domain/interface/itrae.acceso';
import { TraeAcceso } from './application/trae.acceso';
import { ITraeAccesoRepository } from './domain/interface/itrae.acceso.repository';
import { TraeAccesoRepository } from './infrastructure/persistences/trae.acceso.repository';

@Module({
  controllers: [JobsController],
  
  providers: [
{
      provide: ITraeAcceso,
      useClass: TraeAcceso,
    },
    {
      provide: ITraeAccesoRepository,
      useClass: TraeAccesoRepository,
    },

  ],
  
})
export class JobsModule {}
