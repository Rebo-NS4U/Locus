import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from locus_linux.library import RouteLibrary
from locus_linux.planning import RoutePlan


class LibraryTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'routes.json'
        self.library = RouteLibrary(self.path)
        plan = RoutePlan()
        plan.reset([(35, -111), (35.001, -111), (35.002, -111)])
        plan.update(1, {'profile': 'car_roads'})
        self.plan = plan.serialize()

    def test_named_routes_survive_restart_update_rename_and_delete(self):
        identifier = self.library.save_route('Campus loop', self.plan)
        library = RouteLibrary(self.path)
        self.assertEqual(library.get(identifier)['name'], 'Campus loop')
        self.assertEqual(library.get(identifier)['document']['legs'][1]['profile'], 'car_roads')
        library.save_route('Campus commute', self.plan, identifier)
        self.assertEqual(len(library.data['routes']), 1)
        library.rename(identifier, 'Indoor walk')
        self.assertEqual(RouteLibrary(self.path).get(identifier)['name'], 'Indoor walk')
        library.delete(identifier)
        self.assertEqual(RouteLibrary(self.path).data['routes'], [])
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_draft_handles_empty_partial_and_track_routes(self):
        plan = RoutePlan()
        for points in ([], [(1, 2)], [(1, 2), (1.001, 2)]):
            plan.reset(points)
            self.library.save_draft(plan.serialize(), 'Current walk')
            draft = RouteLibrary(self.path).data
            self.assertEqual(len(draft['draft']['pins']), len(points))
            self.assertEqual(draft['draft_name'], 'Current walk')
        track = {'format': 'locus-track', 'version': 1, 'points': [(1, 2), (1.001, 2), (1.002, 2)]}
        identifier = self.library.save_route('GPX route', track)
        self.library.save_draft(track, 'GPX route', identifier)
        restored = RouteLibrary(self.path)
        self.assertEqual(restored.data['draft_route_id'], identifier)
        self.assertEqual(len(restored.get(identifier)['document']['points']), 3)

    def test_invalid_documents_and_names_do_not_damage_saved_routes(self):
        self.library.save_route('Keep me', self.plan)
        before = self.path.read_bytes()
        for name, value in (('', self.plan), ('Broken', {'format': 'bad'})):
            with self.assertRaises(ValueError):
                self.library.save_route(name, value)
        self.assertEqual(self.path.read_bytes(), before)

    def test_corrupt_library_is_not_overwritten(self):
        self.path.write_text('{broken')
        library = RouteLibrary(self.path)
        self.assertIsNotNone(library.error)
        with self.assertRaises(ValueError):
            library.save_route('New', self.plan)
        self.assertEqual(self.path.read_text(), '{broken')

    def test_failed_write_preserves_both_memory_and_saved_data(self):
        self.library.save_route('Keep', self.plan)
        before = self.path.read_bytes()
        with patch.object(Path, 'replace', side_effect=OSError('disk failure')):
            with self.assertRaises(OSError):
                self.library.save_route('New', self.plan)
        self.assertEqual(len(self.library.data['routes']), 1)
        self.assertEqual(self.path.read_bytes(), before)
