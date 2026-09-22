/**
 * Armado del registro de envíos de "¿Conseguiste trabajo?".
 *
 * Existe porque la primera versión hacía un INSERT por usuario dentro de un
 * Promise.all. BigQuery limita las operaciones DML concurrentes por tabla:
 * el 2026-09-22, de 50 inserts paralelos pasaron 25 y 25 fallaron con
 * rateLimitExceeded. Como el error estaba tragado por un .catch, los correos
 * salieron igual y esos 25 usuarios quedaron sin registro — o sea, en la
 * siguiente corrida les habría llegado repetido.
 *
 * Un solo INSERT con todos los VALUES es una sola operación DML.
 */

export type EnvioEmpleo = { ID_USUARIO: string };

export type InsertEnvios = {
  sql: string;
  params: Record<string, string>;
};

/**
 * Devuelve UNA sentencia INSERT con una tupla por usuario, o null si no hay
 * ninguno. Los ids van parametrizados, nunca interpolados.
 */
export function construirInsertEnvios(
  tabla: string,
  usuarios: EnvioEmpleo[],
): InsertEnvios | null {
  const ids = usuarios
    .map((u) => u?.ID_USUARIO)
    .filter((id): id is string => typeof id === 'string' && id.length > 0);

  if (!ids.length) return null;

  const tuplas = ids
    .map(
      (_, i) =>
        `(GENERATE_UUID(), @uid${i}, NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP(), NULL)`,
    )
    .join(',\n          ');

  const params: Record<string, string> = {};
  ids.forEach((id, i) => {
    params[`uid${i}`] = id;
  });

  return {
    sql: `
        INSERT INTO ${tabla}
          (ID, ID_USUARIO, RESPUESTA, EMPRESA, CARGO, FUE_CON_APLICAI, TESTIMONIAL, FECHA_EMAIL, FECHA_RESPUESTA)
        VALUES
          ${tuplas}
      `,
    params,
  };
}
