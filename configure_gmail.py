"""Configura/testa Gmail sem exibir a senha e sem ler ou alterar mensagens.

A senha é solicitada de forma oculta no terminal, fora dos argumentos do processo.
Só é gravada no arquivo local após autenticação e seleção read-only confirmadas.
"""
import getpass
import imaplib
import os
from pathlib import Path
import ssl

from dotenv import dotenv_values, set_key
from runtime_paths import CONFIG_FILE


def main():
    path = CONFIG_FILE
    config = dotenv_values(path)
    user = os.environ.get('GMAIL_EMAIL') or config.get('GMAIL_EMAIL')
    if not user or '@' not in user:
        print('Configure o endereço em GMAIL_EMAIL primeiro.')
        return 1
    password = getpass.getpass('Senha de app do Gmail (entrada oculta): ').replace(' ', '')
    if len(password) != 16 or not password.isascii() or not password.isalpha():
        print('Formato inválido: use a senha de app de 16 letras fornecida pelo Google.')
        return 1
    try:
        with imaplib.IMAP4_SSL('imap.gmail.com', 993,
                              ssl_context=ssl.create_default_context(), timeout=30) as mail:
            mail.login(user, password)
            status, _ = mail.select('INBOX', readonly=True)
            if status != 'OK':
                print('Login aceito, mas não foi possível selecionar INBOX em modo leitura.')
                return 1
    except imaplib.IMAP4.error:
        print('O Gmail recusou a autenticação. A senha não foi salva.')
        return 1
    except (OSError, ssl.SSLError):
        print('Falha de conexão TLS/IMAP. A senha não foi salva.')
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    set_key(path, 'GMAIL_APP_PASSWORD', password)
    print('Gmail autenticado; INBOX acessível em modo somente leitura.')
    print('Senha de app salva no arquivo local. Nenhuma mensagem foi lida ou alterada.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
