"""Local, transactional resource replacement. Python standard library only."""
from pathlib import Path
import csv
import hashlib
import io
import json
import os
import plistlib
import re
import shlex
import shutil
import subprocess
import sys
import tempfile

BACKUP = 'CyberManhunt-RU-backup'
LOCK = '.cybermanhunt-ru-lock'


class PatchError(Exception):
    pass


def digest(data):
    return hashlib.sha256(data).hexdigest()


def valid_name(name):
    return name == 'Managed/Assembly-CSharp.dll' or bool(re.fullmatch(r'(?:sharedassets\d+|resources)\.assets', name))


def read_asset(folder, name):
    if not valid_name(name):
        raise PatchError('Недопустимое имя ресурса: ' + name)
    path = folder / name
    if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
        raise PatchError('Нужен обычный файл, а не ссылка: ' + str(path))
    return path.read_bytes()


def resolve_game(raw, platform):
    raw = raw.strip()
    path = Path(raw).expanduser()
    if not path.exists():
        if raw.startswith('"') and raw.endswith('"'):
            raw = raw[1:-1]
        elif platform == 'macOS':
            parts = shlex.split(raw)
            if len(parts) == 1:
                raw = parts[0]
        path = Path(raw).expanduser()
    if path.is_file() and path.suffix.lower() == '.exe':
        path = path.parent
    if not path.is_dir():
        raise PatchError('Папка не найдена: ' + str(path))
    candidates = [path]
    if platform == 'macOS':
        candidates += [path / 'Contents' / 'Resources' / 'Data']
        candidates += [p / 'Contents' / 'Resources' / 'Data' for p in path.glob('*.app')]
    else:
        candidates += list(path.glob('*_Data'))
    matches = sorted({p.resolve() for p in candidates if (p / 'sharedassets0.assets').is_file()
                      and (p / 'resources.assets').is_file()})
    if len(matches) != 1:
        raise PatchError('Не удалось однозначно найти ресурсы. Укажите саму .app, папку игры или её папку Data / *_Data.')
    return matches[0]


def select_game(raw, platform):
    if raw:
        return resolve_game(raw, platform)
    default = Path.home() / 'Library/Application Support/Steam/steamapps/common/CyberManhunt'
    if platform == 'macOS' and default.is_dir():
        print('Enter — использовать: ' + str(default))
    else:
        default = None
    print('Перетащите папку игры в это окно или вставьте полный путь.')
    answer = input('Путь к игре: ').strip()
    if not answer and default:
        answer = str(default)
    if not answer:
        raise PatchError('Путь не указан. Файлы не изменены.')
    return resolve_game(answer, platform)


def ensure_closed(data):
    names = {'CyberManhunt', 'Cyber Manhunt', 'cy_EA_mac'}
    if sys.platform == 'darwin':
        info = data.parent.parent / 'Info.plist'
        if info.is_file():
            with info.open('rb') as stream:
                executable = plistlib.load(stream).get('CFBundleExecutable')
                if executable:
                    names.add(executable)
        result = subprocess.run(['/usr/bin/pgrep', '-x', '|'.join(re.escape(n) for n in names)],
                                capture_output=True)
        if result.returncode == 0:
            raise PatchError('Сначала закройте Cyber Manhunt, затем повторите запуск патча.')
        if result.returncode != 1:
            raise PatchError('Не удалось проверить запущенные процессы. Файлы не изменены.')
    elif sys.platform == 'win32':
        names |= {p.stem for p in data.parent.glob('*.exe') if p.stem.lower() not in
                  {'unitycrashhandler64', 'unitycrashhandler32', 'unins000', 'uninstall'}}
        result = subprocess.run(['tasklist', '/FO', 'CSV', '/NH'], capture_output=True,
                                creationflags=0x08000000)
        if result.returncode:
            raise PatchError('Не удалось проверить запущенные процессы. Файлы не изменены.')
        running = {row[0].casefold() for row in csv.reader(io.StringIO(result.stdout.decode('utf-8', 'replace'))) if row}
        if any((n + '.exe').casefold() in running for n in names):
            raise PatchError('Сначала закройте Cyber Manhunt, затем повторите запуск патча.')


def load_backup(data):
    root = data / BACKUP
    if not root.exists():
        return None
    if root.is_symlink() or not root.is_dir():
        raise PatchError('Некорректная папка резервной копии: ' + str(root))
    try:
        record = json.loads((root / 'backup.json').read_text(encoding='utf-8'))
        if record.get('format') != 1 or record.get('game') != 'Cyber Manhunt' or not record.get('files'):
            raise ValueError('record')
        for name, hashes in record['files'].items():
            if digest(read_asset(root, name)) != hashes['original_sha256']:
                raise ValueError(name)
            if not re.fullmatch('[0-9a-f]{64}', hashes['patched_sha256']):
                raise ValueError(name)
        return record
    except (ValueError, KeyError, OSError) as exc:
        raise PatchError('Резервная копия повреждена или неполна: ' + str(root)) from exc


def backup_record(originals, targets):
    return {'format': 1, 'game': 'Cyber Manhunt', 'files': {
        name: {'original_sha256': digest(value), 'patched_sha256': digest(targets[name])}
        for name, value in originals.items()}}


