"""Apply ``db/migrations/0002_approvals_period_actions.sql`` (Task 03.6).

Adds ``period.lock`` and ``period.reopen`` to the approval action CHECK, which is what D-40
needs and what the frozen baseline predates. Additive: no row changes.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0001_ledger_system_accounts")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0002_approvals_period_actions")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
