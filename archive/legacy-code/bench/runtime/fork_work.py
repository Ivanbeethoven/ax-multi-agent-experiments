"""Fixed C4 work and continuous timing; paths never determine payload bytes."""
import math

WORK_TAG='child-01'


def logical_work(files, method):
    if type(files) is not int or files<5 or method not in ('native-clone','full-independent-copy'):
        raise ValueError('Complete fork fixture and explicit baseline method required')
    copied=files*4096 if method=='full-independent-copy' else 0
    return dict(files=files,file_bytes=4096,children=1,work_tag=WORK_TAG,method=method,
        logical_read_bytes=copied+4096,
        logical_write_bytes=copied+2*len(WORK_TAG.encode())+len(b'new-child:'+WORK_TAG.encode()),
        fsync_files=files)


def validate_result(value, clock, params):
    expected=logical_work(params['files'],value['method'])
    if params['agents']!=1 or params['children']!=1 or any(value.get(k)!=v for k,v in expected.items()):
        raise ValueError('C4 fixed work differs; attempt names cannot change bytes')
    names=('create_start_ns','create_end_ns','first_read_start_ns','first_read_verified_ns','fsync_end_ns','mutation_end_ns')
    ticks=[clock[k] for k in names]
    if any(type(n) is not int or n<=0 for n in ticks) or ticks!=sorted(ticks):
        raise ValueError('Original ordered monotonic C4 clock required')
    for key,first,last in [('create_to_first_read_seconds',0,3),('create_command_seconds',0,1),
                            ('creation_fsync_seconds',3,4),('mutation_fsync_seconds',4,5),('wall_seconds',0,5)]:
        n=value.get(key)
        if type(n) not in (int,float) or not math.isfinite(n) or n<0 or not math.isclose(n,(ticks[last]-ticks[first])/1e9,rel_tol=1e-12,abs_tol=1e-12):
            raise ValueError('C4 interval must come from its original continuous clock: '+key)
    if value.get('completed') is not True or value.get('passed') is not True:
        raise ValueError('Complete successful C4 work required')
    return expected
