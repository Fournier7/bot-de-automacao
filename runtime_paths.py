"""Resolve o mesmo diretório no Agendador e no aplicativo desktop.

Aplicativos Windows empacotados podem virtualizar LocalAppData. O arquivo local
runtime_location.json contém apenas o caminho físico existente, nunca segredos.
"""
import json
import os
from pathlib import Path


def resolve_data_directory():
    override = os.environ.get('AUTOMACOES_DATA_DIR')
    if override:
        return Path(override)
    locator = Path(__file__).resolve().parent / 'runtime_location.json'
    if locator.is_file():
        location = Path(json.loads(locator.read_text(encoding='utf-8'))['data_directory'])
        if not location.is_absolute():
            raise RuntimeError('O diretório de dados deve ser um caminho absoluto.')
        return location
    return Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'IAAutomacoes'


DATA_DIR = resolve_data_directory()
CONFIG_FILE = Path(os.environ.get('AUTOMACOES_ENV_FILE', str(DATA_DIR / 'credentials.env')))
if not CONFIG_FILE.exists() and 'AUTOMACOES_ENV_FILE' not in os.environ:
    CONFIG_FILE = DATA_DIR / '.env'
