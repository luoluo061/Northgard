"""Run isolated standard-library test suites; no live game or network needed."""
from pathlib import Path
import os
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
SUITES=('companion/tests','research/telemetry','collector','tests','mechanics')


def main():
    failed=[]
    environment={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONDONTWRITEBYTECODE':'1'}
    for suite in SUITES:
        print(f'Running {suite}',flush=True)
        result=subprocess.run([sys.executable,'-X','utf8','-m','unittest','discover','-s',suite,'-p','test_*.py'],
            cwd=ROOT,env=environment)
        if result.returncode:
            failed.append(suite)
    print('All offline suites passed.' if not failed else 'Failed suites: '+', '.join(failed),flush=True)
    return bool(failed)


if __name__=='__main__':
    raise SystemExit(main())
