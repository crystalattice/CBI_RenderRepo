"""Detached install worker: durable log/result and inherited cross-process leases."""
import argparse
import json
import os
from pathlib import Path
import sys

from .cli import main as install
from .installation_history import atomic_json, timestamp


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    parser.add_argument('fds', nargs=2, type=int)
    args = parser.parse_args()
    os.environ['RENDERREPO_WORKER_FDS'] = ','.join(map(str, args.fds))
    path = args.directory / 'job.json'
    record = json.loads(path.read_text())
    code = 2
    try:
        code = install([*record['argv'], '--yes', '--approved-scope',
                        str(args.directory / 'approved-scope.json'),
                        '--download-control', str(args.directory / 'download-control.json')])
    except BaseException as error:
        print(f'Installation worker stopped: {error}', flush=True)
    finally:
        sys.stdout.flush()
        log = (args.directory / record['log_file']).read_text(errors='replace')[-240000:]
        control = json.loads((args.directory / 'download-control.json').read_text())
        record.update(state='done', outcome='completed' if code in {0, 3} else
                      'cancelled' if control['action'] == 'cancel' else 'failed',
                      result={'exit_code': code, 'log': log}, updated_at=timestamp())
        record['attempts'][-1].update(finished_at=record['updated_at'], result=record['result'])
        atomic_json(path, record)
        for fd in args.fds:
            os.close(fd)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
