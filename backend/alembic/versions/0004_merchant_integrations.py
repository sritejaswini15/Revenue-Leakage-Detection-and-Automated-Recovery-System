"""Per-merchant payment integrations, shared login throttling, user activation,
case expiry policy, and payment origin and recovery case.

Idempotent: revision 0001 builds the schema from the current models, so on a fresh
database these objects already exist when this revision runs.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "0004_merchant_integrations"
down_revision = "0003_complete_schema"
branch_labels = None
depends_on = None


def _has_table(name: str) -> bool:
    return name in inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    return any(c["name"] == column for c in inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("users", "is_active"):
        op.add_column("users", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
    if not _has_column("payments", "origin"):
        op.add_column("payments", sa.Column("origin", sa.String(32), nullable=False, server_default="webhook"))
        op.create_index("ix_payments_origin", "payments", ["origin"])
    if not _has_column("payments", "recovery_case_id"):
        # Batch mode so SQLite (which cannot ALTER constraints) rebuilds the table with the key.
        with op.batch_alter_table("payments") as batch:
            batch.add_column(sa.Column("recovery_case_id", sa.String(36), nullable=True))
            batch.create_foreign_key(
                "fk_payments_recovery_case_id", "recovery_cases", ["recovery_case_id"], ["id"], ondelete="SET NULL"
            )
            batch.create_index("ix_payments_recovery_case_id", ["recovery_case_id"])
    if not _has_column("policies", "case_expiry_days"):
        op.add_column("policies", sa.Column("case_expiry_days", sa.Integer(), nullable=False, server_default="7"))

    if not _has_table("login_failures"):
        op.create_table(
            "login_failures",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("email", sa.String(320), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_login_failures_email", "login_failures", ["email"])
        op.create_index("ix_login_failures_created_at", "login_failures", ["created_at"])

    if not _has_table("payment_integrations"):
        op.create_table(
            "payment_integrations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("provider", sa.String(32), nullable=False),
            sa.Column("key_id", sa.String(64), nullable=False),
            sa.Column("key_secret_encrypted", sa.Text(), nullable=False),
            sa.Column("webhook_secret_encrypted", sa.Text(), nullable=True),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_error", sa.String(255), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index(
            "ix_payment_integrations_organization_id", "payment_integrations", ["organization_id"], unique=True
        )


def downgrade() -> None:
    op.drop_table("payment_integrations")
    op.drop_table("login_failures")
    op.drop_column("policies", "case_expiry_days")
    with op.batch_alter_table("payments") as batch:
        batch.drop_index("ix_payments_recovery_case_id")
        batch.drop_constraint("fk_payments_recovery_case_id", type_="foreignkey")
        batch.drop_column("recovery_case_id")
    op.drop_index("ix_payments_origin", "payments")
    op.drop_column("payments", "origin")
    op.drop_column("users", "is_active")
