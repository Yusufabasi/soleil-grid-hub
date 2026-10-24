"""A catalog fingerprint for comparing two databases (docs/04 10.5, ADR-0020).

The migration chain must produce exactly the database ``db/schema.sql`` describes. The only
honest way to prove that is to compare the catalogs, so this module renders every object
that carries meaning — relations, columns, constraints, indexes, triggers (with their enable
state), functions (with owner, security definer and settings), policies, ACLs expanded per
grantee, role settings and memberships — as one sorted list of text lines.

Two artefacts are deliberately excluded: ``django_migrations`` (Django's own bookkeeping
exists only in the migrated database) and the database name inside ``pg_db_role_setting``,
which is normalised to ``{db}`` because the two databases under comparison have different
names.
"""

from __future__ import annotations

import psycopg

__all__ = ["catalog_fingerprint"]

# One statement, one row per catalog fact. UNION ALL (not UNION) keeps duplicates, which is
# what makes a missing or doubled grant visible.
_FINGERPRINT_SQL = """
WITH ns AS (
  SELECT oid, nspname FROM pg_namespace
   WHERE nspname NOT LIKE 'pg_%' AND nspname <> 'information_schema'
), facts AS (
  SELECT format('relation %s %s owner=%s', c.relkind, c.oid::regclass::text,
                pg_get_userbyid(c.relowner)) AS line
    FROM pg_class c JOIN ns ON ns.oid = c.relnamespace
   WHERE c.oid::regclass::text NOT LIKE '%%django_migrations%%'
  UNION ALL
  SELECT format('column %s.%s.%s %s nullable=%s default=%s',
                table_schema, table_name, column_name, data_type, is_nullable,
                coalesce(column_default, '-'))
    FROM information_schema.columns
   WHERE table_schema IN (SELECT nspname FROM ns) AND table_name <> 'django_migrations'
  UNION ALL
  SELECT format('constraint %s %s %s', conrelid::regclass::text, conname,
                pg_get_constraintdef(oid))
    FROM pg_constraint
   WHERE connamespace IN (SELECT oid FROM ns)
     AND conrelid <> 0
     AND conrelid::regclass::text NOT LIKE '%%django_migrations%%'
  UNION ALL
  SELECT format('index %s %s', schemaname, indexdef)
    FROM pg_indexes
   WHERE schemaname IN (SELECT nspname FROM ns)
     AND indexname NOT LIKE '%%django_migrations%%'
  UNION ALL
  SELECT format('trigger %s %s enabled=%s', tgrelid::regclass::text, tgname, tgenabled)
    FROM pg_trigger
   WHERE NOT tgisinternal
     AND tgrelid::regclass::text NOT LIKE '%%django_migrations%%'
  UNION ALL
  SELECT format('function %s.%s(%s) owner=%s secdef=%s config=%s',
                n.nspname, p.proname, pg_get_function_identity_arguments(p.oid),
                pg_get_userbyid(p.proowner), p.prosecdef,
                coalesce(array_to_string(p.proconfig, ','), '-'))
    FROM pg_proc p JOIN ns n ON n.oid = p.pronamespace
  UNION ALL
  SELECT format('policy %s %s cmd=%s roles=%s using=%s check=%s',
                o.polrelid::regclass::text, o.polname, o.polcmd,
                coalesce((SELECT string_agg(r.rolname, ',' ORDER BY r.rolname)
                            FROM pg_roles r WHERE r.oid = ANY(o.polroles)), 'PUBLIC'),
                coalesce(pg_get_expr(o.polqual, o.polrelid), '-'),
                coalesce(pg_get_expr(o.polwithcheck, o.polrelid), '-'))
    FROM pg_policy o
  UNION ALL
  SELECT format('acl relation %s %s %s %s', c.oid::regclass::text,
                coalesce((SELECT rolname FROM pg_roles WHERE oid = a.grantor), 'unknown'),
                coalesce((SELECT rolname FROM pg_roles WHERE oid = a.grantee), 'PUBLIC'),
                a.privilege_type)
    FROM pg_class c JOIN ns ON ns.oid = c.relnamespace, aclexplode(c.relacl) a
   WHERE c.oid::regclass::text NOT LIKE '%%django_migrations%%'
  UNION ALL
  SELECT format('acl function %s %s %s %s', p.oid::regprocedure::text,
                coalesce((SELECT rolname FROM pg_roles WHERE oid = a.grantor), 'unknown'),
                coalesce((SELECT rolname FROM pg_roles WHERE oid = a.grantee), 'PUBLIC'),
                a.privilege_type)
    FROM pg_proc p JOIN ns n ON n.oid = p.pronamespace, aclexplode(p.proacl) a
  UNION ALL
  SELECT format('acl schema %s %s %s %s', n.nspname,
                coalesce((SELECT rolname FROM pg_roles WHERE oid = a.grantor), 'unknown'),
                coalesce((SELECT rolname FROM pg_roles WHERE oid = a.grantee), 'PUBLIC'),
                a.privilege_type)
    FROM pg_namespace n, aclexplode(n.nspacl) a
   WHERE n.nspname IN (SELECT nspname FROM ns)
  UNION ALL
  SELECT format('role_setting %s %s %s',
                CASE WHEN d.datname = current_database() THEN '{db}'
                     ELSE coalesce(d.datname, '*') END,
                r.rolname, array_to_string(s.setconfig, ','))
    FROM pg_db_role_setting s
    LEFT JOIN pg_database d ON d.oid = s.setdatabase
    LEFT JOIN pg_roles r ON r.oid = s.setrole
  UNION ALL
  SELECT format('member %s in %s admin=%s', m.roleid::regrole::text,
                m.member::regrole::text, m.admin_option)
    FROM pg_auth_members m
)
SELECT line FROM facts ORDER BY line
"""


def catalog_fingerprint(dsn: str) -> list[str]:
    """Every catalog fact of the database at ``dsn``, sorted, as comparable text lines."""
    with psycopg.connect(dsn) as connection:
        rows = connection.execute(_FINGERPRINT_SQL).fetchall()
    return [str(row[0]) for row in rows]
