"""Pesquisa vagas júnior de TI em múltiplos sites com Chromium headless."""
from datetime import datetime
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit

from playwright.sync_api import sync_playwright
from gaming_guard import GamePaused, check_game
from job_preferences import location_policy

from common import (DATA_DIR, ai_json, cached, database, one_line, required,
                    run_locked, save_cache, send_once)

# Mesmo caminho no teste manual e no Agendador, sem depender do shell instalador.
_browser_dir = Path(__file__).resolve().parent / '.venv' / 'browsers'
if _browser_dir.is_dir():
    os.environ.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(_browser_dir))

SEARCHES = [
    'Desenvolvedor Junior',
    'Analista de Dados Junior',
    'Inteligência Artificial Junior',
    'Tecnologia da Informação Junior',
    'Suporte de TI Junior',
    'Testes de Software Junior',
    'Analista de Redes Junior',
    'Segurança da Informação Junior',
]

SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {'vagas': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'properties': {
            'id': {'type': 'integer'}, 'titulo': {'type': 'string'},
            'empresa': {'type': 'string'}, 'pontuacao': {'type': 'integer'},
        }, 'required': ['id', 'titulo', 'empresa', 'pontuacao'],
    }}}, 'required': ['vagas'],
}


def clean_link(url):
    """Permite apenas links de vagas reais do site, sem query de rastreamento."""
    parts = urlsplit(url)
    if parts.scheme == 'https' and parts.netloc in ('www.nerdin.com.br', 'nerdin.com.br'):
        if not re.fullmatch(r'/vaga_emprego/[^/]+\.php', parts.path):
            raise ValueError('Link não corresponde a uma vaga no Nerdin.')
        return urlunsplit(('https', 'www.nerdin.com.br', parts.path, '', ''))
    if parts.scheme == 'https' and parts.netloc in ('www.vagas.com.br', 'vagas.com.br'):
        if not re.fullmatch(r'/vagas/v\d+(?:/[^/]+)?/?', parts.path):
            raise ValueError('Link não corresponde a uma vaga no Vagas.com.')
        return urlunsplit(('https', 'www.vagas.com.br', parts.path, '', ''))
    if parts.scheme == 'https' and re.fullmatch(r'[a-z0-9-]+\.gupy\.io', parts.netloc):
        if re.fullmatch(r'/jobs/\d+/?', parts.path):
            return urlunsplit(('https', parts.netloc, parts.path.rstrip('/'), '', ''))
        match = re.fullmatch(r'/job/([A-Za-z0-9_+=-]{1,1000})/?', parts.path)
        if not match:
            raise ValueError('Link não corresponde a uma vaga na Gupy.')
        try:
            encoded = match.group(1)
            payload = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                                                  altchars=b'-_', validate=True))
            if not re.fullmatch(r'\d{1,20}', str(payload['jobId'])):
                raise ValueError('Identificador Gupy inválido.')
        except (ValueError, KeyError, TypeError):
            raise ValueError('Identificador Gupy inválido.') from None
        return urlunsplit(('https', parts.netloc, parts.path.rstrip('/'), '', ''))
    if parts.scheme == 'https' and parts.netloc in ('riovagas.com.br', 'www.riovagas.com.br'):
        if not re.fullmatch(r'/(?:riovagas|vagas-externas)/[^/]+/?', parts.path):
            raise ValueError('Link não corresponde a uma publicação de vaga no RioVagas.')
        return urlunsplit(('https', 'riovagas.com.br', parts.path, '', ''))
    if parts.scheme != 'https' or parts.netloc != 'br.indeed.com':
        raise ValueError('Domínio de vaga inesperado.')
    params = parse_qs(parts.query)
    job_key = (params.get('jk') or params.get('vjk') or [''])[0]
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', job_key):
        raise ValueError('Identificador de vaga Indeed ausente ou inválido.')
    return 'https://br.indeed.com/viewjob?' + urlencode({'jk': job_key})


def link_identity(url):
    """Agrupa os dois formatos oficiais de URL da mesma vaga Gupy."""
    parts = urlsplit(url)
    if parts.netloc.endswith('.gupy.io') and parts.path.startswith('/job/'):
        encoded = parts.path.split('/')[2]
        job_id = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                                             altchars=b'-_'))['jobId']
        return f'https://{parts.netloc}/jobs/{job_id}'
    return url


