# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import math
import os
import tempfile
from pathlib import Path
from .engine import coordinates


class Places:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"favorites": [], "recents": [], "settings": {}}
        self.error = None
        try:
            loaded = json.loads(path.read_text())
            if not isinstance(loaded, dict):
                raise ValueError('Expected a places/settings object')
            data = {key: loaded.get(key, value) for key, value in self.data.items()}
            self.validate(data)
            self.data = data
        except FileNotFoundError:
            pass
        except (OSError, ValueError, KeyError, TypeError) as error:
            self.error = f'Could not read favorites/settings at {path}: {error}. Original file preserved; repair it before saving.'
        self._saved_data = copy.deepcopy(self.data)

    @staticmethod
    def validate(data):
        for group in ('favorites', 'recents'):
            if not isinstance(data[group], list):
                raise ValueError('Expected a list of places')
            for place in data[group]:
                if not isinstance(place, dict) or not isinstance(place.get('name'), str):
                    raise ValueError('Invalid saved place')
                point = place.get('point')
                if not isinstance(point, (list, tuple)) or len(point) != 2 or any(type(value) not in (int, float) for value in point):
                    raise ValueError('Invalid saved coordinates')
                try:
                    coordinates(*point)
                except OverflowError as error:
                    raise ValueError('Invalid saved coordinates') from error
        settings = data['settings']
        if not isinstance(settings, dict):
            raise ValueError('Expected a settings object')
        for key in ('photon', 'valhalla', 'overpass', 'travel_mode'):
            if key in settings and not isinstance(settings[key], str):
                raise ValueError(f'Invalid {key} setting')
        for key, (lower, upper) in {'speed_kph': (0.36, 360), 'red_probability': (0, 100),
                                    'motion_revision': (0, 2**31 - 1),
                                    'default_latitude': (-90, 90), 'default_longitude': (-180, 180)}.items():
            if key in settings and (type(settings[key]) not in (int, float) or
                                    not lower <= settings[key] <= upper or not math.isfinite(settings[key])):
                raise ValueError(f'Invalid {key} setting')
        for key in ('variation', 'night_map', 'follow', 'sidebar_visible', 'reconnect',
                    'notifications', 'traffic_stops', 'setup_seen'):
            if key in settings and not isinstance(settings[key], bool):
                raise ValueError(f'Invalid {key} setting')

    def save(self, data=None):
        temporary = None
        try:
            if self.error:
                raise ValueError(self.error)
            data = copy.deepcopy(self.data if data is None else data)
            self.validate(data)
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='.' + self.path.name + '.', suffix='.tmp', delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(self.path)
        except Exception:
            self.data = copy.deepcopy(self._saved_data)
            raise
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        self.data = data
        self._saved_data = copy.deepcopy(data)

    def update_settings(self, **settings):
        data = copy.deepcopy(self.data)
        data['settings'].update(settings)
        self.save(data)

    def add(self, group, name, point):
        point = coordinates(*point)
        if group not in ("favorites", "recents"):
            raise ValueError("Unknown place list")
        name = name.strip()[:200] or f"{point[0]:.5f}, {point[1]:.5f}"
        data = copy.deepcopy(self.data)
        places = data[group]
        places[:] = [p for p in places if tuple(p["point"]) != point]
        places.insert(0, {"name": name, "point": point})
        if group == "recents":
            del places[20:]
        self.save(data)

    def remove(self, group, index):
        data = copy.deepcopy(self.data)
        data[group].pop(index)
        self.save(data)

    def rename(self, group, index, name):
        if not name.strip():
            raise ValueError("Give this place a name.")
        data = copy.deepcopy(self.data)
        data[group][index]["name"] = name.strip()[:200]
        self.save(data)
