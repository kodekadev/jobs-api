import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import env from './modules/shared/infrastructure/environment';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  app.enableCors({
    origin: [env.frontendUrl, 'http://localhost:3000', /\.run\.app$/, /\.vercel\.app$/],
    methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    credentials: true,
  });

  app.setGlobalPrefix('api');

  await app.listen(env.port, () => {
    console.log(`Servicio levantado en el puerto ${env.port}`);
  });
}
bootstrap();
