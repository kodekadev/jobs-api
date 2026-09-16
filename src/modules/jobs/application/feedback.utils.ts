/** Opciones válidas de cada pregunta cerrada de la encuesta. */
export const OPCIONES = {
  ofertas_relevantes: ['si', 'a_veces', 'no'],
  llamadas: ['si', 'no'],
  consiguio_trabajo: ['si', 'en_procesos', 'todavia_no'],
} as const;

export type PreguntaCerrada = keyof typeof OPCIONES;

/**
 * Devuelve el valor solo si pertenece al set de la pregunta; si no, null.
 * Evita guardar basura o valores de una versión anterior de la encuesta.
 */
export function normalizarOpcion(
  valor: unknown,
  pregunta: PreguntaCerrada,
): string | null {
  if (typeof valor !== 'string') return null;
  const limpio = valor.trim().toLowerCase();
  return (OPCIONES[pregunta] as readonly string[]).includes(limpio) ? limpio : null;
}

export type Distribucion = {
  opcion: string;
  n: number;
  pct: number;
  /** Total de respuestas válidas sobre el que se calculó el porcentaje */
  base: number;
};

/**
 * Porcentajes por opción, calculados solo sobre respuestas válidas.
 * Quien no contestó la pregunta no entra en la base, igual que en el promedio
 * de notas: una pregunta sin responder no es un voto por ninguna opción.
 */
export function distribucion(
  valores: unknown[],
  pregunta: PreguntaCerrada,
): Distribucion[] {
  const validos = valores
    .map((v) => normalizarOpcion(v, pregunta))
    .filter((v): v is string => v !== null);

  const base = validos.length;

  return (OPCIONES[pregunta] as readonly string[]).map((opcion) => {
    const n = validos.filter((v) => v === opcion).length;
    return {
      opcion,
      n,
      pct: base === 0 ? 0 : Math.round((n / base) * 1000) / 10,
      base,
    };
  });
}
