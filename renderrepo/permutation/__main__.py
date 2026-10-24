"""python -m renderrepo.permutation --store PATH COMMAND ..."""
import argparse
import json
from pathlib import Path
import sys

from . import engine


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--store', type=Path, default=Path.home() / '.local/share/renderrepo/permutations')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('plan'); p.add_argument('definition', type=Path)
    p = sub.add_parser('compare'); p.add_argument('manifest', type=Path); p.add_argument('--port', type=int, default=8846)
    p = sub.add_parser('approve'); p.add_argument('experiment'); p.add_argument('--plan-hash', required=True)
    for name in ('run', 'resume', 'pause', 'stop', 'status'):
        p = sub.add_parser(name); p.add_argument('experiment')
    p = sub.add_parser('review'); p.add_argument('experiment'); p.add_argument('job'); p.add_argument('rating', choices=['keeper', 'modify', 'toss', 'unreviewed']); p.add_argument('--notes', default='')
    p = sub.add_parser('export'); p.add_argument('experiment'); p.add_argument('destination', type=Path)
    p = sub.add_parser('serve'); p.add_argument('experiment'); p.add_argument('--port', type=int, default=8844)
    p = sub.add_parser('associate'); p.add_argument('experiment'); p.add_argument('job'); p.add_argument('--confirmed-provider-id', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'compare':
            from .comparison import serve
            serve(args.manifest, args.port)
            return 0
        if args.command == 'plan':
            directory, value = engine.plan(args.definition, args.store)
            print(json.dumps({'experiment': directory.name, 'directory': str(directory), 'renders': value['count'],
                              'hypothetical_estimated_total': value['estimated_total'], 'estimate': value['estimate'],
                              'limits': value['limits'], 'provider': value['provider'],
                              'axes_and_order': [j['settings'] for j in value['jobs']]}, indent=2))
            return 0
        if not __import__('re').fullmatch('[0-9a-f]{64}', args.experiment):
            raise ValueError('Experiment must be the full plan hash.')
        directory = args.store.resolve() / args.experiment
        engine.load(directory)
        if args.command == 'approve':
            engine.approve(directory, args.plan_hash)
        elif args.command in {'run', 'resume'}:
            from .provider import ComfyProvider
            print(json.dumps(engine.run(directory, ComfyProvider(), resume=True), indent=2))
        elif args.command in {'pause', 'stop'}:
            engine.control(directory, args.command)
            print(f'{args.command.capitalize()} requested; current accepted job is not canceled.')
        elif args.command == 'status':
            print(json.dumps({'summary': engine.summary(directory), 'jobs': engine.results(directory),
                              'control': engine.read(directory / 'control.json'), 'execution_state': engine.execution_state(directory)}, indent=2))
        elif args.command == 'review':
            engine.review(directory, args.job, args.rating, args.notes)
        elif args.command == 'export':
            engine.export(directory, args.destination)
        elif args.command == 'associate':
            engine.attach(directory, args.job, args.confirmed_provider_id)
        elif args.command == 'serve':
            from .review_server import serve
            serve(directory, args.port)
        return 0
    except KeyboardInterrupt:
        print('Worker interrupted. Resume reconciles known jobs; unknown submissions remain unresolved.', file=sys.stderr)
        return 130
    except (ValueError, OSError, KeyError, TypeError, ImportError) as error:
        print(f'Permutation error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
