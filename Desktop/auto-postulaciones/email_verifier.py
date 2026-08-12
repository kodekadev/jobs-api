"""
Verifica emails de confirmación de portales de empleo via IMAP.
Todos los *@tektia.cl llegan a bastian.alfaro@gmail.com via Cloudflare Email Routing.
"""
import imaplib
import email
import re
import time
import unicodedata
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

IMAP_HOST = os.getenv("IMAP_HOST", "imap.gmail.com")
IMAP_PORT = int(os.getenv("IMAP_PORT", 993))
IMAP_USER = os.getenv("IMAP_USER", "")
IMAP_PASS = os.getenv("IMAP_PASS", "")
EMAIL_DOMAIN = os.getenv("EMAIL_DOMAIN", "tektia.cl")


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
    sender_filter: si se especifica (ej. "laborum"), solo procesa emails cuyo
                   From contenga esa cadena — evita activar emails de otros portales.
    Retorna el link de confirmación o el código numérico encontrado.
    Retorna None si no llega nada en timeout segundos.
    """
    print(f"  [imap] Esperando verificación para {email_usuario} (timeout={timeout}s)...")
    try:
        mail = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT)
        mail.login(IMAP_USER, IMAP_PASS)
    except Exception as e:
        print(f"  [imap] Error conectando: {e}")
        return None

    deadline = time.time() + timeout
    visto = set()

    while time.time() < deadline:
        try:
            mail.select("INBOX")
            # Buscar emails de hoy (Cloudflare reescribe el To: al reenviar)
            from datetime import datetime
            _MESES = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"]
            _now = datetime.now()
            hoy = f"{_now.day:02d}-{_MESES[_now.month-1]}-{_now.year}"
            _, nums = mail.search(None, f'UNSEEN SINCE "{hoy}"')
            # Siempre del más reciente al más antiguo — llega antes al email fresco
            num_list = list(reversed(nums[0].split()))
            for num in num_list:
                if num in visto:
                    continue
                # PEEK para no marcar como leído antes de confirmar que es nuestro
                _, hdr_data = mail.fetch(num, "(BODY.PEEK[HEADER.FIELDS (DATE FROM TO X-FORWARDED-TO)])")
                hdr_text = hdr_data[0][1].decode(errors="ignore")
                hdr_lower = hdr_text.lower()
                if email_usuario.lower() not in hdr_lower:
                    visto.add(num)  # no es nuestro, skip para siempre
                    continue
                if sender_filter and sender_filter.lower() not in hdr_lower:
                    visto.add(num)  # no es del portal esperado, ignorar
                    continue

                # Verificar frescura del email (descartar códigos y links vencidos)
                if freshness_minutes:
                    from email.utils import parsedate_to_datetime
                    date_line = next(
                        (l for l in hdr_text.splitlines() if l.lower().startswith("date:")), ""
                    )
                    if date_line:
                        try:
                            msg_dt = parsedate_to_datetime(date_line[5:].strip())
                            import datetime as _dt
                            age_secs = (
                                _dt.datetime.now(_dt.timezone.utc) - msg_dt.astimezone(_dt.timezone.utc)
                            ).total_seconds()
                            if age_secs > freshness_minutes * 60:
                                print(f"  [imap] Email muy antiguo ({int(age_secs/60)}m) — ignorando")
                                visto.add(num)
                                continue
                        except Exception:
                            pass  # si no se puede parsear, procesar igual

                # Es nuestro — descargar body completo y marcar como leído
                visto.add(num)
                _, data = mail.fetch(num, "(RFC822)")
                mail.store(num, "+FLAGS", "\\Seen")  # marcar explícitamente como leído
                msg = email.message_from_bytes(data[0][1])
                body = _extraer_body(msg)

                # Decodificar subject correctamente (puede ser objeto Header)
                try:
                    from email.header import decode_header, make_header
                    subject = str(make_header(decode_header(msg.get('Subject', '') or '')))
                except Exception:
                    subject = str(msg.get('Subject', '(sin asunto)'))
                print(f"  [imap] Email encontrado: {subject}")

                # Modo code_only: buscar código de exactamente 6 dígitos (OTP)
                # Solo en emails que parezcan ser de verificación/acceso
                if code_only:
                    _OTP_SUBJ = (
                        "tu código de acceso es", "código de acceso", "código temporal",
                        "your access code", "access code", "verification code",
                        "código", "code", "otp", "verificac",
                    )
                    es_otp_email = any(s in subject.lower() for s in _OTP_SUBJ)
                    if not es_otp_email:
                        continue  # es newsletter u otro — saltar

                    # Buscar en asunto: patrón "Tu código de acceso es: 835722"
                    m = re.search(r'(?:código de acceso es|access code is|código es)[:\s]+(\d{6})', subject, re.I)
                    codes_subj = [m.group(1)] if m else re.findall(r'\b(\d{6})\b', subject)
                    if codes_subj:
                        print(f"  [imap] Código (asunto): {codes_subj[0]}")
                        mail.logout()
                        return codes_subj[0]
                    # Buscar en body (exactamente 6 dígitos)
                    codes_body = re.findall(r'\b(\d{6})\b', body)
                    if codes_body:
                        print(f"  [imap] Código (body): {codes_body[0]}")
                        mail.logout()
                        return codes_body[0]
                    continue  # email de verificación pero sin código de 6 dígitos aún

                _img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico")
                _excluidos = (
                    "facebook.com", "twitter.com", "instagram.com", "linkedin.com",
                    "google.com", "unsubscribe", "privacy", "terms", "help",
                    "apple.com", "microsoft.com",
                )

                def _es_img(url: str) -> bool:
                    return any(url.lower().split("?")[0].endswith(ext) for ext in _img_exts)

                def _excluir_url(url: str) -> bool:
                    return _es_img(url) or any(ex in url.lower() for ex in _excluidos)

                # Buscar link de verificación/activación por keywords
                links = re.findall(
                    r"https?://[^\s\"'<>\]\)]+(?:confirm|verify|activ|validar|activate|token|click)[^\s\"'<>\]\)]*",
                    body, re.I
                )
                links = [l for l in links if not _excluir_url(l)]
                if links:
                    print(f"  [imap] Link encontrado: {links[0][:80]}...")
                    mail.logout()
                    return links[0]

                # Fallback: cualquier link a dominios conocidos de portales
                _dominios_portales = (
                    "laborum.cl", "computrabajo.com", "trabajando.cl",
                    "chiletrabajos.cl", "bumeran.com", "multitrabajos.com",
                )
                for dominio in _dominios_portales:
                    # Buscar solo en href (no img src — evita tracking pixels)
                    fallback = re.findall(
                        rf'href=["\']?(https?://[^\s"\'<>\]\)]*{re.escape(dominio)}[^\s"\'<>\]\)]*)',
                        body, re.I
                    )
                    fallback = [l for l in fallback if not _excluir_url(l)]
                    if fallback:
                        mejor = max(fallback, key=len)
                        print(f"  [imap] Link portal encontrado: {mejor[:80]}...")
                        mail.logout()
                        return mejor

                # Fallback final: el link HTTPS más largo (los tokens de activación son largos)
                all_links = re.findall(r"https?://[^\s\"'<>\]\)]{40,}", body, re.I)
                candidatos = [l for l in all_links if not _excluir_url(l)]
                if candidatos:
                    mejor = max(candidatos, key=len)
                    print(f"  [imap] Link por longitud encontrado: {mejor[:80]}...")
                    mail.logout()
                    return mejor

                # Buscar código numérico de 4-8 dígitos
                codes = re.findall(r'\b(\d{4,8})\b', body)
                if codes:
                    print(f"  [imap] Código encontrado: {codes[0]}")
                    mail.logout()
                    return codes[0]

        except Exception as e:
            print(f"  [imap] Error leyendo inbox: {e}")

        time.sleep(6)

    mail.logout()
    print(f"  [imap] Timeout — no llegó verificación para {email_usuario}")
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
