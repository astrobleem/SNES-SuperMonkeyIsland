"""SCUMM v5 costume extraction — raw binary export."""

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def extract_costumes(room_resource, output_dir: Path) -> int:
    """Extract costume resources from a room's trailing COST chunks.

    Costumes are saved as raw binary for phase 2 decoding.

    Returns:
        Number of costumes extracted
    """
    costs = room_resource.get_trailing('COST')
    if not costs:
        return 0

    costumes_dir = output_dir / 'costumes'
    costumes_dir.mkdir(parents=True, exist_ok=True)
    count = 0

    for i, cost in enumerate(costs):
        path = costumes_dir / f'cost_{i:03d}.bin'
        path.write_bytes(cost.data)
        count += 1
        log.debug("Room %d: saved costume %d (%d bytes)",
                  room_resource.room_id, i, len(cost.data))

    if count > 0:
        log.info("Room %d: saved %d costumes", room_resource.room_id, count)
    return count