def write_synced(path, content):
    if path.is_symlink() or path.parent.is_symlink():
        raise PatchError('Ссылка вместо файла резервной копии: ' + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def replace_resources(data, before, after, originals=None, localized=None):
    """All outputs are validated before entry. Backup first; rollback each replaced file on error."""
    if set(before) != set(after) or not before or not all(valid_name(n) for n in before):
        raise PatchError('Неполный набор ресурсов для замены.')
    changed = [n for n in before if before[n] != after[n]]
    if not changed:
        return False
    ensure_closed(data)
    lock = data / LOCK
    try:
        lock.mkdir()
    except FileExistsError as exc:
        raise PatchError('Другой установщик работает или оставил блокировку: ' + str(lock) +
                         '. Если установщики закрыты, удалите эту пустую папку и повторите запуск.') from exc
    stage = None
    preserve_stage = False
    try:
        for name in before:
            if read_asset(data, name) != before[name]:
                raise PatchError('Ресурсы изменились во время подготовки: ' + name)
        required = sum(len(before[n]) + len(after[n]) for n in changed) + 20 * 1024 * 1024
        if originals is not None:
            required += sum(map(len, originals.values()))
        if shutil.disk_usage(data).free < required:
            raise PatchError('Недостаточно свободного места. Освободите не менее 300 МБ.')
        existing = load_backup(data)
        record = backup_record(originals, localized) if originals is not None else None
        if record and existing:
            if not set(existing['files']).issubset(record['files']) or any(
                    record['files'][name]['original_sha256'] != info['original_sha256'] or
                    digest(before[name]) not in (info['original_sha256'], info['patched_sha256'],
                                                record['files'][name]['patched_sha256'])
                    for name, info in existing['files'].items()) or any(
                    digest(before[name]) not in (record['files'][name]['original_sha256'], record['files'][name]['patched_sha256'])
                    for name in set(record['files']) - set(existing['files'])):
                raise PatchError('Резервная копия относится к другой версии. Сохраните её отдельно, затем повторите установку.')
        stage = Path(tempfile.mkdtemp(prefix='.cmru-stage-', dir=data))
        for name in changed:
            write_synced(stage / (name + '.before'), before[name])
            write_synced(stage / (name + '.after'), after[name])
            shutil.copystat(data / name, stage / (name + '.before'))
            shutil.copystat(data / name, stage / (name + '.after'))
        if record and not existing:
            staged_backup = stage / 'backup'
            staged_backup.mkdir()
            for name, content in originals.items():
                write_synced(staged_backup / name, content)
                if digest((staged_backup / name).read_bytes()) != record['files'][name]['original_sha256']:
                    raise PatchError('Ошибка записи резервной копии: ' + name)
            write_synced(staged_backup / 'backup.json', (json.dumps(record, indent=2) + '\n').encode())
            os.replace(staged_backup, data / BACKUP)
        elif record and existing != record:
            # New components (v3 adds a display-string DLL patch) keep their originals too.
            for name in set(record['files']) - set(existing['files']):
                target = data / BACKUP / name
                if target.exists() and read_asset(data / BACKUP, name) != originals[name]:
                    raise PatchError('Посторонний файл в резервной копии: ' + name)
                write_synced(target, originals[name])
                if digest(read_asset(data / BACKUP, name)) != record['files'][name]['original_sha256']:
                    raise PatchError('Ошибка записи резервной копии: ' + name)
            write_synced(stage / 'backup.json', (json.dumps(record, indent=2) + '\n').encode())
        ensure_closed(data)
        for name in before:
            if read_asset(data, name) != before[name]:
                raise PatchError('Ресурсы изменились перед заменой: ' + name)
        completed = []
        try:
            for name in changed:
                os.replace(stage / (name + '.after'), data / name)
                completed.append(name)
            for name in after:
                if read_asset(data, name) != after[name]:
                    raise PatchError('Контрольная проверка не пройдена: ' + name)
            if record and existing and record != existing:
                # Commit metadata last: an error here rolls back resources and leaves the old record intact.
                os.replace(stage / 'backup.json', data / BACKUP / 'backup.json')
        except BaseException:
            for name in reversed(completed):
                try:
                    os.replace(stage / (name + '.before'), data / name)
                except OSError:
                    preserve_stage = True
            if preserve_stage:
                raise PatchError('Не удалось полностью отменить замену. Исходные файлы оставлены в ' + str(stage))
            raise
        return True
    finally:
        if stage and not preserve_stage:
            shutil.rmtree(stage)
        lock.rmdir()


def restore_backup(data):
    record = load_backup(data)
    if not record:
        raise PatchError('Резервная копия этого установщика не найдена. Для восстановления используйте проверку файлов в Steam.')
    before = {}
    after = {}
    for name, info in record['files'].items():
        before[name] = read_asset(data, name)
        if digest(before[name]) not in (info['original_sha256'], info['patched_sha256']):
            raise PatchError('Файл изменён обновлением или другим модом: ' + name + '. Старые ресурсы не будут установлены поверх него.')
        after[name] = read_asset(data / BACKUP, name)
    changed = replace_resources(data, before, after)
    print('Оригинальные ресурсы восстановлены.' if changed else 'Уже установлены оригинальные ресурсы.')


def run_safely(main):
    try:
        main()
    except (PatchError, OSError, ValueError, EOFError) as exc:
        print('\nОШИБКА: ' + str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nОперация прервана.', file=sys.stderr)
        return 130
    return 0
