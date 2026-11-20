"""scope wasm module names per owning user (M-154).

The ``wasm_modules.name`` column was globally unique even though modules
are per-user resources: a second tenant registering a common name (e.g.
``clean``) hit a raw IntegrityError/500, and the 409-vs-500 difference
leaked whether a name was taken by someone else. Names are now unique
per (user_id, name).

No data migration is needed: the previous global uniqueness guarantees
no duplicate names exist, so scoping the key cannot create conflicts.
Downgrade caveat: reverting requires zero cross-user duplicate names,
otherwise the global unique constraint cannot be recreated.

Revision ID: 0019_wasm_user_scoped_names
Revises: 0018_contract_race_idempotency
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0019_wasm_user_scoped_names"
down_revision: Union[str, None] = "0018_contract_race_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        # SQLite and other backends are created via Base.metadata.create_all,
        # which already carries the composite constraint from the model.
        return
    # NOTE: 0010 declared the column unique=True AND index=True, so the
    # enforcement object is the unique *index* ix_wasm_modules_name, not a
    # table constraint.
    op.drop_index("ix_wasm_modules_name", table_name="wasm_modules")
    op.create_unique_constraint(
        "uq_wasm_modules_user_name", "wasm_modules", ["user_id", "name"]
    )


def downgrade() -> None:
    if op.get_context().dialect.name != "postgresql":
        return
    op.drop_constraint(
        "uq_wasm_modules_user_name", "wasm_modules", type_="unique"
    )
    op.create_index(
        "ix_wasm_modules_name", "wasm_modules", ["name"], unique=True
    )
