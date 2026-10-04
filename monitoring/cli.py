"""Organiser commands. Manifests and exports may contain personal data: keep them private."""
import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from .db import Audit, Batch, Review, Team, database, initialize
from .importer import Manifest, import_manifest
from .planner import allocate


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('init-db')
    validate = sub.add_parser('validate')
    validate.add_argument('manifest')
    imp = sub.add_parser('import')
    imp.add_argument('manifest')
    imp.add_argument('--pilot', action='store_true')
    plan = sub.add_parser('plan')
    plan.add_argument('inventory', help='JSON containing team_ids and assets with source_id/kind/units')
    plan.add_argument('--replicas', type=int, default=3)
    plan.add_argument('--batch-units', type=int, required=True, help='Derived from your pilot; no assumed throughput')
    plan.add_argument('--output', required=True)
    export = sub.add_parser('export-records')
    export.add_argument('--output', required=True)
    demo = sub.add_parser('demo')
    demo.add_argument('--teams', type=int, default=3, help='Synthetic sample size only')
    args = parser.parse_args()
    if args.command == 'plan':
        inventory = json.loads(Path(args.inventory).read_text())
        result = allocate(inventory['team_ids'], inventory['assets'], args.replicas, args.batch_units)
        Path(args.output).write_text(json.dumps(result, indent=2) + '\n')
        print('Allocation saved. Map source IDs to independent team-local entities before import.')
        return
    if args.command == 'validate':
        manifest = Manifest.model_validate_json(Path(args.manifest).read_text())
        print(json.dumps(manifest.coverage(), indent=2))
        return
    engine, sessions = database(os.environ['DATABASE_URL'])
    initialize(engine)
    if args.command == 'import':
        import supervisely as sly
        from .gateway import Gateway
        manifest = Manifest.model_validate_json(Path(args.manifest).read_text())
        import_manifest(sessions, manifest, Gateway(sly.Api.from_env()),
                        int(os.environ['MONITORING_TEAM_ID']), args.pilot)
        print('Event imported. No remote labeling jobs have been created yet.')
    elif args.command == 'demo':
        if os.getenv('LOCAL_DEVELOPMENT', 'false').lower() != 'true' or not os.environ['DATABASE_URL'].startswith('sqlite'):
            raise ValueError('Synthetic demo requires LOCAL_DEVELOPMENT=true and a separate SQLite database')
        if args.teams < 1:
            raise ValueError('Demo team count must be positive')
        monitor = int(os.getenv('LOCAL_USER_ID', '900'))
        data = {'teams': [{'id': i, 'name': f'Demo team {i:02d}', 'monitor_id': monitor,
            'annotator_ids': [1000 + i * 2, 1001 + i * 2], 'batches': [
                {'id': f'demo-{i}-images', 'kind': 'images', 'dataset_id': 2000 + i,
                 'assets': [{'source_id': f'demo-image-{n}', 'entity_id': i * 100 + n} for n in range(1, 5)]},
                {'id': f'demo-{i}-video', 'kind': 'video', 'dataset_id': 3000 + i,
                 'assets': [{'source_id': 'demo-video', 'entity_id': i * 100 + 99, 'frames': 240}]}]
                } for i in range(1, args.teams + 1)]}
        import_manifest(sessions, Manifest.model_validate(data), pilot=True)
        with sessions.begin() as s:
            for b in s.query(Batch).filter(Batch.kind == 'images'):
                b.state, b.job_ids, b.remote_status = 'active', [5000 + b.team_id], 'in_progress'
        print('Synthetic demo loaded. It does not represent a real Supervisely event.')
    elif args.command == 'export-records':
        from sqlalchemy import select
        with sessions() as s:
            def rows(model):
                return [{c.name: getattr(row, c.name) for c in model.__table__.columns}
                        for row in s.scalars(select(model))]
            records = {'schema_version': 1, 'teams': rows(Team), 'batches': rows(Batch),
                       'reviews': rows(Review), 'audit': rows(Audit)}
        Path(args.output).write_text(json.dumps(records, indent=2) + '\n')
        print('Monitoring records exported. Annotation payloads must still be collected from Supervisely.')


if __name__ == '__main__':
    main()
