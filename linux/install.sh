#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/locus-linux --install-launcher
printf '%s\n' 'Installed. Open Locus Linux from your application menu.'
