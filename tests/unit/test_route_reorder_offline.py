import hashlib
import json
import pytest
from tests.unit.test_coordinate_player import manual_points
from utils.core.route_reorder import suggest
from tools.route_reorder_offline import main
from utils.drivers.coordinate_point_routes import PointRoutes


def rows():
    values={(1,2):10,(2,3):10,(3,4):10,(4,1):10,(1,3):2,(3,2):2,(2,4):2}
    return [dict(**{'from':a,'to':b},seconds=[v,v,v],level='L1',scene_verified=True,
                 walk_completed=True) for (a,b),v in values.items()]


def test_directed_measured_costs_choose_anchored_swap():
    report=suggest(manual_points([(i,0,0) for i in range(4)]),rows(),[2,3])
    assert report['status']=='recommended' and report['order']==[1,3,2,4]
    assert report['baseline']['estimated_total_seconds']==40
    assert report['candidate']['estimated_total_seconds']==16


def test_unknown_or_legacy_edges_cannot_be_recommended():
    route=manual_points([(i,0,0) for i in range(4)])
    values=rows();values=[r for r in values if (r['from'],r['to'])!=(4,1)]
    report=suggest(route,values,[2,3])
    assert report['status']=='exploratory'
    assert report['candidate']['estimated_total_seconds'] is None
    assert report['candidate']['unknown_edges']==[[4,1]]
    values=rows()
    for r in values:r['level']='L2'
    assert suggest(route,values,[2,3])['status']=='exploratory'


def test_reverse_edges_are_not_inferred_and_anchors_partition_swaps():
    route=manual_points([(i,0,0) for i in range(4)])
    report=suggest(route,rows()[:1],[2,3])
    assert [2,1] not in [list((e['source'],e['target'])) for e in report['baseline']['known_edges']]
    assert suggest(route,rows(),[2])['order']==[1,2,3,4]


@pytest.mark.parametrize('value',[0,-1,float('nan'),True])
def test_invalid_costs_are_rejected(value):
    values=rows();values[0]['seconds']=[value]
    with pytest.raises(ValueError):suggest(manual_points([(i,0,0) for i in range(4)]),values,[2,3])


def test_cli_publishes_isolated_candidate_but_keeps_original_bytes(tmp_path):
    routes=PointRoutes(tmp_path/'original.json')
    routes.create('original',manual_points([(i,0,0) for i in range(4)]))
    path=routes.path('original');before=path.read_bytes()
    costs=tmp_path/'costs.json'
    costs.write_text(json.dumps({'schema':'route-directed-costs-v1',
        'route_sha256':hashlib.sha256(before).hexdigest(),'edges':rows()}))
    report=main(['--route',str(path),'--whitelist','2,3','--costs',str(costs),
        '--output',str(tmp_path/'report.json'),'--save-as','candidate'])
    assert report['status']=='recommended' and path.read_bytes()==before
    assert routes.store('candidate').load()['reordered_route']['new_to_old']==[1,3,2,4]
