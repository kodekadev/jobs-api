"""
Crea/actualiza los jobs de correo en Jenkins escribiendo sus config.xml.

NOTIFIER ya existe y corre notifier.py a las 9:00, asi que se ACTUALIZA
(se le agrega TZ, las variables de entorno y el aviso de fallo) en vez de
crear un 'resumen-diario' duplicado que enviaria todo dos veces.

Despues de correr esto: Manage Jenkins -> Reload Configuration from Disk.
Para revertir: borrar las carpetas nuevas y restaurar config.xml.bak.
"""
import html
import pathlib
import shutil
import sys

JOBS = pathlib.Path(r"C:\ProgramData\Jenkins\.jenkins\jobs")
PY   = r"C:\Users\bastian\AppData\Local\anaconda3\python.exe"
DIR  = r"C:\Users\bastian\Desktop\auto-postulaciones"
CRED = r"C:\Users\bastian\.secrets\google\credenciales.json"

# nombre_job -> (cron sin TZ, script, comentario)
CONFIG = {
    "NOTIFIER":         ("0 9 * * *",    "notifier.py",                "resumen diario"),
    "plan-expiry":      ("30 9 * * *",   "jenkins_plan_expiry.py",     "vencimiento de plan"),
    "onboarding":       ("0 10 * * *",   "notifier_onboarding.py",     "sin perfil / sin autopilot"),
    "trial-conversion": ("30 10 * * *",  "jenkins_trial_conversion.py","fin de prueba"),
    "retencion":        ("0 11 * * 1,4", "notifier_retention.py",      "sin postulaciones / upsell"),
    "empleo-followup":  ("0 12 * * 1",   "jenkins_empleo_followup.py", "conseguiste trabajo"),
}

PLANTILLA = """<?xml version='1.1' encoding='UTF-8'?>
<project>
  <description>{desc}</description>
  <keepDependencies>false</keepDependencies>
  <properties/>
  <scm class="hudson.scm.NullSCM"/>
  <canRoam>true</canRoam>
  <disabled>false</disabled>
  <blockBuildWhenDownstreamBuilding>false</blockBuildWhenDownstreamBuilding>
  <blockBuildWhenUpstreamBuilding>false</blockBuildWhenUpstreamBuilding>
  <triggers>
    <hudson.triggers.TimerTrigger>
      <spec>{spec}</spec>
    </hudson.triggers.TimerTrigger>
  </triggers>
  <concurrentBuild>false</concurrentBuild>
  <builders>
    <hudson.tasks.BatchFile>
      <command>{command}</command>
      <configuredLocalRules/>
    </hudson.tasks.BatchFile>
  </builders>
  <publishers/>
  <buildWrappers/>
</project>
"""

BAT = [
    "chcp 65001 > nul",
    "set PYTHONIOENCODING=utf-8",
    f'set PYTHON={PY}',
    f'set SCRIPT_DIR={DIR}',
    f'set GOOGLE_APPLICATION_CREDENTIALS={CRED}',
    "",
    '"%PYTHON%" "%SCRIPT_DIR%\\{script}"',
    "if errorlevel 1 (",
    '    "%PYTHON%" "%SCRIPT_DIR%\\notificar_fallo.py" "{job}" %errorlevel%',
    "    exit /b 1",
    ")",
]

dry = "--dry-run" in sys.argv


def _es_admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


if not dry and not _es_admin():
    print("  ERROR: hay que correrlo como administrador.")
    print("  El servicio de Jenkins corre como LocalSystem y solo el grupo")
    print("  Administradores puede escribir en C:\\ProgramData\\Jenkins.")
    print()
    print("  Desde tu PowerShell actual:")
    print("    Start-Process powershell -Verb RunAs -ArgumentList '-NoExit','-Command',"
          "'cd C:\\Users\\bastian\\Desktop\\auto-postulaciones; python crear_jobs_jenkins.py'")
    sys.exit(1)

creados, actualizados = [], []

for job, (cron, script, desc) in CONFIG.items():
    carpeta = JOBS / job
    destino = carpeta / "config.xml"
    existe = destino.exists()

    comando = "\n".join(BAT).format(script=script, job=job)
    # Jenkins guarda los saltos de linea del bat como &#xd;\n
    comando_xml = html.escape(comando, quote=True).replace("\n", "&#xd;\n")
    spec = f"TZ=America/Santiago&#xd;\n{cron}"

    xml = PLANTILLA.format(
        desc=html.escape(f"AplicAI - {desc} ({script})"),
        spec=spec,
        command=comando_xml,
    )

    if dry:
        print(f"  [dry] {'actualizaria' if existe else 'crearia'} {job}  cron={cron}")
        continue

    if existe:
        shutil.copy2(destino, carpeta / "config.xml.bak")
        actualizados.append(job)
    else:
        carpeta.mkdir(parents=True, exist_ok=True)
        creados.append(job)

    destino.write_text(xml, encoding="utf-8")

if not dry:
    print(f"  Creados     : {', '.join(creados) or '(ninguno)'}")
    print(f"  Actualizados: {', '.join(actualizados) or '(ninguno)'} (con .bak)")
    print()
    print("  AHORA: Manage Jenkins -> Reload Configuration from Disk")
