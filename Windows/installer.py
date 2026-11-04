"""Experimental Windows port: find localization by name and key, never by macOS object IDs."""
from pathlib import Path
from layout_patch import patch_layout
from assembly_patch import patch_assembly
import argparse
import gzip
import json
import re
import sys
from patch_common import (BACKUP, PatchError, digest, ensure_closed, load_backup, read_asset,
                          replace_resources, restore_backup, run_safely, select_game, valid_name)

ROOT = Path(__file__).resolve().parent
TOKEN = re.compile(r'<[^>]*>|\\[nrt]|\{[^{}]*\}')


def load_translation():
    info = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    blob = (ROOT / 'translations.json.gz').read_bytes()
    if info.get('format') != 1 or digest(blob) != info['translation_sha256']:
        raise PatchError('Повреждены данные перевода.')
    content = json.loads(gzip.decompress(blob))
    if len(content['rows']) != 16426:
        raise PatchError('Неполная таблица перевода.')
    for key, row in content['rows'].items():
        if not key.startswith('^') or TOKEN.findall(row['en']) != TOKEN.findall(row['ru']) or any(c in row['ru'] for c in '\t\r\n'):
            raise PatchError('Недопустимая строка перевода: ' + key)
    return content


def translate_table(source, rows):
    lines = source.splitlines(keepends=True)
    if not lines:
        raise PatchError('Пустая языковая таблица.')
    header = lines[0].rstrip('\r\n').lstrip('\ufeff').split('\t')
    if header.count('EN') != 1:
        raise PatchError('Английская колонка EN не найдена.')
    column = header.index('EN')
    seen = set()
    result = []
    for line in lines:
        if not line.startswith('^'):
            result.append(line)
            continue
        ending = '\r\n' if line.endswith('\r\n') else ('\n' if line.endswith('\n') else ('\r' if line.endswith('\r') else ''))
        body = line[:-len(ending)] if ending else line
        cells = body.split('\t')
        key = cells[0]
        if len(cells) != len(header) or key in seen or key not in rows:
            raise PatchError('Текстовая таблица отличается от поддерживаемой версии: ' + key)
        if cells[column] != rows[key]['en']:
            raise PatchError('Английский текст отличается от поддерживаемого: ' + key +
                             '. Используйте оригинальные ресурсы совместимой версии; файлы не изменены.')
        seen.add(key)
        cells[column] = rows[key]['ru']
        result.append('\t'.join(cells) + ending)
    if seen != set(rows):
        raise PatchError('В игре отсутствуют строки поддерживаемой версии: ' + str(len(set(rows) - seen)))
    return ''.join(result)


