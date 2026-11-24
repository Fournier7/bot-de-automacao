"""Configura o bot por entrada oculta; descobre um único chat privado.

Não escolhe entre múltiplos chats. --test envia só ao destino confirmado.
--existing-token reutiliza o token já salvo, sem exibi-lo.
"""
import getpass
import os
from pathlib import Path
import re
import sys

from dotenv import dotenv_values, set_key
import requests
from runtime_paths import CONFIG_FILE


def api(token, method, payload=None):
    try:
        response = requests.post(f'https://api.telegram.org/bot{token}/{method}',
                                 json=payload or {}, timeout=(10, 30))
    except requests.RequestException:
        raise RuntimeError('Falha de conexão com o Telegram.') from None
    if not response.ok:
        raise RuntimeError(f'Telegram HTTP {response.status_code}; confira o token e acesso ao bot.')
    try:
        result = response.json()
    except ValueError:
        raise RuntimeError('O Telegram retornou uma resposta inválida.') from None
    if not result.get('ok'):
        raise RuntimeError('O Telegram recusou a operação.')
    return result['result']


def main():
    path = CONFIG_FILE
    config = dotenv_values(path)
    if '--existing-token' in sys.argv:
        token = (config.get('TELEGRAM_BOT_TOKEN') or '').strip()
    else:
        token = getpass.getpass('Token do bot (entrada oculta): ').strip()
    if not re.fullmatch(r'\d+:[A-Za-z0-9_-]+', token):
        raise RuntimeError('Formato de token inválido.')
    bot = api(token, 'getMe')
    if not bot.get('is_bot'):
        raise RuntimeError('A credencial não corresponde a um bot.')
    path.parent.mkdir(parents=True, exist_ok=True)
    set_key(path, 'TELEGRAM_BOT_TOKEN', token)
    print('Bot validado: @' + bot['username'])
    print('Token salvo no arquivo local de credenciais.')
    updates = api(token, 'getUpdates', {'timeout': 0, 'allowed_updates': ['message']})
    chats = {}
    for update in updates:
        chat = update.get('message', {}).get('chat', {})
        if chat.get('type') == 'private':
            chats[str(chat['id'])] = chat
    if not chats:
        print('Aguardando /start no bot: https://t.me/' + bot['username'])
        return 2
    configured = config.get('TELEGRAM_CHAT_ID')
    if configured in chats:
        chat_id = configured
    elif len(chats) == 1:
        chat_id = next(iter(chats))
    else:
        print('Mais de um chat privado encontrado; confirme o destino antes de prosseguir.')
        print('chat_ids disponíveis: ' + ', '.join(chats))
        return 2
    set_key(path, 'TELEGRAM_CHAT_ID', chat_id)
    print('Chat privado identificado e salvo.')
    if '--test' in sys.argv:
        api(token, 'sendMessage', {'chat_id': chat_id,
            'text': '✅ Conexão configurada! Suas automações usarão IA local, sem API paga. '
                    'O bloqueio durante jogos está configurado.'})
        print('Mensagem de teste entregue ao Telegram.')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(str(error))
        raise SystemExit(1)
