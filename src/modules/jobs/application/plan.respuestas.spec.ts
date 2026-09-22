import { RESPUESTAS_EMPLEO } from './plan.service';

/**
 * El correo ofrece tres botones y el controlador valida contra esta lista.
 * Cuando se agregó "Estoy en procesos" al correo pero no acá, el usuario
 * clickeaba y veía "Link inválido": el backend devolvía 400.
 */
describe('RESPUESTAS_EMPLEO', () => {
  it('acepta las tres opciones que ofrece el correo', () => {
    for (const r of ['si', 'en_procesos', 'no']) {
      expect(RESPUESTAS_EMPLEO).toContain(r as any);
    }
  });

  it('no acepta cualquier cosa', () => {
    for (const r of ['', 'quizas', 'SI', 'tal_vez', 'en procesos']) {
      expect(RESPUESTAS_EMPLEO).not.toContain(r as any);
    }
  });

  it('son exactamente tres', () => {
    expect(RESPUESTAS_EMPLEO.length).toBe(3);
  });
});
