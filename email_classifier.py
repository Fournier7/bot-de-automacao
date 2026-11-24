"""Classifica não lidos recebidos nas últimas 24h e alerta apenas urgência Alta."""
from datetime import datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.header import decode_header, make_header
import hashlib
import imaplib
import re
import ssl

from common import (ai_json, cached, database, mark_sent, one_line, positive_int,
                    required, run_locked, save_cache, send_once, was_sent)
import os
from gaming_guard import GamePaused, check_game

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        'urgencia': {'type': 'string', 'enum': ['Alta', 'Média', 'Baixa']},
        'resumo': {'type': 'string', 'maxLength': 240},
    }, 'required': ['urgencia', 'resumo'],
}


def header_text(value):
    try:
        return str(make_header(decode_header(str(value or ''))))
    except (LookupError, UnicodeError):
        return str(value or '')


def body_text(message):
    """Prefere texto simples, aceita HTML e ignora anexos e imagens remotas."""
    from html.parser import HTMLParser

    class Extractor(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts, self.hidden = [], 0

        def handle_starttag(self, tag, attrs):
            if tag in ('style', 'script'):
                self.hidden += 1
            elif tag in ('p', 'br', 'div', 'li'):
                self.parts.append('\n')

        def handle_endtag(self, tag):
            if tag in ('style', 'script'):
                self.hidden = max(0, self.hidden - 1)

        def handle_data(self, data):
            if not self.hidden:
                self.parts.append(data)

    body = message.get_body(preferencelist=('plain', 'html'))
    if body is None:
        return ''
    content = body.get_content(errors='replace')
    if body.get_content_type() == 'text/html':
        parser = Extractor()
        parser.feed(content)
        return ' '.join(' '.join(parser.parts).split())
    return content


def internal_date(metadata):
    """INTERNALDATE é a chegada ao Gmail, diferente do Date fornecido pelo remetente."""
    match = re.search(rb'INTERNALDATE "([^"]+)"', metadata)
    if not match:
        raise ValueError('INTERNALDATE ausente.')
    months = {m: i for i, m in enumerate(
        ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'], 1)}
    day, month, rest = match.group(1).decode('ascii').strip().split('-', 2)
    year, clock, offset = rest.split()
    hours, minutes, seconds = map(int, clock.split(':'))
    delta = timedelta(hours=int(offset[1:3]), minutes=int(offset[3:5]))
    zone = timezone(delta if offset[0] == '+' else -delta)
    return datetime(int(year), months[month], int(day), hours, minutes, seconds, tzinfo=zone)


def compact_body(text):
    # Links de rastreamento e sequências técnicas longas desperdiçam contexto da IA.
    text = re.sub(r'https?://[^\s<>]{180,}', '[link longo omitido]', text)
    text = re.sub(r'[A-Za-z0-9+/_=-]{100,}', '[sequência técnica omitida]', text)
    return text


def main(logger):
    user = required('GMAIL_EMAIL')
    password = required('GMAIL_APP_PASSWORD').replace(' ', '')
    if len(password) != 16 or not password.isascii() or not password.isalpha():
        raise RuntimeError('Gere uma senha de app Gmail de 16 letras; não use a senha normal.')
    required('TELEGRAM_BOT_TOKEN')
    required('TELEGRAM_CHAT_ID')
    mailbox = os.environ.get('GMAIL_MAILBOX', 'INBOX')
    scope = hashlib.sha256(f'{user.lower()}:{mailbox}'.encode()).hexdigest()[:24]
    prefix = f'email:{scope}:'
    conn = database()
    failed = 0
    try:
        # Read-only + BODY.PEEK[] preservam a flag não lido.
        with imaplib.IMAP4_SSL('imap.gmail.com', 993,
                              ssl_context=ssl.create_default_context(), timeout=45) as mail:
            mail.login(user, password)
            status, _ = mail.select(mailbox, readonly=True)
            if status != 'OK':
                raise RuntimeError('Não foi possível selecionar a pasta IMAP.')
            validity = mail.response('UIDVALIDITY')[1][0].decode('ascii')
            now = datetime.now(timezone.utc)
            cutoff = now - timedelta(hours=24)
            # Gmail X-GM-RAW aceita timestamp; INTERNALDATE confirma a janela exata.
            status, data = mail.uid('search', None, 'UNSEEN', 'X-GM-RAW',
                                    f'"after:{int(cutoff.timestamp())}"')
            if status != 'OK':
                raise RuntimeError('Falha na pesquisa IMAP do Gmail.')
            processed = 0
            for uid in data[0].split():
                check_game()
                key = f'{prefix}{validity}:{uid.decode("ascii")}'
                if cached(conn, key) is not None:
                    continue
                if processed >= positive_int('EMAIL_MAX_PER_RUN', 100):
                    logger.info('Limite de e-mails por execução atingido.')
                    break
                processed += 1
                try:
                    status, rows = mail.uid('fetch', uid, '(INTERNALDATE BODY.PEEK[])')
                    if status != 'OK':
                        raise RuntimeError('Falha ao buscar mensagem IMAP.')
                    packet = next((row for row in rows if isinstance(row, tuple)), None)
                    if packet is None:
                        continue  # Pode ter sido excluída entre SEARCH e FETCH.
                    if not cutoff <= internal_date(packet[0]) <= now:
                        continue
                    message = BytesParser(policy=policy.default).parsebytes(packet[1])
                    subject = header_text(message.get('Subject'))
                    sender = header_text(message.get('From'))
                    result = ai_json(
                        'Classifique a urgência de um e-mail e resuma em uma linha, em português. '
                        'Alta: ação pessoal necessária em até 24h com consequência concreta, '
                        'incidente crítico ou risco imediato. Média: ação sem prazo imediato. '
                        'Baixa: informação, publicidade, newsletter ou mensagem sem ação. '
                        'Palavras como urgente sozinhas não justificam Alta. Não valide '
                        'autenticidade de remetentes. Use recebido_em e agora como referência.',
                        {'assunto': subject[:500], 'remetente': sender[:250],
                         'corpo': compact_body(body_text(message))[:positive_int('EMAIL_BODY_MAX_CHARS', 6000)],
                         'recebido_em': internal_date(packet[0]).isoformat(), 'agora': now.isoformat()},
                        SCHEMA, 'classificacao_email')
                    if not result['resumo'].strip():
                        raise RuntimeError('A IA retornou um resumo vazio.')
                    # Só o alerta resumido é persistido; o corpo não é salvo em disco.
                    save_cache(conn, key, {
                        'urgencia': result['urgencia'],
                        'texto': f'🔴 E-mail de urgência ALTA\nDe: {one_line(sender, 180)}\n'
                                 f'Assunto: {one_line(subject, 250)}\n'
                                 f'Resumo: {one_line(result["resumo"], 500)}'})
                    logger.info('E-mail classificado: %s.', result['urgencia'])
                except GamePaused:
                    raise
                except Exception as error:
                    failed += 1
                    logger.error('E-mail não processado (%s); será tentado novamente.', type(error).__name__)

        # Outbox: alertas já classificados sobrevivem a falhas de rede e à leitura do e-mail.
        rows = conn.execute('SELECT key,value FROM cache WHERE key LIKE ?', (prefix + '%',)).fetchall()
        import json
        for key, value in rows:
            result = json.loads(value)
            if not was_sent(conn, key):
                if result['urgencia'] == 'Alta':
                    send_once(conn, key, result['texto'])
                else:
                    mark_sent(conn, key)  # Média/Baixa nunca geram mensagem Telegram.
        if failed:
            raise RuntimeError(f'{failed} e-mail(s) falharam; detalhes por tipo no log.')
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(run_locked('email_classifier', main))
