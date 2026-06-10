import { Controller, Get, Post, Param, Body, UseGuards } from '@nestjs/common';
import { IndeedService } from '../../application/indeed.service';
import { JwtAuthGuard } from '../../../shared/infrastructure/guards/jwt-auth.guard';

@Controller('indeed')
@UseGuards(JwtAuthGuard)
export class IndeedController {
  constructor(private readonly service: IndeedService) {}

  /** Frontend llama esto cuando el usuario ingresa el OTP */
  @Post('otp')
  submitOtp(@Body() body: { user_id: string; otp: string }) {
    return this.service.submitOtp(body.user_id, body.otp);
  }

  /** Frontend poll: ¿está esperando OTP? */
  @Get('otp-status/:id')
  getStatus(@Param('id') id: string) {
    return this.service.getOtpStatus(id);
  }
}
