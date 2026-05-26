import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
whatsapp_number = os.getenv("TWILIO_WHATSAPP_NUMBER")

client = Client(account_sid, auth_token)

def send_whatsapp(numero: str, nombre: str):

    message = client.messages.create(
        from_=whatsapp_number,
        to=f"whatsapp:{numero}",
        body=f"""Hola {nombre} 👋

Recibimos tu solicitud en Kodeka 🚀

En breve te contactaremos."""
    )

    print("Mensaje enviado:", message.sid)
