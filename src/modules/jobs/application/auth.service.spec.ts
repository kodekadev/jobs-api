import { Test } from '@nestjs/testing';
import { BadRequestException, UnauthorizedException } from '@nestjs/common';
import { AuthService } from './auth.service';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import { EmailService } from '../../shared/infrastructure/services/email.service';

const mockBq = { query: jest.fn(), t: (n: string) => `\`jobs.${n}\`` };
const mockEmail = { send: jest.fn(), resetPasswordHtml: jest.fn(() => '') };

describe('AuthService', () => {
  let service: AuthService;

  beforeEach(async () => {
    jest.clearAllMocks();
    const module = await Test.createTestingModule({
      providers: [
        AuthService,
        { provide: BigQueryService, useValue: mockBq },
        { provide: EmailService, useValue: mockEmail },
      ],
    }).compile();
    service = module.get(AuthService);
  });

  // ─── register validations ──────────────────────────────────────────────────
  describe('register()', () => {
    const valid = {
      nombre: 'Test User', email: 'test@example.com',
      celular: '+56912345678', password: 'Pass123', terminos: true,
    };

    it('throws on invalid email', async () => {
      await expect(service.register({ ...valid, email: 'not-an-email' }))
        .rejects.toBeInstanceOf(BadRequestException);
    });

    it('throws on invalid celular', async () => {
      await expect(service.register({ ...valid, celular: '912345678' }))
        .rejects.toBeInstanceOf(BadRequestException);
    });

    it('throws on short password', async () => {
      await expect(service.register({ ...valid, password: 'abc' }))
        .rejects.toBeInstanceOf(BadRequestException);
    });

    it('throws when terminos is false', async () => {
      await expect(service.register({ ...valid, terminos: false }))
        .rejects.toBeInstanceOf(BadRequestException);
    });

    it('throws on duplicate email/celular', async () => {
      mockBq.query.mockResolvedValueOnce([{ ID_USUARIO: '1' }]);
      await expect(service.register(valid)).rejects.toBeInstanceOf(BadRequestException);
    });

    it('succeeds and returns { success: true }', async () => {
      mockBq.query
        .mockResolvedValueOnce([])    // no duplicate
        .mockResolvedValueOnce([]);   // insert
      const res = await service.register(valid);
      expect(res).toEqual({ success: true });
    });
  });

  // ─── login ─────────────────────────────────────────────────────────────────
  describe('login()', () => {
    it('throws when user not found', async () => {
      mockBq.query.mockResolvedValueOnce([]);
      await expect(service.login('a@b.com', '123')).rejects.toBeInstanceOf(UnauthorizedException);
    });

    it('throws when account has no password (Google account)', async () => {
      mockBq.query.mockResolvedValueOnce([{ ID_USUARIO: '1', PASSWORD: null, PLAN: 'FREE' }]);
      await expect(service.login('a@b.com', '123')).rejects.toBeInstanceOf(UnauthorizedException);
    });
  });

  // ─── forgotPassword ────────────────────────────────────────────────────────
  describe('forgotPassword()', () => {
    it('returns success even when email not found', async () => {
      mockBq.query.mockResolvedValueOnce([]);
      const res = await service.forgotPassword('notfound@test.com');
      expect(res).toEqual({ success: true });
    });
  });

  // ─── resetPassword validations ─────────────────────────────────────────────
  describe('resetPassword()', () => {
    it('throws on invalid password format', async () => {
      await expect(service.resetPassword('sometoken', 'short'))
        .rejects.toBeInstanceOf(BadRequestException);
    });

    it('throws when token not found', async () => {
      mockBq.query.mockResolvedValueOnce([]);
      await expect(service.resetPassword('badtoken', 'Valid123'))
        .rejects.toBeInstanceOf(BadRequestException);
    });
  });
});
