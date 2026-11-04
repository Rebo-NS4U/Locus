# SPDX-License-Identifier: GPL-3.0-or-later
"""Private, atomic route library and resumable working draft."""
import copy
import json
import uuid
from .engine import coordinates
from .planning import RoutePlan
from .routes import Route


def document(value):
    value = copy.deepcopy(value)
    if value.get('format') == 'locus-track' and value.get('version') == 1:
        return {'format': 'locus-track', 'version': 1, 'points': Route(value['points']).points}
    if value.get('format') != 'locus-route' or value.get('version') != 1:
        raise ValueError('Unsupported saved route')
    if len(value.get('pins', [])) >= 2:
        return RoutePlan.deserialize(value).serialize()
    if value.get('legs') != []:
        raise ValueError('Incomplete route settings')
    plan = RoutePlan()
    for pin in value.get('pins', []):
        plan.add(coordinates(*pin['point']))
        plan.set_departure(len(plan.pins) - 1, pin.get('departure'))
    return plan.serialize()


class RouteLibrary:
    def __init__(self, path):
        self.path = path
        self.data = {'version': 1, 'routes': [], 'draft': None}
        self.error = None
        try:
            if path.exists():
                loaded = json.loads(path.read_text())
                if loaded.get('version') != 1 or not isinstance(loaded.get('routes'), list):
                    raise ValueError('Unsupported route library')
                ids = set()
                for item in loaded['routes']:
                    if not isinstance(item['id'], str) or not item['id'] or item['id'] in ids or not isinstance(item['name'], str) or not item['name'].strip():
                        raise ValueError('Invalid saved route')
                    ids.add(item['id'])
                    document(item['document'])
                if not isinstance(loaded.get('draft_name', ''), str) or not isinstance(loaded.get('draft_route_id', ''), (str, type(None))):
                    raise ValueError('Invalid draft metadata')
                if loaded.get('draft') is not None:
                    document(loaded['draft'])
                self.data = loaded
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            self.error = f'Could not read route library: {error}'

    def write(self, data):
        if self.error:
            raise ValueError(self.error)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = self.path.with_suffix('.tmp')
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        temporary.chmod(0o600)
        temporary.replace(self.path)
        self.data = data

    def save_route(self, name, value, identifier=None):
        name = name.strip()[:200]
        if not name:
            raise ValueError('Give this route a name')
        value = document(value)
        if value['format'] == 'locus-route' and len(value['pins']) < 2:
            raise ValueError('Add at least two stops before saving')
        data = copy.deepcopy(self.data)
        if identifier is None:
            identifier = uuid.uuid4().hex
            data['routes'].insert(0, {'id': identifier, 'name': name, 'document': value})
        else:
            item = next(item for item in data['routes'] if item['id'] == identifier)
            item.update(name=name, document=value)
        self.write(data)
        return identifier

    def get(self, identifier):
        return copy.deepcopy(next(item for item in self.data['routes'] if item['id'] == identifier))

    def rename(self, identifier, name):
        return self.save_route(name, self.get(identifier)['document'], identifier)

    def delete(self, identifier):
        data = copy.deepcopy(self.data)
        data['routes'] = [item for item in data['routes'] if item['id'] != identifier]
        self.write(data)

    def save_draft(self, value, name="", identifier=None):
        data = copy.deepcopy(self.data)
        data['draft'] = document(value)
        data['draft_name'] = name
        data['draft_route_id'] = identifier
        self.write(data)
