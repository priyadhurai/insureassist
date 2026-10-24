"""Initial schema: users, documents, document_access, query_logs, feedback, audit_events.

Revision ID: 0001
Revises:
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("role IN ('viewer', 'editor', 'admin')", name="ck_users_role"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.String(40), primary_key=True),
        sa.Column("checksum_sha256", sa.String(64), nullable=False, unique=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("chunk_count", sa.Integer(), nullable=False),
        sa.Column(
            "owner_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "document_access",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "document_id",
            sa.String(40),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
        sa.Column("role", sa.String(20), nullable=True, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(user_id IS NULL) <> (role IS NULL)", name="ck_access_one_target"),
        sa.UniqueConstraint("document_id", "user_id", name="uq_access_user"),
        sa.UniqueConstraint("document_id", "role", name="uq_access_role"),
    )
    op.create_table(
        "query_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("question_sha256", sa.String(64), nullable=False),
        sa.Column("question_chars", sa.Integer(), nullable=False),
        sa.Column("question_text", sa.String(4000), nullable=True),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("cited_chunk_ids", sa.JSON(), nullable=False),
        sa.Column("retrieval_ms", sa.Integer(), nullable=False),
        sa.Column("generation_ms", sa.Integer(), nullable=False),
        sa.Column("llm_model", sa.String(100), nullable=False),
    )
    op.create_table(
        "feedback",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "query_id",
            sa.Uuid(),
            sa.ForeignKey("query_logs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("rating", sa.String(4), nullable=False),
        sa.Column("comment", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("rating IN ('up', 'down')", name="ck_feedback_rating"),
        sa.UniqueConstraint("query_id", "user_id", name="uq_feedback_once"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, index=True),
        sa.Column(
            "actor_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(50), nullable=False),
        sa.Column("target_type", sa.String(30), nullable=False),
        sa.Column("target_id", sa.String(80), nullable=True),
        sa.Column("outcome", sa.String(10), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
    )


def downgrade() -> None:
    for table in (
        "audit_events",
        "feedback",
        "query_logs",
        "document_access",
        "documents",
        "users",
    ):
        op.drop_table(table)
