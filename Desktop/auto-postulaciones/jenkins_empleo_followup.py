"""
Dispara la pregunta "¿Conseguiste trabajo?" a los 30 días del registro.

Igual que jenkins_plan_expiry.py, este script NO hace el trabajo: solo llama
al endpoint del backend, que arma y envía los correos. Por eso podría moverse
a Cloud Scheduler y dejar de depender de que el PC esté encendido.

El backend limita cuántos envía por corrida (LIMITE) porque hay ~315
destinatarios acumulados del primer disparo.

Uso:
    python jenkins_empleo_followup.py
    python jenkins_empleo_followup.py --dry-run
    python jenkins_empleo_followup.py --limite 100
"""
import json
import os
import sys
import urllib.request

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_DIR, ".env"), override=True)
except Exception:
    pass

API_URL     = os.environ.get("API_URL", "https://jobs-api-994947687832.us-central1.run.app")
CRON_SECRET = os.environ.get("CRON_SECRET", "")
LIMITE_DEFAULT = 50


def _limite_de_argv() -> int:
    if "--limite" in sys.argv:
        i = sys.argv.index("--limite")
        if i + 1 < len(sys.argv):
            try:
                return max(1, int(sys.argv[i + 1]))
            except ValueError:
                pass
    return LIMITE_DEFAULT


def run(limite: int) -> bool:
    if not CRON_SECRET:
        print("ERROR: falta CRON_SECRET en variables de entorno")
        return False

    url = f"{API_URL.rstrip('/')}/api/cron/empleo-followup?limite={limite}"
    req = urllib.request.Request(
        url, data=b"", method="POST",
        headers={
            "Authorization": f"Bearer {CRON_SECRET}",
            "Content-Type": "application/json",
            "Content-Length": "0",
        },
    )

    print(f"POST {url}")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode())
            enviados   = result.get("enviados", 0)
            pendientes = result.get("pendientes", 0)
            print(f"  Status: {resp.status}")
            print(f"  Enviados: {enviados}  |  Pendientes para las proximas corridas: {pendientes}")

            try:
                from telegram_notify import enviar
                enviar(
                    f"[AplicAI] ¿Conseguiste trabajo?\n"
                    f"Enviados: {enviados}\n"
                    f"Pendientes: {pendientes}"
                )
            except Exception:
                pass
            return True
    except Exception as e:
        print(f"  ERROR: {e}")
        return False


if __name__ == "__main__":
    limite = _limite_de_argv()
    if "--dry-run" in sys.argv:
        print(f"[dry-run] POST a /api/cron/empleo-followup?limite={limite} en {API_URL}")
        print("[dry-run] no se llama al backend, no se envia ningun correo")
        sys.exit(0)
    sys.exit(0 if run(limite) else 1)
