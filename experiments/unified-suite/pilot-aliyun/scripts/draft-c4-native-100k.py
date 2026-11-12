#!/usr/bin/env python3
"""Draft current C4 one-child source and timing specs on the actual executor."""
import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO))
from bench.experiment_step import prepare
from bench.fork_burst import SOURCE_BOUND, native_argv, native_step
from bench.fork_source import QUALIFIED, REQUEST, preflight
from bench.runtime.common import digest, load, spec, write_new
from bench.runtime.fork_release import OPT0, verify as verify_release

TREE_SHA = '02aeda272edbab9512595f7b968d840a9cfdc0e8302ed036399bf4151f08bd13'
BINARIES = {
    'brewfs-fork': ('brewfs', OPT0),
    'juicefs-clone': ('juicefs', '6d450d3c151b6affede364d9a198d2505111b0612a5fb4d31613444c635174cc'),
}
AWS_ENV = ('AWS_ACCESS_KEY_ID', 'AWS_SECRET_ACCESS_KEY', 'AWS_REGION', 'AWS_EC2_METADATA_DISABLED')


def ref(path, expected=None):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
        raise ValueError('Absolute canonical input file required')
    sha = digest(path)
    if expected is not None and sha != expected:
        raise ValueError('Pinned input differs: '+path.name)
    return {'path':str(path), 'sha256':sha}


def output(path):
    path = Path(path)
    if (not path.is_absolute() or path.resolve() != path or not path.parent.is_dir()
            or path.exists() or path.is_symlink()):
        raise ValueError('New canonical absolute output with an existing parent required')
    return path


def named(text):
    if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{1,63}', text):
        raise ValueError('Simple private attempt/child name required')
    return text


def release_binding(args, method, binary_ref):
    binary, sha = BINARIES[method]
    if Path(binary_ref['path']).name != binary:
        raise ValueError('Binary basename must match the native product')
    review, review_sha = getattr(args, 'release_review', None), getattr(args, 'release_review_sha256', None)
    if review is None and review_sha is None:
        if binary_ref['sha256'] != sha:
            raise ValueError('Pinned qualification executable differs; optimized BrewFS requires release review')
        return None
    if method != 'brewfs-fork' or not review or not review_sha:
        raise ValueError('Both release review path and SHA-256 are required for BrewFS only')
    return verify_release(ref(review, review_sha), binary_ref)


def engine(args, method):
    binary = ref(args.binary)
    return binary, release_binding(args, method, binary)


def release_inputs(binding):
    return () if binding is None else (binding['review'], binding['build_receipt'])


def check_release_inputs(command, binding):
    if any(pin not in command['inputs'] for pin in release_inputs(binding)):
        raise ValueError('Reviewed native step does not bind the same release provenance')


def brew(args):
    publication = ref(args.publication)
    binary, release = engine(args,'brewfs-fork')
    native = dict(binary=binary, redis_url=args.redis_url,
                  namespace=args.namespace, revision=load(publication['path'])['fork_source'],
                  data_config=ref(args.data_config))
    native_argv({'method':'brewfs-fork','native':native}, 'probe')
    return native, publication, release


def step(*, cell, method, native, tree, child, cwd, environment=None, extra=()):
    cwd = Path(cwd)
    if not cwd.is_absolute() or cwd.resolve(strict=True)!=cwd or not cwd.is_dir():
        raise ValueError('Existing canonical absolute step cwd required')
    argv = native_argv({'method':method,'native':native}, child)
    inputs = [tree, *extra]
    value = dict(schema='experiment-step-v1',reviewed=False,cell_id=named(cell),
                 stage='fork-'+named(child),argv=argv,cwd=str(cwd),inputs=inputs,
                 executable_sha256=native['binary']['sha256'],inherit_environment=[])
    if method=='brewfs-fork':
        inputs.append(native['data_config'])
        if environment is None:
            raise ValueError('BrewFS needs a protected AWS environment JSON')
        protected = ref(environment)
        if Path(environment).stat().st_mode & 0o777 != 0o600:
            raise ValueError('Protected environment must be mode 0600')
        keys = load(protected['path'])
        if not isinstance(keys, dict) or not set(AWS_ENV).issubset(keys) or set(keys)-set((*AWS_ENV,'AWS_SESSION_TOKEN')):
            raise ValueError('Protected environment must contain only the required AWS fields')
        inputs.append(protected)
        value.update(protected_environment=protected,inherit_environment=sorted(keys))
    elif environment is not None:
        raise ValueError('JuiceFS uses volume credentials from its prepared metadata')
    return value


