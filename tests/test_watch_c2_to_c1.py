import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

script = Path(__file__).resolve().parents[1]/'experiments/unified-suite/pilot-aliyun/scripts/watch-c2-to-c1.py'
spec = importlib.util.spec_from_file_location('c2_to_c1_watch', script)
watch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(watch)


class C2ToC1Watch(unittest.TestCase):
    def test_terminal_journal_binds_exact_command_boot_and_order(self):
        unit = 'ax-pilot-test-c2'
        invocation = '2' * 32
        cmd = ['/usr/bin/python3', '/test/runner.py', '--pin', 'test']

        def row(event, message, at):
            return dict(UNIT=unit+'.service', INVOCATION_ID=invocation, _BOOT_ID='a',
                        __CURSOR='cursor', __MONOTONIC_TIMESTAMP=str(at),
                        MESSAGE_ID=event, MESSAGE=message)

        start = row(watch.START, 'Started '+unit+'.service - '+' '.join(cmd)+'.', 1)
        end = row(watch.STOP, unit+'.service: Deactivated successfully.', 2)
        with patch.object(watch.subprocess, 'check_output', return_value='\n'.join(map(json.dumps, (start, end)))):
            self.assertEqual(watch.journal(unit, invocation, cmd, True)['stop']['MESSAGE'], end['MESSAGE'])
        for rows in ([start], [start, dict(end, _BOOT_ID='b')],
                     [start, dict(end, __MONOTONIC_TIMESTAMP='0')],
                     [dict(start, MESSAGE='Different C2'), end]):
            with self.subTest(rows=rows):
                with patch.object(watch.subprocess, 'check_output', return_value='\n'.join(map(json.dumps, rows))):
                    with self.assertRaisesRegex(ValueError, 'Exact C2 journal'):
                        watch.journal(unit, invocation, cmd, True)

    def test_replaced_or_failed_original_never_reaches_terminal_proof(self):
        cases = [(dict(InvocationID='other'), 'replaced'),
                 (dict(InvocationID='one', ActiveState='failed'), 'unit failed'),
                 (dict(InvocationID='one', ActiveState='inactive', MainPID='0', ExecMainStatus='1'), 'did not exit zero')]
        for state, message in cases:
            with self.subTest(state=state):
                with patch.object(watch, 'show', return_value=state), patch.object(watch, 'journal') as journal:
                    with self.assertRaisesRegex(ValueError, message):
                        watch.wait('ax-pilot-test-c2', 'one', [], 60)
                    journal.assert_not_called()

    def test_retention_uses_inner_run_pin_and_requires_all_six_archives(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            tool = root/'retainer.py'
            tool.write_text('# frozen retainer\n')
            request = root/'request.json'
            inner = root/'inner-result.json'
            request.write_text('{}')
            inner.write_text('{"passed":true}')
            retained = root/'retained'
            archive = root/'retained.tar.gz'
            check = root/'archive-check.json'
            spec = dict(c2='c2', future=dict(unit='ax-pilot-test-c2'), retainer=watch.ref(tool),
                        retention=dict(out=str(retained), result=str(retained/'result.json'),
                                       archive=str(archive), archive_check=str(check)),
                        modes=['off','on','on','off','off','on'], attempts=[str(i) for i in range(6)])
            proof = dict(request=watch.ref(request), run=watch.ref(inner))

            def pack(_, __, argv):
                self.assertEqual(argv[argv.index('--completed-sha256')+1], proof['run']['sha256'])
                self.assertEqual(argv[argv.index('--request-sha256')+1], proof['request']['sha256'])
                retained.mkdir()
                (retained/'request.json').write_bytes(request.read_bytes())
                (retained/'completed.json').write_bytes(inner.read_bytes())
                # A passing wrapper with five trials is still incomplete.
                trials = [dict(tag=tag, mode=mode, roles=list(watch.ROLES if mode=='on' else watch.ROLES[:1]),
                               passed=True) for tag, mode in zip(spec['attempts'], spec['modes'])]
                (retained/'result.json').write_text(json.dumps(dict(passed=True, cell_id='c2', trials=trials[:-1])))
                archive.write_bytes(b'payload')
                check.write_text(json.dumps(dict(passed=True, cell_id='c2', kind='six-calibration-trials',
                                                 archive=str(archive), sha256=watch.digest(archive))))

            with patch.object(watch, 'logged', side_effect=pack):
                with self.assertRaisesRegex(ValueError, 'original six'):
                    watch.retain(spec, proof, root)


if __name__ == '__main__':
    unittest.main()
