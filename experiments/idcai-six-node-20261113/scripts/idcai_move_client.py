"""Call AX's cold reopen on the target over the existing WireGuard network."""
import json
from pathlib import Path
import re
import shlex
import subprocess as sp
import time
from urllib.parse import urlsplit

from bench.runtime.move_target import digest, different_host, identity


def reopen(*, ssh_config, host, repo, tools, product, binary, expected_tree,
           config, redis_url, endpoint, env, out, check_timeout=300):
    if (urlsplit(redis_url).hostname, urlsplit(endpoint).hostname) != ('10.77.13.24', '10.77.13.21'):
        raise ValueError('C4 target must use the common private Redis and RustFS endpoints')
    cmd = ['ssh','-F',str(ssh_config),'-o','BatchMode=yes','-o','ConnectTimeout=10',host]
    remote = 'cd ' + shlex.quote(str(repo)) + ' && python3 -m bench.runtime.move_target'
    source = identity()
    target = json.loads(sp.run(cmd+[remote+' --identity'],check=True,capture_output=True,text=True,timeout=30).stdout)
    different_host(source,target)
    request = dict(source_identity=source,tools=str(tools),product=product,check_timeout=check_timeout,
                   binary_sha256={product:digest(binary)},expected_tree=expected_tree,
                   config=config,redis_url=redis_url,env={key:env[key] for key in
                   ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY','AWS_REGION','AWS_EC2_METADATA_DISABLED','NO_PROXY')})
    run_ids = [part for part in Path(out).parts if re.fullmatch(r'ax-four-[0-9a-f]{12}', part)]
    if len(run_ids) != 1:
        raise ValueError('C4 output must identify one owned run for process metrics')
    request['env']['AX_IDCAI_RUN'] = run_ids[0]
    started = time.monotonic_ns()
    response = sp.run(cmd+[remote],input=json.dumps(request),capture_output=True,text=True,timeout=check_timeout+240)
    try:
        result = json.loads(response.stdout)
    except json.JSONDecodeError:
        result = {'passed':False,'error':'target did not return a JSON receipt','target_exit_code':response.returncode}
    result.update(source_identity=source,transport='private WireGuard, shared endpoints, no payload copy',
                  physical_move=True,controller_elapsed_seconds=(time.monotonic_ns()-started)/1e9)
    # Do not leak credentials if a subprocess includes its argv in an error.
    raw = json.dumps(result,indent=2)+'\n'
    for value in (env['AWS_ACCESS_KEY_ID'],env['AWS_SECRET_ACCESS_KEY'],urlsplit(redis_url).password):
        if value:
            raw=raw.replace(value,'[redacted]')
    (out/'move.json').write_text(raw)
    if response.returncode or not result.get('passed'):
        raise RuntimeError('C4 target did not pass verification; see move.json')
    return json.loads(raw)
