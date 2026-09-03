import { Controller, Post, Get, Query, Body, HttpCode } from '@nestjs/common';
import { AuthService } from '../../application/auth.service';
import { Public } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Controller('auth')
export class AuthController {
  constructor(private readonly authService: AuthService) {}

  @Public()
  @Post('login')
  @HttpCode(200)
  login(@Body() body: { email: string; password: string }) {
    return this.authService.login(body.email, body.password);
  }

  @Public()
  @Post('google')
  @HttpCode(200)
  loginGoogle(@Body() body: { credential?: string; email?: string; nombre?: string }) {
    return this.authService.loginGoogle(body.credential, body.email, body.nombre);
  }

  @Public()
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

  @Public() // el usuario aún no tiene token al verificar su email
  @Post('verify-email')
  @HttpCode(200)
  verifyEmail(@Body() body: { email: string; code: string }) {
    return this.authService.verifyEmail(body.email, body.code);
  }

  @Public()
  @Post('resend-verification')
  @HttpCode(200)
  resendVerification(@Body() body: { email: string }) {
    return this.authService.resendVerification(body.email);
  }

  @Public()
  @Post('forgot-password')
  @HttpCode(200)
  forgotPassword(@Body() body: { email: string }) {
    return this.authService.forgotPassword(body.email);
  }

  @Public() // autenticado por el token de reset de un solo uso
  @Post('reset-password')
  @HttpCode(200)
  resetPassword(@Body() body: { token: string; password: string }) {
    return this.authService.resetPassword(body.token, body.password);
  }

  @Post('change-password')
  @HttpCode(200)
  changePassword(@Body() body: { id: string; currentPassword: string; newPassword: string }) {
    return this.authService.changePassword(body.id, body.currentPassword, body.newPassword);
  }

  @Post('deactivate')
  @HttpCode(200)
  deactivateAccount(@Body() body: { id: string }) {
    return this.authService.deactivateAccount(body.id);
  }

  @Post('delete')
  @HttpCode(200)
  deleteAccount(@Body() body: { id: string; password: string }) {
    return this.authService.deleteAccount(body.id, body.password);
  }

  @Post('set-celular')
  @HttpCode(200)
  setCelular(@Body() body: { id: string; celular: string }) {
    return this.authService.setCelular(body.id, body.celular);
  }

  @Public()
  @Get('unsubscribe')
  @HttpCode(200)
  unsubscribe(@Query('email') email: string, @Query('token') token: string) {
    return this.authService.unsubscribe(email, token);
  }
}
