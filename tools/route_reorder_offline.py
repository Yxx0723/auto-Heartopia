"""Usage: python tools/route_reorder_offline.py --help"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from utils.core.route_reorder import costs_from_memory, suggest
from utils.drivers.coordinate_point_store import PointStore, atomic_write_json
from utils.drivers.coordinate_point_routes import PointRoutes


def main(argv=None):
    p=argparse.ArgumentParser(description='离线有向实走成本排序；不会操作游戏或自动修改原路线')
    p.add_argument('--route',type=Path,required=True)
    p.add_argument('--whitelist',required=True,help='允许换序的原点位编号，逗号分隔；第1点固定')
    p.add_argument('--costs',type=Path,help='route-directed-costs-v1 JSON，须绑定原路线SHA256')
    p.add_argument('--memory',type=Path,help='旧路线记忆，仅生成探索性成本')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--open-route',action='store_true')
    p.add_argument('--save-as',help='明确另存候选路线名称；不改变当前选择')
    p.add_argument('--accept-exploratory',action='store_true')
    args=p.parse_args(argv)
    raw=args.route.read_bytes();digest=hashlib.sha256(raw).hexdigest()
    route=PointStore(args.route,require_existing=True).load()
    whitelist=[int(v) for v in args.whitelist.split(',') if v.strip()]
    rows=[]
    if args.costs:
        data=json.loads(args.costs.read_text(encoding='utf-8-sig'))
        if data.get('schema')!='route-directed-costs-v1' or data.get('route_sha256')!=digest:
            raise ValueError('成本报告未绑定当前原路线SHA256')
        rows+=data['edges']
    if args.memory:rows+=costs_from_memory(route,json.loads(args.memory.read_text(encoding='utf-8-sig')))
    report=suggest(route,rows,whitelist,closed_loop=not args.open_route)
    report.update(source_sha256=digest,source=str(args.route.resolve()),saved_route=None)
    protected={args.route.resolve()}
    for path in (args.costs,args.memory):
        if path:protected.add(path.resolve())
    if args.output.resolve() in protected:raise ValueError('报告不能覆盖路线或成本输入')
    if args.save_as:
        if report['status']=='rejected' or (report['status']=='exploratory' and not args.accept_exploratory):
            raise ValueError('该建议不能另存；探索性建议须显式 --accept-exploratory')
        routes=PointRoutes(args.route)
        if routes.path(args.save_as).resolve()==args.output.resolve():raise ValueError('报告不能覆盖候选路线')
        routes.create_reordered(args.route.stem,args.save_as,report['order'],whitelist,expected_sha256=digest)
        report['saved_route']=str(routes.path(args.save_as))
    atomic_write_json(args.output,report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return report


if __name__=='__main__':main()
