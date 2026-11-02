"""One bounded object parser for HTTP bodies, WS frames and private bootstraps."""
import json
from .cloud_tasks import CloudError


def _pairs(items):
    result={}
    for key,value in items:
        if key in result:raise ValueError('duplicate_json_key')
        result[key]=value
    return result


def _constant(value):raise ValueError('nonfinite_json')


def parse_object(data,*,byte_limit,depth_limit):
    if isinstance(data,str):
        try:data=data.encode('utf-8')
        except UnicodeError:raise CloudError('invalid_request',422) from None
    if len(data)>byte_limit:raise CloudError('request_too_large',413)
    try:
        depth,quoted,escaped=0,False,False
        for value in data:
            if quoted:
                if escaped:escaped=False
                elif value==92:escaped=True
                elif value==34:quoted=False
            elif value==34:quoted=True
            elif value in (91,123):
                depth+=1
                if depth>depth_limit:raise ValueError('json_too_deep')
            elif value in (93,125):depth-=1
        result=json.loads(data,object_pairs_hook=_pairs,parse_constant=_constant)
        if not isinstance(result,dict):raise ValueError('object_required')
        json.dumps(result,ensure_ascii=False,allow_nan=False).encode('utf-8')
        return result
    except (ValueError,RecursionError,UnicodeError):raise CloudError('invalid_request',422) from None
