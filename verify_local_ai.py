"""Teste opcional de IA local com dados fictícios, sem Gmail ou Telegram.

Respeita o bloqueio de jogos e mede o pico aproximado de memória dos processos
do Ollama. RSS pode incluir páginas compartilhadas; não é consumo exclusivo exato.
"""
import json
from pathlib import Path
import threading
import time

import psutil
from common import DATA_DIR, ai_json, run_locked


def main(logger):
    finished = threading.Event()
    samples = []
    root = (DATA_DIR / 'ollama').resolve()

    def sample():
        while not finished.wait(0.5):
            total = 0
            for process in psutil.process_iter(['exe']):
                executable = process.info.get('exe')
                if not executable:
                    continue
                try:
                    if Path(executable).resolve().is_relative_to(root):
                        total += process.memory_info().rss
                except (psutil.Error, OSError):
                    pass
            samples.append(total)

    monitor = threading.Thread(target=sample, daemon=True)
    monitor.start()
    start = time.monotonic()
    try:
        schema = {'type': 'object', 'additionalProperties': False,
                  'properties': {'urgencia': {'type': 'string', 'enum': ['Alta', 'Média', 'Baixa']}},
                  'required': ['urgencia']}
        result = ai_json('Classifique publicidade como Baixa. Retorne JSON.',
                         {'assunto': 'Newsletter de ofertas', 'corpo': 'Publicidade semanal.'},
                         schema, 'teste_local')
        if result['urgencia'] != 'Baixa':
            raise RuntimeError('O modelo local falhou no exemplo básico de classificação.')
    finally:
        finished.set()
        monitor.join(timeout=2)
    summary = {'teste_local': 'OK', 'tempo_segundos': round(time.monotonic() - start, 1),
               'pico_rss_ollama_gib_aproximado': round(max(samples, default=0) / 2**30, 2),
               'modelo_descarregado_apos_resposta': True}
    print(json.dumps(summary))
    logger.info('Teste sintético da IA local concluído, sem envios externos.')


if __name__ == '__main__':
    raise SystemExit(run_locked('verify_local_ai', main))
