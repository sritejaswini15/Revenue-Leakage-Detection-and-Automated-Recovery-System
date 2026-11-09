"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
"""
from typing import Sequence, Union
from alembic import op

revision: str = ${repr(up_revision)}
down_revision: Union[str, None] = ${repr(down_revision)}


def upgrade() -> None:
    ${upgrades or "pass"}


def downgrade() -> None:
    ${downgrades or "pass"}
