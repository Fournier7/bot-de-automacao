"""Protege o jogo consultando nomes de processos, sem acessar memória do Valorant.

Se o jogo abrir durante uma execução, encerra somente subprocessos destas
automações e o Ollama portátil instalado por este projeto. Não toca no jogo,
Vanguard, Riot Client nem em navegadores pessoais.
"""
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
import os
import threading
import json
import re
import winreg

import psutil
from runtime_paths import DATA_DIR

GAMES = {
    'steam.exe', 'steamwebhelper.exe', 'gameoverlayui.exe',
    'riotclientservices.exe', 'riotclientux.exe', 'riotclientuxrender.exe',
    'valorant.exe', 'valorant-win64-shipping.exe',
    'leagueclient.exe', 'leagueclientux.exe', 'leagueclientuxrender.exe',
    'league of legends.exe',
    'robloxplayerbeta.exe', 'robloxplayerlauncher.exe', 'robloxstudiobeta.exe',
    # Conservador: outros launchers também impedem trabalho em segundo plano.
    'epicgameslauncher.exe', 'epicwebhelper.exe', 'goggalaxy.exe', 'galaxyclient.exe',
    'battle.net.exe', 'ubisoftconnect.exe', 'upc.exe',
    'eadesktop.exe', 'ealauncher.exe', 'origin.exe', 'minecraft.windows.exe',
    'minecraftlauncher.exe', 'playnite.desktopapp.exe', 'playnite.fullscreenapp.exe',
    'gamingapp.exe', 'xboxpcapp.exe',
}
# Programas utilitários distribuídos pela Steam não são jogos.
NON_GAMES = {'wallpaper64.exe', 'wallpaper32.exe', 'wallpaperui.exe'}
_paused = threading.Event()
PROJECT_DIR = Path(__file__).resolve().parent


class GamePaused(RuntimeError):
    pass


@lru_cache(maxsize=1)
def game_locations():
    """Pastas de launchers, bibliotecas Steam/Epic e jogos registrados no Windows.

    Sem lista universal de jogos: instalações portáteis não registradas precisam
    de pasta adicional em GAME_EXTRA_DIRS ou do arquivo de pausa manual.
    """
    local = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
    roots = [Path('C:/Riot Games'), Path('C:/XboxGames'), Path('C:/Games'),
             local / 'Roblox']
    files = set()
    for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
        base = Path(os.environ.get(variable, 'C:/Program Files'))
        roots.extend([base / 'Epic Games', base / 'Steam' / 'steamapps' / 'common',
                      base / 'GOG Galaxy' / 'Games', base / 'EA Games'])
    for extra in os.environ.get('GAME_EXTRA_DIRS', '').split(';'):
        if extra.strip():
            roots.append(Path(extra.strip()))
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam') as key:
            steam_path = Path(winreg.QueryValueEx(key, 'SteamPath')[0])
        roots.append(steam_path / 'steamapps' / 'common')
        manifest = steam_path / 'steamapps' / 'libraryfolders.vdf'
        if manifest.is_file():
            for path in re.findall(r'"path"\s+"([^"]+)"', manifest.read_text(encoding='utf-8')):
                roots.append(Path(path.replace('\\\\', '\\')) / 'steamapps' / 'common')
    except (OSError, ValueError):
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'System\GameConfigStore\Children') as key:
            for index in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, winreg.EnumKey(key, index)) as game:
                        location = winreg.QueryValueEx(game, 'MatchedExeFullPath')[0]
                    if location:
                        files.add(os.path.normcase(os.path.abspath(location)))
                except OSError:
                    continue
    except OSError:
        pass
    epic = Path(os.environ.get('PROGRAMDATA', 'C:/ProgramData')) / 'Epic' / 'EpicGamesLauncher' / 'Data' / 'Manifests'
    try:
        for manifest in epic.glob('*.item'):
            try:
                location = json.loads(manifest.read_text(encoding='utf-8')).get('InstallLocation')
                if location:
                    roots.append(Path(location))
            except (OSError, ValueError):
                continue
    except OSError:
        pass
    return [path.resolve() for path in roots], files


def game_running():
    if (DATA_DIR / 'PAUSED').exists():
        return True
    roots, files = game_locations()
    try:
        for process in psutil.process_iter(['name', 'exe']):
            name = (process.info.get('name') or '').lower()
            if name in NON_GAMES:
                continue
            if name in GAMES:
                return True
            executable = process.info.get('exe')
            if executable:
                location = Path(executable).resolve()
                if os.path.normcase(str(location)) in files or any(location.is_relative_to(root) for root in roots):
                    return True
        return False
    except psutil.Error:
        raise GamePaused('Não foi possível verificar o jogo; execução adiada.') from None


def check_game():
    if _paused.is_set() or game_running():
        raise GamePaused('Jogo, launcher ou pausa manual detectado; execução adiada.')


def stop_our_workloads():
    allowed = [(DATA_DIR / 'ollama').resolve(), (PROJECT_DIR / '.venv').resolve()]

    def owned(process):
        try:
            location = Path(process.exe()).resolve()
            return any(location.is_relative_to(root) for root in allowed)
        except (psutil.Error, OSError):
            return False

    targets = {}
    # Somente filhos do Python desta execução, com binário dentro do projeto.
    for child in psutil.Process().children(recursive=True):
        if owned(child):
            targets[child.pid] = child
    # O servidor compartilhado pode ter sido iniciado por uma execução anterior.
    ollama_exe = (DATA_DIR / 'ollama' / 'ollama.exe').resolve()
    for process in psutil.process_iter(['name']):
        if (process.info.get('name') or '').lower() != 'ollama.exe':
            continue
        try:
            if Path(process.exe()).resolve() == ollama_exe:
                for child in process.children(recursive=True):
                    if owned(child):
                        targets[child.pid] = child
                targets[process.pid] = process
        except (psutil.Error, OSError):
            continue
    for process in targets.values():
        try:
            process.terminate()
        except psutil.Error:
            pass


@contextmanager
def guard_game(logger):
    _paused.clear()
    check_game()
    finished = threading.Event()

    def monitor():
        while not finished.wait(2):
            try:
                blocked = game_running()
            except GamePaused:
                blocked = True  # Falha de detecção bloqueia o trabalho.
            if blocked:
                _paused.set()
                logger.info('Jogo detectado; interrompendo os processos da automação.')
                stop_our_workloads()
                return

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    try:
        yield
        check_game()
    finally:
        finished.set()
        watcher.join(timeout=3)


def was_game_pause():
    return _paused.is_set()
