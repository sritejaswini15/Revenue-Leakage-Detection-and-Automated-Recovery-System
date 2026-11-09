"""Ensure complete schema with all indexes and constraints.

Safe to run against any database that has 0001 and 0002 applied.
All operations are idempotent - tables/indexes created in 0001 via
Base.metadata.create_all are not re-created; only genuinely missing
indexes and the auth_sessions table are added here.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0003_complete_schema"
down_revision = "0002_webhook_receipts"
branch_labels = None
depends_on = None


def _table_exists(bind, name: str) -> bool:
    return name in inspect(bind).get_table_names()


def _index_exists(bind, table: str, index: str) -> bool:
    return any(i["name"] == index for i in inspect(bind).get_indexes(table))


def upgrade() -> None:
    bind = op.get_bind()

    # auth_sessions - may be missing on very early databases that used the
    # 0001 create_all path before AuthSession was added to the model.
    if not _table_exists(bind, "auth_sessions"):
        op.create_table(
            "auth_sessions",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_auth_sessions_user_id", "auth_sessions", ["user_id"])
        op.create_index("ix_auth_sessions_token_hash", "auth_sessions", ["token_hash"], unique=True)
        op.create_index("ix_auth_sessions_expires_at", "auth_sessions", ["expires_at"])

    # Ensure critical performance indexes exist
    critical_indexes = [
        ("recovery_cases", "ix_recovery_cases_created_at", ["created_at"]),
        ("recovery_cases", "ix_recovery_cases_status", ["status"]),
        ("audit_events", "ix_audit_events_created_at", ["created_at"]),
        ("audit_events", "ix_audit_events_event_name", ["event_name"]),
        ("revenue_events", "ix_revenue_events_event_type", ["event_type"]),
        ("payments", "ix_payments_status", ["status"]),
    ]
    for table, index_name, columns in critical_indexes:
        if _table_exists(bind, table) and not _index_exists(bind, table, index_name):
            op.create_index(index_name, table, columns)


def downgrade() -> None:
    # Not removing auth_sessions or indexes - they are used by production data
    pass
