"""SQLAlchemy ORM models for all database entities."""
from sqlalchemy import (
    Column, String, Float, Integer, DateTime, Boolean,
    ForeignKey, Text, JSON, Enum as SAEnum, Index
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
import uuid
import enum
from app.database import Base


def gen_uuid():
    return str(uuid.uuid4())


# ─── Enums ────────────────────────────────────────────────────────────────────

class SubscriptionPlan(str, enum.Enum):
    free = "free"
    analyst = "analyst"
    enterprise = "enterprise"

class OrgRole(str, enum.Enum):
    """Role-based access control levels within an Organization (workspace)."""
    owner = "owner"      # full control incl. billing + member management
    admin = "admin"      # manage members, trigger scrapes/syncs, manage data
    analyst = "analyst"  # create/edit scenarios, run models, create projects
    viewer = "viewer"    # read-only

class ProductType(str, enum.Enum):
    hydrogen = "hydrogen"
    ammonia = "ammonia"
    methanol = "methanol"
    saf = "saf"

class CatalystType(str, enum.Enum):
    ruthenium = "ruthenium"
    nickel = "nickel"
    iron = "iron"
    ni_ru_bimetallic = "ni_ru_bimetallic"
    cobalt = "cobalt"

class DataSource(str, enum.Enum):
    academic_paper = "academic_paper"
    patent = "patent"
    regulatory_filing = "regulatory_filing"
    company_disclosure = "company_disclosure"
    conference_proceedings = "conference_proceedings"

class AlertType(str, enum.Enum):
    efficiency_threshold = "efficiency_threshold"
    cost_movement = "cost_movement"
    regulatory_change = "regulatory_change"
    project_fid = "project_fid"
    patent_filing = "patent_filing"


# ─── Users & Auth ─────────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    company = Column(String(255))
    job_title = Column(String(255))
    plan = Column(SAEnum(SubscriptionPlan), default=SubscriptionPlan.free)
    stripe_customer_id = Column(String(255), unique=True)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)  # platform super-admin (cross-org)

    # ── Multi-tenant workspace membership + RBAC ──────────────────────────────
    organization_id = Column(UUID, ForeignKey("organizations.id", ondelete="SET NULL"), index=True)
    role = Column(SAEnum(OrgRole), default=OrgRole.viewer, nullable=False)

    requests_today = Column(Integer, default=0)
    requests_this_month = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="members", foreign_keys=[organization_id])
    api_keys = relationship("APIKey", back_populates="user", cascade="all, delete-orphan")
    alerts = relationship("Alert", back_populates="user", cascade="all, delete-orphan")
    webhooks = relationship("Webhook", back_populates="user", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="user")
    scenarios = relationship("Scenario", back_populates="created_by_user", foreign_keys="Scenario.created_by")


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    user_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    key_hash = Column(String(255), unique=True, nullable=False)
    key_prefix = Column(String(16), nullable=False)  # first 8 chars for display
    name = Column(String(100), default="Default Key")
    is_active = Column(Boolean, default=True)
    last_used = Column(DateTime(timezone=True))
    expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="api_keys")

    __table_args__ = (Index("ix_api_keys_key_hash", "key_hash"),)


# ─── Organizations (Multi-tenant Workspaces) ──────────────────────────────────

class Organization(Base):
    """A team/workspace. Users belong to one org; scenarios & org-private
    projects are shared across all members of the org."""
    __tablename__ = "organizations"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    slug = Column(String(120), unique=True, nullable=False, index=True)
    plan = Column(SAEnum(SubscriptionPlan), default=SubscriptionPlan.free, nullable=False)
    seats = Column(Integer, default=5)
    billing_email = Column(String(255))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    members = relationship(
        "User", back_populates="organization", foreign_keys="User.organization_id"
    )
    scenarios = relationship(
        "Scenario", back_populates="organization", cascade="all, delete-orphan"
    )


# ─── Scenarios (Saved/Shared LCOH models for multi-scenario comparison) ────────

