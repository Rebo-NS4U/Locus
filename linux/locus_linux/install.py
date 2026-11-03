# SPDX-License-Identifier: GPL-3.0-or-later
"""Install a per-user menu entry pointing to this Python environment."""
import os
import shutil
import sys
from pathlib import Path


def install_launcher(data_home=None, executable=None):
    data = Path(data_home) if data_home else Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    executable = str(executable or Path(sys.prefix) / "bin/locus-linux")
    if any(c in executable for c in "\n\r"):
        raise ValueError("Unsupported executable path")
    quoted = '"' + executable.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%') + '"'
    applications = data / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    icons = data / "icons/hicolor/scalable/apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent / "web/locus.svg", icons / "locus-linux.svg")
    desktop = applications / "locus-linux.desktop"
    desktop.write_text("[Desktop Entry]\nType=Application\nName=Locus Linux\nComment=iPhone location simulation, routes and joystick\n"
                       f"Exec={quoted}\nIcon=locus-linux\nTerminal=false\nCategories=Utility;\nStartupWMClass=locus-linux\n")
    return desktop
