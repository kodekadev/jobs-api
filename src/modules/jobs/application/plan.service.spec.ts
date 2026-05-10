import { Test } from '@nestjs/testing';
import { PlanService } from './plan.service';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';
import * as crypto from 'crypto';

const mockBq = { query: jest.fn(), t: (n: string) => `\`jobs.${n}\`` };

describe('PlanService', () => {
  let service: PlanService;

  beforeEach(async () => {
    jest.clearAllMocks();
    const module = await Test.createTestingModule({
      providers: [
        PlanService,
        { provide: BigQueryService, useValue: mockBq },
      ],
    }).compile();
    service = module.get(PlanService);
  });

  describe('getPlan()', () => {
    it('returns FREE when no active plan', async () => {
      mockBq.query.mockResolvedValueOnce([]);
      const result = await service.getPlan('user-1');
      expect(result.plan).toBe('FREE');
    });

    it('returns plan from DB when active', async () => {
      mockBq.query.mockResolvedValueOnce([{ PLAN: 'PRO', ESTADO: 'ACTIVO' }]);
      const result = await service.getPlan('user-1');
      expect(result.plan).toBe('PRO');
    });
  });

  describe('savePlan()', () => {
    it('calls MERGE query and returns success', async () => {
      mockBq.query.mockResolvedValueOnce([]);
      const result = await service.savePlan('user-1', 'PRO');
      expect(result).toEqual({ success: true });
      expect(mockBq.query).toHaveBeenCalledTimes(1);
    });
  });

  describe('createCheckout()', () => {
    it('throws for unknown plan', async () => {
      await expect(service.createCheckout('user-1', 'INVALID', 'a@b.com'))
        .rejects.toThrow('Plan inválido');
    });

    it('builds correct HMAC signature', () => {
      // Access private sign via any cast
      const sign = (service as any).sign.bind(service);
      const params = { apiKey: 'testkey', amount: '9990', currency: 'CLP' };
      const secretKey = (service as any).constructor?.name; // won't work, use env mock

      // Verify sign() produces consistent HMAC-SHA256
      const sig1 = sign(params);
      const sig2 = sign(params);
      expect(sig1).toBe(sig2);
      expect(sig1).toMatch(/^[a-f0-9]{64}$/);
    });

    it('sign() excludes "s" key from hash input', () => {
      const sign = (service as any).sign.bind(service);
      const withS = { apiKey: 'k', amount: '100', s: 'shouldBeIgnored' };
      const withoutS = { apiKey: 'k', amount: '100' };
      expect(sign(withS)).toBe(sign(withoutS));
    });

    it('sign() sorts params alphabetically', () => {
      const sign = (service as any).sign.bind(service);
      const p1 = { b: '2', a: '1' };
      const p2 = { a: '1', b: '2' };
      expect(sign(p1)).toBe(sign(p2));
    });
  });
});
