#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/exora --install-launcher
printf '%s\n' 'Installed. Open Exora from your application menu.'
