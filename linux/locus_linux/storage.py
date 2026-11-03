# SPDX-License-Identifier: GPL-3.0-or-later
import json
from pathlib import Path
from .engine import coordinates


class Places:
    def __init__(self, path: Path):
        self.path = path
        self.data = {"favorites": [], "recents": [], "settings": {}}
        if path.exists():
            loaded = json.loads(path.read_text())
            for key in self.data:
                if key in loaded:
                    self.data[key] = loaded[key]
            for group in ("favorites", "recents"):
                for place in self.data[group]:
                    coordinates(*place["point"])

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.path)

    def add(self, group, name, point):
        point = coordinates(*point)
        if group not in ("favorites", "recents"):
            raise ValueError("Unknown place list")
        name = name.strip()[:200] or f"{point[0]:.5f}, {point[1]:.5f}"
        places = self.data[group]
        places[:] = [p for p in places if tuple(p["point"]) != point]
        places.insert(0, {"name": name, "point": point})
        if group == "recents":
            del places[20:]
        self.save()

    def remove(self, group, index):
        self.data[group].pop(index)
        self.save()

    def rename(self, group, index, name):
        if not name.strip():
            raise ValueError("Give this place a name.")
        self.data[group][index]["name"] = name.strip()[:200]
        self.save()
