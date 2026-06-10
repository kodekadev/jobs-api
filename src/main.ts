import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import env from './modules/shared/infrastructure/environment';

async function bootstrap() {
  const app = await NestFactory.create(AppModule);

  const allowedOrigins: (string | RegExp)[] = [
    env.frontendUrl,
    'http://localhost:3000',
    /\.run\.app$/,
    /\.vercel\.app$/,
    /^chrome-extension:\/\//,
  ];

  app.enableCors({
    origin: (origin, callback) => {
      if (!origin) return callback(null, true);
      const ok = allowedOrigins.some(o =>
        typeof o === 'string' ? o === origin : o.test(origin),
      );
      callback(null, ok);
    },
    methods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS', 'PATCH'],
    credentials: true,
  });

  app.setGlobalPrefix('api');

  await app.listen(env.port, () => {
    console.log(`Servicio levantado en el puerto ${env.port}`);
  });
}
bootstrap();
