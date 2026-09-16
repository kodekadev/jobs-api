import { normalizarOpcion, distribucion } from './feedback.utils';

describe('normalizarOpcion', () => {
  it('acepta las opciones validas de cada pregunta', () => {
    expect(normalizarOpcion('si', 'ofertas_relevantes')).toBe('si');
    expect(normalizarOpcion('a_veces', 'ofertas_relevantes')).toBe('a_veces');
    expect(normalizarOpcion('en_procesos', 'consiguio_trabajo')).toBe('en_procesos');
  });

  it('normaliza mayusculas y espacios', () => {
    expect(normalizarOpcion('  SI  ', 'llamadas')).toBe('si');
    expect(normalizarOpcion('Todavia_No', 'consiguio_trabajo')).toBe('todavia_no');
  });

  it('rechaza valores que no pertenecen a esa pregunta', () => {
    expect(normalizarOpcion('a_veces', 'llamadas')).toBeNull();
    expect(normalizarOpcion('entrevista', 'consiguio_trabajo')).toBeNull();
  });

  it('rechaza null, undefined, vacio y no-strings', () => {
    for (const v of [null, undefined, '', '   ', 0, 5, {}, []]) {
      expect(normalizarOpcion(v, 'ofertas_relevantes')).toBeNull();
    }
  });
});

describe('distribucion', () => {
  it('calcula porcentajes solo sobre respuestas validas', () => {
    // 6 validas (3 si, 2 a_veces, 1 no) + 3 que no contestaron
    const valores = ['si', 'si', 'si', 'a_veces', 'a_veces', 'no', null, undefined, ''];

    expect(distribucion(valores, 'ofertas_relevantes')).toEqual([
      { opcion: 'si', n: 3, pct: 50, base: 6 },
      { opcion: 'a_veces', n: 2, pct: 33.3, base: 6 },
      { opcion: 'no', n: 1, pct: 16.7, base: 6 },
    ]);
  });

  it('los porcentajes suman ~100 cuando hay respuestas', () => {
    const d = distribucion(['si', 'no', 'no', 'a_veces'], 'ofertas_relevantes');
    const suma = d.reduce((a, b) => a + b.pct, 0);
    expect(Math.abs(suma - 100)).toBeLessThan(0.2);
  });

  it('devuelve base 0 y pct 0 cuando nadie contesto', () => {
    expect(distribucion([null, undefined, ''], 'llamadas')).toEqual([
      { opcion: 'si', n: 0, pct: 0, base: 0 },
      { opcion: 'no', n: 0, pct: 0, base: 0 },
    ]);
  });

  it('lista vacia no rompe', () => {
    expect(distribucion([], 'consiguio_trabajo').every((d) => d.base === 0)).toBe(true);
  });

  it('ignora valores de la encuesta antigua', () => {
    // 'entrevista' / 'proceso' / 'nada' eran las opciones del banner viejo
    const d = distribucion(['entrevista', 'proceso', 'nada', 'si'], 'consiguio_trabajo');
    expect(d.find((x) => x.opcion === 'si')).toEqual({ opcion: 'si', n: 1, pct: 100, base: 1 });
  });
});