def fold(text):
    return ''.join(c for c in unicodedata.normalize('NFD', text.lower())
                   if unicodedata.category(c) != 'Mn')


def target_title(title):
    """Filtro verificável: cargo e nível explícitos no título, antes da IA.

    Evita que buscas amplas ou respostas de IA com pontuação zero virem vagas
    recomendadas. Pode excluir anúncios cujo nível só consta na descrição.
    """
    title = fold(title)
    if not re.search(r'\b(junior|jr)\b', title):
        return False
    if re.search(r'\b(senior|sr|pleno|plena|estagio|estagiario|estagiaria|trainee)\b', title):
        return False
    developer = any(term in title for term in
                    ('desenvolvedor', 'desenvolvedora', 'developer', 'programador', 'programadora',
                     'software engineer', 'engenheiro de software', 'engenheira de software'))
    if any(term in title for term in ('desenvolvedor de negocios', 'business developer', 'programador de producao')):
        developer = False
    data = ('analista' in title and 'dados' in title) or any(term in title for term in
           ('data analyst', 'engenheiro de dados', 'engenheira de dados', 'cientista de dados',
            'data engineer', 'data scientist', 'administrador de banco de dados'))
    ai = any(term in title for term in
             ('inteligencia artificial', 'machine learning', 'engenheiro de ia', 'analista de ia', 'ai engineer'))
    if developer or data or ai:
        return True
    # A empresa ser de tecnologia não transforma um cargo financeiro/comercial em TI.
    if any(term in title for term in ('financeiro', 'financeira', 'controladoria', 'contabil',
                                     'contabilidade', 'marketing', 'social media', 'vendas',
                                     'comercial', 'recursos humanos', 'administrativo', 'redes sociais')):
        return False
    explicit_it = bool(re.search(r'\b(ti|tic)\b', title)) or any(term in title for term in
                              ('tecnologia da informacao', 'informatica', 'information technology'))
    systems = 'analista de sistemas' in title or 'systems analyst' in title
    support = any(term in title for term in ('suporte', 'help desk', 'service desk')) and (
        explicit_it or any(term in title for term in ('sistemas', 'software', 'help desk', 'service desk')))
    testing = any(term in title for term in ('testes de software', 'teste de software', 'software tester',
                                            'analista de testes', 'analista qa', 'qa analyst'))
    networks = 'analista de redes' in title or 'redes de computadores' in title
    infrastructure = any(term in title for term in ('devops', 'cloud', 'nuvem')) or (
        'infraestrutura' in title and (explicit_it or 'sistemas' in title or 'redes' in title))
    security = any(term in title for term in ('seguranca da informacao', 'seguranca de informacao',
                                             'ciberseguranca', 'cybersecurity', 'cyber security', 'information security'))
    general_it = explicit_it and any(term in title for term in ('analista', 'tecnico', 'assistente', 'suporte', 'consultor'))
    return systems or support or testing or networks or infrastructure or security or general_it


def eligible_job(row):
    if not target_title(row.get('titulo', '')):
        return None
    policy = location_policy(row)
    return dict(row, **policy) if policy else None


