from services.whatsapp_service import send_whatsapp

def process_lead(lead_data: dict):
    """
    Recibe un diccionario con los datos del lead y envía WhatsApp.
    Espera que lead_data tenga:
        - 'nombre': str
        - 'telefono': str (formato +569XXXXXXXX)
    """
    nombre = lead_data.get("nombre", "Cliente")
    telefono = lead_data.get("telefono")

    if not telefono:
        print("❌ No se recibió número de teléfono")
        return {"status": "error", "message": "No hay teléfono"}

    # Llamamos al servicio de WhatsApp
    send_whatsapp(telefono, nombre)
    return {"status": "success", "message": f"Mensaje enviado a {nombre}"}
