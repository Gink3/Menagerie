"""Local originals plus a versioned, atomic manifest for future API uploads."""
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import uuid


def timestamp():
    return datetime.now(timezone.utc).isoformat()


class Session:
    def __init__(self, root, identifier, kind, camera, mode):
        identifier = identifier.strip()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', identifier):
            raise ValueError('Use a UUID or RFID UID containing only letters, numbers, underscores or hyphens.')
        ident = str(uuid.uuid4())
        self.path = Path(root).expanduser() / identifier / ident
        self.path.mkdir(parents=True, exist_ok=False)
        self.data = dict(schema_version=1, id=ident, miniature_identifier=identifier,
                         identifier_kind=kind, created_at=timestamp(), status='capturing',
                         mode=mode, camera=camera, images=[])
        self.write()

    def write(self):
        temporary = self.path / 'session.tmp'
        temporary.write_text(json.dumps(self.data, indent=2) + '\n')
        temporary.replace(self.path / 'session.json')

    def photo_path(self, angle):
        name = 'manual' if angle is None else f'{angle:03d}deg'
        return self.path / f'{name}_{uuid.uuid4().hex[:8]}.png'

    def add(self, event, angle, position):
        self.data['images'].append(dict(original_path=Path(event['path']).name,
                                       angle_degrees=angle, motor_position=position,
                                       width=event['width'], height=event['height'],
                                       captured_at=timestamp(), capture=event.get('capture')))
        self.write()

    def finish(self, status, reason=''):
        self.data.update(status=status, finished_at=timestamp(), reason=reason)
        self.write()
