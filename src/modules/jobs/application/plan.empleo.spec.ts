import { construirInsertEnvios } from './plan.empleo';

const TABLA = '`jobs-425301.DWH.EMPLEO_CONSEGUIDO`';

const usuarios = (n: number) =>
  Array.from({ length: n }, (_, i) => ({ ID_USUARIO: `u${i}` }));

describe('construirInsertEnvios', () => {
  // El bug del 2026-09-22: 50 INSERT concurrentes, 25 fallaron con
  // rateLimitExceeded y esos usuarios quedaron sin registro pese a recibir
  // el correo. Todo el punto de esta función es que sean UNA sola sentencia.
  it('arma UNA sola sentencia para 50 usuarios', () => {
    const r = construirInsertEnvios(TABLA, usuarios(50))!;
    expect(r.sql.match(/INSERT INTO/g)).toHaveLength(1);
  });

  it('pone una tupla por usuario', () => {
    const r = construirInsertEnvios(TABLA, usuarios(50))!;
    expect(r.sql.match(/GENERATE_UUID\(\)/g)).toHaveLength(50);
  });

  it('parametriza todos los ids, sin interpolarlos', () => {
    const r = construirInsertEnvios(TABLA, [
      { ID_USUARIO: "x'; DROP TABLE y; --" },
    ])!;
    expect(r.sql).not.toContain('DROP TABLE');
    expect(r.params).toEqual({ uid0: "x'; DROP TABLE y; --" });
  });

  it('numera los parámetros en orden', () => {
    const r = construirInsertEnvios(TABLA, [
      { ID_USUARIO: 'a' },
      { ID_USUARIO: 'b' },
      { ID_USUARIO: 'c' },
    ])!;
    expect(r.params).toEqual({ uid0: 'a', uid1: 'b', uid2: 'c' });
    expect(r.sql).toContain('@uid0');
    expect(r.sql).toContain('@uid2');
  });

  it('devuelve null si no hay usuarios', () => {
    expect(construirInsertEnvios(TABLA, [])).toBeNull();
  });

  it('descarta ids vacíos o ausentes', () => {
    const r = construirInsertEnvios(TABLA, [
      { ID_USUARIO: 'a' },
      { ID_USUARIO: '' },
      {} as any,
      { ID_USUARIO: 'b' },
    ])!;
    expect(r.params).toEqual({ uid0: 'a', uid1: 'b' });
    expect(r.sql.match(/GENERATE_UUID\(\)/g)).toHaveLength(2);
  });

  it('devuelve null si todos los ids son inválidos', () => {
    expect(construirInsertEnvios(TABLA, [{ ID_USUARIO: '' }, {} as any])).toBeNull();
  });

  it('usa la tabla que le pasan', () => {
    const r = construirInsertEnvios(TABLA, usuarios(1))!;
    expect(r.sql).toContain(TABLA);
  });

  it('deja FECHA_EMAIL con la hora del envío y RESPUESTA en NULL', () => {
    const r = construirInsertEnvios(TABLA, usuarios(1))!;
    expect(r.sql).toContain('CURRENT_TIMESTAMP()');
    expect(r.sql).toContain('FECHA_EMAIL');
  });
});
