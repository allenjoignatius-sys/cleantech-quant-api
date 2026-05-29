"""
Automated weekly market-intelligence PDF briefing (ReportLab).

Designed to be invoked by a Celery cron task: it takes a structured weekly
data payload (new FID announcements, grid-price anomalies, catalyst highlights)
and renders a formatted, branded PDF, returned as bytes for S3 upload / email.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

INK = colors.HexColor("#0D1117")
ACCENT = colors.HexColor("#1F6FEB")
LIME = colors.HexColor("#2EA043")
MUTED = colors.HexColor("#8B949E")
PANEL = colors.HexColor("#161B22")


@dataclass
class WeeklyBriefingData:
    week_of: str = field(default_factory=lambda: date.today().isoformat())
    new_fids: List[Dict[str, Any]] = field(default_factory=list)
    price_anomalies: List[Dict[str, Any]] = field(default_factory=list)
    catalyst_highlights: List[Dict[str, Any]] = field(default_factory=list)
    summary: Optional[str] = None


def _styles():
    ss = getSampleStyleSheet()
    ss.add(ParagraphStyle("CTQTitle", parent=ss["Title"], textColor=INK, fontSize=22, spaceAfter=4))
    ss.add(ParagraphStyle("CTQSub", parent=ss["Normal"], textColor=MUTED, fontSize=10, spaceAfter=12))
    ss.add(ParagraphStyle("CTQH2", parent=ss["Heading2"], textColor=ACCENT, fontSize=14, spaceBefore=14, spaceAfter=6))
    ss.add(ParagraphStyle("CTQBody", parent=ss["Normal"], fontSize=9.5, leading=13))
    ss.add(ParagraphStyle("CTQCell", parent=ss["Normal"], fontSize=8.5, leading=11))
    ss.add(ParagraphStyle("CTQCellLink", parent=ss["Normal"], fontSize=8.5, leading=11, textColor=ACCENT))
    return ss


def _link(text: str, url: Optional[str]) -> str:
    if not url:
        return text or "—"
    return f'<a href="{url}" color="#1F6FEB">{text or url}</a>'


def _table(headers: List[str], rows: List[List[str]], ss, col_widths=None) -> Table:
    data = [[Paragraph(f"<b>{h}</b>", ss["CTQCell"]) for h in headers]]
    data += rows
    t = Table(data, repeatRows=1, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), INK),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F0F3F6")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D0D7DE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
    ]))
    return t


def build_weekly_briefing(data: WeeklyBriefingData) -> bytes:
    """Render the weekly briefing and return ``.pdf`` bytes."""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"CleanTech Quant Weekly Briefing — {data.week_of}",
        author="CleanTech Quant",
    )
    ss = _styles()
    story: List[Any] = []

    story.append(Paragraph("CleanTech Quant — Weekly Market Intelligence", ss["CTQTitle"]))
    story.append(Paragraph(f"Week of {data.week_of} · Green Hydrogen &amp; Derivatives Desk", ss["CTQSub"]))
    story.append(HRFlowable(width="100%", thickness=1.2, color=ACCENT, spaceAfter=8))

    if data.summary:
        story.append(Paragraph(data.summary, ss["CTQBody"]))

    # ── New FID announcements ──────────────────────────────────────────────
    story.append(Paragraph(f"New Final Investment Decisions ({len(data.new_fids)})", ss["CTQH2"]))
    if data.new_fids:
        rows = []
        for f in data.new_fids:
            rows.append([
                Paragraph(str(f.get("name", "—")), ss["CTQCell"]),
                Paragraph(str(f.get("country", "—")), ss["CTQCell"]),
                Paragraph(_fmt_num(f.get("capacity_mw"), " MW"), ss["CTQCell"]),
                Paragraph(_fmt_num(f.get("investment_usd_millions"), "", prefix="$", suffix_m=True), ss["CTQCell"]),
                Paragraph(_link("source", f.get("url")), ss["CTQCellLink"]),
            ])
        story.append(_table(["Project", "Country", "Capacity", "Investment", "Reference"], rows, ss,
                            col_widths=[55 * mm, 25 * mm, 22 * mm, 28 * mm, 22 * mm]))
    else:
        story.append(Paragraph("No new FID announcements this week.", ss["CTQBody"]))

    # ── Grid price anomalies ───────────────────────────────────────────────
    story.append(Paragraph(f"Grid Price Anomalies ({len(data.price_anomalies)})", ss["CTQH2"]))
    if data.price_anomalies:
        rows = []
        for a in data.price_anomalies:
            rows.append([
                Paragraph(str(a.get("geography", "—")), ss["CTQCell"]),
                Paragraph(_fmt_num(a.get("price_eur_mwh"), " €/MWh"), ss["CTQCell"]),
                Paragraph(_fmt_num(a.get("z_score"), "σ", signed=True), ss["CTQCell"]),
                Paragraph(str(a.get("note", "")), ss["CTQCell"]),
            ])
        story.append(_table(["Market", "Spot", "Deviation", "Note"], rows, ss,
                            col_widths=[35 * mm, 30 * mm, 25 * mm, 62 * mm]))
    else:
        story.append(Paragraph("No significant grid-price anomalies detected.", ss["CTQBody"]))

    # ── Catalyst highlights ────────────────────────────────────────────────
    if data.catalyst_highlights:
        story.append(Paragraph(f"Catalyst &amp; Efficiency Highlights ({len(data.catalyst_highlights)})", ss["CTQH2"]))
        rows = []
        for c in data.catalyst_highlights:
            rows.append([
                Paragraph(str(c.get("title", "—")), ss["CTQCell"]),
                Paragraph(str(c.get("metric", "")), ss["CTQCell"]),
                Paragraph(_link("DOI", c.get("url") or (f"https://doi.org/{c['doi']}" if c.get("doi") else None)),
                          ss["CTQCellLink"]),
            ])
        story.append(_table(["Finding", "Headline metric", "Reference"], rows, ss,
                            col_widths=[80 * mm, 40 * mm, 22 * mm]))

    story.append(Spacer(1, 12))
    story.append(HRFlowable(width="100%", thickness=0.6, color=MUTED, spaceBefore=6, spaceAfter=4))
    story.append(Paragraph(
        "Generated automatically by CleanTech Quant. Figures are model-derived; "
        "click any reference to view the underlying source.", ss["CTQSub"]))

    doc.build(story)
    return buf.getvalue()


def _fmt_num(v, unit="", prefix="", signed=False, suffix_m=False) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    s = f"{f:+.1f}" if signed else f"{f:,.0f}" if abs(f) >= 100 else f"{f:,.1f}"
    if suffix_m:
        unit = "m"
    return f"{prefix}{s}{unit}"
