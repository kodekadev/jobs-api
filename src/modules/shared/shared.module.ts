/* eslint-disable prettier/prettier */
import { MiddlewareConsumer, Module, NestModule } from '@nestjs/common';
import { StatusApiController } from './infrastructure/controllers/status-api.controller';
@Module({
  imports: [],
  controllers: [ StatusApiController],
  providers: [

  ],
  exports: [],
})

export class SharedModule implements NestModule {
  configure(consumer: MiddlewareConsumer) {
    //consumer.apply(TokenAuthMiddleware).forRoutes(LogController);
  }
}
