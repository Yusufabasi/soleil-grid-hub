"""Apply ``db/migrations/0006_ingest_ledger_transaction_read.sql``.

One statement: the webhook pool may read the three columns it needs to know whether a posting
already happened. Additive; ``db/schema.sql`` stays frozen.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0005_api_idempotency_keys")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0006_ingest_ledger_transaction_read")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