def build_resources(data, originals=None, require_windows=True):
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.WinDLL('msvcp140.dll')
        except OSError as exc:
            raise PatchError('Нужен Microsoft Visual C++ Redistributable x64. Установите официальный пакет '
                             'https://aka.ms/vc14/vc_redist.x64.exe и повторите запуск. Файлы игры не изменены.') from exc
    try:
        import UnityPy
    except (ImportError, OSError) as exc:
        raise PatchError('Не удалось загрузить встроенный UnityPy. Распакуйте весь пакет, включая папку runtime; '
                         'при ошибке DLL установите Microsoft Visual C++ Redistributable x64. Причина: ' + str(exc)) from exc
    if UnityPy.__version__ != '1.25.4':
        raise PatchError('Нужен UnityPy 1.25.4 из комплекта установщика.')
    translation = load_translation()
    font_names = set(translation['font_names'])
    files = sorted(p for p in data.glob('*.assets') if valid_name(p.name))
    if not files:
        raise PatchError('Файлы ресурсов не найдены.')
    plans = {}
    tables = []
    roboto = None
    found_fonts = set()
    source_hashes = {}
    for path in files:
        source = originals[path.name] if originals and path.name in originals else read_asset(data, path.name)
        source_hashes[path.name] = digest(source)
        env = UnityPy.load(source)
        edits = []
        for obj in env.objects:
            if obj.type.name == 'TextAsset':
                value = obj.read()
                if value.m_Name == 'textFiles':
                    translated = translate_table(value.m_Script, translation['rows'])
                    edits.append(('text', obj.path_id, translated))
                    tables.append((path.name, obj.path_id))
            elif obj.type.name == 'Font':
                value = obj.read()
                if value.m_Name == 'Roboto-Regular':
                    font = bytes(value.m_FontData)
                    if digest(font) != translation['roboto_sha256']:
                        raise PatchError('В этой версии используется другой Roboto. Поддержку кириллицы подтвердить нельзя; файлы не изменены.')
                    roboto = font
                elif value.m_Name in font_names:
                    found_fonts.add(value.m_Name)
                    edits.append(('font', obj.path_id, None))
                elif value.m_FontData:
                    raise PatchError('Неизвестный встроенный шрифт: ' + value.m_Name + '. Требуется адаптация патча.')
        if edits:
            if require_windows and int(env.file.target_platform) not in (5, 19):
                raise PatchError('Ресурсы предназначены не для Windows: ' + path.name)
            plans[path.name] = edits
    if len(tables) != 1 or roboto is None or found_fonts != font_names:
        raise PatchError('Не найдены все ожидаемые языковые ресурсы и шрифты. Эта версия не поддерживается.')
    print('Совпали все 16 426 ключей и исходные тексты; найден шрифт с кириллицей.')
    before = {}
    after = {}
    for name, edits in plans.items():
        print('Подготовка и проверка: ' + name, flush=True)
        source = originals[name] if originals and name in originals else read_asset(data, name)
        if digest(source) != source_hashes[name]:
            raise PatchError('Ресурс изменился при проверке: ' + name)
        env = UnityPy.load(source)
        objects = {obj.path_id: obj for obj in env.objects}
        changed = {edit[1] for edit in edits}
        for kind, object_id, translated in edits:
            value = objects[object_id].read()
            if kind == 'text':
                value.m_Script = translated
            else:
                value.m_FontData = roboto
                value.m_FontNames = ['Roboto']
            value.save()
        if name == 'resources.assets':
            ui_changes = patch_layout(env)
            if len(ui_changes) < 60:
                raise PatchError('Не найдены ожидаемые элементы интерфейса. Нужна адаптация патча.')
            changed.update(ui_changes)
        output = env.file.save()
        check = UnityPy.load(output)
        old = UnityPy.load(source)
        a = {o.path_id: o for o in old.objects}
        b = {o.path_id: o for o in check.objects}
        if set(a) != set(b) or check.file.target_platform != old.file.target_platform:
            raise PatchError('Изменилась структура ресурса: ' + name)
        for object_id in a:
            if object_id not in changed and a[object_id].get_raw_data() != b[object_id].get_raw_data():
                raise PatchError('Изменился посторонний объект: ' + name)
        for kind, object_id, translated in edits:
            value = b[object_id].read()
            if (kind == 'text' and value.m_Script != translated) or (kind == 'font' and bytes(value.m_FontData) != roboto):
                raise PatchError('Записанный перевод или шрифт не совпадает: ' + name)
        before[name] = source
        after[name] = output
    name = 'Managed/Assembly-CSharp.dll'
    source = originals[name] if originals and name in originals else read_asset(data, name)
    try:
        output, edits = patch_assembly(source)
    except (ValueError, ImportError) as exc:
        raise PatchError('Не удалось подготовить подписи интерфейса: ' + str(exc)) from exc
    before[name] = source
    after[name] = output
    return before, after


def process(mode, data):
    if mode == 'restore':
        restore_backup(data)
        return
    record = load_backup(data)
    originals = None
    actual = {}
    if record:
        originals = {}
        states = []
        for name, info in record['files'].items():
            actual[name] = read_asset(data, name)
            checksum = digest(actual[name])
            if checksum not in (info['original_sha256'], info['patched_sha256']):
                raise PatchError('После установки ресурс изменился: ' + name + '. Восстановите актуальный оригинал через Steam и сохраните прежнюю резервную копию отдельно.')
            states.append(checksum == info['patched_sha256'])
            originals[name] = read_asset(data / BACKUP, name)
    if mode == 'install':
        ensure_closed(data)
    source, localized = build_resources(data, originals)
    if mode == 'check':
        print('Проверка совместимости и сборки пройдена. Файлы игры не изменены. Запуск в самой Windows-игре этим тестом не проверяется.')
        return
    before = {name: actual.get(name, value) for name, value in source.items()}
    if before == localized:
        print('Текущая редакция русификатора уже установлена; контрольные суммы совпадают.')
        return
    replace_resources(data, before, localized, source, localized)
    print('Русификатор установлен. В настройках игры оставьте English.')


def main():
    parser = argparse.ArgumentParser(description='Cyber Manhunt — экспериментальный перенос перевода на Windows')
    parser.add_argument('mode', choices=['install', 'restore', 'check'])
    parser.add_argument('game', nargs='?', help='Папка игры или *_Data')
    args = parser.parse_args()
    if sys.platform != 'win32':
        raise PatchError('Этот установщик предназначен для Windows. Для macOS используйте соседнюю папку macOS.')
    print('Cyber Manhunt — русский перевод. Windows: экспериментальная версия; запуск игры на Windows не проверялся.')
    process(args.mode, select_game(args.game, 'Windows'))


if __name__ == '__main__':
    sys.exit(run_safely(main))
