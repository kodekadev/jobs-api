import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import environment from './modules/shared/infrastructure/environment';


async function bootstrap() {
  const app = await NestFactory.create(AppModule);  
  await app.listen(environment.port, () => {
    console.log(`Servicio levantado en el puerto ${environment.port}`);
  });  
}
bootstrap();
