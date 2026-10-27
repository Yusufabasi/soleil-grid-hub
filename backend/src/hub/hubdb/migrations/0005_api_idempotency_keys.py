"""Apply ``db/migrations/0005_api_idempotency_keys.sql``.

One statement: the api pool may read the idempotency key it reserves, completes and replays.
Additive; ``db/schema.sql`` stays frozen.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0004_payments_ingest_support")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0005_api_idempotency_keys")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
