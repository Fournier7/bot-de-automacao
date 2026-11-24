"""Filtro local verificável: 100% remoto OU Rio capital/Baixada Fluminense.

Modalidade/local são fatos extraídos do anúncio, nunca criados pela IA. Ausência
de evidência rejeita a vaga. Ser publicada num portal do Rio não é suficiente.
"""
import re
import unicodedata

ALLOWED_CITIES = (
    'Rio de Janeiro', 'Belford Roxo', 'Duque de Caxias', 'Guapimirim', 'Itaguaí',
    'Japeri', 'Magé', 'Mesquita', 'Nilópolis', 'Nova Iguaçu', 'Paracambi',
    'Queimados', 'São João de Meriti', 'Seropédica',
)


def fold(text):
    return ''.join(char for char in unicodedata.normalize('NFD', str(text).lower())
                   if unicodedata.category(char) != 'Mn')


def labeled(text, labels):
    expression = r'^\s*(?:' + '|'.join(labels) + r')\s*:\s*([^\n]+)'
    match = re.search(expression, text, re.I | re.M)
    return match.group(1).strip() if match else ''


def evidence(row):
    text = row.get('texto_sujo', '')
    title = row.get('titulo', '')
    location = row.get('localidade') or labeled(text, ['Cidade', 'Localidade', 'Local de trabalho'])
    mode = row.get('modalidade') or labeled(text, [
        'Forma de trabalho', 'Modelo de trabalho', 'Modalidade', 'Formato de trabalho'])
    # Títulos no formato “Cargo — Rio de Janeiro” também fornecem localidade.
    if not location:
        pieces = re.split(r'\s[-–—|]\s', title)
        if len(pieces) > 1:
            suffix = pieces[-1].strip()
            if fold(suffix) == 'rj' and len(pieces) > 2:
                suffix = pieces[-2].strip() + ' - RJ'
            if any(re.search(r'\b' + re.escape(fold(city)) + r'\b', fold(suffix))
                   for city in ALLOWED_CITIES) or 'baixada fluminense' in fold(suffix):
                location = suffix
    return location.strip(), mode.strip()


def location_policy(row):
    location, mode = evidence(row)
    normalized_location = fold(location)
    region = None
    # “Estado do Rio de Janeiro” e “RJ” sozinhos não identificam um município.
    if not re.search(r'\bestado\b|\bstate\b', normalized_location):
        for city in ALLOWED_CITIES:
            # Não confunde “Niterói, Rio de Janeiro” (cidade/estado) com Rio capital.
            if re.match(r'^(?:cidade de |municipio de )?' + re.escape(fold(city)) +
                        r'(?:\s*$|\s*[-–—,/•(])', normalized_location):
                region = city
                break
        if not region and normalized_location.startswith('baixada fluminense'):
            region = 'Baixada Fluminense'
    if region:
        return {'localidade': location, 'modalidade': mode or 'Não informada',
                'criterio_local': 'Rio de Janeiro / Baixada Fluminense'}

    # Só campos específicos ou o título estabelecem modalidade; texto genérico não.
    dedicated = fold(mode + '\n' + location + '\n' + row.get('titulo', ''))
    text = fold(row.get('texto_sujo', ''))
    remote = re.search(r'\b(remot[oa]|remote|home[ -]?office|teletrabalho|work from home)\b', dedicated)
    conditional = re.search(r'hibrid|presencial|possibilidade|ocasional|parcial|negociavel|flexivel', dedicated)
    attendance = re.search(r'(?:encontros?|visitas?|reunioes?|atividades?)\s+presenciais|'
                           r'\d+\s*(?:dias?|vezes?)\s+(?:por semana\s+)?(?:no escritorio|presenciais)|'
                           r'comparecer\s+(?:ao|no)\s+escritorio', text)
    if remote and not conditional and not attendance:
        return {'localidade': location or 'Remoto', 'modalidade': '100% home office',
                'criterio_local': '100% home office'}
    return None