class Scenario(Base):
    __tablename__ = "scenarios"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    organization_id = Column(
        UUID, ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    created_by = Column(UUID, ForeignKey("users.id", ondelete="SET NULL"))
    name = Column(String(255), nullable=False)
    description = Column(Text)
    scenario_type = Column(String(50), default="lcoh")  # lcoh | commodity

    inputs = Column(JSON, nullable=False)        # serialized LCOHInputs
    policy = Column(JSON)                         # serialized PolicyConfig
    product_type = Column(SAEnum(ProductType), default=ProductType.hydrogen)
    result_cache = Column(JSON)                   # last computed LCOHResult snapshot

    is_shared = Column(Boolean, default=True)     # visible to whole org
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    organization = relationship("Organization", back_populates="scenarios")
    created_by_user = relationship("User", back_populates="scenarios", foreign_keys=[created_by])

    __table_args__ = (Index("ix_scenario_org_created", "organization_id", "created_at"),)


# ─── Carbon Market Prices (EU ETS & equivalents) ──────────────────────────────

class CarbonPrice(Base):
    __tablename__ = "carbon_prices"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    market = Column(String(50), nullable=False, default="EU_ETS")  # EU_ETS | UK_ETS | CCA
    price = Column(Float, nullable=False)         # per tonne CO2e in `currency`
    currency = Column(String(8), default="EUR")
    source = Column(String(255))
    captured_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (Index("ix_carbon_market_time", "market", "captured_at"),)


# ─── Core Data: Catalysts ─────────────────────────────────────────────────────

class CatalystBenchmark(Base):
    __tablename__ = "catalyst_benchmarks"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    catalyst_type = Column(SAEnum(CatalystType), nullable=False)
    catalyst_composition = Column(String(255))  # e.g. "Ni-Ru 5wt%"

    # Operating conditions
    temperature_celsius = Column(Float, nullable=False)
    pressure_bar = Column(Float)
    space_velocity_h = Column(Float)  # WHSV or GHSV
    feed_nh3_purity_pct = Column(Float, default=99.9)

    # Performance metrics
    nh3_conversion_pct = Column(Float, nullable=False)     # 0-100
    h2_yield_pct = Column(Float)
    h2_purity_ppm_nh3 = Column(Float)    # residual NH3 in H2 product
    energy_penalty_pct = Column(Float)   # energy lost in cracking process

    # Economics
    catalyst_cost_usd_per_kg = Column(Float)
    catalyst_lifetime_hours = Column(Integer)
    capex_usd_per_tpd_h2 = Column(Float)    # capital cost per tonne H2/day
    opex_usd_per_kg_h2 = Column(Float)

    # Metadata
    source_type = Column(SAEnum(DataSource))
    source_url = Column(Text)
    source_doi = Column(String(255))
    institution = Column(String(255))
    year = Column(Integer)
    scale = Column(String(50))  # "lab", "pilot", "demonstration", "commercial"
    trl = Column(Integer)       # Technology Readiness Level 1-9
    notes = Column(Text)
    raw_data = Column(JSON)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        Index("ix_catalyst_type_temp", "catalyst_type", "temperature_celsius"),
        Index("ix_catalyst_year", "year"),
    )


class DegradationCurve(Base):
    """Time-series efficiency degradation data for a specific cracker installation."""
    __tablename__ = "degradation_curves"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    catalyst_benchmark_id = Column(UUID, ForeignKey("catalyst_benchmarks.id"))
    project_id = Column(UUID, ForeignKey("projects.id"))

    hours_of_operation = Column(Integer, nullable=False)
    conversion_pct = Column(Float, nullable=False)   # NH3 conversion at this timestamp
    energy_penalty_pct = Column(Float)
    h2_purity_ppm_nh3 = Column(Float)
    temperature_drift_celsius = Column(Float)  # deviation from set point

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Reverse relationships (previously missing — broke mapper configuration)
    project = relationship("Project", back_populates="degradation_curves")
    catalyst_benchmark = relationship("CatalystBenchmark")


# ─── Cost Models ──────────────────────────────────────────────────────────────

class CostDatapoint(Base):
    __tablename__ = "cost_datapoints"

    id = Column(UUID, primary_key=True, default=gen_uuid)

    # Context
    geography = Column(String(100), nullable=False)   # "Japan", "Germany", "Australia"
    technology = Column(String(100))                  # "Topsoe", "KBR", "Amogy"
    production_scale_tpd = Column(Float)              # tonnes H2 per day
    year = Column(Integer, nullable=False)

    # Cost components (all USD/kg H2 unless noted)
    nh3_feedstock_cost = Column(Float)     # delivered NH3 price
    cracking_capex_levelized = Column(Float)
    cracking_opex = Column(Float)
    electricity_cost = Column(Float)       # for compression, purification
    catalyst_replacement = Column(Float)
    total_delivered_h2_cost = Column(Float, nullable=False)

    # Sensitivity parameters
    discount_rate_pct = Column(Float, default=8.0)
    plant_lifetime_years = Column(Integer, default=20)
    capacity_factor_pct = Column(Float, default=90.0)

    source_type = Column(SAEnum(DataSource))
    source_url = Column(Text)
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_cost_geo_year", "geography", "year"),)


