import {
  Controller, Get, Post, Param, Body, UseInterceptors,
  UploadedFile, HttpCode,
} from '@nestjs/common';
import { FileInterceptor } from '@nestjs/platform-express';
import { ProfileService } from '../../application/profile.service';

@Controller('profile')
export class ProfileController {
  constructor(private readonly profileService: ProfileService) {}

  @Get(':id')
  getProfile(@Param('id') id: string) {
    return this.profileService.getProfile(id);
  }

  @Post('update')
  @HttpCode(200)
  updateProfile(@Body() body: { id: string; profesion: string; experiencia: string }) {
    return this.profileService.updateProfile(body.id, body.profesion, body.experiencia);
  }

  @Post('image')
  @UseInterceptors(FileInterceptor('file'))
  uploadImage(
    @UploadedFile() file: Express.Multer.File,
    @Body('id') id: string,
  ) {
    return this.profileService.uploadImage(id, file.buffer, file.mimetype, file.originalname);
  }

  @Post('cv')
  @UseInterceptors(FileInterceptor('file'))
  uploadCv(
    @UploadedFile() file: Express.Multer.File,
    @Body('id') id: string,
  ) {
    return this.profileService.uploadCv(id, file.buffer, file.mimetype, file.originalname);
  }

  @Post('postulaciones-auto')
  @HttpCode(200)
  toggleAuto(@Body() body: { id: string; activo: number }) {
    return this.profileService.toggleAutoPostulaciones(body.id, body.activo ? 1 : 0);
  }
}
