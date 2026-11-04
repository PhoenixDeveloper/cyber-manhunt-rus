#!/bin/zsh
set -u
script_dir=${0:A:h}
patch_mode=${1:-check}
if (( $# )); then shift; fi
python_bin=''
for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; raise SystemExit(sys.version_info < (3,9))' >/dev/null 2>&1; then
        python_bin=$candidate
        break
    fi
done
if [[ -z "$python_bin" ]]; then
    print 'Нужен Python 3.9 или новее: https://www.python.org/downloads/macos/'
    read '?Нажмите Enter для закрытия…'
    exit 1
fi
PYTHONDONTWRITEBYTECODE=1 "$python_bin" "$script_dir/installer.py" "$patch_mode" "$@"
patch_status=$?
if [[ -t 0 ]]; then read '?Нажмите Enter для закрытия…'; fi
exit "$patch_status"
