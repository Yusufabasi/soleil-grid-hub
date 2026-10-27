"""Apply ``db/migrations/0001_ledger_system_accounts.sql`` (Task 03.3).

The system accounts are deployment seed data, not application data: no role but the migrator
may create an account that is not a customer's wallet, so the file runs once here and is
idempotent on re-run.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0000_baseline")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0001_ledger_system_accounts")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
