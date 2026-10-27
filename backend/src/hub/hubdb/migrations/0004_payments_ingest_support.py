"""Apply ``db/migrations/0004_payments_ingest_support.sql`` (Task 04.7).

Two statements with one purpose: the webhook pool may read the ledger account ids a posting
names, and a delivery that disagrees with the top-up it claims to pay for has somewhere to land
(``hub.post_suspense_receipt``). Additive; ``db/schema.sql`` stays frozen.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0003_ledger_owner_conflict_guard_grant")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0004_payments_ingest_support")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
