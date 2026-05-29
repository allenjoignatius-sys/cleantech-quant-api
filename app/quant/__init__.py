"""
app.quant — Institutional-grade techno-economic engine.

Pure, dependency-light (numpy/scipy only) calculation modules with NO database
or web-framework coupling, so they can be unit-tested in isolation and reused
by API routes, Celery tasks, Excel/PDF exporters and the CLI.

Modules:
    lcoh         Levelised Cost of Hydrogen (electrolysis) + Policy/Subsidy engine
    commodities  Derived green-molecule costs (NH3, MeOH, SAF) from LCOH
    carbon       EU-ETS / carbon-market abatement value vs. grey/SMR hydrogen
    monte_carlo  Vectorised 10k-run stochastic LCOH simulation
    excel_export Working openpyxl financial model (live formulas, P&L, balance sheet)
"""

from app.quant.lcoh import (  # noqa: F401
    LCOHInputs,
    LCOHResult,
    PolicyConfig,
    POLICY_CATALOG,
    calculate_lcoh,
    levelized_lcoh,
)

__all__ = [
    "LCOHInputs",
    "LCOHResult",
    "PolicyConfig",
    "POLICY_CATALOG",
    "calculate_lcoh",
    "levelized_lcoh",
]
