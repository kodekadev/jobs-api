import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module';
import env from './modules/shared/infrastructure/environment';

async function bootstrap() {
  // Fail-fast: en Cloud Run (K_SERVICE definido) el secret JWT es obligatorio.
  // Sin esto, los tokens se firmarían con el default público del repo y
  // cualquiera podría forjar sesiones.
  if (process.env.K_SERVICE && !process.env.JWT_SECRET) {
    throw new Error(
      'JWT_SECRET no está configurado. Agrégalo a las variables de entorno del servicio en Cloud Run.',
    );
  }

  const app = await NestFactory.create(AppModule, { rawBody: true });

  app.enableCors({
    origin: [
      env.frontendUrl,
      'http://localhost:3000',
      // Solo aplicai.cl y sus subdominios (el regex anterior aceptaba
      // cualquier dominio terminado en "aplicai.cl", p.ej. evil-aplicai.cl)
      /^https:\/\/([a-z0-9-]+\.)?aplicai\.cl$/,
      // Deploys propios — no cualquier *.vercel.app / *.run.app
      /^https:\/\/jobs-web[a-z0-9-]*\.vercel\.app$/,
      /^https:\/\/jobs-web-994947687832\.us-central1\.run\.app$/,
    ],
    methods: ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'],
    credentials: true,
  });

  app.setGlobalPrefix('api');

  await app.listen(env.port, () => {
    console.log(`Servicio levantado en el puerto ${env.port}`);
  });
}
bootstrap();
