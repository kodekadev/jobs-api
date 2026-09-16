/**
 * Fuente única de los límites diarios de postulación en el backend.
 * Debe mantenerse igual a auto-postulaciones/plan_limits.py, que es quien
 * los aplica de verdad al postular; este lado solo los muestra.
 */
export const PLAN_LIMITS: Record<string, number> = {
  FREE: 5,
  TRIAL: 10,
  PRO: 25,
  TURBO: 40,
  SPRINT: 40,
  PREMIUM: 50,
  OWNER: 75,
};

export const PLAN_POR_DEFECTO = 'FREE';

export function getPlanLimit(plan?: string | null): number {
  const clave = (plan || PLAN_POR_DEFECTO).trim().toUpperCase();
  return PLAN_LIMITS[clave] ?? PLAN_LIMITS[PLAN_POR_DEFECTO];
}

export type UsuarioConLimite = {
  plan?: string | null;
  /** Vigencia del plan: si venció, rige el límite de FREE. */
  plan_vigente?: boolean;
  /** Override individual; tiene prioridad sobre el plan. */
  limite_diario?: number | string | null;
};

/**
 * Límite diario del usuario: su override individual si es válido; si no, el
 * de su plan vigente. Un plan vencido usa el límite de FREE.
 */
export function getDailyLimit(user: UsuarioConLimite): number {
  const override = user?.limite_diario;
  if (override !== null && override !== undefined && typeof override !== 'boolean') {
    const n = Number(override);
    if (Number.isInteger(n) && n > 0) return n;
  }

  if (user?.plan_vigente === false) return PLAN_LIMITS[PLAN_POR_DEFECTO];
  return getPlanLimit(user?.plan);
}
