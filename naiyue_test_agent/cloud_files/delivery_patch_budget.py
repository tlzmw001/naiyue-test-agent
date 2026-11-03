"""Bound Git binary-patch expansion before handing untrusted bytes to Git."""
import base64,re,zlib
from naiyue_test_agent.contracts.cloud_tasks import CloudError


def _budget():raise CloudError('cloud_delivery_patch_budget',413)


def _invalid():raise CloudError('cloud_delivery_patch_invalid',409)


def _delta_sizes(prefix,limit):
    position=0;values=[]
    for _ in range(2):
        value=0
        for shift in range(0,70,7):
            if position>=len(prefix):return None
            byte=prefix[position];position+=1;value|=(byte&127)<<shift
            if value>limit:_budget()
            if not byte&128:break
        else:_invalid()
        values.append(value)
    return values


def _inflate(stream,kind,declared,file_limit):
    decoder=zlib.decompressobj();count=0;prefix=bytearray()
    while line:=stream.readline(128):
        if line==b'\n':break
        if not line.endswith(b'\n'):_invalid()
        encoded=line[:-1];marker=encoded[0] if encoded else 0
        size=marker-64 if 65<=marker<=90 else marker-70 if 97<=marker<=122 else 0
        if not size or len(encoded)!=1+((size+3)//4)*5:_invalid()
        pending=base64.b85decode(encoded[1:])[:size]
        while pending:
            output=decoder.decompress(pending,min(65536,declared-count+1));pending=decoder.unconsumed_tail
            count+=len(output)
            if count>declared:_budget()
            if kind==b'delta':
                prefix.extend(output[:max(0,20-len(prefix))]);_delta_sizes(prefix,file_limit)
            if decoder.unused_data:_invalid()
    if not decoder.eof or count!=declared:_invalid()
    if kind==b'literal':return declared
    values=_delta_sizes(prefix,file_limit)
    if values is None:_invalid()
    return values[1]


def check_patch_budget(path,input_limits,result_limits):
    blocks=declared_total=expanded_total=0;binary=False
    maximum_blocks=2*(input_limits.max_files+result_limits.max_files)
    expanded_limit=input_limits.max_input_bytes+result_limits.max_result_bytes
    try:
        with path.open('rb') as stream:
            while line:=stream.readline(128):
                if line==b'GIT binary patch\n':binary=True;continue
                header=re.fullmatch(rb'(literal|delta) ([0-9]+)\n',line) if binary else None
                if header:
                    kind,number=header.groups();declared=int(number);blocks+=1;declared_total+=declared
                    if (blocks>maximum_blocks or declared_total>expanded_limit+result_limits.max_patch_bytes
                            or declared>(input_limits.max_file_bytes if kind==b'literal' else result_limits.max_patch_bytes)):_budget()
                    expanded_total+=_inflate(stream,kind,declared,input_limits.max_file_bytes)
                    if expanded_total>expanded_limit:_budget()
                elif line.startswith(b'diff --git '):binary=False
                if not line.endswith(b'\n'):
                    while line and not line.endswith(b'\n'):line=stream.readline(128)
    except (ValueError,zlib.error,IndexError) as error:
        raise CloudError('cloud_delivery_patch_invalid',409) from error