# ─── Project Intelligence ──────────────────────────────────────────────────────

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    name = Column(String(255), nullable=False)
    developer = Column(String(255))
    location_country = Column(String(100))
    location_city = Column(String(100))
    latitude = Column(Float)
    longitude = Column(Float)

    # Project specs
    cracker_capacity_tpd_h2 = Column(Float)
    technology_vendor = Column(String(255))
    catalyst_type = Column(SAEnum(CatalystType))
    feedstock_source = Column(String(255))   # where NH3 comes from
    product_type = Column(SAEnum(ProductType), default=ProductType.hydrogen)

    # Org scoping: NULL = global/platform project (visible to all);
    # set = private to that organization's workspace.
    organization_id = Column(UUID, ForeignKey("organizations.id", ondelete="SET NULL"), index=True)

    # Status tracking
    status = Column(String(50))  # "announced", "fid", "construction", "operational"
    announced_date = Column(DateTime(timezone=True))
    fid_date = Column(DateTime(timezone=True))
    construction_start = Column(DateTime(timezone=True))
    commissioning_date = Column(DateTime(timezone=True))
    target_operational_date = Column(DateTime(timezone=True))

    # Financial
    total_capex_usd_millions = Column(Float)
    financing_structure = Column(String(100))  # "project finance", "balance sheet"
    offtaker = Column(String(255))

    # Links & metadata
    announcement_url = Column(Text)
    press_release_url = Column(Text)
    regulatory_filing_url = Column(Text)
    tags = Column(JSON, default=list)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    degradation_curves = relationship("DegradationCurve", back_populates="project")

    __table_args__ = (Index("ix_project_country_status", "location_country", "status"),)


# ─── Patents ──────────────────────────────────────────────────────────────────

class Patent(Base):
    __tablename__ = "patents"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    patent_number = Column(String(50), unique=True, nullable=False)
    title = Column(Text, nullable=False)
    abstract = Column(Text)
    assignee = Column(String(255))
    inventors = Column(JSON, default=list)
    filing_date = Column(DateTime(timezone=True))
    publication_date = Column(DateTime(timezone=True))
    grant_date = Column(DateTime(timezone=True))
    jurisdiction = Column(String(10))    # "US", "EP", "JP", "KR"
    ipc_codes = Column(JSON, default=list)  # International Patent Classification
    catalyst_types_mentioned = Column(JSON, default=list)
    claimed_efficiency_pct = Column(Float)  # if disclosed
    source_url = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_patent_assignee_date", "assignee", "filing_date"),)


# ─── Alerts ───────────────────────────────────────────────────────────────────

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    user_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    alert_type = Column(SAEnum(AlertType), nullable=False)
    conditions = Column(JSON, nullable=False)  # flexible condition logic
    is_active = Column(Boolean, default=True)
    last_triggered = Column(DateTime(timezone=True))
    trigger_count = Column(Integer, default=0)
    notification_channels = Column(JSON, default=list)  # ["email", "webhook", "slack"]

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    user = relationship("User", back_populates="alerts")


# ─── Webhooks ─────────────────────────────────────────────────────────────────

class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    user_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    url = Column(Text, nullable=False)
    secret = Column(String(64), nullable=False)
    events = Column(JSON, default=list)   # e.g. ["project.fid", "catalyst.new_benchmark"]
    is_active = Column(Boolean, default=True)
    failure_count = Column(Integer, default=0)
    last_delivered = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="webhooks")


# ─── Reports ──────────────────────────────────────────────────────────────────

class Report(Base):
    __tablename__ = "reports"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    user_id = Column(UUID, ForeignKey("users.id"), nullable=False)
    name = Column(String(255), nullable=False)
    report_type = Column(String(100))   # "catalyst_comparison", "cost_curve", "project_overview"
    parameters = Column(JSON)
    status = Column(String(50), default="pending")  # pending, running, complete, failed
    s3_url = Column(Text)
    row_count = Column(Integer)
    file_size_bytes = Column(Integer)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True))

    user = relationship("User", back_populates="reports")


# ─── Audit Log ────────────────────────────────────────────────────────────────

class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(UUID, primary_key=True, default=gen_uuid)
    user_id = Column(UUID, ForeignKey("users.id"))
    action = Column(String(100), nullable=False)
    resource_type = Column(String(100))
    resource_id = Column(UUID)
    ip_address = Column(String(45))
    user_agent = Column(Text)
    extra = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_audit_user_created", "user_id", "created_at"),)
