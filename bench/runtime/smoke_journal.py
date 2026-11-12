"""Small functional probes: durable phase timings and bounded in-process work."""
from contextlib import contextmanager
import json
from pathlib import Path
import signal
import subprocess
import time


class SmokeJournal:
    def __init__(self, path, secrets=()):
        self.path = Path(path)
        self.secrets = tuple(value for value in secrets if value)
        self.sequence = 0

    def clean(self, value):
        text = str(value)
        for secret in self.secrets:
            text = text.replace(secret, '[redacted]')
        return text

    def emit(self, **row):
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')

    @contextmanager
    def stage(self, name, *, timeout=None, **details):
        self.sequence += 1
        ident = self.sequence
        start = time.monotonic_ns()
        self.emit(id=ident, event='start', name=self.clean(name), monotonic_ns=start,
                  details=details)
        previous = None
        if timeout is not None:
            if signal.getitimer(signal.ITIMER_REAL)[0]:
                raise RuntimeError('Refusing to overwrite an active deadline')
            previous = signal.getsignal(signal.SIGALRM)
            def expired(*_):
                raise TimeoutError(f'{name} exceeded {timeout}s')
            signal.signal(signal.SIGALRM, expired)
            signal.setitimer(signal.ITIMER_REAL, timeout)
        try:
            yield
        except BaseException as exc:
            self.emit(id=ident, event='end', name=self.clean(name),
                      elapsed_ns=time.monotonic_ns()-start, status='failed',
                      error=self.clean(exc)[-3000:])
            raise
        else:
            self.emit(id=ident, event='end', name=self.clean(name),
                      elapsed_ns=time.monotonic_ns()-start, status='passed')
        finally:
            if previous is not None:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, previous)

    def run(self, argv, **kwargs):
        command = list(map(str, argv))
        with self.stage('command:' + Path(command[0]).name,
                        argv=[self.clean(arg) for arg in command],
                        timeout_seconds=kwargs.get('timeout'),child_umask=kwargs.get('umask',-1)):
            result = subprocess.run(command, **kwargs)
            self.emit(event='returncode', returncode=result.returncode)
            return result
