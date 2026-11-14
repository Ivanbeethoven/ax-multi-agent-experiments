"""Start A355 correctness checks using an existing remote source checkout."""
import argparse
import json
from pathlib import Path, PurePosixPath
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ssh", default="idcai-60525")
    parser.add_argument("--hostname", required=True)
    parser.add_argument("--port", type=int, default=60525)
    parser.add_argument("--source", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--cargo-home", required=True)
    parser.add_argument("--toolchain", required=True)
    parser.add_argument("--base-commit", required=True)
    parser.add_argument("--role", choices=("original", "candidate"), default="candidate")
    args = parser.parse_args()
    for name in ("source", "out", "target", "cargo_home", "toolchain"):
        path = PurePosixPath(getattr(args, name))
        if not path.is_absolute() or ".." in path.parts:
            parser.error(name + " must be an absolute remote Linux path")
    options = vars(args).copy()
    for name in ("ssh", "hostname", "port"):
        options.pop(name)
    runner = (Path(__file__).resolve().parent / "run_checks.py").read_text()
    script = """import json,os,subprocess,socket
from pathlib import Path
assert socket.gethostname()=='A355'
options=OPTIONS
out=Path(options['out'])
assert not out.exists(), 'use a new output directory for each attempt'
out.parent.mkdir(parents=True,exist_ok=True)
path=out.with_name(out.name+'-runner.py')
path.open('x').write(RUNNER)
command=['unshare','--net','--mount','python3',str(path)]
for name,value in options.items(): command.extend(['--'+name.replace('_','-'),value])
env=os.environ.copy()
env['ENOENT_HOST_NETNS']=os.readlink('/proc/self/ns/net')
env['ENOENT_HOST_MNTNS']=os.readlink('/proc/self/ns/mnt')
with out.with_name(out.name+'-launcher.log').open('xb') as log:
 child=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print(json.dumps({'pid':child.pid,'report':str(out/'report.json'),'command':command}))
""".replace("OPTIONS", repr(options)).replace("RUNNER", repr(runner))
    result = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
                             "-o", "ProxyCommand=none", "-o", "ProxyJump=none",
                             "-o", "Hostname=" + args.hostname, "-p", str(args.port), args.ssh, "python3", "-"],
                            input=script.encode(), capture_output=True, timeout=30)
    print(result.stdout.decode(), end="")
    if result.returncode:
        raise RuntimeError(result.stderr.decode())


if __name__ == "__main__":
    main()
