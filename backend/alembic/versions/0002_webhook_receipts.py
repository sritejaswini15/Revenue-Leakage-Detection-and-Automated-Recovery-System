"""Persist provider webhook identity and raw receipt state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "0002_webhook_receipts"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The legacy 0001 revision uses Base.metadata.create_all, which may have
    # already created newly added tables on a fresh database. Keep upgrades
    # safe for both that path and databases created from older revisions.
    if "webhook_receipts" in inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "webhook_receipts",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("organization_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False, server_default="razorpay"),
        sa.Column("provider_event_id", sa.String(length=160), nullable=False),
        sa.Column("dedupe_key", sa.String(length=128), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="RECEIVED"),
        sa.Column("revenue_event_id", sa.String(length=36), nullable=True),
        sa.Column("rejection_reason", sa.String(length=255), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["revenue_event_id"], ["revenue_events.id"]),
        sa.UniqueConstraint("organization_id", "provider_event_id", name="uq_webhook_receipt_org_event"),
        sa.UniqueConstraint("dedupe_key"),
    )
    op.create_index("ix_webhook_receipts_organization_id", "webhook_receipts", ["organization_id"])
    op.create_index("ix_webhook_receipts_provider_event_id", "webhook_receipts", ["provider_event_id"])
    op.create_index("ix_webhook_receipts_dedupe_key", "webhook_receipts", ["dedupe_key"])
    op.create_index("ix_webhook_receipts_status", "webhook_receipts", ["status"])
    op.create_index("ix_webhook_receipts_revenue_event_id", "webhook_receipts", ["revenue_event_id"])


def downgrade() -> None:
    op.drop_table("webhook_receipts")