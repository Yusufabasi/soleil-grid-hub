"""Apply ``db/migrations/0007_ingest_wallet_balance_read.sql``.

One statement: the roles that post to a wallet may read that wallet's balance through a definer
function, which is what the low-balance warning the posting path stages needs. Additive;
``db/schema.sql`` stays frozen.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0006_ingest_ledger_transaction_read")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0007_ingest_wallet_balance_read")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