def source(args):
    tree = ref(args.tree, TREE_SHA)
    mount_ref, ready = ref(args.mount_spec), ref(args.ready)
    mount = spec(mount_ref,'experiment-mount-v1')
    if load(ready['path']).get('spec') != mount_ref or Path(ready['path']).name!='ready.json':
        raise ValueError('Ready receipt must identify the exact source mount spec')
    value = dict(schema=REQUEST,reviewed=False,cell_id=named(args.cell),method=args.method,
                 input_tree=tree,mount_spec=mount_ref,ready=ready)
    if args.method=='brewfs-fork':
        value['native'], publication, release = brew(args)
        carrier_step = ref(args.carrier_step)
        if release is not None:
            check_release_inputs(spec(carrier_step, 'experiment-step-v1'), release)
            value['release_candidate'] = release
        value['carrier'] = dict(publication=publication,step=carrier_step,
                                stdout=ref(Path(args.carrier_output)/'stdout.log'),
                                finished=ref(Path(args.carrier_output)/'finished.json'))
    else:
        binary, _ = engine(args,args.method)
        value['native'] = dict(binary=binary,mount=mount['mount_point'],
                               source=args.source,destination_parent=args.destination_parent,threads=4)
        value['writer'] = dict(ready=ref(args.writer_ready),stopped=ref(args.writer_stopped))
    return value


def measured(args):
    qualified_ref = ref(args.qualification)
    qualified = load(qualified_ref['path'])
    if qualified.get('schema')!=QUALIFIED or (qualified.get('files'),qualified.get('bytes'))!=(100000,409600000):
        raise ValueError('Exact full 100k source qualification required')
    req = spec(qualified['request'],REQUEST)
    method, native, cell = (req[key] for key in ('method','native','cell_id'))
    tree = ref(req['input_tree']['path'],TREE_SHA)
    binary = ref(native['binary']['path'])
    if tree != req['input_tree'] or binary != native['binary']:
        raise ValueError('Wrong measured fixture or binary')
    release = release_binding(args, method, binary)
    if req.get('release_candidate') != release:
        raise ValueError('Source qualification does not bind this release provenance')
    stopped = ref(args.probe_stopped)
    if bool(args.measurement_spec)!=bool(args.measurement_ready):
        raise ValueError('Both measurement mount spec and ready receipt are needed')
    measurement = None if args.measurement_spec is None else {
        'mount_spec':ref(args.measurement_spec),'ready':ref(args.measurement_ready)}
    preflight(qualified_ref,cell_id=cell,method=method,native=native,input_tree=tree,
              probe_stopped=stopped,measurement=measurement)
    proof = dict(receipt=qualified_ref,probe_stopped=stopped,measurement=measurement)
    value = dict(schema=SOURCE_BOUND,reviewed=False,cell_id=cell,method=method,parallelism=1,
                 native=native,input_tree=tree,source_qualification=proof)
    if release is not None:
        value['release_candidate'] = release
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='action',required=True)

    def fields(parser, keys):
        for key in keys.split():
            parser.add_argument('--'+key, required=True)

    for action in ('carrier-step','source-brewfs','source-juicefs','measure-step','burst'):
        sub = subs.add_parser(action)
        fields(sub,'out')
        if action.startswith('source') or action=='carrier-step':
            fields(sub,'cell tree binary')
        if action.startswith('source'):
            fields(sub,'mount-spec ready')
        if action in ('carrier-step','source-brewfs'):
            fields(sub,'redis-url namespace publication data-config')
        if action in ('carrier-step','source-brewfs','measure-step','burst'):
            sub.add_argument('--release-review')
            sub.add_argument('--release-review-sha256')
        if action=='carrier-step':
            fields(sub,'cwd aws-environment')
        if action=='source-brewfs':
            fields(sub,'carrier-step carrier-output')
        if action=='source-juicefs':
            fields(sub,'source destination-parent writer-ready writer-stopped')
        if action in ('measure-step','burst'):
            fields(sub,'qualification probe-stopped child')
            sub.add_argument('--measurement-spec')
            sub.add_argument('--measurement-ready')
        if action=='measure-step':
            fields(sub,'cwd')
            sub.add_argument('--aws-environment')
        if action=='burst':
            fields(sub,'step')
    args = p.parse_args()
    out = output(args.out)
    if args.action=='carrier-step':
        native, publication, release = brew(args)
        value = step(cell=args.cell,method='brewfs-fork',native=native,tree=ref(args.tree,TREE_SHA),
                     child='source-probe',cwd=args.cwd,environment=args.aws_environment,
                     extra=(publication, *release_inputs(release)))
    elif args.action.startswith('source'):
        args.method = 'brewfs-fork' if args.action=='source-brewfs' else 'juicefs-clone'
        value = source(args)
    else:
        value = measured(args)
        release = value.get('release_candidate')
        child = named(args.child)
        if args.action=='measure-step':
            value = step(cell=value['cell_id'],method=value['method'],native=value['native'],
                         tree=value['input_tree'],child=child,cwd=args.cwd,environment=args.aws_environment,
                         extra=(value['source_qualification']['receipt'], *release_inputs(release)))
        else:
            child_ref = ref(args.step)
            command, _ = prepare(child_ref['path'],child_ref['sha256'])
            if (command['stage'],command['cell_id'])!=('fork-'+child,value['cell_id']):
                raise ValueError('Reviewed child step has another identity')
            native_step(value,child,command)
            check_release_inputs(command, release)
            value['children'] = [{'id':child,'step':child_ref}]
    write_new(out,value)
    print(digest(out),out)


if __name__=='__main__':
    main()
