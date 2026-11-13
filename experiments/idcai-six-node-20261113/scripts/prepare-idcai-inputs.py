"""Create the two 100,000-file inputs through the pinned AX generator."""
import argparse
import importlib.util
from pathlib import Path
import subprocess

BASE = Path('/srv/ax-idcai-20261113')
AX_REVISION = '86f704be9129fbb5f6e466cfbbb15fdf383fc4e8'
SMALLFILES_SHA = 'd83c345374a6f8f858239db75991b4faec85701be8ffa7409d7dfda8037c0795'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    repo = BASE/'src/ax'
    if subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'], text=True).strip() != AX_REVISION:
        raise ValueError('Input generator requires the recorded AX revision')
    source = repo/'experiments/unified-suite/pilot-aliyun/scripts/prepare-pilot-tenk.py'
    spec = importlib.util.spec_from_file_location('ax_input', source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare(args.out, BASE/'bin/smallfiles', SMALLFILES_SHA, 100000, False)


if __name__ == '__main__':
    main()
