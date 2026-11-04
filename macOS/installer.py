#!/usr/bin/env python3
"""Portable macOS Cyber Manhunt patch. No pip packages required."""
from pathlib import Path
import argparse
import json
import re
import subprocess
import sys
import tempfile
from patch_common import PatchError, digest, ensure_closed, read_asset, replace_resources, run_safely, select_game

ROOT = Path(__file__).resolve().parent


def load_payload():
    manifest = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != 1 or manifest.get('platform') != 'macOS' or set(manifest['files']) != {'sharedassets0.assets', 'resources.assets', 'Managed/Assembly-CSharp.dll', 'StreamingAssets/DataConfig/tnt_deploy_data44.data'}:
        raise PatchError('Неподдерживаемый манифест патча.')
    patches = {}
    for name, entry in manifest['files'].items():
        for kind in ('original', 'patched'):
            if not re.fullmatch('[0-9a-f]{64}', entry[kind + '_sha256']) or not 0 < entry[kind + '_size'] < 300 * 1024 * 1024:
                raise PatchError('Повреждён манифест патча.')
        for mode in ('install', 'restore'):
            info = entry[mode]
            filename = Path(name).name + '.' + mode + '.bsdiff'
            if info['file'] != filename:
                raise PatchError('Некорректное имя файла патча.')
            content = (ROOT / 'payload' / filename).read_bytes()
            if len(content) != info['size'] or digest(content) != info['sha256']:
                raise PatchError('Повреждён файл патча: ' + filename)
            patches[name, mode] = content
        for previous in entry.get('previous', []):
            if not re.fullmatch('[0-9a-f]{64}', previous['sha256']) or not 0 < previous['size'] < 300 * 1024 * 1024:
                raise PatchError('Повреждён манифест предыдущего перевода.')
            info = previous['restore']
            if not re.fullmatch(re.escape(Path(name).name) + r'\.restore-v[123456]\.bsdiff', info['file']):
                raise PatchError('Некорректное имя патча предыдущего перевода.')
            content = (ROOT / 'payload' / info['file']).read_bytes()
            if len(content) != info['size'] or digest(content) != info['sha256']:
                raise PatchError('Повреждён патч предыдущего перевода.')
            patches[name, previous['sha256']] = content
    return manifest, patches


def apply_delta(source, patch, target_hash, target_size):
    if not Path('/usr/bin/bspatch').is_file():
        raise PatchError('Системная утилита /usr/bin/bspatch не найдена. Этот пакет предназначен для macOS.')
    with tempfile.TemporaryDirectory(prefix='cybermanhunt-ru-') as work:
        folder = Path(work)
        (folder / 'source').write_bytes(source)
        (folder / 'change').write_bytes(patch)
        result = subprocess.run(['/usr/bin/bspatch', str(folder / 'source'), str(folder / 'target'),
                                 str(folder / 'change')], capture_output=True)
        if result.returncode or not (folder / 'target').is_file():
            raise PatchError('Не удалось применить двоичный патч. Ресурсы игры не заменены.')
        output = (folder / 'target').read_bytes()
    if len(output) != target_size or digest(output) != target_hash:
        raise PatchError('Результат патча не прошёл контрольную проверку. Ресурсы игры не заменены.')
    return output


def process(mode, data):
    manifest, patches = load_payload()
    before = {}
    states = {}
    for name, entry in manifest['files'].items():
        before[name] = read_asset(data, name)
        checksum = digest(before[name])
        if checksum == entry['original_sha256']:
            states[name] = 'original'
        elif checksum == entry['patched_sha256']:
            states[name] = 'patched'
        elif any(checksum == p['sha256'] and len(before[name]) == p['size'] for p in entry.get('previous', [])):
            states[name] = checksum
        else:
            raise PatchError('Несовместимый ресурс: ' + name + '. Нужна macOS-сборка с ресурсами версии 1.3.93 / Steam 9369524. '
                             'Обновлённые файлы или сторонний мод требуют другого патча; ничего не изменено.')
    print('Ресурсы: ' + str(data))
    for name, state in states.items():
        label = {'original': 'оригинал', 'patched': 'русский перевод, редакция ' + str(manifest['revision'])}.get(state, 'предыдущий перевод — доступно обновление')
        print('  ' + name + ': ' + label)
    if mode == 'check':
        print('Проверка пройдена. Версия совместима; файлы не изменены.')
        return
    target = 'patched' if mode == 'install' else 'original'
    if all(state == target for state in states.values()):
        print('Русификатор уже установлен.' if mode == 'install' else 'Оригинал уже восстановлен.')
        return
    ensure_closed(data)
    after = {}
    originals = {}
    localized = {}
    for name, entry in manifest['files'].items():
        source = before[name]
        if states[name] == 'original':
            originals[name] = source
        else:
            restore_key = 'restore' if states[name] == 'patched' else states[name]
            originals[name] = apply_delta(source, patches[name, restore_key], entry['original_sha256'], entry['original_size'])
        localized[name] = source if states[name] == 'patched' else apply_delta(
            originals[name], patches[name, 'install'], entry['patched_sha256'], entry['patched_size'])
        after[name] = localized[name] if mode == 'install' else originals[name]
    replace_resources(data, before, after, originals if mode == 'install' else None,
                      localized if mode == 'install' else None)
    print('Русификатор установлен. В настройках игры оставьте English.' if mode == 'install'
          else 'Оригинальные ресурсы восстановлены.')


def main():
    parser = argparse.ArgumentParser(description='Cyber Manhunt — переносимый русификатор macOS')
    parser.add_argument('mode', choices=['install', 'restore', 'check'])
    parser.add_argument('game', nargs='?', help='Папка игры, .app или Contents/Resources/Data')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        raise PatchError('Этот установщик предназначен для macOS. Для Windows используйте соседнюю папку Windows.')
    print('Cyber Manhunt 1.3.93 — русская локализация (macOS)')
    process(args.mode, select_game(args.game, 'macOS'))


if __name__ == '__main__':
    sys.exit(run_safely(main))
