import { Controller, Post, Body, HttpCode } from '@nestjs/common';
import { AuthService } from '../../application/auth.service';

@Controller('auth')
export class AuthController {
  constructor(private readonly authService: AuthService) {}

  @Post('login')
  @HttpCode(200)
  login(@Body() body: { email: string; password: string }) {
    return this.authService.login(body.email, body.password);
  }

  @Post('google')
  @HttpCode(200)
  loginGoogle(@Body() body: { email: string; nombre: string }) {
    return this.authService.loginGoogle(body.email, body.nombre);
  }

  @Post('register')
  register(
    @Body()
    body: {
      nombre: string;
      email: string;
      celular: string;
      password: string;
      terminos: boolean;
    },
  ) {
    return this.authService.register(body);
  }

  @Post('forgot-password')
  @HttpCode(200)
  forgotPassword(@Body() body: { email: string }) {
    return this.authService.forgotPassword(body.email);
  }

  @Post('reset-password')
  @HttpCode(200)
  resetPassword(@Body() body: { token: string; password: string }) {
    return this.authService.resetPassword(body.token, body.password);
  }
}