def scrape_indeed(logger):
    check_game()
    collected = {}
    # Não conecta ao navegador pessoal, não instala extensões e nunca usa headful.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale='pt-BR', accept_downloads=False)
        page = context.new_page()
        page.set_default_timeout(15000)
        try:
            for query in SEARCHES:
                check_game()
                response = page.goto('https://br.indeed.com/jobs?' + urlencode({'q': query}),
                                     wait_until='domcontentloaded', timeout=60000)
                if response is None or response.status >= 400:
                    status = response.status if response else 'sem resposta'
                    raise RuntimeError(f'Indeed HTTP {status}; site indisponível ou coleta bloqueada.')
                # Detecta bloqueios também quando a página retorna HTTP 200.
                # Nenhuma técnica stealth, proxy ou solução de CAPTCHA é usada.
                page.wait_for_function('''() =>
                    document.querySelector('h2.jobTitle a, a.jcs-JobTitle, [data-testid="job-title"] a') ||
                    /nenhuma vaga|não encontramos|nao encontramos|0 vagas|did not match any jobs|no jobs found|captcha|verify you are human|verifique que você é humano|just a moment|access denied|additional verification|verificação adicional/i.test(
                        document.title + ' ' + document.body.innerText)
                ''', timeout=20000)
                if any(term in fold(page.title() + ' ' + page.locator('body').inner_text()[:3000])
                       for term in ['captcha', 'access denied', 'verifique que voce e humano',
                                    'verify you are human', 'just a moment',
                                    'additional verification', 'verificacao adicional']):
                    raise RuntimeError('Indeed exige verificação humana; coleta interrompida.')
                # Seletores específicos + fallback sem depender de classes de estilo.
                rows = page.evaluate('''() => {
                    const anchors = [...document.querySelectorAll(
                        'h2.jobTitle a, a.jcs-JobTitle, [data-testid="job-title"] a')];
                    const seen = new Set();
                    return anchors.filter(a => {
                        if (seen.has(a.href)) return false;
                        seen.add(a.href); return true;
                    }).slice(0, 10).map(a => {
                        const card = a.closest('.job_seen_beacon, .jobsearch-SerpJobCard, [data-testid="slider_item"]')
                            || a.closest('li') || a.parentElement.parentElement;
                        const jobKey = a.getAttribute('data-jk') || card.getAttribute('data-jk');
                        return {
                            titulo: (a.innerText || a.getAttribute('title') || '').trim(),
                            empresa: (card.querySelector('[data-testid="company-name"], .companyName, [itemprop="hiringOrganization"]')
                                ?.innerText || '').trim(),
                            localidade: card.querySelector('[data-testid="text-location"], .companyLocation')?.innerText?.trim() || '',
                            modalidade: card.querySelector('[data-testid="workplace-type"]')?.innerText?.trim() || '',
                            link: jobKey ? 'https://br.indeed.com/viewjob?jk=' + encodeURIComponent(jobKey) : a.href,
                            texto_sujo: card.innerText.slice(0, 1000)
                        };
                    });
                }''')
                if not rows:
                    body = fold(page.locator('body').inner_text())
                    if not any(x in body for x in ['nenhuma vaga', 'nao encontramos', '0 vagas',
                                                   'did not match any jobs', 'no jobs found']):
                        raise RuntimeError('Layout de vagas mudou; revise os seletores em job_finder.py.')
                for row in rows:
                    row = eligible_job(row)
                    if row is None:
                        continue
                    link = clean_link(row['link'])
                    if link not in collected:
                        row['link'] = link
                        row['fonte'] = 'Indeed'
                        row['consulta'] = query
                        collected[link] = row
                logger.info('Busca concluída: %s; %d resultado(s).', query, len(rows))
                page.wait_for_timeout(1500)  # Intervalo entre buscas; sem burlar bloqueios.
        finally:
            context.close()
            browser.close()
    return [dict(row, id=index) for index, row in enumerate(collected.values(), 1)]


