"""Refuse divergent/unknown migration histories before any schema mutation."""
import json
import os
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
import django

django.setup()

from django.db import connection
from django.db.migrations.executor import MigrationExecutor

executor = MigrationExecutor(connection)
loader = executor.loader
known = set(loader.disk_migrations)
for migration in loader.disk_migrations.values():
    known.update(migration.replaces)
unknown = sorted(set(loader.applied_migrations) - known)
if unknown:
    raise SystemExit("Unknown applied migrations; reconcile the database branch before proceeding: " + repr(unknown))
loader.check_consistent_history(connection)
pending = [[migration.app_label, migration.name] for migration, backwards in executor.migration_plan(loader.graph.leaf_nodes())]
if "--expect-current" in sys.argv and pending:
    raise SystemExit("The restored/deployed database is not at this release's migration leaves: " + repr(pending))
print(json.dumps({"migration_history_known": True, "pending_migrations": pending}))
