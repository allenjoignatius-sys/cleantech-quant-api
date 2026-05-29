"""Enterprise multi-tenancy: organizations, RBAC roles, scenarios, carbon prices

Revision ID: 0002
Revises: 0001
Create Date: 2025-05-29 00:00:00.000000

Adds the B2B enterprise data model on top of the v1 schema:
  * organizations (multi-tenant workspaces)
  * users.organization_id + users.role (RBAC)
  * scenarios (saved/shared LCOH models for multi-scenario comparison)
  * carbon_prices (EU-ETS & equivalents time series)
  * projects.product_type + projects.organization_id (commodity + org scoping)
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

# create_type=False: we create/drop these explicitly (checkfirst) so the inline
# column references must not also emit CREATE TYPE.
org_role = postgresql.ENUM("owner", "admin", "analyst", "viewer", name="orgrole", create_type=False)
product_type = postgresql.ENUM("hydrogen", "ammonia", "methanol", "saf", name="producttype", create_type=False)
# subscriptionplan already exists from revision 0001 — reference without creating.
subscription_plan = postgresql.ENUM(
    "free", "analyst", "enterprise", name="subscriptionplan", create_type=False
)


def upgrade() -> None:
    bind = op.get_bind()
    org_role.create(bind, checkfirst=True)
    product_type.create(bind, checkfirst=True)

    # ── organizations ─────────────────────────────────────────────────────────
    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(120), nullable=False),
        sa.Column("plan", subscription_plan, server_default="free", nullable=False),
        sa.Column("seats", sa.Integer(), server_default="5"),
        sa.Column("billing_email", sa.String(255)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)

    # ── users: org membership + RBAC role ───────────────────────────────────────
    op.add_column("users", sa.Column("organization_id", postgresql.UUID(as_uuid=False),
                                     sa.ForeignKey("organizations.id", ondelete="SET NULL")))
    op.add_column("users", sa.Column("role", org_role, server_default="viewer", nullable=False))
    op.create_index("ix_users_organization_id", "users", ["organization_id"])

    # ── scenarios ───────────────────────────────────────────────────────────────
    op.create_table(
        "scenarios",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("organization_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("organizations.id", ondelete="CASCADE")),
        sa.Column("created_by", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("scenario_type", sa.String(50), server_default="lcoh"),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("policy", postgresql.JSONB()),
        sa.Column("product_type", product_type, server_default="hydrogen"),
        sa.Column("result_cache", postgresql.JSONB()),
        sa.Column("is_shared", sa.Boolean(), server_default="true"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_scenario_org_created", "scenarios", ["organization_id", "created_at"])

    # ── carbon_prices ────────────────────────────────────────────────────────────
    op.create_table(
        "carbon_prices",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("market", sa.String(50), nullable=False, server_default="EU_ETS"),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(8), server_default="EUR"),
        sa.Column("source", sa.String(255)),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_carbon_market_time", "carbon_prices", ["market", "captured_at"])
    op.create_index("ix_carbon_prices_captured_at", "carbon_prices", ["captured_at"])

    # ── projects: commodity type + org scoping ──────────────────────────────────
    op.add_column("projects", sa.Column("product_type", product_type, server_default="hydrogen"))
    op.add_column("projects", sa.Column("organization_id", postgresql.UUID(as_uuid=False),
                                        sa.ForeignKey("organizations.id", ondelete="SET NULL")))
    op.create_index("ix_projects_organization_id", "projects", ["organization_id"])


def downgrade() -> None:
    op.drop_index("ix_projects_organization_id", "projects")
    op.drop_column("projects", "organization_id")
    op.drop_column("projects", "product_type")

    op.drop_table("carbon_prices")
    op.drop_index("ix_scenario_org_created", "scenarios")
    op.drop_table("scenarios")

    op.drop_index("ix_users_organization_id", "users")
    op.drop_column("users", "role")
    op.drop_column("users", "organization_id")

    op.drop_index("ix_organizations_slug", "organizations")
    op.drop_table("organizations")

    bind = op.get_bind()
    product_type.drop(bind, checkfirst=True)
    org_role.drop(bind, checkfirst=True)
