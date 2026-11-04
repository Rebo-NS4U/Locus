import configparser
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from locus_linux.install import install_launcher
from locus_linux.theme import read_colors, read_button_style


class DesktopTests(unittest.TestCase):
    def test_shell_button_settings_and_invalid_file_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            theme = root / 'rebos_shell/config/theme.json'
            theme.parent.mkdir(parents=True)
            theme.write_text(json.dumps({'shellStyle': 'colorful', 'roundness': 16}))
            self.assertEqual(read_button_style(root), {'radius': 14.6, 'mode': 'colorful'})
            theme.write_text(json.dumps({'shellStyle': 'custom', 'roundness': 10}))
            self.assertEqual(read_button_style(root)['radius'], 6)
            theme.write_text('{invalid')
            self.assertEqual(read_button_style(root), {'radius': 14.6, 'mode': 'glass'})

    def test_theme_reads_active_desktop_palette_and_updates(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'qt6ct').mkdir()
            palette = root / 'colors.conf'
            settings = root / 'qt6ct/qt6ct.conf'
            settings.write_text(f'[Appearance]\ncolor_scheme_path={palette}\n')
            colors = ['#ff112233'] * 22
            colors[12] = '#ffaabbcc'
            palette.write_text('[ColorScheme]\nactive_colors=' + ','.join(colors))
            self.assertEqual(read_colors(root)['accent'], '#aabbcc')
            colors[12] = '#ffddeeff'
            palette.write_text('[ColorScheme]\nactive_colors=' + ','.join(colors))
            self.assertEqual(read_colors(root)['accent'], '#ddeeff')

    def test_installer_quotes_paths_and_ships_icon(self):
        with tempfile.TemporaryDirectory() as directory:
            desktop = install_launcher(directory, '/tmp/Locus test/bin/locus-linux')
            self.assertIn('Exec=env -u LD_PRELOAD "/tmp/Locus test/bin/locus-linux"', desktop.read_text())
            self.assertTrue((Path(directory) / 'icons/hicolor/scalable/apps/locus-linux.svg').exists())
            subprocess.run(['desktop-file-validate', str(desktop)], check=True)


if __name__ == '__main__':
    unittest.main()
