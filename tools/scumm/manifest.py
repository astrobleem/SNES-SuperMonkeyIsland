"""SCUMM resource manifest generation."""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)


def generate_manifest(output_dir: Path, index_data, rooms_extracted: dict,
                      global_scripts: int, global_sounds: int,
                      global_costumes: int, global_charsets: int) -> dict:
    """Generate a manifest.json summarizing all extracted resources.

    Args:
        output_dir: root extraction directory
        index_data: parsed IndexData
        rooms_extracted: dict of room_id → extraction info
        global_scripts/sounds/costumes/charsets: total counts

    Returns:
        The manifest dict
    """
    manifest = {
        'game': 'The Secret of Monkey Island (CD Talkie)',
        'scumm_version': 5,
        'index': {
            'num_rooms': len(index_data.room_names),
            'num_scripts': index_data.num_scripts,
            'num_sounds': index_data.num_sounds,
            'num_costumes': index_data.num_costumes,
            'num_charsets': index_data.num_charsets,
            'num_objects': len(index_data.object_owner_state),
            'maxs': index_data.maxs,
        },
        'extraction': {
            'rooms_extracted': len(rooms_extracted),
            'total_backgrounds': sum(
                1 for r in rooms_extracted.values() if r.get('background')),
            'total_object_images': sum(
                r.get('object_images', 0) for r in rooms_extracted.values()),
            'total_scripts': global_scripts,
            'total_sounds': global_sounds,
            'total_costumes': global_costumes,
            'total_charsets': global_charsets,
        },
        'rooms': {},
    }

    for room_id, info in sorted(rooms_extracted.items()):
        room_name = index_data.room_names.get(room_id, f'room_{room_id}')
        manifest['rooms'][str(room_id)] = {
            'name': room_name,
            'directory': info.get('directory', ''),
            'background': info.get('background', False),
            'dimensions': info.get('dimensions', None),
            'object_images': info.get('object_images', 0),
            'num_objects': info.get('num_objects', 0),
            'scripts': info.get('scripts', 0),
            'costumes': info.get('costumes', 0),
            'sounds': info.get('sounds', 0),
            'charsets': info.get('charsets', 0),
        }

    manifest_path = output_dir / 'manifest.json'
    with open(manifest_path, 'w') as f:
        json.dump(manifest, f, indent=2)
    log.info("Manifest saved → %s", manifest_path)

    return manifest
