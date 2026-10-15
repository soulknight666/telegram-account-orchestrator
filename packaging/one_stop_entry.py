"""Single-file entry: use executable-relative state, never the caller's .env."""
import multiprocessing
import os
import sys
from pathlib import Path


def main():
    multiprocessing.freeze_support()
    if getattr(sys, 'frozen', False):
        home = Path(sys.executable).resolve().parent
        os.chdir(home)
        os.environ['TAM_ENV_FILE'] = str(home / '.env')
        os.environ['TAM_EXPORT_DIR'] = str(home / 'export')
    # Windowed PyInstaller builds have no streams. Uvicorn inspects isatty().
    for name in ('stdout', 'stderr'):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, 'w', encoding='utf-8'))
    if sys.argv[1:2] == ['--smoke-test']:
        from tam.launcher import smoke_test_payload
        print(__import__('json').dumps(smoke_test_payload(), ensure_ascii=False, sort_keys=True))
        return 0
    if sys.argv[1:2] == ['--self-test']:
        from tam.one_stop_selftest import run
        run(Path(sys.argv[2]))
        return 0
    from tam.launcher import main as launch
    return launch()


if __name__ == '__main__':
    raise SystemExit(main())