def scrape_riovagas(logger):
    """Alternativa pública autorizada: WordPress no RioVagas, sem login ou API paga."""
    check_game()
    collected = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        # O conteúdo das vagas é renderizado no servidor; dispensa scripts de anúncios.
        context = browser.new_context(locale='pt-BR', accept_downloads=False,
                                      java_script_enabled=False)
        context.route('**/*', lambda route: route.abort() if
                      route.request.resource_type in ('image', 'media', 'font') else route.continue_())
        page = context.new_page()
        detail = context.new_page()
        try:
            for query in SEARCHES:
                check_game()
                response = page.goto('https://riovagas.com.br/riovagas/?' + urlencode({'s': query}),
                                     wait_until='domcontentloaded', timeout=45000)
                if not response or response.status >= 400:
                    raise RuntimeError('RioVagas indisponível ou coleta bloqueada.')
                page.wait_for_function('''() => document.querySelector('article .entry-title a') ||
                    document.body.classList.contains('search-no-results') ||
                    /nada encontrado|nada correspondeu|nenhum resultado|não encontramos|nothing found|no results|nenhuma vaga/i.test(
                        document.body.innerText)''', timeout=15000)
                rows = page.evaluate('''() => [...document.querySelectorAll('article .entry-title a')]
                    .slice(0, 10).map(a => ({titulo: a.innerText.trim(), link: a.href,
                        texto_sujo: a.closest('article').innerText.slice(0, 1000)}))''')
                for row in rows:
                    check_game()
                    if len(collected) >= 30:
                        break
                    if not target_title(row['titulo']):
                        continue  # Dispensa abrir anúncios claramente fora do escopo de TI júnior.
                    link = clean_link(row['link'])
                    if link in collected:
                        continue
                    # Busca a publicação para obter empresa e contexto, não só o título.
                    result = detail.goto(link, wait_until='domcontentloaded', timeout=45000)
                    if not result or result.status >= 400:
                        raise RuntimeError('RioVagas não permitiu abrir uma publicação de vaga.')
                    content = detail.locator('.entry-content').first
                    if not content.count():
                        raise RuntimeError('Layout de publicação RioVagas mudou.')
                    text = content.inner_text()
                    policy = location_policy(dict(row, texto_sujo=text))
                    if policy is None:
                        continue
                    company = detail.evaluate('''() => {
                        function find(obj) {
                            if (!obj || typeof obj !== 'object') return '';
                            if (obj['@type'] === 'JobPosting') return obj.hiringOrganization?.name || '';
                            for (const value of Object.values(obj)) {
                                if (Array.isArray(value)) {
                                    for (const item of value) { const found = find(item); if (found) return found; }
                                } else if (value && typeof value === 'object') {
                                    const found = find(value); if (found) return found;
                                }
                            }
                            return '';
                        }
                        for (const script of document.querySelectorAll('script[type="application/ld+json"]')) {
                            try { const found = find(JSON.parse(script.textContent)); if (found) return found; }
                            catch {}
                        }
                        return '';
                    }''')
                    if not company:
                        match = re.search(r'(?:Nome da [Ee]mpresa|Empresa contratante)\s*:\s*([^\n]+)', text)
                        company = match.group(1).strip() if match else 'Não informada'
                    if fold(company).strip() in ('riovagas', 'rio vagas'):
                        company = 'Não informada'  # Publicador não é a empresa contratante.
                    row.update(link=link, empresa=company, texto_sujo=text[:1000],
                               consulta=query, fonte='RioVagas', **policy)
                    collected[link] = row
                    page.wait_for_timeout(500)
                logger.info('RioVagas: %s; %d resultado(s).', query, len(rows))
                page.wait_for_timeout(1000)
        finally:
            context.close()
            browser.close()
    return [dict(row, id=index) for index, row in enumerate(collected.values(), 1)]


