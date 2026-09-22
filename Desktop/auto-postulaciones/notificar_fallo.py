"""
Avisa por Telegram que un job de Jenkins fallo.

Se invoca desde el .bat del job cuando el script anterior devolvio error,
para que un fallo quede visible sin depender de que alguien mire Jenkins.
Cada job lo llama por separado, asi que un fallo no afecta a los demas.

Uso:
    python notificar_fallo.py <nombre-del-job> [codigo-de-salida]
"""
import os
import sys

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)


def main() -> int:
    job = sys.argv[1] if len(sys.argv) > 1 else "(desconocido)"
    codigo = sys.argv[2] if len(sys.argv) > 2 else "?"

    texto = (
        f"[AplicAI] FALLO el job '{job}'\n"
        f"Codigo de salida: {codigo}\n"
        f"Revisa el log en Jenkins."
    )
    print(f"  [fallo] {job} (codigo {codigo}) — avisando por Telegram")

    try:
        from telegram_notify import enviar
        enviar(texto)
    except Exception as e:
        # Si ni el aviso funciona, al menos que quede en el log del job
        print(f"  [fallo] no se pudo avisar por Telegram: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
