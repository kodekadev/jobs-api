export interface RatingSummary {
  /** Promedio con 1 decimal, o null si nadie calificó */
  average: number | null;
  /** Cantidad de notas válidas (1-5) */
  calificaciones: number;
  /** Cantidad total de respuestas, con y sin nota */
  respuestas: number;
}

/**
 * Una nota es válida solo si es un entero entre 1 y 5.
 * Las respuestas sin calificar se guardaron históricamente como 0 (no NULL),
 * así que hay que excluirlas explícitamente además de null/undefined.
 */
export function isValidRating(value: unknown): value is number {
  const n = typeof value === 'string' ? Number(value) : value;
  return typeof n === 'number' && Number.isInteger(n) && n >= 1 && n <= 5;
}

export function summarizeRatings(rawValues: unknown[]): RatingSummary {
  const validas = rawValues.filter(isValidRating);
  const suma = validas.reduce((acc, v) => acc + v, 0);

  return {
    average: validas.length === 0 ? null : Math.round((suma / validas.length) * 10) / 10,
    calificaciones: validas.length,
    respuestas: rawValues.length,
  };
}
