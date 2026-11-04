#!/usr/bin/env python3
"""Publish verified archives without storing credentials or replacing assets."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request


def credential():
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        return token
    result = subprocess.run(['git', 'credential', 'fill'],
        input='protocol=https\nhost=github.com\n\n', text=True, capture_output=True,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'}, timeout=30)
    fields = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode or not fields.get('password'):
        raise RuntimeError('GitHub authentication is required; use GH_TOKEN or Git credential helper')
    return fields['password']


def request(token, method, url, data=None, content_type='application/json', missing=False):
    if not url.startswith(('https://api.github.com/', 'https://uploads.github.com/')):
        raise ValueError('Unsupported API host')
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': content_type,
        'User-Agent': 'cyber-manhunt-rus-release'})
    try:
        with urllib.request.urlopen(req, timeout=180) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if missing and error.code == 404:
            return None
        raise RuntimeError(f'GitHub API {method} failed with HTTP {error.code}') from None


def publish(root, artifacts, tag, repository, latest=False):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repository):
        raise ValueError('Invalid repository')
    if not re.fullmatch(r'v[1-9][0-9]*', tag):
        raise ValueError('Invalid revision tag')
    n = int(tag[1:])
    if json.loads((root / 'macOS/manifest.json').read_text())['revision'] != n:
        raise ValueError('Tag does not match package revision')
    if json.loads((root / 'Windows/manifest.json').read_text())['revision'] != n:
        raise ValueError('Tag does not match Windows revision')
    report = json.loads((artifacts / 'publication.json').read_text())
    if report['revision'] != n:
        raise ValueError('Artifact revision mismatch')
    expected = {a['name']: a['sha256'] for a in report['artifacts']}
    if set(expected) != {f'Cyber-Manhunt-RU-{tag}-macOS.zip', f'Cyber-Manhunt-RU-{tag}-Windows.zip'}:
        raise ValueError('Both platform archives are required')
    expected['SHA256SUMS.txt'] = hashlib.sha256((artifacts / 'SHA256SUMS.txt').read_bytes()).hexdigest()
    for name, digest in expected.items():
        if hashlib.sha256((artifacts / name).read_bytes()).hexdigest() != digest:
            raise ValueError('Artifact checksum mismatch: ' + name)
    notes = (root / 'release-notes' / (tag + '.md')).read_text()
    title = notes.splitlines()[0].lstrip('# ').strip()
    token = credential()
    base = 'https://api.github.com/repos/' + repository
    # A release must point at an existing, verified version tag.
    ref = request(token, 'GET', base + '/git/ref/tags/' + tag)
    release = request(token, 'GET', base + '/releases/tags/' + tag, missing=True)
    if release is None:
        release = request(token, 'POST', base + '/releases', {
            'tag_name': tag, 'name': title, 'body': notes, 'draft': True,
            'prerelease': False, 'make_latest': 'true' if latest else 'false'})
    existing = {a['name']: a for a in release.get('assets', [])}
    for name, digest in expected.items():
        if name in existing:
            if existing[name].get('digest') != 'sha256:' + digest:
                raise RuntimeError('Existing release asset differs; refusing to overwrite: ' + name)
            continue
        upload = release['upload_url'].split('{', 1)[0] + '?name=' + urllib.parse.quote(name)
        content_type = 'application/zip' if name.endswith('.zip') else 'text/plain'
        asset = request(token, 'POST', upload, (artifacts / name).read_bytes(), content_type)
        if asset.get('size') != (artifacts / name).stat().st_size or asset.get('digest') != 'sha256:' + digest:
            raise RuntimeError('Uploaded release asset checksum mismatch: ' + name)
    release = request(token, 'PATCH', base + '/releases/' + str(release['id']), {
        'draft': False, 'make_latest': 'true' if latest else 'false'})
    return {'tag': tag, 'url': release['html_url'], 'assets': [
        {'name': a['name'], 'size': a['size'], 'digest': a.get('digest'), 'url': a['browser_download_url']}
        for a in release['assets']], 'tag_object': ref['object']['sha']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--artifacts', type=Path, default=Path('dist'))
    parser.add_argument('--tag', required=True)
    parser.add_argument('--repository', default='PhoenixDeveloper/cyber-manhunt-rus')
    parser.add_argument('--latest', action='store_true')
    args = parser.parse_args()
    print(json.dumps(publish(args.root, args.artifacts, args.tag, args.repository, args.latest),
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

