"""Testes sem credenciais, sem custos de API e sem envios reais.

O teste Playwright usa uma página simulada do Indeed, não o site de produção.
"""
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

# Isola estado antes de importar common; não lê credenciais pessoais.
_temporary = tempfile.TemporaryDirectory()
os.environ['LOCALAPPDATA'] = _temporary.name
os.environ['AUTOMACOES_DATA_DIR'] = str(Path(_temporary.name) / 'IAAutomacoes')
os.environ['AUTOMACOES_ENV_FILE'] = str(Path(_temporary.name) / 'no-secrets.env')
import common
import email_classifier as emails
import job_finder as jobs
import gaming_guard
from job_preferences import location_policy, ALLOWED_CITIES


def tearDownModule():
    logging.shutdown()
    _temporary.cleanup()


def fresh_db():
    conn = sqlite3.connect(':memory:')
    conn.execute('CREATE TABLE cache (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    conn.execute('CREATE TABLE sent (key TEXT PRIMARY KEY)')
    return conn


class Tests(unittest.TestCase):
    def test_rio_capital_and_every_baixada_city_are_accepted(self):
        for city in ALLOWED_CITIES:
            with self.subTest(city=city):
                self.assertIsNotNone(location_policy({'localidade': city + ' - RJ', 'modalidade': 'Presencial'}))
        self.assertIsNotNone(location_policy({'localidade': 'Nova Iguaçu - RJ', 'modalidade': 'Híbrido'}))
        self.assertIsNotNone(location_policy({'localidade': 'Baixada Fluminense', 'modalidade': 'Presencial'}))

    def test_other_cities_and_unspecified_state_are_excluded(self):
        for city in ('São Paulo - SP', 'Niterói - RJ', 'São Gonçalo - RJ', 'Petrópolis - RJ',
                     'Volta Redonda - RJ', 'RJ', 'Estado do Rio de Janeiro', 'Niterói, Rio de Janeiro'):
            with self.subTest(city=city):
                self.assertIsNone(location_policy({'localidade': city, 'modalidade': 'Híbrido'}))

    def test_remote_anywhere_requires_explicit_non_hybrid_mode(self):
        for mode in ('100% Home Office', 'Remoto', 'Remote'):
            self.assertIsNotNone(location_policy({'localidade': 'São Paulo - SP', 'modalidade': mode}))
        for mode in ('Híbrido', 'Home office parcial', 'Possibilidade de trabalho remoto', 'Presencial'):
            self.assertIsNone(location_policy({'localidade': 'São Paulo - SP', 'modalidade': mode}))
        self.assertIsNone(location_policy({'localidade': 'São Paulo - SP', 'modalidade': 'Remoto',
                                          'texto_sujo': 'Encontros presenciais a cada mês.'}))

    def test_no_location_cannot_be_invented_from_portal_or_description(self):
        self.assertIsNone(location_policy({'fonte': 'RioVagas', 'texto_sujo': 'Portal do Rio de Janeiro'}))
        self.assertIsNone(location_policy({'texto_sujo': 'Benefício: possibilidade de home office.'}))
        self.assertIsNotNone(location_policy({'titulo': 'Desenvolvedor Junior — Belford Roxo'}))
        self.assertIsNotNone(location_policy({'texto_sujo': 'Cidade: Duque de Caxias\nForma de trabalho: Presencial'}))

    def test_nonmatching_location_never_reaches_ai(self):
        with patch.object(jobs, 'ai_json') as ai:
            result = jobs.structure([{'id': 1, 'titulo': 'Desenvolvedor Junior',
                                     'localidade': 'São Paulo - SP', 'modalidade': 'Híbrido'}])
        self.assertEqual(result, [])
        ai.assert_not_called()

    def test_only_junior_it_titles_are_accepted(self):
        accepted = ['Desenvolvedor Júnior', 'Analista de Dados Jr', 'Analista de Sistemas Junior',
                    'Analista de Suporte de TI Júnior', 'Técnico de Informática Jr',
                    'Analista QA Júnior', 'Analista de Redes Júnior',
                    'Analista de Segurança da Informação Júnior', 'DevOps Junior',
                    'Engenheiro de Software Júnior', 'Cientista de Dados Junior']
        rejected = ['Analista Financeiro Junior - Empresa de TI', 'Controladoria Junior',
                    'Assistente de Marketing Junior', 'Analista de Redes Sociais Junior',
                    'Suporte Junior - Eletrodomésticos', 'Programador de Produção Junior',
                    'Desenvolvedor de Negócios Junior', 'Analista de Sistemas Pleno',
                    'Desenvolvedor Sênior', 'Estágio Desenvolvedor Junior', 'Desenvolvedor Junior/Trainee']
        for title in accepted:
            with self.subTest(title=title):
                self.assertTrue(jobs.target_title(title))
        for title in rejected:
            with self.subTest(title=title):
                self.assertFalse(jobs.target_title(title))

    def setUp(self):
        # Testes funcionam também se o usuário estiver jogando; não iniciam IA real.
        for module in (common, emails, jobs):
            patcher = patch.object(module, 'check_game')
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_game_blocks_automation_before_work(self):
        action = MagicMock()
        with patch.object(gaming_guard, 'game_running', return_value=True):
            self.assertEqual(common.run_locked('game_test', action), 0)
        action.assert_not_called()

    def test_game_detection_uses_process_name(self):
        process = MagicMock()
        process.info = {'name': 'VALORANT-Win64-Shipping.exe'}
        with patch.object(gaming_guard.psutil, 'process_iter', return_value=[process]):
            self.assertTrue(gaming_guard.game_running())
        process.info = {'name': 'RiotClientServices.exe'}
        with patch.object(gaming_guard.psutil, 'process_iter', return_value=[process]):
            self.assertTrue(gaming_guard.game_running())
        for name in ('steam.exe', 'RobloxPlayerBeta.exe', 'League of Legends.exe'):
            process.info = {'name': name}
            with patch.object(gaming_guard.psutil, 'process_iter', return_value=[process]):
                self.assertTrue(gaming_guard.game_running())
        process.info = {'name': 'game_unknown.exe', 'exe': str(Path('C:/Games/NewGame/game_unknown.exe'))}
        with patch.object(gaming_guard.psutil, 'process_iter', return_value=[process]):
            self.assertTrue(gaming_guard.game_running())
        process.info = {'name': 'notepad.exe', 'exe': 'C:/Windows/System32/notepad.exe'}
        with patch.object(gaming_guard.psutil, 'process_iter', return_value=[process]):
            self.assertFalse(gaming_guard.game_running())
        process.info = {'name': 'wallpaper64.exe', 'exe': 'C:/Games/WallpaperEngine/wallpaper64.exe'}
        with patch.object(gaming_guard.psutil, 'process_iter', return_value=[process]):
            self.assertFalse(gaming_guard.game_running())

    def test_game_opening_interrupts_work(self):
        import time
        with patch.object(gaming_guard, 'game_running', side_effect=[False, True]), \
             patch.object(gaming_guard, 'stop_our_workloads') as stop:
            with self.assertRaises(gaming_guard.GamePaused):
                with gaming_guard.guard_game(logging.getLogger('test')):
                    deadline = time.monotonic() + 4
                    while not gaming_guard.was_game_pause() and time.monotonic() < deadline:
                        time.sleep(0.05)
            stop.assert_called_once()
        gaming_guard._paused.clear()

    def test_ai_only_uses_local_endpoint(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {
            'done': True, 'done_reason': 'stop',
            'message': {'content': '{"urgencia":"Alta","resumo":"Ação hoje."}'}}
        session = MagicMock()
        session.__enter__.return_value = session
        session.post.return_value = response
        with patch.object(common, 'ensure_local_ai'), \
             patch.object(common.requests, 'Session', return_value=session):
            result = common.ai_json('Classifique', {}, emails.SCHEMA, 'teste')
        self.assertEqual(result['urgencia'], 'Alta')
        self.assertIs(session.trust_env, False)
        self.assertEqual(session.post.call_args.args[0], 'http://127.0.0.1:11434/api/chat')
        self.assertEqual(session.post.call_args.kwargs['json']['model'], 'qwen3:4b-instruct')

    def test_email_date_and_mime(self):
        date = emails.internal_date(b'1 (INTERNALDATE " 2-Nov-2026 09:30:00 -0300")')
        self.assertEqual(date.astimezone(timezone.utc).hour, 12)
        message = EmailMessage()
        message.set_content('Texto principal')
        message.add_alternative('<p>Alternativa HTML</p>', subtype='html')
        message.add_attachment(b'arquivo', maintype='application', subtype='octet-stream', filename='a.bin')
        self.assertIn('Texto principal', emails.body_text(message))
        html = EmailMessage()
        html.set_content('<script>oculto</script><p>Visível</p>', subtype='html')
        self.assertEqual(emails.body_text(html), 'Visível')
        compact = emails.compact_body('Pague hoje. https://example.com/' + 'a' * 300 +
                                      '\nLink curto: https://example.com/vaga')
        self.assertIn('Pague hoje.', compact)
        self.assertIn('https://example.com/vaga', compact)
        self.assertNotIn('a' * 300, compact)

    def test_delivery_dedup_and_failure(self):
        conn = fresh_db()
        with patch('common.telegram', side_effect=RuntimeError('Falha simulada')):
            with self.assertRaises(RuntimeError):
                common.send_once(conn, 'a', 'alerta')
        self.assertFalse(common.was_sent(conn, 'a'))
        with patch('common.telegram') as send:
            common.send_once(conn, 'a', 'alerta')
            common.send_once(conn, 'a', 'alerta')
            send.assert_called_once_with('alerta')
        conn.close()

    def test_classification_and_pending_outbox(self):
        conn = fresh_db()
        actual = emails.main
        cutoff_date = datetime.now(timezone.utc) - timedelta(hours=1)
        stamp = cutoff_date.strftime('%d-%b-%Y %H:%M:%S +0000')
        # strftime %b depende de locale; o Python de testes usa meses ingleses.
        messages = {}
        for uid, subject in [(b'1', 'Incidente'), (b'2', 'Newsletter')]:
            message = EmailMessage()
            message['Subject'] = subject
            message['From'] = 'teste@example.com'
            message.set_content('Conteúdo de teste')
            messages[uid] = [(f'1 (INTERNALDATE "{stamp}")'.encode(), message.as_bytes()), b')']

        class FakeIMAP:
            unread = b'1 2'
            def __init__(self, *args, **kwargs): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def login(self, *args): pass
            def select(self, box, readonly=False):
                assert readonly
                return 'OK', [b'2']
            def response(self, name): return name, [b'77']
            def uid(self, command, *args):
                if command == 'search':
                    assert 'UNSEEN' in args
                    return 'OK', [self.unread]
                assert args[1] == '(INTERNALDATE BODY.PEEK[])'
                return 'OK', messages[args[0]]

        env = {'GMAIL_EMAIL': 'test@example.com', 'GMAIL_APP_PASSWORD': 'abcdefghijklmnop',
               'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_CHAT_ID': '123'}
        with patch.dict(os.environ, env), patch.object(emails, 'database', return_value=conn), \
             patch.object(emails.imaplib, 'IMAP4_SSL', FakeIMAP), \
             patch.object(emails, 'ai_json', side_effect=[
                 {'urgencia': 'Alta', 'resumo': 'Ação em uma hora.'},
                 {'urgencia': 'Baixa', 'resumo': 'Informativo.'}]) as ai, \
             patch.object(common, 'telegram', side_effect=RuntimeError('Falha de rede')):
            # main fecha a conexão; wrapper mantém o banco de teste entre execuções.
            class Wrapper:
                def __getattr__(self, name): return getattr(conn, name)
                def __enter__(self): return conn.__enter__()
                def __exit__(self, *args): return conn.__exit__(*args)
                def close(self): pass
            with patch.object(emails, 'database', return_value=Wrapper()):
                with self.assertRaises(RuntimeError):
                    actual(logging.getLogger('test'))
                self.assertEqual(ai.call_count, 2)
                # Usuário leu os e-mails. Alerta pendente ainda deve ser entregue.
                FakeIMAP.unread = b''
                with patch.object(common, 'telegram') as send:
                    actual(logging.getLogger('test'))
                    actual(logging.getLogger('test'))
                    send.assert_called_once()
                    self.assertIn('ALTA', send.call_args.args[0])
                    self.assertNotIn('Newsletter', send.call_args.args[0])
        conn.close()

    def test_job_link_and_invalid_ai(self):
        self.assertEqual(jobs.clean_link('https://br.indeed.com/rc/clk?jk=abcdef1234567890&src=x'),
                         'https://br.indeed.com/viewjob?jk=abcdef1234567890')
        with self.assertRaises(ValueError):
            jobs.clean_link('https://malicious.example/viewjob?jk=abcdef1234567890')
        self.assertEqual(jobs.clean_link('https://riovagas.com.br/riovagas/desenvolvedor-junior/?src=x'),
                         'https://riovagas.com.br/riovagas/desenvolvedor-junior/')
        with self.assertRaises(ValueError):
            jobs.clean_link('https://riovagas.com.br/riovagas/')
        with patch.object(jobs, 'ai_json', return_value={'vagas': [
                {'id': 999, 'titulo': 'X', 'empresa': 'Y', 'pontuacao': 100}]}):
            with self.assertRaises(RuntimeError):
                jobs.structure([{'id': 1, 'titulo': 'Desenvolvedor Junior',
                                 'localidade': 'Rio de Janeiro - RJ', 'link': 'https://br.indeed.com/viewjob?jk=abcdef1234567890'}])

    def test_unrelated_jobs_do_not_reach_ai(self):
        with patch.object(jobs, 'ai_json') as ai:
            self.assertEqual(jobs.structure([
                {'id': 1, 'titulo': 'Sênior Software Engineer'},
                {'id': 2, 'titulo': 'Analista Financeiro Júnior'},
                {'id': 3, 'titulo': 'Social Media Júnior'}]), [])
            ai.assert_not_called()

    def test_zero_score_is_never_recommended(self):
        with patch.object(jobs, 'ai_json', return_value={'vagas': [
                {'id': 1, 'titulo': 'Desenvolvedor Junior', 'empresa': 'Empresa', 'pontuacao': 0}]}):
            self.assertEqual(jobs.structure([{'id': 1, 'titulo': 'Desenvolvedor Junior',
                             'localidade': 'Rio de Janeiro - RJ', 'link': 'https://br.indeed.com/viewjob?jk=abcdef1234567890'}]), [])

    def test_indeed_block_uses_riovagas(self):
        rows = [{'titulo': 'Desenvolvedor Junior', 'empresa': 'Empresa',
                 'localidade': 'Rio de Janeiro - RJ', 'link': 'https://riovagas.com.br/riovagas/desenvolvedor-junior/'}]
        first = MagicMock(side_effect=RuntimeError('HTTP 403'))
        fallback = MagicMock(return_value=rows)
        with patch.object(jobs, 'source_collectors', return_value=[('Indeed', first), ('RioVagas', fallback)]):
            self.assertEqual(len(jobs.scrape(logging.getLogger('test'))), 1)
            fallback.assert_called_once()
            self.assertEqual(jobs.LAST_SOURCE_STATUS[0]['status'], 'indisponível')

    def test_all_sites_are_consulted_after_a_success(self):
        first = MagicMock(return_value=[{'titulo': 'Desenvolvedor Junior', 'empresa': 'A',
                          'localidade': 'Rio de Janeiro - RJ', 'link': 'https://www.vagas.com.br/vagas/v123/dev'}])
        second = MagicMock(return_value=[{'titulo': 'Analista de Dados Jr', 'empresa': 'B',
                           'localidade': 'Rio de Janeiro - RJ', 'link': 'https://www.nerdin.com.br/vaga_emprego/dados-123.php'}])
        with patch.object(jobs, 'source_collectors', return_value=[('Vagas.com', first), ('Nerdin', second)]):
            rows = jobs.scrape(logging.getLogger('test'))
        self.assertEqual(len(rows), 2)
        first.assert_called_once()
        second.assert_called_once()

    def test_cross_site_duplicates_and_non_it_titles(self):
        rows = jobs.combine_sources([
            [{'titulo': 'Desenvolvedor(a) Jr.', 'empresa': 'ACME', 'fonte': 'Vagas.com',
              'localidade': 'Rio de Janeiro - RJ', 'link': 'https://www.vagas.com.br/vagas/v123/dev'}],
            [{'titulo': 'Desenvolvedor Júnior', 'empresa': 'ACME', 'fonte': 'Nerdin',
              'localidade': 'Rio de Janeiro - RJ', 'link': 'https://www.nerdin.com.br/vaga_emprego/dev-123.php'},
             {'titulo': 'Analista Financeiro Junior', 'empresa': 'ACME', 'fonte': 'Nerdin',
              'localidade': 'Rio de Janeiro - RJ', 'link': 'https://www.nerdin.com.br/vaga_emprego/financeiro-123.php'}]])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['fontes'], ['Vagas.com', 'Nerdin'])

    def test_gupy_links_and_two_formats_of_same_job(self):
        import base64
        token = base64.b64encode(b'{"jobId":123,"source":"gupy_portal"}').decode()
        link = jobs.clean_link('https://empresa.gupy.io/job/' + token + '?jobBoardSource=x')
        self.assertEqual(jobs.link_identity(link), 'https://empresa.gupy.io/jobs/123')
        with self.assertRaises(ValueError):
            jobs.clean_link('https://evil.com.gupy.io/jobs/123')
        with self.assertRaises(ValueError):
            jobs.clean_link('https://empresa.gupy.io/job/invalid')

    def test_candidate_limit_is_shared_fairly_between_sites(self):
        groups = [[{'titulo': f'Desenvolvedor Junior {i}', 'empresa': 'A', 'fonte': 'Vagas.com',
                    'localidade': 'Rio de Janeiro - RJ', 'link': f'https://www.vagas.com.br/vagas/v{100+i}/dev'} for i in range(5)],
                  [{'titulo': 'Analista de Dados Jr', 'empresa': 'B', 'fonte': 'Nerdin',
                    'localidade': 'Rio de Janeiro - RJ', 'link': 'https://www.nerdin.com.br/vaga_emprego/dados-123.php'}]]
        rows = jobs.combine_sources(groups, limit=2)
        self.assertEqual([row['fonte'] for row in rows], ['Vagas.com', 'Nerdin'])

    def test_all_failed_sites_are_not_an_empty_search(self):
        with patch.object(jobs, 'source_collectors', return_value=[
                ('A', MagicMock(side_effect=RuntimeError())), ('B', MagicMock(side_effect=RuntimeError()))]):
            with self.assertRaises(RuntimeError):
                jobs.scrape(logging.getLogger('test'))

    def test_game_pause_does_not_start_another_source(self):
        first = MagicMock(side_effect=gaming_guard.GamePaused('Jogo aberto'))
        fallback = MagicMock()
        with patch.object(jobs, 'source_collectors', return_value=[('Indeed', first), ('RioVagas', fallback)]):
            with self.assertRaises(gaming_guard.GamePaused):
                jobs.scrape(logging.getLogger('test'))
            fallback.assert_not_called()

    def test_digest_utf16_limit(self):
        sample = [dict(titulo='💻' * 180, empresa='E' * 140,
                       link=f'https://br.indeed.com/viewjob?jk=abcdef123456789{i}') for i in range(10)]
        messages = jobs.digest_messages(sample, '2026-11-02')
        self.assertTrue(len(messages) > 1)
        self.assertTrue(all(len(m.encode('utf-16-le')) // 2 <= 3500 for m in messages))
        self.assertEqual(sum(m.count('https://br.indeed.com/') for m in messages), 10)

    def test_indeed_failure_is_not_an_empty_digest(self):
        conn = fresh_db()
        with patch.dict(os.environ, {'TELEGRAM_BOT_TOKEN': 'fake', 'TELEGRAM_CHAT_ID': '123'}), \
             patch.object(jobs, 'database', return_value=conn), \
             patch.object(jobs, 'scrape', side_effect=RuntimeError('Indeed bloqueado')), \
             patch.object(jobs, 'send_once') as send, \
             patch.object(jobs, 'structure') as structure:
            with self.assertRaises(RuntimeError):
                jobs.main(logging.getLogger('test'))
            self.assertIn(':collection-failure', send.call_args.args[1])
            self.assertIn('não pôde ser concluída', send.call_args.args[2])
            structure.assert_not_called()

    def test_playwright_scrape_fixture(self):
        from playwright.sync_api import sync_playwright
        fixture = '''<html><head><title>Vagas</title></head><body><li>
            <div class="job_seen_beacon"><h2 class="jobTitle">
            <a class="jcs-JobTitle" data-jk="abcdef1234567890"
              href="https://br.indeed.com/rc/clk?jk=abcdef1234567890">Desenvolvedor Júnior</a>
            </h2><span data-testid="company-name">Empresa Teste</span>
            <span data-testid="text-location">Rio de Janeiro - RJ</span><p>Desenvolvimento de software nível júnior.</p></div></li></body></html>'''
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            # Contexto de teste responde localmente; nenhuma conexão com o Indeed.
            context = browser.new_context()
            context.route('**/*', lambda route: route.fulfill(
                status=200, content_type='text/html; charset=utf-8', body=fixture))
            class Browser:
                def new_context(self, **kwargs): return context
                def close(self): browser.close()
            class Chromium:
                def launch(self, **kwargs):
                    assert kwargs['headless'] is True
                    return Browser()
            class PW:
                chromium = Chromium()
                def __enter__(self): return self
                def __exit__(self, *args): pass
            with patch.object(jobs, 'sync_playwright', return_value=PW()):
                result = jobs.scrape_indeed(logging.getLogger('test'))
            self.assertEqual(len(result), 1)  # Três buscas, mesma vaga: deduplicação.
            self.assertEqual(result[0]['empresa'], 'Empresa Teste')
            self.assertEqual(result[0]['titulo'], 'Desenvolvedor Júnior')
            self.assertEqual(result[0]['link'], 'https://br.indeed.com/viewjob?jk=abcdef1234567890')


if __name__ == '__main__':
    unittest.main(verbosity=2)
