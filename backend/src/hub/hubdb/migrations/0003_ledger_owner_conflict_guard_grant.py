"""Apply ``db/migrations/0003_ledger_owner_conflict_guard_grant.sql`` (Task 03.5).

One GRANT: ``ledger_owner`` may read ``hub.staff_customer_link``, which ``approvals_guard``
queries when a definer function consumes an approval. Additive and idempotent; see CR-001.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0002_approvals_period_actions")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0003_ledger_owner_conflict_guard_grant")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
