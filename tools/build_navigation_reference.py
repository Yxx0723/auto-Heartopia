"""Convert the layered local extraction into bounded advisory navigation data.

No process access, target API, network, or route edits. Game geometry remains
in the local ignored runtime directory, not in the public source repository.
The collision heightfield is deliberately not interpreted as a walkable mesh.
"""
import argparse
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from utils.core.navigation_reference import validate_reference


def build_reference(source):
    source = Path(source)
    manifest = json.loads((source / 'manifest.json').read_text(encoding='utf-8'))
    result = {'schema': 'navigation-reference-v1', 'scene': 'startown',
              'game_hash': manifest['game_hash'], 'bounds_xz': manifest['bounds_xz'], 'layers': []}
    for stem, kind, provenance in (
            ('mountain_walls', 'mountain', 'cooked source mesh; same-name placement; active shape unverified'),
            ('water_surfaces', 'water', 'visual CPU mesh and linked transform; swimming boundary unverified'),
            ('bridge_collisions', 'bridge', 'SceneColliderData mesh reference and transform; active shape unverified')):
        vertices, triangles = [], []
        with (source / (stem+'.obj')).open(encoding='utf-8') as stream:
            for line in stream:
                parts = line.split()
                if parts and parts[0] == 'v':
                    vertices.append([float(v) for v in parts[1:]])
                elif parts and parts[0] == 'f':
                    triangles.append([int(i)-1 for i in parts[1:]])
        expected = manifest['layers'][stem]
        if len(vertices) != expected['vertices'] or len(triangles) != expected['triangles']:
            raise ValueError('Export manifest geometry count mismatch: '+stem)
        result['layers'].append({'kind': kind, 'source': provenance,
                                 'vertices': vertices, 'triangles': triangles})
    return validate_reference(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    result = build_reference(args.source)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix('.tmp')
    temporary.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False), encoding='utf-8')
    temporary.replace(args.output)
    print(json.dumps({'revision': result['revision'], 'layers': len(result['layers']),
                      'triangles': sum(len(v['triangles']) for v in result['layers']),
                      'bytes': args.output.stat().st_size}))


if __name__ == '__main__':
    main()
