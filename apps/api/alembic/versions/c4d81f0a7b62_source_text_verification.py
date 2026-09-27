"""separate verification axis for source text

Revision ID: c4d81f0a7b62
Revises: 86c3caacf23b
Create Date: 2026-09-27

OCR accuracy and textual fidelity are different questions. A page can have
perfect OCR of a document that is itself a modern paraphrase, so the archive now
records, per document and per chunk, whether the text has actually been checked
against the original. `documents.verification_status` and
`document_chunks.quote_verified` drive what the RAG layer is allowed to present
as a quotation.

Existing rows default to `unverified_secondary` / `quote_verified = false`
because nothing in this archive has yet been compared to an original by a human.
That is the honest starting position; curated sources can be promoted
individually.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4d81f0a7b62"
down_revision = "86c3caacf23b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "documents",
        sa.Column(
            "verification_status",
            sa.String(length=32),
            nullable=False,
            server_default="unverified_secondary",
        ),
    )
    op.add_column(
        "document_chunks",
        sa.Column(
            "quote_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_index(
        "ix_documents_verification_status", "documents", ["verification_status"], unique=False
    )
    # Existing chunks are all unchecked, so make that explicit rather than
    # leaving the old implicit default of "trusted".
    op.execute("UPDATE document_chunks SET quote_verified = false")


def downgrade() -> None:
    op.drop_index("ix_documents_verification_status", table_name="documents")
    op.drop_column("document_chunks", "quote_verified")
    op.drop_column("documents", "verification_status")
