"""Compare >=3 explicit baseline/candidate pairs; never controls playback."""
import argparse
import json
import re
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.core.playback_comparison import compare
from utils.drivers.coordinate_point_store import atomic_write_json


def main():
    p=argparse.ArgumentParser();p.add_argument('--pairs',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    args=p.parse_args();data=json.loads(args.pairs.read_text(encoding='utf-8-sig'))
    if data.get('schema')!='collection-paired-input-v1':raise ValueError('配对清单版本不支持')
    for key in ('baseline_revision','candidate_revision'):
        if not re.fullmatch('[0-9a-f]{7,40}',data.get(key,'')):raise ValueError('配对清单需要两版提交号')
    protected={args.pairs.resolve()}
    for pair in data['pairs']:
        for kind in ('baseline','candidate'):
            root=Path(pair[kind]).resolve()
            protected.update(root/name for name in ('summary.json','events.jsonl','trajectory.jsonl','route.json'))
    if args.output.resolve() in protected:raise ValueError('报告不能覆盖输入或回放归档')
    report=compare(data['pairs'])
    report.update(baseline_revision=data['baseline_revision'],candidate_revision=data['candidate_revision'])
    atomic_write_json(args.output,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
