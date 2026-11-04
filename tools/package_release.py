#!/usr/bin/env python3
"""Validate the public package and build separate deterministic platform ZIPs."""
from pathlib import Path
import argparse
import bz2
import gzip
import hashlib
import io
import json
import re
import stat
import zipfile

COMMON_DIRS = {'DeepL', 'Литературная редакция', 'Формат дат', 'Проверка подсветки'}
COMMON_FILES = {'README.md', 'NOTICE.md', 'CHANGELOG.md', 'Инструкция.txt',
                'Что изменилось.txt', 'Проверка.json', 'Публикация.json'}
EXCLUDED = {'.git', '.DS_Store', '__MACOSX', '__pycache__', 'dist'}
PATTERNS = {
    'personal DeepL glossary URL': re.compile(rb'https://(?:www\.)?deepl\.com/(?:[a-z]{2}/)?glossary/[0-9a-f-]{20,}', re.I),
    'GitHub credential': re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{25,}|github_pat_[A-Za-z0-9_]{30,})'),
    'private key': re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----'),
    'AWS access key': re.compile(rb'(?<![A-Za-z0-9+/])AKIA[0-9A-Z]{16}(?![A-Za-z0-9+/])'),
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_files(root):
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if set(rel.parts) & EXCLUDED or path.name.endswith(('.pyc', '.pyo')):
            continue
        if path.is_symlink():
            raise ValueError('Symlink is not allowed: ' + rel.as_posix())
        if path.is_file():
            yield path


def scan_data(name, data, seen, depth=0):
    digest = sha(data)
    if digest in seen:
        return
    seen.add(digest)
    for label, pattern in PATTERNS.items():
        if pattern.search(data):
            raise ValueError(f'{label} found in {name}')
    if depth >= 3:
        return
    if data.startswith(b'\x1f\x8b'):
        scan_data(name + ':gzip', gzip.decompress(data), seen, depth + 1)
    elif data.startswith(b'PK\x03\x04'):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                if not info.is_dir():
                    scan_data(name + ':' + info.filename, archive.read(info), seen, depth + 1)
    elif data.startswith(b'BSDIFF40'):
        def number(value):
            n = int.from_bytes(value, 'little') & ((1 << 63) - 1)
            return -n if value[-1] & 128 else n
        control, diff = number(data[8:16]), number(data[16:24])
        if control < 0 or diff < 0 or 32 + control + diff > len(data):
            raise ValueError('Malformed binary patch: ' + name)
        blocks = [data[32:32 + control], data[32 + control:32 + control + diff], data[32 + control + diff:]]
        for i, block in enumerate(blocks):
            scan_data(f'{name}:bsdiff{i}', bz2.decompress(block), seen, depth + 1)


def revision(root):
    numbers = []
    for platform in ('macOS', 'Windows'):
        manifest = json.loads((root / platform / 'manifest.json').read_text())
        numbers.append(manifest.get('revision'))
    if numbers[0] != numbers[1] or not isinstance(numbers[0], int):
        raise ValueError('Platform revisions differ or are missing')
    return numbers[0]


def write_zip(path, root, files):
    sums = ''.join(sha(p.read_bytes()) + '  ' + p.relative_to(root).as_posix() + '\n' for p in files)
    expected = {}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for p in files:
            name = 'Cyber Manhunt/' + p.relative_to(root).as_posix()
            data = p.read_bytes()
            info = zipfile.ZipInfo(name, (2026, 11, 4, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_DEFLATED
            mode = 0o100755 if p.stat().st_mode & 0o111 else 0o100644
            info.external_attr = mode << 16
            archive.writestr(info, data)
            expected[name] = sha(data)
        info = zipfile.ZipInfo('Cyber Manhunt/SHA256SUMS.txt', (2026, 11, 4, 0, 0, 0))
        info.create_system = 3
        info.external_attr = 0o100644 << 16
        archive.writestr(info, sums.encode())
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise ValueError('ZIP integrity error')
        for name, digest in expected.items():
            if sha(archive.read(name)) != digest:
                raise ValueError('ZIP round-trip mismatch: ' + name)
            if name.endswith('.command') and not archive.getinfo(name).external_attr >> 16 & stat.S_IXUSR:
                raise ValueError('Lost executable flag: ' + name)
    return {'name': path.name, 'size': path.stat().st_size, 'sha256': sha(path.read_bytes()), 'files': len(expected) + 1}


def build(root, output, expected_tag=None, seen=None):
    root = root.resolve()
    output = output.resolve()
    if output == root or root in output.parents and output.relative_to(root).parts[0] != 'dist':
        raise ValueError('Use dist/ or an output directory outside the repository')
    n = revision(root)
    if expected_tag and expected_tag != f'v{n}':
        raise ValueError(f'Tag {expected_tag} does not match revision {n}')
    all_files = list(source_files(root))
    cache = seen if seen is not None else set()
    for path in all_files:
        scan_data(path.relative_to(root).as_posix(), path.read_bytes(), cache)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = []
    for platform in ('macOS', 'Windows'):
        selected = []
        for path in all_files:
            rel = path.relative_to(root)
            if rel.name == 'SHA256SUMS.txt':
                continue
            if rel.parts[0] == platform or rel.parts[0] in COMMON_DIRS or len(rel.parts) == 1 and rel.name in COMMON_FILES:
                selected.append(path)
        if not any(p.relative_to(root).parts[0] == platform for p in selected):
            raise ValueError('Empty platform payload: ' + platform)
        target = output / f'Cyber-Manhunt-RU-v{n}-{platform}.zip'
        artifacts.append(write_zip(target, root, selected))
    sums = output / 'SHA256SUMS.txt'
    sums.write_text(''.join(a['sha256'] + '  ' + a['name'] + '\n' for a in artifacts))
    report = {'revision': n, 'artifacts': artifacts, 'scanned_unique_units': len(cache)}
    (output / 'publication.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--output', type=Path, default=Path('dist'))
    parser.add_argument('--tag')
    args = parser.parse_args()
    print(json.dumps(build(args.root, args.output, args.tag), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
