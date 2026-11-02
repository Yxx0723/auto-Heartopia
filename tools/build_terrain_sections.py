"""Reproduce vertical-wall profiles from a pinned, already-exported cooked mesh."""
import hashlib
import json
from pathlib import Path
import sys


def generate(source, output):
    from utils.core.terrain_reference import TERRAIN_EDGES
    raw = Path(source).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != '8ce4bd25cbb960c8e4e14bdaebf294eda8dc8bfa58f3157d2be58845b75deb5a':
        raise ValueError('Unexpected source mesh fingerprint')
    mesh = json.loads(raw)
    if mesh.get('schema') != 'main-terrain-mesh-v1' or mesh.get('read_only') is not True \
            or mesh.get('source') != 'mc_terrain_collider01' \
            or len(mesh['vertices']) != 6852 or len(mesh['triangles']) != 6867 \
            or len(set(mesh['face_ids'])) != 6867 or max(mesh['face_ids']) >= 6870:
        raise ValueError('Unexpected cooked terrain schema')
    grouped = {}
    for index, ids in enumerate(mesh['triangles']):
        triangle = tuple(tuple(mesh['vertices'][i]) for i in ids)
        points = sorted(set((p[0], p[2]) for p in triangle))
        if len(points) == 2:
            key = (*points[0], *points[1], min(p[1] for p in triangle), max(p[1] for p in triangle))
            grouped.setdefault(key, []).append((mesh['face_ids'][index], triangle))
    profiles, missing = {}, []
    for index, edge in enumerate(TERRAIN_EDGES):
        a, b = sorted(((edge[0], edge[1]), (edge[2], edge[3])))
        faces = grouped.get((*a, *b, edge[4], edge[5]))
        if faces:
            profiles[index] = (tuple(edge), tuple(faces))
        else:
            missing.append(index)
    text = '"""Generated vertical terrain profiles; missing matches KEEP original walls.\n'
    text += 'Source: mc_terrain_collider01, cooked identity-transform main-map geometry.\n'
    text += 'Rebuild with tools/build_terrain_sections.py and the pinned source export.\n"""\n'
    text += f'TERRAIN_SECTION_SOURCE_SHA256 = {digest!r}\n'
    text += "TERRAIN_SECTION_REVISION = 'vertical-body-sections-v1-8ce4bd25cbb9'\n"
    text += 'TERRAIN_WALL_PROFILES = {\n'
    text += ''.join(f'    {index}: {value!r},\n' for index, value in profiles.items()) + '}\n'
    Path(output).write_text(text, encoding='utf-8')
    return {'source_sha256': digest, 'matched_edges': len(profiles), 'fallback_edges': missing,
            'total_edges': len(TERRAIN_EDGES), 'output_bytes': len(text.encode())}


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    if len(sys.argv) != 3:
        raise SystemExit('Usage: build_terrain_sections.py SOURCE.json OUTPUT.py')
    print(json.dumps(generate(sys.argv[1], sys.argv[2])))
