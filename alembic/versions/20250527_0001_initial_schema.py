"""Initial schema — all tables

Revision ID: 0001
Revises: 
Create Date: 2025-05-27 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users ────────────────────────────────────────────────────────────────
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("company", sa.String(255)),
        sa.Column("job_title", sa.String(255)),
        sa.Column("plan", sa.Enum("free", "analyst", "enterprise", name="subscriptionplan"),
                  server_default="free", nullable=False),
        sa.Column("stripe_customer_id", sa.String(255), unique=True),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("is_admin", sa.Boolean(), server_default="false"),
        sa.Column("requests_today", sa.Integer(), server_default="0"),
        sa.Column("requests_this_month", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("last_seen", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    # ── api_keys ─────────────────────────────────────────────────────────────
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("key_hash", sa.String(255), unique=True, nullable=False),
        sa.Column("key_prefix", sa.String(16), nullable=False),
        sa.Column("name", sa.String(100), server_default="Default Key"),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("last_used", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)

    # ── catalyst_benchmarks ──────────────────────────────────────────────────
    op.create_table(
        "catalyst_benchmarks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("catalyst_type",
                  sa.Enum("ruthenium", "nickel", "iron", "ni_ru_bimetallic", "cobalt", name="catalysttype"),
                  nullable=False),
        sa.Column("catalyst_composition", sa.String(255)),
        sa.Column("temperature_celsius", sa.Float(), nullable=False),
        sa.Column("pressure_bar", sa.Float()),
        sa.Column("space_velocity_h", sa.Float()),
        sa.Column("feed_nh3_purity_pct", sa.Float(), server_default="99.9"),
        sa.Column("nh3_conversion_pct", sa.Float(), nullable=False),
        sa.Column("h2_yield_pct", sa.Float()),
        sa.Column("h2_purity_ppm_nh3", sa.Float()),
        sa.Column("energy_penalty_pct", sa.Float()),
        sa.Column("catalyst_cost_usd_per_kg", sa.Float()),
        sa.Column("catalyst_lifetime_hours", sa.Integer()),
        sa.Column("capex_usd_per_tpd_h2", sa.Float()),
        sa.Column("opex_usd_per_kg_h2", sa.Float()),
        sa.Column("source_type",
                  sa.Enum("academic_paper", "patent", "regulatory_filing",
                          "company_disclosure", "conference_proceedings", name="datasource")),
        sa.Column("source_url", sa.Text()),
        sa.Column("source_doi", sa.String(255)),
        sa.Column("institution", sa.String(255)),
        sa.Column("year", sa.Integer()),
        sa.Column("scale", sa.String(50)),
        sa.Column("trl", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.Column("raw_data", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_catalyst_type_temp", "catalyst_benchmarks", ["catalyst_type", "temperature_celsius"])
    op.create_index("ix_catalyst_year", "catalyst_benchmarks", ["year"])

    # ── projects ─────────────────────────────────────────────────────────────
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("developer", sa.String(255)),
        sa.Column("location_country", sa.String(100)),
        sa.Column("location_city", sa.String(100)),
        sa.Column("latitude", sa.Float()),
        sa.Column("longitude", sa.Float()),
        sa.Column("cracker_capacity_tpd_h2", sa.Float()),
        sa.Column("technology_vendor", sa.String(255)),
        sa.Column("catalyst_type",
                  sa.Enum("ruthenium", "nickel", "iron", "ni_ru_bimetallic", "cobalt", name="catalysttype"),
                  existing_type=sa.Enum("ruthenium", "nickel", "iron", "ni_ru_bimetallic", "cobalt", name="catalysttype")),
        sa.Column("feedstock_source", sa.String(255)),
        sa.Column("status", sa.String(50)),
        sa.Column("announced_date", sa.DateTime(timezone=True)),
        sa.Column("fid_date", sa.DateTime(timezone=True)),
        sa.Column("construction_start", sa.DateTime(timezone=True)),
        sa.Column("commissioning_date", sa.DateTime(timezone=True)),
        sa.Column("target_operational_date", sa.DateTime(timezone=True)),
        sa.Column("total_capex_usd_millions", sa.Float()),
        sa.Column("financing_structure", sa.String(100)),
        sa.Column("offtaker", sa.String(255)),
        sa.Column("announcement_url", sa.Text()),
        sa.Column("press_release_url", sa.Text()),
        sa.Column("regulatory_filing_url", sa.Text()),
        sa.Column("tags", postgresql.JSONB(), server_default="[]"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_project_country_status", "projects", ["location_country", "status"])

    # ── degradation_curves ────────────────────────────────────────────────────
    op.create_table(
        "degradation_curves",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("catalyst_benchmark_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("catalyst_benchmarks.id")),
        sa.Column("project_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("projects.id")),
        sa.Column("hours_of_operation", sa.Integer(), nullable=False),
        sa.Column("conversion_pct", sa.Float(), nullable=False),
        sa.Column("energy_penalty_pct", sa.Float()),
        sa.Column("h2_purity_ppm_nh3", sa.Float()),
        sa.Column("temperature_drift_celsius", sa.Float()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ── cost_datapoints ──────────────────────────────────────────────────────
    op.create_table(
        "cost_datapoints",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("geography", sa.String(100), nullable=False),
        sa.Column("technology", sa.String(100)),
        sa.Column("production_scale_tpd", sa.Float()),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("nh3_feedstock_cost", sa.Float()),
        sa.Column("cracking_capex_levelized", sa.Float()),
        sa.Column("cracking_opex", sa.Float()),
        sa.Column("electricity_cost", sa.Float()),
        sa.Column("catalyst_replacement", sa.Float()),
        sa.Column("total_delivered_h2_cost", sa.Float(), nullable=False),
        sa.Column("discount_rate_pct", sa.Float(), server_default="8.0"),
        sa.Column("plant_lifetime_years", sa.Integer(), server_default="20"),
        sa.Column("capacity_factor_pct", sa.Float(), server_default="90.0"),
        sa.Column("source_type",
                  sa.Enum("academic_paper", "patent", "regulatory_filing",
                          "company_disclosure", "conference_proceedings", name="datasource"),
                  existing_type=sa.Enum("academic_paper", "patent", "regulatory_filing",
                                        "company_disclosure", "conference_proceedings", name="datasource")),
        sa.Column("source_url", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_cost_geo_year", "cost_datapoints", ["geography", "year"])

    # ── patents ──────────────────────────────────────────────────────────────
    op.create_table(
        "patents",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("patent_number", sa.String(50), unique=True, nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text()),
        sa.Column("assignee", sa.String(255)),
        sa.Column("inventors", postgresql.JSONB(), server_default="[]"),
        sa.Column("filing_date", sa.DateTime(timezone=True)),
        sa.Column("publication_date", sa.DateTime(timezone=True)),
        sa.Column("grant_date", sa.DateTime(timezone=True)),
        sa.Column("jurisdiction", sa.String(10)),
        sa.Column("ipc_codes", postgresql.JSONB(), server_default="[]"),
        sa.Column("catalyst_types_mentioned", postgresql.JSONB(), server_default="[]"),
        sa.Column("claimed_efficiency_pct", sa.Float()),
        sa.Column("source_url", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_patent_assignee_date", "patents", ["assignee", "filing_date"])

    # ── alerts ───────────────────────────────────────────────────────────────
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("alert_type",
                  sa.Enum("efficiency_threshold", "cost_movement", "regulatory_change",
                          "project_fid", "patent_filing", name="alerttype"), nullable=False),
        sa.Column("conditions", postgresql.JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("last_triggered", sa.DateTime(timezone=True)),
        sa.Column("trigger_count", sa.Integer(), server_default="0"),
        sa.Column("notification_channels", postgresql.JSONB(), server_default='["email"]'),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ── webhooks ─────────────────────────────────────────────────────────────
    op.create_table(
        "webhooks",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret", sa.String(64), nullable=False),
        sa.Column("events", postgresql.JSONB(), server_default='["*"]'),
        sa.Column("is_active", sa.Boolean(), server_default="true"),
        sa.Column("failure_count", sa.Integer(), server_default="0"),
        sa.Column("last_delivered", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )

    # ── reports ──────────────────────────────────────────────────────────────
    op.create_table(
        "reports",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("report_type", sa.String(100)),
        sa.Column("parameters", postgresql.JSONB()),
        sa.Column("status", sa.String(50), server_default="pending"),
        sa.Column("s3_url", sa.Text()),
        sa.Column("row_count", sa.Integer()),
        sa.Column("file_size_bytes", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
    )

    # ── audit_logs ───────────────────────────────────────────────────────────
    op.create_table(
        "audit_logs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id")),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("resource_type", sa.String(100)),
        sa.Column("resource_id", postgresql.UUID(as_uuid=False)),
        sa.Column("ip_address", sa.String(45)),
        sa.Column("user_agent", sa.Text()),
        sa.Column("extra", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_audit_user_created", "audit_logs", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_logs")
    op.drop_table("reports")
    op.drop_table("webhooks")
    op.drop_table("alerts")
    op.drop_table("patents")
    op.drop_table("cost_datapoints")
    op.drop_table("degradation_curves")
    op.drop_table("projects")
    op.drop_table("catalyst_benchmarks")
    op.drop_table("api_keys")
    op.drop_table("users")
    # Drop enums
    op.execute("DROP TYPE IF EXISTS subscriptionplan")
    op.execute("DROP TYPE IF EXISTS catalysttype")
    op.execute("DROP TYPE IF EXISTS datasource")
    op.execute("DROP TYPE IF EXISTS alerttype")
