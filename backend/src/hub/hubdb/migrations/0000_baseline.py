"""Baseline: apply ``db/baseline/0000_baseline.sql``, a byte copy of ``db/schema.sql``.

This migration is the only one that creates roles and grants, so the first run on a new
environment needs a superuser login in ``HUB_MIGRATE_URL`` (docs/04 10.5, and the runbook in
``hub.settings.migrate``). Everything after it is a numbered file in ``db/migrations`` with a
wrapper of the same stem next to this module, and the platform's ``migrator`` login suffices.

The SQL is passed as a one-element list: Django sends a list to the driver as one statement
batch, which is what the file is (it is written for ``psql -f``, not split by the wrapper).
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    initial = True
    atomic = True
    dependencies = []

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0000_baseline")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
