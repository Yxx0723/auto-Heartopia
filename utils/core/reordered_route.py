"""Validated provenance and isolated memory namespace for offline reordered routes."""
import hashlib
import json
import re
from copy import deepcopy


def point_digest(point):
    value=deepcopy(point);value.pop('id',None)
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,
        separators=(',',':'),allow_nan=False).encode()).hexdigest()


def isolation_id(data):
    meta=data.get('reordered_route')
    if meta is None:return None
    if not isinstance(meta,dict) or meta.get('schema')!='reordered-route-v1':
        raise ValueError('优化路线隔离信息无效')
    identity=meta.get('isolation_id')
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):
        raise ValueError('优化路线记忆标识无效')
    source=meta.get('source_sha256')
    if not isinstance(source,str) or not re.fullmatch('[0-9a-f]{64}',source):
        raise ValueError('优化路线来源摘要无效')
    mapping=meta.get('new_to_old');points=data.get('points',[])
    if not isinstance(mapping,list) or any(type(i)is not int for i in mapping) \
            or sorted(mapping)!=list(range(1,len(points)+1)):
        raise ValueError('优化路线点位映射无效')
    fingerprints=meta.get('source_points')
    if not isinstance(fingerprints,dict) or any(
            fingerprints.get(str(old))!=point_digest(point) for old,point in zip(mapping,points)):
        raise ValueError('优化路线内容与映射不一致，请重新生成候选路线')
    return identity
