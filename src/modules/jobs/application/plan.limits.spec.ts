import { getPlanLimit, getDailyLimit, PLAN_LIMITS } from './plan.limits';

describe('getPlanLimit', () => {
  it('devuelve el limite de cada plan conocido', () => {
    expect(getPlanLimit('FREE')).toBe(5);
    expect(getPlanLimit('TRIAL')).toBe(10);
    expect(getPlanLimit('PRO')).toBe(25);
    expect(getPlanLimit('TURBO')).toBe(40);
    expect(getPlanLimit('SPRINT')).toBe(40);
    expect(getPlanLimit('PREMIUM')).toBe(50);
    expect(getPlanLimit('OWNER')).toBe(75);
  });

  it('normaliza mayusculas y espacios', () => {
    expect(getPlanLimit('  premium ')).toBe(50);
  });

  it('un plan desconocido, vacio o nulo cae a FREE', () => {
    for (const p of ['NO_EXISTE', '', null, undefined]) {
      expect(getPlanLimit(p as any)).toBe(5);
    }
  });
});

describe('getDailyLimit', () => {
  it('usa el limite del plan vigente', () => {
    expect(getDailyLimit({ plan: 'PRO', plan_vigente: true })).toBe(25);
  });

  it('OWNER y SPRINT no caen a FREE', () => {
    // Antes faltaban en la tabla del backend y se mostraban como 5.
    expect(getDailyLimit({ plan: 'OWNER', plan_vigente: true })).toBe(75);
    expect(getDailyLimit({ plan: 'SPRINT', plan_vigente: true })).toBe(40);
  });

  it('un plan vencido usa el limite de FREE', () => {
    expect(getDailyLimit({ plan: 'PREMIUM', plan_vigente: false })).toBe(5);
  });

  it('el override individual gana sobre el plan', () => {
    expect(getDailyLimit({ plan: 'FREE', plan_vigente: true, limite_diario: 30 })).toBe(30);
    expect(getDailyLimit({ plan: 'PRO', plan_vigente: true, limite_diario: '12' })).toBe(12);
  });

  it('el override gana incluso con el plan vencido', () => {
    expect(getDailyLimit({ plan: 'PRO', plan_vigente: false, limite_diario: 7 })).toBe(7);
  });

  it('un override invalido cae al limite del plan', () => {
    for (const malo of [null, undefined, 0, -3, '', 'abc', true, 2.5]) {
      expect(getDailyLimit({ plan: 'PRO', plan_vigente: true, limite_diario: malo as any })).toBe(25);
    }
  });

  it('un usuario sin datos cae a FREE', () => {
    expect(getDailyLimit({})).toBe(5);
  });
});

describe('coherencia con plan_limits.py', () => {
  it('los valores no cambiaron', () => {
    expect(PLAN_LIMITS).toEqual({
      FREE: 5, TRIAL: 10, PRO: 25, TURBO: 40,
      SPRINT: 40, PREMIUM: 50, OWNER: 75,
    });
  });
});
