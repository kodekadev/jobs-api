import { Test } from '@nestjs/testing';
import { PostulacionesService } from './postulaciones.service';
import { BigQueryService } from '../../shared/infrastructure/services/bigquery.service';

const mockBq = { query: jest.fn(), t: (n: string) => `\`jobs.${n}\`` };

describe('PostulacionesService', () => {
  let service: PostulacionesService;

  beforeEach(async () => {
    jest.clearAllMocks();
    const module = await Test.createTestingModule({
      providers: [
        PostulacionesService,
        { provide: BigQueryService, useValue: mockBq },
      ],
    }).compile();
    service = module.get(PostulacionesService);
  });

  describe('getByUser()', () => {
    it('returns empty grouped object when no postulaciones', async () => {
      mockBq.query
        .mockResolvedValueOnce([{ CARGOS: JSON.stringify(['Desarrollador']) }])
        .mockResolvedValueOnce([]);
      const result = await service.getByUser('user-1');
      expect(result).toEqual({ postulaciones: {}, total: 0 });
    });

    it('groups postulaciones by matching cargo', async () => {
      mockBq.query
        .mockResolvedValueOnce([{ CARGOS: JSON.stringify(['Desarrollador', 'Analista']) }])
        .mockResolvedValueOnce([
          { CARGO: 'Desarrollador', EMPRESA: 'Acme', FECHA_POSTULACION: new Date(), ESTADO: 'Enviada', LINK: '' },
          { CARGO: 'Analista', EMPRESA: 'Corp', FECHA_POSTULACION: new Date(), ESTADO: 'Enviada', LINK: '' },
          { CARGO: 'Otro cargo', EMPRESA: 'Inc', FECHA_POSTULACION: new Date(), ESTADO: 'Enviada', LINK: '' },
        ]);
      const result = await service.getByUser('user-1');
      expect(result.total).toBe(3);
      expect(result.postulaciones['Desarrollador']).toHaveLength(1);
      expect(result.postulaciones['Analista']).toHaveLength(1);
      expect(result.postulaciones['Otros']).toHaveLength(1);
    });

    it('puts all postulaciones in Otros when no cargos configured', async () => {
      mockBq.query
        .mockResolvedValueOnce([])
        .mockResolvedValueOnce([
          { CARGO: 'Cualquier cargo', EMPRESA: 'X', FECHA_POSTULACION: new Date(), ESTADO: 'Enviada', LINK: '' },
        ]);
      const result = await service.getByUser('user-1');
      expect(result.postulaciones['Otros']).toHaveLength(1);
    });

    it('is case-insensitive when matching cargo', async () => {
      mockBq.query
        .mockResolvedValueOnce([{ CARGOS: JSON.stringify(['desarrollador web']) }])
        .mockResolvedValueOnce([
          { CARGO: 'Desarrollador Web', EMPRESA: 'Y', FECHA_POSTULACION: new Date(), ESTADO: 'Enviada', LINK: '' },
        ]);
      const result = await service.getByUser('user-1');
      expect(result.postulaciones['desarrollador web']).toHaveLength(1);
    });
  });
});
