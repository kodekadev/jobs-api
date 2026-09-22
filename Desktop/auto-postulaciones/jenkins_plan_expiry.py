"""
Jenkins Job: Dispara el cron de plan-expiry en el backend.

Llama a POST /api/cron/plan-expiry en el backend NestJS para enviar
los emails de aviso a usuarios cuyo plan vence en 7 o 1 día.

Reemplaza al Cloud Scheduler de Google si se prefiere correr desde Jenkins local.

Uso:
    python jenkins_plan_expiry.py

Variables de entorno requeridas:
    JOBS_API_URL    URL base del backend (ej: https://jobs-api-994947687832.us-central1.run.app)
    CRON_SECRET     El secret Bearer del backend

Jenkins: cron H 12 * * * (cada día a las ~12 UTC = 9 AM Santiago)
"""

import os
import sys
import json
import urllib.request
import urllib.error

API_URL     = os.environ.get("JOBS_API_URL",  "https://jobs-api-994947687832.us-central1.run.app")
CRON_SECRET = os.environ.get("CRON_SECRET",   "")


def _telegram(msg: str) -> None:
    try:
        from telegram_notify import enviar
        enviar(msg)
    except Exception as e:
        print(f"  [telegram] {e}")


def run_plan_expiry() -> bool:
    if not CRON_SECRET:
        print("ERROR: falta CRON_SECRET en variables de entorno")
        sys.exit(1)

    url  = f"{API_URL.rstrip('/')}/api/cron/plan-expiry"
    data = b""
    req  = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {CRON_SECRET}",
            "Content-Type": "application/json",
            "Content-Length": "0",
        },
    )

    print(f"POST {url}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode()
            result = json.loads(body)
            print(f"  Status: {resp.status}")
            print(f"  Respuesta: {body[:300]}")
            enviados_7  = result.get("enviados_7_dias", "?")
            enviados_1  = result.get("enviados_1_dia",  "?")
            print(f"\nEmails enviados: 7 dias -> {enviados_7} | 1 dia -> {enviados_1}")
            if enviados_7 or enviados_1:
                _telegram(
                    f"[AplicAI] Plan-expiry cron ejecutado\n"
                    f"Avisos 7 días: {enviados_7} | Avisos 1 día: {enviados_1}"
                )
            return True

    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"  HTTP {e.code}: {body[:300]}")
        _telegram(f"[AplicAI] ERROR plan-expiry: HTTP {e.code}\n{body[:200]}")
        return False

    except Exception as e:
        print(f"  Error: {e}")
        _telegram(f"[AplicAI] ERROR plan-expiry: {e}")
        return False


if __name__ == "__main__":
    # Este script solo dispara el endpoint del backend; en dry-run se
    # muestra el POST que se haria y no se llama a nadie.
    if "--dry-run" in sys.argv:
        print("[dry-run] POST a /api/cron/plan-expiry en " + API_URL)
        print("[dry-run] no se llama al backend, no se envia ningun correo")
        sys.exit(0)
    ok = run_plan_expiry()
    sys.exit(0 if ok else 1)
