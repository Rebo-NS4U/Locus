# SPDX-License-Identifier: GPL-3.0-or-later
"""Follow the user's desktop palette, with a portable warm fallback."""
import configparser
import re
from pathlib import Path
from PySide6.QtGui import QColor, QFont, QPalette

DEFAULT = {"bg": "#423429", "surface": "#46372a", "raised": "#684e3a", "text": "#efdfd6",
           "muted": "#9e8d82", "accent": "#ffb77e", "on_accent": "#4d2600", "border": "#70543d"}


def read_colors(config=None):
    root = Path(config) if config else Path.home() / ".config"
    colors = DEFAULT.copy()
    parser = configparser.ConfigParser()
    parser.read(root / "qt6ct/qt6ct.conf")
    palette_file = parser.get("Appearance", "color_scheme_path", fallback="")
    if palette_file:
        palette = configparser.ConfigParser()
        palette.read(Path(palette_file).expanduser())
        values = palette.get("ColorScheme", "active_colors", fallback="").split(",")
        for key, index in {"text": 0, "surface": 1, "raised": 2, "bg": 9, "accent": 12, "on_accent": 13, "muted": 20, "border": 4}.items():
            if len(values) > index and re.fullmatch(r"#[0-9a-fA-F]{8}", values[index].strip()):
                colors[key] = "#" + values[index].strip()[-6:]
    return colors


def apply_theme(app, colors=None):
    c = colors or read_colors()
    app.setStyle("Fusion")
    palette = QPalette()
    for role, key in [(QPalette.Window, "bg"), (QPalette.WindowText, "text"), (QPalette.Base, "surface"),
                      (QPalette.AlternateBase, "raised"), (QPalette.Text, "text"), (QPalette.Button, "surface"),
                      (QPalette.ButtonText, "text"), (QPalette.Highlight, "accent"), (QPalette.HighlightedText, "on_accent"),
                      (QPalette.ToolTipBase, "surface"), (QPalette.ToolTipText, "text"), (QPalette.Link, "accent")]:
        palette.setColor(role, QColor(c[key]))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor(c["muted"]))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor(c["muted"]))
    app.setPalette(palette)
    config = configparser.ConfigParser()
    config.read(Path.home() / ".config/qt6ct/qt6ct.conf")
    font = config.get("Fonts", "general", fallback='"Noto Sans,11"').strip('"').split(",")
    app.setFont(QFont(font[0], int(font[1]) if len(font) > 1 and font[1].isdigit() else 11))
    app.setStyleSheet("""
        QMainWindow, QDialog { background: %(bg)s; color: %(text)s; }
        QGroupBox { border: 1px solid %(border)s; border-radius: 12px; margin-top: 14px; padding: 16px 10px 10px; font-weight: 600; }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 5px; color: %(accent)s; }
        QPushButton, QToolButton { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 7px; padding: 6px 9px; }
        QPushButton:hover { background: %(raised)s; border-color: %(accent)s; }
        QPushButton:pressed, QPushButton:checked { background: %(accent)s; color: %(on_accent)s; }
        QPushButton:disabled { color: %(muted)s; }
        QPushButton#primary { background: %(accent)s; color: %(on_accent)s; font-weight: 600; }
        QLineEdit, QDoubleSpinBox, QComboBox, QListWidget { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 7px; padding: 5px; selection-background-color: %(accent)s; selection-color: %(on_accent)s; }
        QTabWidget::pane { border: 0; } QTabBar::tab { padding: 10px 13px; border-bottom: 2px solid transparent; }
        QTabBar::tab:selected { color: %(accent)s; border-bottom-color: %(accent)s; }
        QLabel#subtitle { color: %(muted)s; } QLabel#status { background: %(surface)s; border-radius: 5px; padding: 3px 6px; font-size: 12px; }
        QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; padding: 4px; }
        QMenu::item { padding: 7px 18px; } QMenu::item:selected { background: %(raised)s; color: %(accent)s; }
        QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; }
        QScrollArea { border: 0; } QSplitter::handle { background: %(bg)s; }
    """ % c)
    return c
