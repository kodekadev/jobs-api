from fastapi import FastAPI
from pydantic import BaseModel
from services.whatsapp_service import send_whatsapp

app = FastAPI()


class Lead(BaseModel):
    nombre: str
    telefono: str
    mensaje: str


@app.get("/")
def root():
    return {"mensaje": "Servidor Kodeka funcionando....papa 🚀"}


@app.post("/lead")
def create_lead(lead: Lead):

    # Aquí después guardaremos en DB
    print(f"Nuevo lead recibido: {lead}")

    # Simulación envío WhatsApp
    send_whatsapp(lead.telefono, lead.nombre)

    return {"status": "lead procesado correctamente"}

from fastapi import Body

@app.post("/send-whatsapp")
def send_whatsapp_endpoint(data: dict = Body(...)):

    nombre = data.get("nombre")
    telefono = data.get("telefono")
    asunto = data.get("asunto")

    if not telefono:
        return {"status": "sin telefono"}

    print("======== NUEVO ENVÍO DESDE NEXT ========")
    print(f"Nombre: {nombre}")
    print(f"Teléfono: {telefono}")
    print(f"Asunto: {asunto}")
    
    send_whatsapp(telefono, nombre)

    return {"status": "whatsapp enviado correctamente"}

# if __name__ == "__main__":
#     send_whatsapp("+56928764172", "Bastian")
