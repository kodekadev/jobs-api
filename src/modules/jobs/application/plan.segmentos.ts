/**
 * Planes excluidos de los correos genéricos de vencimiento
 * (notifyExpiringPlans y notifyPostExpiry, que dispara Cloud Scheduler).
 *
 * TRIAL está fuera a propósito: toda la secuencia de trial la maneja
 * auto-postulaciones/jenkins_trial_conversion.py (job Jenkins
 * "trial-conversion", 10:30), que segmenta por uso — solo manda la
 * secuencia completa a quien tiene >= 5 postulaciones — y cubre los días
 * -3, -1 y 0 con copy propio.
 *
 * Ese script define "trial" como UPPER(PLAN) = 'TRIAL', así que esta lista
 * es su complemento exacto: ningún usuario queda sin cobertura ni recibe
 * las dos vías. Si TRIAL vuelve acá, los días -3 y -1 llegan dos correos
 * del mismo tema con 90 minutos de diferencia.
 */
export const PLANES_SIN_AVISO_GENERICO = ['FREE', 'TRIAL'] as const;

/**
 * Fragmento SQL para el WHERE de esos crones.
 * @param alias alias de PLAN_CONTRATADO en la consulta.
 */
export function filtroPlanesSql(alias = 'pc'): string {
  const lista = PLANES_SIN_AVISO_GENERICO.map((p) => `'${p}'`).join(', ');
  return `${alias}.PLAN NOT IN (${lista})`;
}
