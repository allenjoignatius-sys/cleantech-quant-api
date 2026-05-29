"""app.reports — automated document generation (ReportLab PDF briefings)."""
from app.reports.pdf import build_weekly_briefing, WeeklyBriefingData  # noqa: F401

__all__ = ["build_weekly_briefing", "WeeklyBriefingData"]