def scrape_board(logger, source):
    """Coleta DOM público de Nerdin, Vagas.com ou Gupy. Uma aba por fonte."""
    if source == 'Nerdin':
        urls = ['https://www.nerdin.com.br/vagas-estagio-junior.php']
        selector = '.vaga-card'
        expression = '''() => [...document.querySelectorAll('.vaga-card')].slice(0, 40).map(card => {
            const title = card.querySelector('.vaga-titulo')?.cloneNode(true);
            title?.querySelectorAll('.vaga-nova-badge').forEach(node => node.remove());
            return {titulo: title?.innerText?.trim() || title?.textContent?.trim() || '',
                empresa: card.querySelector('.vaga-empresa-nome')?.innerText?.trim() || 'Não informada',
                localidade: card.querySelector('.vaga-local-linha')?.innerText?.trim() || '',
                modalidade: card.querySelector('.vaga-resumo-linha')?.innerText?.trim() || '',
                link: new URL(card.getAttribute('data-href'), location.href).href,
                texto_sujo: card.innerText.slice(0, 1000)};
        })'''
    elif source == 'Vagas.com':
        urls = ['https://www.vagas.com.br/vagas-de-' + re.sub(r'[^a-z0-9]+', '-', fold(q)).strip('-')
                for q in SEARCHES]
        selector = 'a.link-detalhes-vaga'
        expression = '''() => [...document.querySelectorAll('a.link-detalhes-vaga')].slice(0, 10).map(a => {
            const card = a.closest('li.vaga, article, li');
            return {titulo: (a.getAttribute('title') || a.innerText).trim(),
                empresa: card?.querySelector('.emprVaga')?.innerText?.trim() || 'Não informada',
                localidade: card?.querySelector('.vaga-local')?.innerText?.trim() || '',
                modalidade: '',
                link: a.href, texto_sujo: card?.innerText.slice(0, 1000) || ''};
        })'''
    elif source == 'Gupy':
        urls = ['https://portal.gupy.io/job-search/' + quote('term=' + q, safe='') for q in
                ('junior', 'desenvolvedor junior', 'dados junior', 'tecnologia junior')]
        selector = '#job-listing-results a[href*=".gupy.io/"]'
        expression = '''() => [...document.querySelectorAll('#job-listing-results a[href*=".gupy.io/"]')]
            .slice(0, 20).map(a => ({titulo: a.querySelector('h3')?.innerText?.trim() || '',
                empresa: a.querySelector('p')?.innerText?.trim() || 'Não informada',
                localidade: a.querySelector('[data-testid="job-location"]')?.innerText?.trim() || '',
                modalidade: a.querySelector('[data-testid="PersonalVideoRoundedIcon"]')?.parentElement?.innerText?.trim() || '',
                link: a.href, texto_sujo: a.innerText.slice(0, 1000)}))'''
    else:
        raise ValueError('Fonte desconhecida.')
    collected, successful = {}, 0
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale='pt-BR', accept_downloads=False)
        context.route('**/*', lambda route: route.abort() if route.request.resource_type in
                      ('image', 'media', 'font') else route.continue_())
        page = context.new_page()
        try:
            for url in urls:
                check_game()
                if len(collected) >= 30:
                    break
                try:
                    response = page.goto(url, wait_until='domcontentloaded', timeout=35000)
                    if response is None or response.status >= 400:
                        raise RuntimeError('Fonte indisponível ou acesso bloqueado.')
                    page.wait_for_function('''selector => document.querySelector(selector) ||
                        /nenhuma vaga|nenhum resultado|não encontramos|nao encontramos|0 vagas|no jobs found/i.test(
                            document.body.innerText)''', arg=selector, timeout=12000)
                    rows = page.evaluate(expression)
                    for row in rows:
                        row = eligible_job(row)
                        if row is not None:
                            try:
                                link = clean_link(row['link'])
                            except ValueError:
                                logger.warning('%s: link inesperado ignorado.', source)
                                continue
                            if len(collected) < 30 and link not in collected:
                                collected[link] = dict(row, link=link, fonte=source)
                    successful += 1
                    logger.info('%s: página coletada; %d candidato(s) de TI júnior.', source, len(collected))
                except GamePaused:
                    raise
                except Exception as error:
                    check_game()
                    logger.warning('%s: página indisponível (%s); seguindo as demais.', source, type(error).__name__)
                page.wait_for_timeout(800)
        finally:
            context.close()
            browser.close()
    if not successful:
        raise RuntimeError(f'{source}: nenhuma página pôde ser coletada.')
    return list(collected.values())


def scrape_nerdin(logger):
    return scrape_board(logger, 'Nerdin')


def scrape_vagas(logger):
    return scrape_board(logger, 'Vagas.com')


def scrape_gupy(logger):
    return scrape_board(logger, 'Gupy')


def source_collectors():
    # Todas são consultadas. Não é uma cadeia que para na primeira fonte disponível.
    # Vagas.com permanece fora da rodada por preferência explícita do usuário.
    return [('Nerdin', scrape_nerdin), ('Gupy', scrape_gupy),
            ('RioVagas', scrape_riovagas), ('Indeed', scrape_indeed)]


LAST_SOURCE_STATUS = []


def combine_sources(groups, limit=30):
    """Intercala fontes para nenhuma ocupar o lote inteiro; deduplica antes da IA."""
    unique, by_url, by_role = [], {}, {}
    for offset in range(max((len(rows) for rows in groups), default=0)):
        for rows in groups:
            check_game()
            if offset >= len(rows):
                continue
            row = eligible_job(rows[offset])
            if row is None:
                continue
            try:
                link = clean_link(row['link'])
            except (KeyError, ValueError):
                continue
            company = one_line(row.get('empresa') or 'Não informada', 160)
            title = re.sub(r'\((?:a|o|a/o|o/a)\)', '', fold(row['titulo']))
            title = re.sub(r'\b(jr|junior)\b', 'junior', title)
            title = re.sub(r'[^a-z0-9]+', ' ', title).strip()
            employer = re.sub(r'[^a-z0-9]+', ' ', fold(company)).strip()
            role = (title, employer, fold(row['localidade']), fold(row['modalidade'])) if employer not in (
                'nao informada', 'nao informado', 'confidencial', 'empresa confidencial', 'sigilo', '') else None
            identity = link_identity(link)
            existing = by_url.get(identity) or (by_role.get(role) if role else None)
            if existing is not None:
                if row['fonte'] not in existing['fontes']:
                    existing['fontes'].append(row['fonte'])
                continue
            if len(unique) >= limit:
                continue
            normalized = dict(row, empresa=company, link=link, id=len(unique) + 1,
                              fontes=[row['fonte']])
            unique.append(normalized)
            by_url[identity] = normalized
            if role:
                by_role[role] = normalized
    return unique


