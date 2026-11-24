"""Utilitário de configuração manual: revela seu chat_id, nunca o token."""
import sys
import requests
from common import required, telegram


def main():
    token = required('TELEGRAM_BOT_TOKEN')
    try:
        response = requests.get(f'https://api.telegram.org/bot{token}/getUpdates',
                                params={'timeout': 0}, timeout=(10, 30))
        if not response.ok:
            raise RuntimeError(f'Telegram HTTP {response.status_code}.')
        data = response.json()
        if not data.get('ok'):
            raise RuntimeError('Telegram rejeitou a consulta.')
        chats = {}
        for update in data['result']:
            message = update.get('message', {})
            chat = message.get('chat', {})
            if chat:
                chats[chat['id']] = chat.get('type', '')
        for chat_id, kind in chats.items():
            print(f'chat_id={chat_id} tipo={kind}')
        if not chats:
            print('Abra seu bot no celular, envie /start e execute novamente.')
        if '--test' in sys.argv:
            telegram('✅ Conexão das automações com o Telegram funcionando.')
            print('Mensagem de teste enviada.')
    except requests.RequestException:
        raise RuntimeError('Falha de conexão com o Telegram.') from None


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(str(error) if type(error) is RuntimeError else type(error).__name__)
        raise SystemExit(1)
