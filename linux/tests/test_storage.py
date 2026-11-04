import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from locus_linux.storage import Places


class PlacesRecoveryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'places.json'

    def test_corrupt_state_opens_with_defaults_and_is_not_overwritten(self):
        invalid = ['{broken', '[]', 'null', '{"favorites": {}}',
                   '{"recents": [{"name": "Bad", "point": [91, 0]}]}',
                   '{"favorites": [{"name": "Bad", "point": ["1", 0]}]}',
                   json.dumps({'favorites': [{'name': 'Bad', 'point': [10**400, 0]}]}),
                   '{"settings": []}', '{"settings": {"speed_kph": "fast"}}',
                   '{"settings": {"speed_kph": NaN}}',
                   '{"settings": {"red_probability": 999999999999999999999}}',
                   '{"settings": {"photon": []}}']
        for content in invalid:
            with self.subTest(content=content):
                self.path.write_text(content)
                places = Places(self.path)
                self.assertIsNotNone(places.error)
                self.assertEqual(places.data, {'favorites': [], 'recents': [], 'settings': {}})
                with self.assertRaises(ValueError):
                    places.add('favorites', 'New', (1, 2))
                self.assertEqual(self.path.read_text(), content)

    def test_failed_mutations_preserve_memory_and_disk(self):
        places = Places(self.path)
        places.add('favorites', 'Keep', (1, 2))
        before = copy.deepcopy(places.data)
        saved = self.path.read_bytes()
        operations = [lambda: places.add('favorites', 'New', (3, 4)),
                      lambda: places.rename('favorites', 0, 'Changed'),
                      lambda: places.remove('favorites', 0),
                      lambda: places.update_settings(notifications=False)]
        for operation in operations:
            with self.subTest(operation=operation), patch.object(Path, 'replace', side_effect=OSError('disk failure')):
                with self.assertRaises(OSError):
                    operation()
                self.assertEqual(places.data, before)
                self.assertEqual(self.path.read_bytes(), saved)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_failed_legacy_save_rolls_back_direct_memory_edits(self):
        places = Places(self.path)
        places.add('favorites', 'Keep', (1, 2))
        before = copy.deepcopy(places.data)
        places.data['settings']['notifications'] = False
        with patch.object(Path, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                places.save()
        self.assertEqual(places.data, before)

    def test_valid_partial_state_and_settings_survive_restart(self):
        self.path.write_text(json.dumps({'favorites': [{'name': 'Keep', 'point': [1, 2]}]}))
        places = Places(self.path)
        self.assertIsNone(places.error)
        places.update_settings(notifications=False, speed_kph=5)
        restored = Places(self.path)
        self.assertEqual(restored.data['favorites'][0]['name'], 'Keep')
        self.assertEqual(restored.data['settings'], {'notifications': False, 'speed_kph': 5})
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_unreadable_file_is_reported_without_attempting_write(self):
        self.path.write_text('{}')
        with patch.object(Path, 'read_text', side_effect=PermissionError('denied')):
            places = Places(self.path)
        self.assertIsNotNone(places.error)
        with self.assertRaises(ValueError):
            places.save()
        self.assertEqual(self.path.read_text(), '{}')
