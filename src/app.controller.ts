import { Controller, Get } from '@nestjs/common';
import { AppService } from './app.service';
import { Public } from './modules/shared/infrastructure/guards/jwt-auth.guard';

@Controller()
export class AppController {
  constructor(private readonly appService: AppService) {}

  @Public() // health check
  @Get()
  getHello(): string {
    return this.appService.getHello();
  }
}
