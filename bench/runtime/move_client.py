"""Keep Redis/S3 private while a second host opens the same filesystem."""
import json
import shlex
import subprocess as sp
import time
from urllib.parse import urlsplit

from bench.runtime.move_target import identity, different_host, digest


def reopen(*, ssh_config, host, repo, tools, product, binary,
           expected_tree, config, redis_url, endpoint, env, out, check_timeout=300):
    base=['ssh','-F',str(ssh_config),'-o','BatchMode=yes','-o','ConnectTimeout=15',host]
    remote='cd '+shlex.quote(str(repo))+' && python3 -m bench.runtime.move_target'
    target=json.loads(sp.run(base+[remote+' --identity'],check=True,capture_output=True,text=True,timeout=30).stdout)
    source=identity(); different_host(source,target)
    ports=[urlsplit(redis_url).port,urlsplit(endpoint).port]
    if not all(ports) or len(set(ports))!=2:
        raise ValueError('Distinct explicit Redis and S3 ports required')
    forwards=[part for port in ports for part in ['-R',f'127.0.0.1:{port}:127.0.0.1:{port}']]
    command=base[:-1]+['-o','ExitOnForwardFailure=yes','-N',*forwards,host]
    tunnel=None
    result={}
    with (out/'tunnel.log').open('w') as log:
        try:
            tunnel=sp.Popen(command,stdin=sp.DEVNULL,stdout=log,stderr=log)
            # ExitOnForwardFailure handles remote conflicts; explicitly test both ports.
            probe='import socket; '+ '; '.join(
                f'socket.create_connection(("127.0.0.1",{port}),timeout=2).close()' for port in ports)
            ready=False
            for _ in range(20):
                if tunnel.poll() is not None:
                    raise RuntimeError('SSH tunnel failed; see tunnel.log')
                checked=sp.run(base+['python3 -c '+shlex.quote(probe)],capture_output=True,timeout=15)
                if checked.returncode==0:
                    ready=True;break
                time.sleep(.25)
            if not ready:
                raise RuntimeError('Target backend forwards not ready')
            request=dict(source_identity=source,tools=str(tools),product=product,check_timeout=check_timeout,
                binary_sha256={product:digest(binary)},
                expected_tree=expected_tree,config=config,redis_url=redis_url,
                env={name:env[name] for name in ('AWS_ACCESS_KEY_ID','AWS_SECRET_ACCESS_KEY',
                     'AWS_REGION','AWS_EC2_METADATA_DISABLED','NO_PROXY')})
            started=time.monotonic_ns()
            response=sp.run(base+[remote],input=json.dumps(request),capture_output=True,text=True,
                            timeout=check_timeout+240)
            result=json.loads(response.stdout)
            result.update(source_identity=source,transport='SSH reverse loopback forwards',
                physical_move=True,controller_elapsed_seconds=(time.monotonic_ns()-started)/1e9)
            if response.returncode or not result.get('passed'):
                (out/'move.json').write_text(json.dumps(result,indent=2)+'\n')
                raise RuntimeError('Target verification failed; see move.json')
        finally:
            if tunnel is not None:
                tunnel.terminate()
                try: tunnel.wait(timeout=5)
                except sp.TimeoutExpired: tunnel.kill();tunnel.wait()
            result['tunnel_exited']=tunnel is None or tunnel.poll() is not None
            (out/'move.json').write_text(json.dumps(result,indent=2)+'\n')
    return result
