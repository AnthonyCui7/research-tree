"""Who can reach the tables, asked of the database itself.

Supabase puts a REST API in front of every table in `public` and grants its
`anon`, `authenticated` and `service_role` roles full access to whatever the
migrations create there. Migration 0005 revokes that and turns on row-level
security; these two tests are what stops the next migration from quietly
handing it back, since a table added later starts with RLS off and inherits
whatever default privileges are in force when it is created.
"""

from __future__ import annotations

from sqlalchemy import text

APPLICATION_ROLE = "research_tree_app"


def test_every_table_has_row_level_security(postgres_engine) -> None:
    with postgres_engine.begin() as conn:
        unprotected = (
            conn.execute(
                text(
                    """
                    SELECT c.relname
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity
                    ORDER BY c.relname
                    """
                )
            )
            .scalars()
            .all()
        )
    assert unprotected == [], (
        "these tables would be readable by any role holding a grant on them; "
        "add them to the row-level-security block of a migration"
    )


def test_every_table_lets_the_application_role_through(postgres_engine) -> None:
    """Without a policy the app role reads nothing, and the weekly backup dumps nothing.

    `pg_dump` runs with row security left on (it has no way round it as a role
    that cannot bypass it), so a table missing this policy would be dumped as
    empty instead of failing. That is the one failure here that would go
    unnoticed until a restore.
    """

    with postgres_engine.begin() as conn:
        unreachable = (
            conn.execute(
                text(
                    """
                    SELECT c.relname
                    FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relrowsecurity
                      AND NOT EXISTS (
                          SELECT 1 FROM pg_policy p
                          WHERE p.polrelid = c.oid
                            AND :application_role = ANY (
                                SELECT pg_get_userbyid(unnest(p.polroles))
                            )
                      )
                    ORDER BY c.relname
                    """
                ),
                {"application_role": APPLICATION_ROLE},
            )
            .scalars()
            .all()
        )
    assert unreachable == [], (
        f"{APPLICATION_ROLE} has no row-level-security policy on these tables, so the "
        "application cannot read them and the backup would dump them empty"
    )


def test_only_the_owner_and_the_application_role_hold_privileges(postgres_engine) -> None:
    """Every grant is one we made: the table's owner, or the role the app connects as."""

    with postgres_engine.begin() as conn:
        unexpected = conn.execute(
            text(
                """
                SELECT DISTINCT g.grantee, g.table_name
                FROM information_schema.role_table_grants g
                JOIN pg_class c ON c.relname = g.table_name
                JOIN pg_namespace n ON n.oid = c.relnamespace AND n.nspname = 'public'
                WHERE g.table_schema = 'public'
                  AND g.grantee <> pg_get_userbyid(c.relowner)
                  AND g.grantee <> :application_role
                ORDER BY g.grantee, g.table_name
                """
            ),
            {"application_role": APPLICATION_ROLE},
        ).all()
    assert unexpected == [], f"unexpected grants on application tables: {unexpected}"
