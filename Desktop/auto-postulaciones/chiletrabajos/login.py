"""
ChileTrabajos — Login y gestión de sesión Selenium.

Centraliza la lógica de driver y login que estaba duplicada en
completar_perfil.py y postular.py.

Uso:
    from chiletrabajos.login import get_session
    driver = get_session(user_id="jobs2", email="...", password="...")
    # ... usar driver ...
    driver.quit()
"""
import os
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

_ROOT = os.path.dirname(os.path.dirname(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import bq

BASE_URL  = "https://www.chiletrabajos.cl"
LOGIN_URL = f"{BASE_URL}/chtlogin"
PORTAL_ID = "chiletrabajos"


def make_driver() -> webdriver.Chrome:
    options = Options()
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--window-size=1366,768")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    options.add_experimental_option("useAutomationExtension", False)
    driver = webdriver.Chrome(options=options)
    driver.execute_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
    return driver


def _do_login(driver: webdriver.Chrome, email: str, password: str) -> bool:
    wait = WebDriverWait(driver, 15)
    try:
        driver.get(LOGIN_URL)
        time.sleep(2)
        wait.until(EC.presence_of_element_located((By.ID, "username"))).send_keys(email)
        driver.find_element(By.ID, "password").send_keys(password)
        wait.until(EC.element_to_be_clickable(
            (By.XPATH, "//input[@value='Iniciar Sesión'] | //button[@type='submit']")
        )).click()
        time.sleep(4)
        ok = "chtlogin" not in driver.current_url
        print(f"  [cht-login] Login {'OK' if ok else 'FALLÓ'}: {driver.current_url[:60]}")
        return ok
    except Exception as e:
        print(f"  [cht-login] Error: {e}")
        return False


def get_session(user_id: str, email: str, password: str) -> "webdriver.Chrome | None":
    """
    Retorna un driver Selenium autenticado en ChileTrabajos.

    Args:
        user_id  : ID del usuario (solo para logs)
        email    : email de la cuenta en chiletrabajos.cl
        password : contraseña de la cuenta

    Returns:
        driver autenticado, o None si el login falló.
    """
    driver = make_driver()
    if _do_login(driver, email, password):
        return driver
    print(f"  [cht-login] No se pudo iniciar sesión para {user_id}")
    try:
        driver.quit()
    except Exception:
        pass
    return None


if __name__ == "__main__":
    import argparse

    os.environ.setdefault(
        "GOOGLE_APPLICATION_CREDENTIALS",
        r"C:\Users\bastian\Desktop\Script_Python\jobs-425301-ba25295bbbd0.json",
    )
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_ROOT, ".env"))

    parser = argparse.ArgumentParser(description="Login ChileTrabajos")
    parser.add_argument("--user", help="ID de usuario específico")
    args = parser.parse_args()

    users = bq.get_active_users()
    if args.user:
        users = [u for u in users if u["ID_USUARIO"].lower() == args.user.lower()]

    for u in users:
        uid = u["ID_USUARIO"]
        cuenta = bq.get_portal_account(uid, PORTAL_ID)
        if not cuenta:
            print(f"[{uid}] Sin cuenta en {PORTAL_ID}")
            continue
        driver = get_session(uid, cuenta["email"], cuenta["password"])
        if driver:
            print(f"[{uid}] Login OK — {driver.current_url[:60]}")
            driver.quit()
        else:
            print(f"[{uid}] Login FALLÓ")
