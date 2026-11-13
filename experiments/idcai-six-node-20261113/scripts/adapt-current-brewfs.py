"""Apply current upstream BrewFS names to a fresh shared-backend AX adapter."""
from pathlib import Path
import sys

REVISION = '8a215a65cad19d34fd3fd7f04029f97d09617364'


def once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Unexpected adapter source: '+old[:90])
    return text.replace(old, new, 1)


def main(repo):
    scripts = repo/'experiments/unified-suite/pilot-aliyun/scripts'
    for name in ('rbm-lifecycle.py', 'rbm-native-fork.py'):
        path = scripts/name
        text = path.read_text()
        text = text.replace('workspace-v1', 'workspace').replace('flat-v1', 'flat')
        text = text.replace('BREWFS_PACKED_V3_', 'BREWFS_PACKED_')
        if name == 'rbm-lifecycle.py':
            begin = text.index("        mount_env.update(BREWFS_WORKSPACE_PACKED_METADATA=")
            end = text.index("    if 'packed_budget_diagnostic'", begin)
            text = text[:begin] + '''        if a.packed_metadata != 'on' or a.eager_workspace:
            raise ValueError('Current upstream adapter requires the normal Packed workspace path')
        mount_env['BREWFS_PACKED_CACHE'] = 'on'
        result['packed_cache'] = {'mode': 'on', 'bytes': 268435456}
''' + text[end:]
        else:
            from idcai_fork_operations import adapt_native
            begin = text.index("    if a.product == 'brewfs':\n", text.index('def run(work):'))
            end = text.index("    result['binary_sha256']", begin)
            text = text[:begin] + '''    if a.product == 'brewfs':
        if not a.brewfs_provenance:
            raise ValueError('Current upstream build receipt required')
        proof = json.loads(a.brewfs_provenance.read_text())
        with a.brewfs.open('rb') as stream:
            actual = hashlib.file_digest(stream, 'sha256').hexdigest()
        if not proof['passed'] or not proof['source_clean'] or proof['source_commit'] != a.brewfs_revision or actual != proof['artifacts']['brewfs']['sha256']:
            raise ValueError('Upstream source or deployed binary differs from build receipt')
        result['build_provenance'] = proof
''' + text[end:]
            begin = text.index("                arm['phase'] = 'reject-unpublished-import'")
            end = text.index("                arm['phase'] = 'publish-packed-snapshot'", begin)
            text = text[:begin] + "                arm['carrier_guard'] = 'upstream CLI has no --require-packed-carrier flag; check published fork_base below'\n" + text[end:]
            text = text.replace(",'--require-packed-carrier'", '')
            begin = text.index("                arm['carrier_guard']")
            end = text.index("                arm['phase'] = 'native-fork'", begin)
            text = text[:begin] + '''                carrier = revision
                arm['fork_source_kind'] = 'immutable revision created by upstream init-packed-volume'
                arm['live_workspace_publication_tested'] = False
''' + text[end:]
            text = text.replace('same_published_carrier', 'same_fork_base')
            text = text.replace('Child does not have the published lower and a new empty upper',
                                'Child does not have the original immutable lower and a new empty upper')
            # These old Redis key probes are not a current binding oracle; CLI
            # fork_base, full inherited content and isolation are checked above.
            begin = text.index("                for label, record in [('parent',parent),('child',child)]:")
            end = text.index('                parent_path = child_path = point', begin)
            text = text[:begin] + text[end:]
            text = adapt_native(text)
        text = text.replace("'formal_sample': False", "'formal_sample': False, 'brewfs_upstream_commit': '" + REVISION + "'")
        compile(text, str(path), 'exec')
        path.write_text(text)
    helper = Path(__file__).with_name('idcai_fork_operations.py')
    (scripts/helper.name).write_bytes(helper.read_bytes())


if __name__ == '__main__':
    main(Path(sys.argv[1]))
