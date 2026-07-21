import requests

BOT_TOKEN = '6204104817:AAH546uCoFs6MZLKtYODBiIhIb1pB1TmpOI'
CHAT_ID   = '-922418247'

def enviar(texto: str) -> None:
    try:
        url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
        r = requests.post(url, json={'chat_id': CHAT_ID, 'text': str(texto)}, timeout=10)
        data = r.json()
        if not data.get('ok'):
            print(f'  ! Telegram error: {data.get("description", data)}')
        else:
            print('  OK Telegram notificado')
    except Exception as e:
        print(f'  ! Telegram: {e}')
