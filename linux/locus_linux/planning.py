# SPDX-License-Identifier: GPL-3.0-or-later
"""Editable pin-to-pin routing document; identity survives moving a pin."""
import copy
import math
import uuid
from .engine import coordinates
from .routes import Route, MAX_POINTS, distance

MAX_PINS = 99
SHORT_WALK_METRES = 300

PROFILES = {
    "walk_sidewalks": {"label": "Walk · prefer sidewalks", "costing": "pedestrian", "speed": 5.04, "color": "#84d5c4", "options": {"sidewalk_factor": .4, "walkway_factor": .8, "use_tracks": 0, "max_hiking_difficulty": 0}},
    "walk_trails": {"label": "Walk · prefer trails", "costing": "pedestrian", "speed": 4.3, "color": "#b7d085", "options": {"walkway_factor": .5, "sidewalk_factor": 1.5, "use_tracks": 1, "max_hiking_difficulty": 3}},
    "run_paths": {"label": "Run · paths", "costing": "pedestrian", "speed": 11.88, "color": "#84d5c4", "options": {"walking_speed": 11.88, "walkway_factor": .8}},
    "car_roads": {"label": "Car · roads", "costing": "auto", "speed": 30, "color": "#ffb77e", "options": {"exclude_unpaved": True, "use_tracks": 0}},
    "car_tracks": {"label": "Car · prefer dirt tracks", "costing": "auto", "speed": 15, "color": "#dec56e", "options": {"exclude_unpaved": False, "use_tracks": 1, "use_highways": 0, "use_tolls": 0}},
    "offroad_walk": {"label": "Walk · free off-road path", "costing": None, "speed": 4.3, "color": "#c5cb96", "options": {}},
    "offroad_car": {"label": "Car · free off-road path", "costing": None, "speed": 15, "color": "#ffb595", "options": {}},
    "cycle_paths": {"label": "Cycle · paths", "costing": "bicycle", "speed": 23.4, "color": "#cebdfe", "options": {"use_roads": .1}},
}

# Allow ordinary walking connections without making sidewalk detours cheaper.
PROFILES["walk_auto"] = {**PROFILES["walk_sidewalks"], "label": "Walking",
                         "options": {"sidewalk_factor": 1, "walkway_factor": .9,
                                     "driveway_factor": 1, "alley_factor": 1,
                                     "use_tracks": 1, "max_hiking_difficulty": 1}}

# Keep existing profile IDs so saved projects retain their routing settings.
PROFILES["run_roads"] = {**PROFILES["run_paths"], "label": "Run · prefer sidewalks", "options": {"walking_speed": 11.88, "sidewalk_factor": .4, "walkway_factor": .8, "use_tracks": 0, "max_hiking_difficulty": 0}}
PROFILES["cycle_roads"] = {**PROFILES["cycle_paths"], "label": "Cycle · prefer roads", "options": {"use_roads": 1}}

TRAVEL_PATHS = {
    "Walk": (("Roads / sidewalks", "walk_sidewalks"), ("Trails / paths", "walk_trails"), ("Free off-road", "offroad_walk")),
    "Run": (("Roads / sidewalks", "run_roads"), ("Trails / paths", "run_paths")),
    "Cycle": (("Roads", "cycle_roads"), ("Trails / paths", "cycle_paths")),
    "Drive": (("Roads", "car_roads"), ("Dirt tracks", "car_tracks"), ("Free off-road", "offroad_car")),
}


# Legacy profiles remain readable in saved projects; new edits need only a mode.
TRAVEL_MODES = {"Walking": "walk_auto", "Driving": "car_roads", "Cycling": "cycle_paths"}


def travel_mode(profile):
    if profile.startswith(("car_", "offroad_car")):
        return "Driving"
    if profile.startswith("cycle_"):
        return "Cycling"
    return "Walking"



def specification(value=None, default="walk_auto"):
    value = value or {}
    profile = value.get("profile", default)
    if profile not in PROFILES:
        raise ValueError("Unknown routing profile")
    speed_mode = value.get("speed_mode", "auto")
    if speed_mode not in ("auto", "manual"):
        raise ValueError("Choose automatic or manual speed")
    speed = float(value.get("speed_kph", PROFILES[profile]["speed"]))
    if not math.isfinite(speed) or not .36 <= speed <= 360:
        raise ValueError("Segment speed must be 0.36–360 km/h")
    controls = [coordinates(*p) for p in value.get("controls", [])]
    limit = 10 if PROFILES[profile]["costing"] else 1000
    if len(controls) > limit:
        raise ValueError(f"This profile supports up to {limit} bends between pins")
    return {"profile": profile, "speed_mode": speed_mode, "speed_kph": speed, "controls": controls}


