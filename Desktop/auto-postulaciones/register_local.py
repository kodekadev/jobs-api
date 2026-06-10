"""
Ejecutor local de registro en portales — primer plano, browser visible.
Uso:
    python register_local.py <user_id>
"""
import os
import sys

_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    for _line in open(_env_path):
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _v = _line.split("=", 1)
            os.environ.setdefault(_k.strip(), _v.strip())

# Forzar entorno local (browser visible, sin Xvfb)
os.environ.pop("K_SERVICE", None)
os.environ.pop("CLOUD_RUN_JOB", None)
os.environ["SELENIUM_HEADLESS"] = "0"

if len(sys.argv) < 2:
    print("Uso: python register_local.py <user_id>")
    sys.exit(1)

os.environ["SINGLE_USER_ID"] = sys.argv[1]
print(f"Usuario: {sys.argv[1]}\n")

import register
register.main()
