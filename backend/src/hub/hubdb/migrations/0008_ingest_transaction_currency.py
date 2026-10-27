"""Apply ``db/migrations/0008_ingest_transaction_currency.sql``.

One statement: the roles that post a reversal may read the currency of the transaction they are
reversing, through a definer function, without reading the transaction or its entries. Additive;
``db/schema.sql`` stays frozen.
"""

from __future__ import annotations

from django.db import migrations

from hub.hubdb.sqlfiles import sql_text


class Migration(migrations.Migration):
    atomic = True
    dependencies = [("hubdb", "0007_ingest_wallet_balance_read")]

    operations = [
        migrations.RunSQL(
            sql=[sql_text("0008_ingest_transaction_currency")],
            reverse_sql=migrations.RunSQL.noop,
        )
    ]
