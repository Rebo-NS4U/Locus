# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant panel inspired controls using the exported desktop palette."""
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
    # The panel uses rounded surfaces and subdued accent-tinted focus fills.
    # Derive interaction colors from the active export so wallpaper changes
    # update every state together, including light palettes.
    def mix(first, second, amount):
        a, b = QColor(first), QColor(second)
        return QColor(*(round(x * (1 - amount) + y * amount)
                        for x, y in zip(a.getRgb()[:3], b.getRgb()[:3]))).name()

    def alpha(key, opacity):
        color = QColor(c[key])
        return f"rgba({color.red()}, {color.green()}, {color.blue()}, {opacity})"

    style = dict(c, glass=alpha("bg", 190), card=alpha("surface", 145),
                 edge=alpha("text", 28), field=alpha("bg", 130),
                 hover=mix(c["surface"], c["accent"], .16),
                 pressed=mix(c["surface"], c["accent"], .28),
                 primary_hover=mix(c["accent"], c["text"], .12),
                 disabled=mix(c["surface"], c["bg"], .5))
    app.setStyleSheet("""
        QMainWindow { background: transparent; color: %(text)s; }
        QDialog { background: %(bg)s; color: %(text)s; }
        QWidget#shell { background: %(glass)s; border: 1px solid %(edge)s; border-radius: 22px; }
        QWidget#toolbar { background: %(card)s; border: 1px solid %(edge)s; border-radius: 18px; }
        QWidget#statusbar { background: transparent; }
        QLabel#brand { color: %(accent)s; font-size: 16px; font-weight: 700; padding: 0 8px 0 0; }
        QLabel#pageTitle { font-size: 23px; font-weight: 700; padding-top: 8px; padding-bottom: 3px; }
        QLineEdit#routeName { font-size: 16px; font-weight: 600; padding: 10px; border-color: %(edge)s; }
        QTabWidget#inspector { background: %(card)s; border: 1px solid %(edge)s; border-radius: 18px; padding: 8px; }
        QWidget#inspectorPage, QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; }
        QLabel { color: %(text)s; background: transparent; }
        QScrollBar:vertical { background: transparent; width: 7px; margin: 4px 0; }
        QScrollBar::handle:vertical { background: %(border)s; border-radius: 3px; min-height: 30px; }
        QScrollBar::handle:vertical:hover { background: %(accent)s; }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
        QSlider::groove:horizontal { background: %(border)s; height: 4px; border-radius: 2px; }
        QSlider::handle:horizontal { background: %(accent)s; width: 14px; margin: -5px 0; border-radius: 7px; }
        QProgressBar { background: %(field)s; border: 0; border-radius: 6px; text-align: center; color: %(text)s; }
        QProgressBar::chunk { background: %(accent)s; border-radius: 6px; }
        QGroupBox { background: %(card)s; border: 1px solid %(edge)s; border-radius: 18px; margin-top: 14px; padding: 16px 10px 10px; }
        QGroupBox::title { subcontrol-origin: margin; left: 16px; padding: 0 6px; color: %(accent)s; }
        QPushButton, QToolButton { background: %(card)s; color: %(text)s; border: 1px solid %(edge)s; border-radius: 16px; min-height: 20px; padding: 7px 12px; }
        QPushButton:hover, QToolButton:hover { background: %(hover)s; }
        QPushButton:pressed, QToolButton:pressed { background: %(pressed)s; border-color: %(accent)s; }
        QPushButton:checked, QToolButton:checked { background: %(accent)s; color: %(on_accent)s; border-color: %(accent)s; }
        QPushButton:focus, QToolButton:focus { border: 1px solid %(accent)s; }
        QPushButton#primary { background: %(accent)s; color: %(on_accent)s; border-color: %(accent)s; }
        QPushButton#primary:hover, QPushButton:checked:hover, QToolButton:checked:hover { background: %(primary_hover)s; }
        QPushButton#primary:pressed { background: %(primary_hover)s; border-color: %(text)s; }
        QPushButton:disabled, QPushButton#primary:disabled, QToolButton:disabled { background: %(disabled)s; color: %(muted)s; border-color: %(border)s; }
        QToolButton::menu-indicator { image: none; }
        QLineEdit, QDoubleSpinBox, QSpinBox, QComboBox, QListWidget, QTextEdit, QPlainTextEdit { background: %(field)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 12px; padding: 6px; selection-background-color: %(accent)s; selection-color: %(on_accent)s; }
        QLineEdit:focus, QDoubleSpinBox:focus, QSpinBox:focus, QComboBox:focus, QListWidget:focus { border-color: %(accent)s; }
        QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s; selection-background-color: %(accent)s; selection-color: %(on_accent)s; border: 1px solid %(border)s; }
        QListWidget::item { padding: 6px; border-radius: 8px; }
        QListWidget::item:hover { background: %(hover)s; }
        QListWidget::item:selected { background: %(pressed)s; color: %(text)s; border-left: 3px solid %(accent)s; }
        QTabWidget::pane { border: 0; }
        QTabBar::tab { background: transparent; color: %(muted)s; padding: 8px 12px; border: 1px solid transparent; border-radius: 12px; margin: 2px; }
        QTabBar::tab:hover { background: %(hover)s; color: %(text)s; }
        QTabBar::tab:selected { background: %(accent)s; color: %(on_accent)s; }
        QCheckBox, QRadioButton { color: %(text)s; spacing: 8px; }
        QCheckBox:disabled, QRadioButton:disabled { color: %(muted)s; }
        QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; border: 1px solid %(border)s; background: %(surface)s; }
        QCheckBox::indicator { border-radius: 5px; }
        QRadioButton::indicator { border-radius: 9px; }
        QCheckBox::indicator:hover, QRadioButton::indicator:hover { border-color: %(accent)s; background: %(hover)s; }
        QCheckBox::indicator:checked, QRadioButton::indicator:checked { background: %(accent)s; border: 4px solid %(on_accent)s; }
        QCheckBox::indicator:disabled, QRadioButton::indicator:disabled { background: %(disabled)s; border-color: %(muted)s; }
        QLabel#subtitle { color: %(muted)s; }
        QLabel#status { background: transparent; border-radius: 10px; padding: 4px 8px; }
        QMenu { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; border-radius: 12px; padding: 6px; }
        QMenu::item { padding: 7px 18px; border-radius: 8px; }
        QMenu::item:selected { background: %(hover)s; color: %(text)s; }
        QMenu::item:disabled { color: %(muted)s; }
        QMenu::separator { height: 1px; background: %(border)s; margin: 5px 8px; }
        QToolTip { background: %(surface)s; color: %(text)s; border: 1px solid %(border)s; }
        QScrollArea { border: 0; } QSplitter::handle { background: transparent; }
    """ % style)
    return c
