import { isValidRating, summarizeRatings } from './rating.utils';

describe('isValidRating', () => {
  it('acepta enteros del 1 al 5', () => {
    expect([1, 2, 3, 4, 5].every(isValidRating)).toBe(true);
  });

  it('rechaza null, undefined, 0, vacíos y fuera de rango', () => {
    const invalidos = [null, undefined, 0, '', '  ', NaN, -1, 6, 3.5, {}, []];
    expect(invalidos.some(isValidRating)).toBe(false);
  });

  it('acepta notas numéricas que llegan como string desde BigQuery', () => {
    expect(isValidRating('4')).toBe(true);
  });
});

describe('summarizeRatings', () => {
  it('promedia solo notas válidas cuando todas lo son', () => {
    expect(summarizeRatings([5, 5, 4, 2])).toEqual({
      average: 4,
      calificaciones: 4,
      respuestas: 4,
    });
  });

  it('ignora null, undefined, 0 y vacíos al promediar', () => {
    const valores = [5, null, 3, 0, undefined, 1, '', 4];

    expect(summarizeRatings(valores)).toEqual({
      average: 3.3, // (5+3+1+4)/4 = 3.25 → 3.3
      calificaciones: 4,
      respuestas: 8,
    });
  });

  it('devuelve average null cuando ninguna respuesta tiene nota', () => {
    expect(summarizeRatings([0, null, undefined, '', 0])).toEqual({
      average: null,
      calificaciones: 0,
      respuestas: 5,
    });
  });

  it('devuelve average null con lista vacía', () => {
    expect(summarizeRatings([])).toEqual({
      average: null,
      calificaciones: 0,
      respuestas: 0,
    });
  });

  it('reproduce los datos reales del panel: servicio 3.7 y postulaciones 3.2 sobre 9 de 36', () => {
    const sinNota = Array(27).fill(0);

    // Servicio general: 5 notas de 5, 2 de 3 y 2 de 1 → suma 33
    const servicio = [5, 5, 5, 5, 5, 3, 3, 1, 1, ...sinNota];
    expect(summarizeRatings(servicio)).toEqual({
      average: 3.7, // 33/9 = 3.666… → 3.7
      calificaciones: 9,
      respuestas: 36,
    });

    // Calidad de postulaciones: suma 29 sobre 9 notas
    const postulaciones = [5, 5, 4, 4, 3, 3, 2, 2, 1, ...sinNota];
    expect(summarizeRatings(postulaciones)).toEqual({
      average: 3.2, // 29/9 = 3.222… → 3.2
      calificaciones: 9,
      respuestas: 36,
    });
  });

  it('la suma del histograma 1-5 coincide con la cantidad de calificaciones', () => {
    const valores = [5, 5, 5, 5, 5, 3, 3, 1, 1, ...Array(27).fill(0)];
    const { calificaciones } = summarizeRatings(valores);

    const histograma = [1, 2, 3, 4, 5].map(
      (nota) => valores.filter((v) => v === nota).length,
    );
    const totalBarras = histograma.reduce((a, b) => a + b, 0);

    expect(totalBarras).toBe(calificaciones);
    expect(totalBarras).toBe(9);
  });
});
