"""
scripts/seed_data.py
Populates the database with reference data from published literature.
Run once after initial migration:
    docker compose exec api python scripts/seed_data.py
    # or via CLI:
    python -m app.cli seed-database
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SEED_BENCHMARKS = [
    # ── Ruthenium catalysts (highest performance, highest cost) ─────────────
    {
        "catalyst_type": "ruthenium", "catalyst_composition": "Ru/MgO (K-promoted)",
        "temperature_celsius": 400.0, "pressure_bar": 1.0, "nh3_conversion_pct": 97.3,
        "energy_penalty_pct": 13.1, "catalyst_cost_usd_per_kg": 17500.0,
        "catalyst_lifetime_hours": 26280, "opex_usd_per_kg_h2": 3.42,
        "source_type": "academic_paper", "source_doi": "10.1039/D4EE00540A",
        "institution": "University of Tokyo / CSIRO", "year": 2024, "scale": "lab", "trl": 6,
        "notes": "K-promoted Ru/MgO. Low-temperature record. Sintering-resistant to 450°C.",
    },
    {
        "catalyst_type": "ruthenium", "catalyst_composition": "Ru/C (carbon support)",
        "temperature_celsius": 450.0, "pressure_bar": 3.0, "nh3_conversion_pct": 99.2,
        "energy_penalty_pct": 13.2, "catalyst_cost_usd_per_kg": 15000.0,
        "catalyst_lifetime_hours": 35040, "opex_usd_per_kg_h2": 3.35,
        "source_type": "company_disclosure",
        "institution": "Topsoe / KBR (commercial reference)", "year": 2024,
        "scale": "demonstration", "trl": 7,
        "notes": "Baseline commercial Ru cracker reference used by JERA Blue Point TEA.",
    },
    {
        "catalyst_type": "ruthenium", "catalyst_composition": "Ru-Cs/Al₂O₃",
        "temperature_celsius": 430.0, "pressure_bar": 1.5, "nh3_conversion_pct": 98.7,
        "energy_penalty_pct": 13.5, "catalyst_cost_usd_per_kg": 16200.0,
        "catalyst_lifetime_hours": 30000, "opex_usd_per_kg_h2": 3.38,
        "source_type": "academic_paper", "source_doi": "10.1016/j.ijhydene.2023.11.018",
        "institution": "Tohoku University", "year": 2023, "scale": "lab", "trl": 6,
    },

    # ── Ni-Ru bimetallic (performance/cost balance) ─────────────────────────
    {
        "catalyst_type": "ni_ru_bimetallic", "catalyst_composition": "Ni-Ru 5wt% / Al₂O₃",
        "temperature_celsius": 500.0, "pressure_bar": 1.0, "nh3_conversion_pct": 96.8,
        "energy_penalty_pct": 14.9, "catalyst_cost_usd_per_kg": 2100.0,
        "catalyst_lifetime_hours": 17520, "opex_usd_per_kg_h2": 2.89,
        "source_type": "academic_paper", "source_doi": "10.1039/D3SC05838A",
        "institution": "Amogy / CSIRO joint paper", "year": 2024,
        "scale": "pilot", "trl": 6,
        "notes": "Amogy's flagship formulation for maritime applications.",
    },
    {
        "catalyst_type": "ni_ru_bimetallic", "catalyst_composition": "Ni-Ru 2wt% / CeO₂",
        "temperature_celsius": 480.0, "pressure_bar": 2.0, "nh3_conversion_pct": 94.3,
        "energy_penalty_pct": 15.8, "catalyst_cost_usd_per_kg": 890.0,
        "catalyst_lifetime_hours": 12000, "opex_usd_per_kg_h2": 2.61,
        "source_type": "academic_paper", "source_doi": "10.1021/acscatal.3c05882",
        "institution": "KIST (Korea Institute of Science and Technology)", "year": 2023,
        "scale": "lab", "trl": 5,
    },

    # ── Nickel catalysts (low cost, high temperature) ────────────────────────
    {
        "catalyst_type": "nickel", "catalyst_composition": "Ni-Fe / Al₂O₃",
        "temperature_celsius": 600.0, "pressure_bar": 1.0, "nh3_conversion_pct": 87.3,
        "energy_penalty_pct": 18.7, "catalyst_cost_usd_per_kg": 45.0,
        "catalyst_lifetime_hours": 8760, "opex_usd_per_kg_h2": 2.18,
        "source_type": "company_disclosure",
        "institution": "ThyssenKrupp Uhde (pilot plant)", "year": 2024,
        "scale": "pilot", "trl": 6,
        "notes": "TK Uhde first pilot-scale results. 91% claimed at 580°C in subsequent optimisation.",
    },
    {
        "catalyst_type": "nickel", "catalyst_composition": "Ni/SiO₂-Al₂O₃",
        "temperature_celsius": 550.0, "pressure_bar": 1.0, "nh3_conversion_pct": 82.4,
        "energy_penalty_pct": 20.3, "catalyst_cost_usd_per_kg": 38.0,
        "catalyst_lifetime_hours": 7000, "opex_usd_per_kg_h2": 2.04,
        "source_type": "academic_paper", "source_doi": "10.1016/j.apcatb.2023.122634",
        "institution": "Dalian Institute of Chemical Physics (DICP)", "year": 2023,
        "scale": "lab", "trl": 5,
    },

    # ── Iron-based (lowest cost, research stage) ─────────────────────────────
    {
        "catalyst_type": "iron", "catalyst_composition": "Fe-K₂O / Al₂O₃",
        "temperature_celsius": 620.0, "pressure_bar": 1.0, "nh3_conversion_pct": 82.1,
        "energy_penalty_pct": 22.4, "catalyst_cost_usd_per_kg": 28.0,
        "catalyst_lifetime_hours": 5000, "opex_usd_per_kg_h2": 1.87,
        "source_type": "academic_paper", "source_doi": "10.1039/D3CY01502J",
        "institution": "TU Delft / Academic Research Consortium", "year": 2023,
        "scale": "lab", "trl": 4,
        "notes": "Promoted iron catalyst. Attractive for large-scale given Haber-Bosch catalyst heritage.",
    },
]

SEED_COST_DATAPOINTS = [
    {"geography": "Japan", "technology": "Ruthenium cracker", "production_scale_tpd": 250.0,
     "year": 2025, "nh3_feedstock_cost": 0.72, "cracking_capex_levelized": 0.85,
     "cracking_opex": 0.31, "electricity_cost": 0.12, "catalyst_replacement": 0.12,
     "total_delivered_h2_cost": 4.12, "discount_rate_pct": 7.0, "source_type": "regulatory_filing",
     "notes": "METI H2 import chain TEA 2024. JERA Blue Point reference scenario."},
    {"geography": "Japan", "technology": "Ni-Ru bimetallic cracker", "production_scale_tpd": 250.0,
     "year": 2025, "nh3_feedstock_cost": 0.72, "cracking_capex_levelized": 0.91,
     "cracking_opex": 0.28, "electricity_cost": 0.14, "catalyst_replacement": 0.04,
     "total_delivered_h2_cost": 3.89, "source_type": "academic_paper",
     "notes": "Based on DOE H2A model adapted for Ni-Ru catalyst parameters."},
    {"geography": "Germany", "technology": "Ruthenium cracker", "production_scale_tpd": 100.0,
     "year": 2025, "nh3_feedstock_cost": 0.85, "cracking_capex_levelized": 0.98,
     "cracking_opex": 0.35, "electricity_cost": 0.19, "catalyst_replacement": 0.12,
     "total_delivered_h2_cost": 5.29, "source_type": "academic_paper",
     "notes": "Fraunhofer ISI Germany H2 import report 2023. Uniper import terminal reference."},
    {"geography": "Korea", "technology": "Ruthenium cracker", "production_scale_tpd": 150.0,
     "year": 2025, "nh3_feedstock_cost": 0.68, "cracking_capex_levelized": 0.87,
     "cracking_opex": 0.30, "electricity_cost": 0.11, "catalyst_replacement": 0.12,
     "total_delivered_h2_cost": 3.78, "source_type": "regulatory_filing",
     "notes": "POSCO / KEPCO joint import chain study 2023."},
    {"geography": "Japan", "technology": "Ruthenium cracker", "production_scale_tpd": 250.0,
     "year": 2030, "nh3_feedstock_cost": 0.58, "cracking_capex_levelized": 0.71,
     "cracking_opex": 0.25, "electricity_cost": 0.09, "catalyst_replacement": 0.08,
     "total_delivered_h2_cost": 3.11, "source_type": "academic_paper",
     "notes": "IEA 2023 net zero scenario — assumes scale learning rate + green NH3 cost reduction."},
]

SEED_PROJECTS = [
    {"name": "JERA Blue Point Green Hydrogen Project", "developer": "JERA Co. / Mitsui & Co.",
     "location_country": "USA", "location_city": "St. James, Louisiana",
     "latitude": 29.98, "longitude": -90.77,
     "cracker_capacity_tpd_h2": 250.0, "technology_vendor": "Topsoe / KBR",
     "catalyst_type": "ruthenium", "feedstock_source": "Blue Point green NH3 plant",
     "status": "construction", "total_capex_usd_millions": 4000.0,
     "financing_structure": "project finance", "offtaker": "JERA Co. (Japan)",
     "target_operational_date": "2029-01-01",
     "announcement_url": "https://www.jera.co.jp/en/news/20250415",
     "tags": ["japan", "green-h2", "large-scale", "fid-2025"]},
    {"name": "Air Liquide Antwerp NH₃ Cracker", "developer": "Air Liquide",
     "location_country": "Belgium", "location_city": "Antwerp",
     "latitude": 51.22, "longitude": 4.40,
     "cracker_capacity_tpd_h2": 20.0, "technology_vendor": "Air Liquide proprietary",
     "catalyst_type": "ruthenium", "feedstock_source": "Port of Antwerp NH3 imports",
     "status": "operational", "total_capex_usd_millions": None,
     "financing_structure": "balance sheet", "offtaker": "Industrial customers (EU)",
     "target_operational_date": "2025-01-01",
     "tags": ["eu", "operational", "demonstration"]},
    {"name": "Uniper Germany H2 Import Terminal", "developer": "Uniper SE",
     "location_country": "Germany", "location_city": "Wilhelmshaven",
     "latitude": 53.53, "longitude": 8.11,
     "cracker_capacity_tpd_h2": 100.0, "technology_vendor": "ThyssenKrupp Uhde",
     "feedstock_source": "Norwegian / Middle East green ammonia",
     "status": "announced", "total_capex_usd_millions": 800.0,
     "target_operational_date": "2028-01-01",
     "tags": ["eu", "germany", "import-terminal"]},
    {"name": "Amogy Maritime Demonstration", "developer": "Amogy Inc.",
     "location_country": "USA", "location_city": "New York, NY",
     "latitude": 40.71, "longitude": -74.01,
     "cracker_capacity_tpd_h2": 1.0, "technology_vendor": "Amogy",
     "catalyst_type": "ni_ru_bimetallic",
     "status": "fid", "total_capex_usd_millions": None,
     "financing_structure": "balance sheet", "offtaker": "Maritime demonstration",
     "target_operational_date": "2026-06-01",
     "tags": ["maritime", "demo", "sofc"]},
    {"name": "POSCO Green Hydrogen Import Terminal", "developer": "POSCO Holdings",
     "location_country": "South Korea", "location_city": "Pohang",
     "latitude": 36.03, "longitude": 129.36,
     "cracker_capacity_tpd_h2": 180.0, "technology_vendor": "KBR",
     "feedstock_source": "Australian / Middle East green NH3",
     "status": "announced", "total_capex_usd_millions": 1200.0,
     "target_operational_date": "2030-01-01",
     "tags": ["korea", "large-scale", "import"]},
]


async def seed():
    from app.database import AsyncSessionLocal
    from app.models import (
        CatalystBenchmark, CatalystType, DataSource,
        CostDatapoint, Project,
    )

    print("Seeding database with reference data...")

    async with AsyncSessionLocal() as db:
        # ── Benchmarks ──────────────────────────────────────────────────────
        from sqlalchemy import select
        existing = (await db.execute(select(CatalystBenchmark))).scalars().first()
        if existing:
            print("  Benchmarks already seeded — skipping.")
        else:
            for b in SEED_BENCHMARKS:
                b_copy = dict(b)
                b_copy["catalyst_type"] = CatalystType(b_copy["catalyst_type"])
                if "source_type" in b_copy:
                    b_copy["source_type"] = DataSource(b_copy["source_type"])
                db.add(CatalystBenchmark(**b_copy))
            await db.commit()
            print(f"  ✓ Seeded {len(SEED_BENCHMARKS)} catalyst benchmarks")

        # ── Cost datapoints ──────────────────────────────────────────────────
        existing_c = (await db.execute(select(CostDatapoint))).scalars().first()
        if existing_c:
            print("  Cost datapoints already seeded — skipping.")
        else:
            for c in SEED_COST_DATAPOINTS:
                c_copy = dict(c)
                if "source_type" in c_copy:
                    c_copy["source_type"] = DataSource(c_copy["source_type"])
                db.add(CostDatapoint(**c_copy))
            await db.commit()
            print(f"  ✓ Seeded {len(SEED_COST_DATAPOINTS)} cost datapoints")

        # ── Projects ─────────────────────────────────────────────────────────
        existing_p = (await db.execute(select(Project))).scalars().first()
        if existing_p:
            print("  Projects already seeded — skipping.")
        else:
            from datetime import datetime
            for p in SEED_PROJECTS:
                p_copy = dict(p)
                if "catalyst_type" in p_copy and p_copy["catalyst_type"]:
                    p_copy["catalyst_type"] = CatalystType(p_copy["catalyst_type"])
                if "target_operational_date" in p_copy and isinstance(p_copy["target_operational_date"], str):
                    p_copy["target_operational_date"] = datetime.fromisoformat(p_copy["target_operational_date"])
                db.add(Project(**p_copy))
            await db.commit()
            print(f"  ✓ Seeded {len(SEED_PROJECTS)} projects")

    print("Seed complete.")


if __name__ == "__main__":
    asyncio.run(seed())