def scrape(logger):
    LAST_SOURCE_STATUS.clear()
    groups = []
    for name, collector in source_collectors():
        check_game()
        try:
            rows = [dict(approved, fonte=name) for row in collector(logger)
                    if (approved := eligible_job(row)) is not None]
        except GamePaused:
            raise
        except Exception as error:
            check_game()
            LAST_SOURCE_STATUS.append({'fonte': name, 'status': 'indisponível', 'candidatos': 0})
            logger.warning('%s indisponível (%s); as outras fontes continuam.', name, type(error).__name__)
        else:
            groups.append(rows)
            LAST_SOURCE_STATUS.append({'fonte': name, 'status': 'ok', 'candidatos': len(rows)})
    if not groups:
        raise RuntimeError('Nenhuma fonte de vagas pôde ser consultada.')
    combined = combine_sources(groups)
    logger.info('Coleta de múltiplos sites concluída: %d candidato(s) único(s).', len(combined))
    return combined


def structure(raw):
    raw = [approved for row in raw if (approved := eligible_job(row)) is not None][:30]
    if not raw:
        return []
    # Mantém o lote dentro da janela local sem reservar um contexto muito grande.
    budget = max(100, min(600, 6000 // len(raw)))
    model_input = [{'id': row['id'], 'titulo': row['titulo'][:180],
                    'empresa': (row.get('empresa') or 'Não informada')[:120],
                    'localidade': row['localidade'], 'modalidade': row['modalidade'],
                    'texto_sujo': row.get('texto_sujo', '')[:budget]} for row in raw]
    result = ai_json(
        'Limpe os textos de cards de vagas. Selecione até 10 vagas únicas para '
        'Tecnologia da Informação em nível júnior: desenvolvimento, programação, dados, '
        'inteligência artificial, análise de sistemas, suporte de TI, testes/QA de software, '
        'redes de computadores, infraestrutura de TI, nuvem, DevOps e segurança da informação. '
        'Considere Jr, Júnior e Junior equivalentes. Exclua pleno, sênior e vagas fora '
        'dessas áreas. A vaga deve ser 100% home office OU localizada na cidade do Rio de Janeiro '
        'ou na Baixada Fluminense; não invente cidade ou modalidade. Use só evidências '
        'presentes nos cards. Preserve título e empresa, removendo apenas espaços, '
        'HTML e ruído. Se empresa não constar, use "Não informada". Retorne o id '
        'original; pontuação inteira de 0 a 100 conforme aderência ao cargo e nível '
        '(100 = título exato com nível explícito). Ordene por pontuação decrescente. '
        'Não crie vagas ou informações ausentes. Sem resultados compatíveis, retorne lista vazia.',
        model_input, SCHEMA, 'vagas_junior')
    if len(result['vagas']) > 10:
        raise RuntimeError('A IA retornou mais de 10 vagas.')
    source = {row['id']: row for row in raw}
    used, cleaned = set(), []
    for row in result['vagas']:
        index = row['id']
        if index not in source or index in used or not 0 <= row['pontuacao'] <= 100:
            raise RuntimeError('JSON de vagas contém id ou pontuação inválidos.')
        used.add(index)
        if not row['titulo'].strip() or not row['empresa'].strip():
            raise RuntimeError('JSON de vagas contém campos vazios.')
        if row['pontuacao'] < 60:
            continue
        # Links nunca vêm da IA: são recuperados do DOM pelo id validado.
        cleaned.append(dict(row, titulo=one_line(source[index]['titulo'], 250),
                            empresa=one_line(source[index].get('empresa') or 'Não informada', 160),
                            link=source[index]['link'], fonte=source[index].get('fonte', 'Indeed'),
                            localidade=source[index]['localidade'], modalidade=source[index]['modalidade'],
                            criterio_local=source[index]['criterio_local'],
                            fontes=source[index].get('fontes', [source[index].get('fonte', 'Indeed')])))
    return sorted(cleaned, key=lambda row: -row['pontuacao'])


def digest_messages(jobs, day, sources=None):
    header = f'💼 TI JÚNIOR — {day}\n{len(jobs)} vaga(s) compatível(is) encontrada(s).'
    header += '\n100% home office OU Rio capital / Baixada Fluminense'
    if sources:
        header += '\nSites consultados: ' + ', '.join(sources)
    if not jobs:
        return [header + '\nNenhuma vaga compatível nas buscas de hoje.']
    # Cada parte fica abaixo do limite do Telegram, inclusive com emojis (UTF-16).
    messages, current = [], header
    for position, row in enumerate(jobs, 1):
        source = one_line(', '.join(row.get('fontes', [row.get('fonte', '')])), 120)
        place = one_line(row.get('localidade', 'Não informada'), 120)
        mode = one_line(row.get('modalidade', 'Não informada'), 100)
        block = f'\n\n{position}. {one_line(row["titulo"], 180)}\n' \
                f'Empresa: {one_line(row["empresa"], 140)}\nLocal: {place}\nModalidade: {mode}\n' + \
                (f'Site: {source}\n' if source else '') + row['link']
        if len(block.encode('utf-16-le')) // 2 > 3500:
            raise RuntimeError('Link de vaga excepcionalmente longo.')
        if len((current + block).encode('utf-16-le')) // 2 > 3500:
            messages.append(current)
            current = '💼 Vagas júnior — continuação' + block
        else:
            current += block
    messages.append(current)
    return messages


def main(logger):
    token = required('TELEGRAM_BOT_TOKEN')
    chat = required('TELEGRAM_CHAT_ID')
    # Usa a data local do Windows. Configure o fuso de Brasília no sistema.
    day = datetime.now().strftime('%Y-%m-%d')
    destination = hashlib.sha256(f'{token}:{chat}'.encode()).hexdigest()[:20]
    key = f'jobs:v6-ti-rio-baixada-remoto:{destination}:{day}'
    conn = database()
    try:
        report = cached(conn, key)
        if report is None:
            try:
                raw = scrape(logger)
            except GamePaused:
                raise
            except Exception:
                # Não confunde bloqueio/layout quebrado com busca sem resultados.
                # Um único aviso por dia; nunca anuncia vagas que não foram coletadas.
                send_once(conn, f'{key}:collection-failure',
                          '⚠️ A busca de TI júnior nos sites configurados não pôde ser concluída: '
                          'os sites podem estar bloqueando a coleta ou mudaram de layout. Nenhuma lista de vagas '
                          'foi gerada. A automação continua sem serviços pagos.')
                raise
            jobs = structure(raw)
            statuses = [dict(status) for status in LAST_SOURCE_STATUS]
            sources = [item['fonte'] for item in statuses if item['status'] == 'ok']
            messages = digest_messages(jobs, day, sources)
            unavailable = [item['fonte'] for item in statuses if item['status'] != 'ok']
            if unavailable:
                note = '\n\nSites indisponíveis nesta execução: ' + ', '.join(unavailable) + '.'
                if len((messages[-1] + note).encode('utf-16-le')) // 2 <= 3500:
                    messages[-1] += note
                else:
                    messages.append(note.strip())
            report = {'data': day, 'vagas': jobs, 'fontes': statuses, 'mensagens': messages}
            # Congela o boletim para retentativas não duplicarem partes já enviadas.
            save_cache(conn, key, report)
        (DATA_DIR / 'vagas_ultimo_resultado.json').write_text(
            json.dumps({'data': report['data'], 'vagas': report['vagas'], 'fontes': report.get('fontes', [])},
                       ensure_ascii=False, indent=2), encoding='utf-8')
        for index, message in enumerate(report['mensagens']):
            send_once(conn, f'{key}:part:{index}', message)
        logger.info('Boletim diário entregue: %d vaga(s).', len(report['vagas']))
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(run_locked('job_finder', main))