class RoutePlan:
    def __init__(self):
        self.pins = []
        self.settings = {}
        self.results = {}
        self.selected = {}
        self.revision = 0

    @property
    def points(self):
        return [p["point"] for p in self.pins]

    def key(self, index):
        return (self.pins[index]["id"], self.pins[index + 1]["id"])

    def spec(self, index):
        return copy.deepcopy(self.settings.get(self.key(index), specification()))

    def change(self):
        self.revision += 1
        keys = {self.key(i) for i in range(max(0, len(self.pins) - 1))}
        self.settings = {k: v for k, v in self.settings.items() if k in keys}
        self.results = {k: v for k, v in self.results.items() if k in keys}
        self.selected = {k: v for k, v in self.selected.items() if k in keys}

    def add(self, point, default="walk_auto"):
        if len(self.pins) >= MAX_PINS:
            raise ValueError(f"A route supports up to {MAX_PINS} main pins")
        self.pins.append({"id": uuid.uuid4().hex, "point": coordinates(*point)})
        if len(self.pins) > 1:
            self.settings[self.key(len(self.pins) - 2)] = specification(default=default)
        self.change()

    def reset(self, points):
        self.pins, self.settings, self.results, self.selected = [], {}, {}, {}
        for point in points:
            self.add(point)
        self.change()

    def insert(self, index, point, default="walk_auto"):
        if not 0 <= index <= len(self.pins) or len(self.pins) >= MAX_PINS:
            raise ValueError(f"Insert a pin within a route of at most {MAX_PINS} pins")
        point = coordinates(*point)
        inherited = self.spec(index - 1) if 0 < index < len(self.pins) else None
        left_controls, right_controls = [], []
        if inherited:
            path = [self.points[index - 1], *inherited["controls"], self.points[index]]
            edge = min(range(len(path) - 1), key=lambda i: distance(path[i], point) + distance(point, path[i + 1]) - distance(path[i], path[i + 1]))
            left_controls, right_controls = inherited["controls"][:edge], inherited["controls"][edge:]
        self.pins.insert(index, {"id": uuid.uuid4().hex, "point": point})
        if index > 0:
            self.settings[self.key(index - 1)] = {**(inherited or specification(default=default)), "controls": left_controls}
        if index < len(self.pins) - 1:
            self.settings[self.key(index)] = {**(inherited or specification(default=default)), "controls": right_controls}
        self.change()

    def move(self, index, point):
        self.pins[index]["point"] = coordinates(*point)
        for leg in (index - 1, index):
            if 0 <= leg < len(self.pins) - 1:
                self.results.pop(self.key(leg), None)
        self.change()

    def remove(self, index):
        self.pins.pop(index)
        self.change()

    def reorder(self, index, target):
        self.pins[index], self.pins[target] = self.pins[target], self.pins[index]
        self.change()

    def update(self, index, value):
        key = self.key(index)
        validated = specification(value)
        if self.spec(index) == validated:
            return
        self.settings[key] = validated
        self.results.pop(key, None)
        self.selected.pop(key, None)
        self.change()

    def requests(self):
        return [{"start": self.points[i], "end": self.points[i + 1], "spec": self.spec(i)} for i in range(len(self.pins) - 1)]

    def accept(self, result, revision):
        if revision != self.revision:
            raise ValueError("Route changed while planning. Plan again to use your edits.")
        if len(result) != len(self.pins) - 1:
            raise ValueError("Planner returned an incomplete route")
        for i, options in enumerate(result):
            if not options:
                raise ValueError(f"No route for segment {i + 1}")
            self.results[self.key(i)] = options
            self.selected[self.key(i)] = min(self.selected.get(self.key(i), 0), len(options) - 1)

    def select(self, index, alternative):
        options = self.results.get(self.key(index), [])
        if not 0 <= alternative < len(options):
            raise ValueError("Unknown segment alternative")
        self.selected[self.key(index)] = alternative

    def segments(self):
        result = []
        for i in range(len(self.pins) - 1):
            spec = self.spec(i)
            profile = PROFILES[spec["profile"]]
            options = self.results.get(self.key(i), [])
            choice = options[self.selected.get(self.key(i), 0)] if options else None
            points = choice["points"] if choice else [self.points[i], *spec["controls"], self.points[i + 1]]
            stops = [{"point": points[stop['index']], "kind": stop['kind']} for stop in choice.get('stops', [])] if choice else []
            result.append({"points": points, "controls": spec["controls"], "color": profile["color"], "label": profile["label"], "ready": bool(options), "connectors": choice.get("connectors", []) if choice else [], "stops": stops})
        return result

    def assemble(self):
        points, speeds, sources, driving, motorized, stops = [], [], [], [], [], []
        for i in range(len(self.pins) - 1):
            key = self.key(i)
            if not self.results.get(key):
                raise ValueError(f"Plan segment {i + 1} before starting")
            option = self.results[key][self.selected.get(key, 0)]
            shape = [coordinates(*p) for p in option["points"]]
            if points and distance(points[-1], shape[0]) > .05:
                raise ValueError("Segments do not join at the shared pin; replan the route")
            offset = len(points) - 1 if points else 0
            stops.extend({'index': stop['index'] + offset, 'kind': stop['kind']} for stop in option.get('stops', []))
            points.extend(shape if not points else shape[1:])
            speeds.extend(option["speeds"])
            sources.extend(option["sources"])
            driving.extend(option["driving"])
            motorized.extend(option.get('motorized', option['driving']))
            if len(points) > MAX_POINTS:
                raise ValueError("Combined route exceeds the point limit")
        Route(points, speeds=speeds, sources=sources, driving=driving, motorized=motorized, stops=stops)
        return {"points": points, "speeds": speeds, "sources": sources, "driving": driving, "motorized": motorized, "stops": stops}

    def serialize(self):
        return {"format": "locus-route", "version": 1, "pins": copy.deepcopy(self.pins), "legs": [self.spec(i) for i in range(len(self.pins) - 1)]}

    @classmethod
    def deserialize(cls, value):
        if value.get("format") != "locus-route" or value.get("version") != 1:
            raise ValueError("Not a supported Locus route project")
        pins, legs = value.get("pins", []), value.get("legs", [])
        if not 2 <= len(pins) <= MAX_PINS or len(legs) != len(pins) - 1:
            raise ValueError(f"Project needs 2–{MAX_PINS} pins and one setting per segment")
        plan = cls()
        for pin in pins:
            plan.add(pin["point"])
        for i, spec in enumerate(legs):
            plan.update(i, spec)
        return plan
