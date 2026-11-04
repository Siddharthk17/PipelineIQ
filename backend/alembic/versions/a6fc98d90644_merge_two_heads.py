"""merge two heads (0013 and k1a2b3c4d5e0)

Revision ID: a6fc98d90644
Revises: 0013, k1a2b3c4d5e0
Create Date: 2026-11-04 00:00:00.000000

The 0013 branch (wasm usage column + run indexes) and the k1a2b3c4d5e0
branch (contract_violations append-only RULEs) both descend from
j1a2b3c4d5e9 and touch disjoint objects, so a no-op merge restores a
single head for `alembic upgrade head`.
"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = 'a6fc98d90644'
down_revision: Union[str, None] = ('0013', 'k1a2b3c4d5e0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
