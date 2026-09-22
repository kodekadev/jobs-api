import { PLANES_SIN_AVISO_GENERICO, filtroPlanesSql } from './plan.segmentos';

describe('PLANES_SIN_AVISO_GENERICO', () => {
  // Guard: sacar TRIAL de esta lista reactiva el duplicado con el job
  // Jenkins "trial-conversion" (auto-postulaciones/jenkins_trial_conversion.py).
  // Los días -3 y -1 el usuario recibiría dos correos del mismo tema:
  // uno a las 09:00 desde Cloud Scheduler y otro a las 10:30 desde Jenkins.
  it('excluye TRIAL para no duplicar con trial-conversion', () => {
    expect(PLANES_SIN_AVISO_GENERICO).toContain('TRIAL');
  });

  it('excluye FREE, que no vence', () => {
    expect(PLANES_SIN_AVISO_GENERICO).toContain('FREE');
  });

  it('no excluye ningún plan de pago', () => {
    for (const plan of ['PRO', 'TURBO', 'PREMIUM', 'SPRINT']) {
      expect(PLANES_SIN_AVISO_GENERICO).not.toContain(plan);
    }
  });
});

describe('filtroPlanesSql', () => {
  it('arma un NOT IN con el alias por defecto', () => {
    expect(filtroPlanesSql()).toBe("pc.PLAN NOT IN ('FREE', 'TRIAL')");
  });

  it('respeta el alias que le pasen', () => {
    expect(filtroPlanesSql('p')).toBe("p.PLAN NOT IN ('FREE', 'TRIAL')");
  });

  it('refleja la constante, no una lista escrita a mano', () => {
    for (const plan of PLANES_SIN_AVISO_GENERICO) {
      expect(filtroPlanesSql()).toContain(`'${plan}'`);
    }
  });
});
