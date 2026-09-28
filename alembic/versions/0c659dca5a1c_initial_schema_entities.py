"""initial_schema_entities

Revision ID: 0c659dca5a1c
Revises: 
Create Date: 2026-09-28 06:43:57.344980+00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0c659dca5a1c"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. users table
    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)
    op.create_index(op.f("ix_users_telegram_id"), "users", ["telegram_id"], unique=True)

    # 2. company_branches table
    op.create_table(
        "company_branches",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column(
            "platform_type",
            sa.Enum("YANDEX", "GIS2", "GOOGLE", "AVITO", name="platform_type_enum"),
            nullable=False,
        ),
        sa.Column("platform_url", sa.String(length=1024), nullable=False),
        sa.Column(
            "tone_of_voice",
            sa.Enum("OFFICIAL", "FRIENDLY", "HUMOROUS", name="tone_of_voice_enum"),
            server_default="OFFICIAL",
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_company_branches_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_branches")),
    )
    op.create_index(op.f("ix_company_branches_user_id"), "company_branches", ["user_id"], unique=False)

    # 3. subscriptions table
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("TRIAL", "ACTIVE", "PAST_DUE", "CANCELED", name="subscription_status_enum"),
            server_default="TRIAL",
            nullable=False,
        ),
        sa.Column("trial_ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_provider_id", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscriptions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
    )
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"], unique=False)

    # 4. reviews table
    op.create_table(
        "reviews",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=False),
        sa.Column("author_name", sa.String(length=255), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("generated_reply", sa.Text(), nullable=True),
        sa.Column("final_reply", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum("NEW", "PENDING_APPROVAL", "APPROVED", "REJECTED", "PUBLISHED", name="review_status_enum"),
            server_default="NEW",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating >= 1 AND rating <= 5", name="check_rating_range"),
        sa.ForeignKeyConstraint(
            ["branch_id"],
            ["company_branches.id"],
            name=op.f("fk_reviews_branch_id_company_branches"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reviews")),
        sa.UniqueConstraint("branch_id", "external_id", name="uq_branch_external_review"),
    )
    op.create_index(op.f("ix_reviews_branch_id"), "reviews", ["branch_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_reviews_branch_id"), table_name="reviews")
    op.drop_table("reviews")
    op.execute("DROP TYPE IF EXISTS review_status_enum")

    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_table("subscriptions")
    op.execute("DROP TYPE IF EXISTS subscription_status_enum")

    op.drop_index(op.f("ix_company_branches_user_id"), table_name="company_branches")
    op.drop_table("company_branches")
    op.execute("DROP TYPE IF EXISTS tone_of_voice_enum")
    op.execute("DROP TYPE IF EXISTS platform_type_enum")

    op.drop_index(op.f("ix_users_telegram_id"), table_name="users")
    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

