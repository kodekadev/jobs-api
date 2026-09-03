"""
Verifica emails de confirmación de portales de empleo via Gmail API.
Todos los *@tektia.cl llegan a nexonempresa7@gmail.com via Cloudflare Email Routing.
"""
import imaplib
import email
import re
import time
import base64
import unicodedata
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")
EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN", "tektia.cl")

_GMAIL_CREDS = os.environ.get("GMAIL_CREDS_PATH") or r"C:\Users\bastian\.secrets\google\gmail_oauth_credentials.json"
_GMAIL_TOKEN = os.environ.get("GMAIL_TOKEN_PATH") or r"C:\Users\bastian\.secrets\google\gmail_token.json"
_GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _get_gmail_service():
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if os.path.exists(_GMAIL_TOKEN):
        creds = Credentials.from_authorized_user_file(_GMAIL_TOKEN, _GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(_GMAIL_CREDS, _GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(_GMAIL_TOKEN, "w") as f:
            f.write(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def _gmail_body(msg: dict) -> str:
    def _decode(data: str) -> str:
        try:
            return base64.urlsafe_b64decode(data + "==").decode(errors="ignore")
        except Exception:
            return ""

    def _extract(part: dict) -> str:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data", "")
        sub = part.get("parts", [])
        if sub:
            return "\n".join(_extract(p) for p in sub)
        if mime in ("text/html", "text/plain") and data:
            return _decode(data)
        return ""

    return _extract(msg.get("payload", {}))


def generar_email(nombre: str, portal: str = "") -> str:
    """
    Genera un email para el usuario en tektia.cl.
    "Juan González" → "juan.gonzalez@tektia.cl"
    Con sufijo numérico para evitar colisiones: "juan.gonzalez2@tektia.cl"
    El parámetro portal ya no se incluye en la dirección.
    """
    nfkd = unicodedata.normalize("NFKD", nombre.lower())
    n = nfkd.encode("ascii", "ignore").decode()
    n = re.sub(r"[^a-z0-9]+", ".", n).strip(".")
    return f"{n}@{EMAIL_DOMAIN}"


def _extraer_body(msg) -> str:
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            try:
                if ct == "text/plain" and not plain:
                    plain = part.get_payload(decode=True).decode(errors="ignore")
                elif ct == "text/html" and not html:
                    html = part.get_payload(decode=True).decode(errors="ignore")
            except Exception:
                pass
        return html + "\n" + plain
    try:
        body = msg.get_payload(decode=True).decode(errors="ignore")
        return body
    except Exception:
        return ""


def esperar_verificacion(
    email_usuario: str,
    timeout: int = 90,
    code_only: bool = False,
    freshness_minutes: int = 5,
    sender_filter: str = "",
) -> str | None:
    """
    Espera un email de verificación para email_usuario (ej. juan@tektia.cl).
    Usa Gmail API (polling cada 2s) — más rápido y confiable que IMAP.
    sender_filter: filtra por remitente (ej. "computrabajo").
    """
    import datetime as _dt

    print(f"  [gmail] Esperando verificación para {email_usuario} (timeout={timeout}s)...")
    try:
        service = _get_gmail_service()
    except Exception as e:
        print(f"  [gmail] Error autenticando: {e}")
        return None

    # Filtrar por destinatario directamente — el header To se preserva con la dirección
    # original @tektia.cl aunque Cloudflare reenvíe a Gmail
    query = f"newer_than:2h {email_usuario}"
    print(f"  [gmail] Query: {query}")

    _img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")
    _excluidos = (
        "facebook.com", "twitter.com", "instagram.com", "linkedin.com",
        "google.com", "unsubscribe", "privacy", "terms", "help",
        "apple.com", "microsoft.com",
    )
    _dominios_portales = (
        "laborum.cl", "computrabajo.com", "trabajando.cl",
        "chiletrabajos.cl", "bumeran.com", "multitrabajos.com",
        "getonbrd.com", "empleaxchile.cl",
    )

    def _es_img(url):
        return any(url.lower().split("?")[0].endswith(ext) for ext in _img_exts)

    def _excluir_url(url):
        return _es_img(url) or any(ex in url.lower() for ex in _excluidos)

    deadline = time.time() + timeout
    visto = set()

    while time.time() < deadline:
        try:
            result = service.users().messages().list(
                userId="me", q=query, maxResults=20
            ).execute()

            for msg_ref in result.get("messages", []):
                msg_id = msg_ref["id"]
                if msg_id in visto:
                    continue

                # Headers primero — evita descargar body innecesariamente
                meta = service.users().messages().get(
                    userId="me", id=msg_id, format="metadata",
                    metadataHeaders=["From", "To", "Delivered-To", "X-Forwarded-To", "Subject"]
                ).execute()

                headers = {
                    h["name"].lower(): h["value"]
                    for h in meta.get("payload", {}).get("headers", [])
                }
                recipient_blob = " ".join([
                    headers.get("to", ""),
                    headers.get("delivered-to", ""),
                    headers.get("x-forwarded-to", ""),
                ]).lower()

                if email_usuario.lower() not in recipient_blob:
                    visto.add(msg_id)
                    continue

                # Verificar frescura por internalDate (ms epoch)
                if freshness_minutes:
                    age_secs = (
                        _dt.datetime.now(_dt.timezone.utc)
                        - _dt.datetime.fromtimestamp(
                            int(meta.get("internalDate", 0)) / 1000,
                            tz=_dt.timezone.utc,
                        )
                    ).total_seconds()
                    if age_secs > freshness_minutes * 60:
                        visto.add(msg_id)
                        service.users().messages().modify(
                            userId="me", id=msg_id,
                            body={"removeLabelIds": ["UNREAD"]},
                        ).execute()
                        continue

                subject = headers.get("subject", "(sin asunto)")
                print(f"  [gmail] Email encontrado: {subject}")

                # Body completo
                full = service.users().messages().get(
                    userId="me", id=msg_id, format="full"
                ).execute()
                body = _gmail_body(full)

                visto.add(msg_id)
                service.users().messages().modify(
                    userId="me", id=msg_id,
                    body={"removeLabelIds": ["UNREAD"]},
                ).execute()

                if code_only:
                    _OTP_SUBJ = (
                        "tu código de acceso es", "código de acceso", "código temporal",
                        "your access code", "access code", "verification code",
                        "código", "code", "otp", "verificac",
                    )
                    if not any(s in subject.lower() for s in _OTP_SUBJ):
                        continue
                    m = re.search(r'(?:código de acceso es|access code is|código es)[:\s]+(\d{6})', subject, re.I)
                    codes_subj = [m.group(1)] if m else re.findall(r'\b(\d{6})\b', subject)
                    if codes_subj:
                        print(f"  [gmail] Código (asunto): {codes_subj[0]}")
                        return codes_subj[0]
                    codes_body = re.findall(r'\b(\d{6})\b', body)
                    if codes_body:
                        print(f"  [gmail] Código (body): {codes_body[0]}")
                        return codes_body[0]
                    continue

                links = re.findall(
                    r"https?://[^\s\"'<>\]\)]+(?:confirm|verify|activ|validar|activate|token|click)[^\s\"'<>\]\)]*",
                    body, re.I,
                )
                links = [l for l in links if not _excluir_url(l)]
                if links:
                    print(f"  [gmail] Link encontrado: {links[0][:80]}...")
                    return links[0]

                for dominio in _dominios_portales:
                    fallback = re.findall(
                        rf'href=["\']?(https?://[^\s"\'<>\]\)]*{re.escape(dominio)}[^\s"\'<>\]\)]*)',
                        body, re.I,
                    )
                    fallback = [l for l in fallback if not _excluir_url(l)]
                    if fallback:
                        mejor = max(fallback, key=len)
                        print(f"  [gmail] Link portal encontrado: {mejor[:80]}...")
                        return mejor

                all_links = re.findall(r"https?://[^\s\"'<>\]\)]{40,}", body, re.I)
                candidatos = [l for l in all_links if not _excluir_url(l)]
                if candidatos:
                    mejor = max(candidatos, key=len)
                    print(f"  [gmail] Link por longitud: {mejor[:80]}...")
                    return mejor

                codes = re.findall(r'\b(\d{4,8})\b', body)
                if codes:
                    print(f"  [gmail] Código encontrado: {codes[0]}")
                    return codes[0]

        except Exception as e:
            print(f"  [gmail] Error: {e}")

        time.sleep(2)

    print(f"  [gmail] Timeout — no llegó verificación para {email_usuario}")
    return None


def buscar_emails_hiringroom(dias: int = 3) -> list[dict]:
    """
    Lee el inbox y devuelve los emails NO leídos de Hiringroom que contienen
    un link de preguntas (questionSender). Retorna lista de:
      {email_to, url, subject, num}
    donde email_to es el @tektia.cl al que fue enviado (= el candidato).
    """
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASS)
    except Exception as e:
        print(f"  [imap-hr] Error conectando: {e}")
        return []

    resultados = []
    try:
        mail.select("INBOX")
        from datetime import datetime, timedelta
        _MESES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
        _since = datetime.now() - timedelta(days=dias)
        since_str = f"{_since.day:02d}-{_MESES[_since.month-1]}-{_since.year}"
        _, nums = mail.search(None, f'UNSEEN SINCE "{since_str}"')
        num_list = nums[0].split()

        for num in num_list:
            try:
                _, hdr_data = mail.fetch(num, "(BODY.PEEK[HEADER.FIELDS (FROM TO X-FORWARDED-TO SUBJECT)])")
                hdr_text = hdr_data[0][1].decode(errors="ignore")

                # Filtrar por remitente Hiringroom
                from_line = next((l for l in hdr_text.splitlines() if l.lower().startswith("from:")), "")
                if "hiringroom" not in from_line.lower():
                    continue

                # Extraer subject
                subj_line = next((l for l in hdr_text.splitlines() if l.lower().startswith("subject:")), "")
                subject = subj_line[8:].strip()

                # Solo emails con preguntas de screening
                if "preguntas" not in subject.lower() and "questionsender" not in subject.lower():
                    continue

                # Destinatario (@tektia.cl)
                to_line   = next((l for l in hdr_text.splitlines() if l.lower().startswith("to:")), "")
                fwd_line  = next((l for l in hdr_text.splitlines() if "x-forwarded-to" in l.lower()), "")
                email_to  = ""
                for ln in (to_line, fwd_line):
                    m = re.search(rf'([\w.\-]+@{re.escape(EMAIL_DOMAIN)})', ln, re.I)
                    if m:
                        email_to = m.group(1).lower()
                        break

                if not email_to:
                    continue

                # Descargar body para extraer URL
                _, data = mail.fetch(num, "(RFC822)")
                msg  = email.message_from_bytes(data[0][1])
                body = _extraer_body(msg)

                # Extraer URL de Hiringroom (questionSender o delivery redirect)
                url = ""
                for pattern in [
                    r'https?://link\.hiringroom\.com/t/questionSender/[^\s"<>\]\)]+',
                    r'https?://delivery\.hiringroom\.com/[^\s"<>\]\)]+',
                ]:
                    found = re.findall(pattern, body, re.I)
                    if found:
                        url = found[0].rstrip(".")
                        break

                if not url:
                    continue

                # Marcar como leído (lo procesaremos)
                mail.store(num, "+FLAGS", "\\Seen")

                print(f"  [imap-hr] {email_to} | {subject[:60]}")
                resultados.append({
                    "email_to": email_to,
                    "url":      url,
                    "subject":  subject,
                    "num":      num,
                })

            except Exception as e:
                print(f"  [imap-hr] Error en email {num}: {e}")
                continue

    except Exception as e:
        print(f"  [imap-hr] Error buscando: {e}")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    return resultados


if __name__ == "__main__":
    # Test: envía un email a cualquier @tektia.cl y verifica que llega
    import sys
    test_email = sys.argv[1] if len(sys.argv) > 1 else f"test.prueba@{EMAIL_DOMAIN}"
    print(f"Esperando email para {test_email}...")
    resultado = esperar_verificacion(test_email, timeout=20)
    print(f"Resultado: {resultado}")
