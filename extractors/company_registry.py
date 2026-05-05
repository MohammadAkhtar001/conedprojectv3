"""
Company registry.

Maps utility company names to the stable identifiers each government data
source uses.  Hand-curated rather than scraped — these IDs are stable and
the only correct way to address a company in each system.

CIK   = SEC Central Index Key (10 digit, used by data.sec.gov)
EIN   = IRS Employer Identification Number (used by ProPublica /nonprofits)
        Each utility's CORPORATE FOUNDATION has its own EIN distinct from
        the operating company.  The foundation EIN is what we want for 990
        philanthropy data.
EIA_OP_ID = EIA Operator ID from EIA Form 861/923 — used to attribute
            generation and reliability data.
EGRID_ORIS = EPA eGRID parent operator name — used to sum plant-level CO₂.

Adding a new utility: find the four IDs from public sources, add an entry.
This file is the single place company-specific knowledge lives.

Sources for IDs:
- CIK:        https://www.sec.gov/cgi-bin/browse-edgar (search by company name)
- EIN:        https://projects.propublica.org/nonprofits (search by foundation name)
- EIA_OP_ID:  https://www.eia.gov/electricity/data/eia861/ (download zip,
              find operator name in the workbook)
- EGRID:      https://www.epa.gov/egrid (operator names in the latest
              published year's Excel file)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class Company:
    name: str
    aliases: tuple[str, ...]      # alternate names users might type
    is_integrated_generator: bool # True for Duke, Southern, Dominion etc.
    cik: Optional[str]            # SEC, 10-digit zero-padded, e.g. "0001047862"
    foundation_ein: Optional[str] # IRS EIN of the corporate foundation
    foundation_name: Optional[str]
    eia_op_ids: tuple[int, ...] = ()    # may be multiple operating subs
    egrid_operator_names: tuple[str, ...] = ()
    state_puc_codes: tuple[str, ...] = ()  # informational
    notes: str = ""


REGISTRY: dict[str, Company] = {
    "con_edison": Company(
        name="Con Edison",
        aliases=("Consolidated Edison", "ConEd", "Consolidated Edison Inc",
                 "Consolidated Edison Company of New York", "CECONY"),
        is_integrated_generator=False,
        cik="0001047862",                       # Consolidated Edison, Inc.
        foundation_ein="13-3088574",            # Consolidated Edison Foundation, Inc.
        foundation_name="Consolidated Edison Foundation",
        eia_op_ids=(13511, 49328),              # CECONY + O&R
        egrid_operator_names=(),                # no owned generation
        state_puc_codes=("NY-PSC",),
        notes="Pure T&D distributor (NY); divested all generation by 2008.",
    ),
    "duke_energy": Company(
        name="Duke Energy",
        aliases=("Duke Energy Corporation", "DUK"),
        is_integrated_generator=True,
        cik="0001326160",                       # Duke Energy Corporation
        foundation_ein="56-2598287",            # Duke Energy Foundation
        foundation_name="Duke Energy Foundation",
        eia_op_ids=(3046, 7140, 5416),          # multiple subs (DEC, DEP, DEF, DEI, DEK, DEO)
        egrid_operator_names=(
            "Duke Energy Carolinas, LLC",
            "Duke Energy Progress, LLC",
            "Duke Energy Florida, LLC",
            "Duke Energy Indiana, LLC",
            "Duke Energy Kentucky, Inc",
            "Duke Energy Ohio, Inc",
        ),
        state_puc_codes=("NC-NCUC", "SC-PSC", "FL-PSC", "IN-IURC", "OH-PUCO", "KY-PSC"),
        notes="Integrated generator across 6 states.",
    ),
    "national_grid": Company(
        name="National Grid USA",
        aliases=("National Grid", "NGG", "Niagara Mohawk", "KeySpan",
                 "Massachusetts Electric", "Narragansett Electric"),
        is_integrated_generator=False,
        cik=None,            # US operating subs file separately; parent is UK-listed
        foundation_ein="04-3403615",            # National Grid USA Foundation, Inc.
        foundation_name="National Grid USA Foundation",
        eia_op_ids=(13501, 40209),              # Niagara Mohawk + Mass Electric
        egrid_operator_names=(),
        state_puc_codes=("NY-PSC", "MA-DPU", "RI-PUC"),
        notes="US subsidiary of UK-listed parent.  Parent = National Grid plc; "
              "we benchmark only US data.",
    ),
    "pge": Company(
        name="Pacific Gas and Electric",
        aliases=("PG&E", "PG&E Corporation", "Pacific Gas & Electric", "PCG"),
        is_integrated_generator=True,
        cik="0001004980",                       # PG&E Corp
        foundation_ein="94-3251362",            # PG&E Corporation Foundation
        foundation_name="PG&E Corporation Foundation",
        eia_op_ids=(14328,),
        egrid_operator_names=("Pacific Gas and Electric Company",),
        state_puc_codes=("CA-CPUC",),
        notes="Owns Diablo Canyon nuclear + hydro fleet (renewable-heavy generation).",
    ),
    "eversource": Company(
        name="Eversource Energy",
        aliases=("Eversource", "ES", "NSTAR", "Northeast Utilities"),
        is_integrated_generator=False,
        cik="0000072741",                       # Eversource Energy
        foundation_ein="06-1530393",            # Eversource Energy Foundation, Inc.
        foundation_name="Eversource Energy Foundation",
        eia_op_ids=(13998, 13771),              # Connecticut Light & Power + NSTAR
        egrid_operator_names=(),
        state_puc_codes=("CT-PURA", "MA-DPU", "NH-PUC"),
        notes="Pure T&D in New England.",
    ),
    "southern": Company(
        name="Southern Company",
        aliases=("Southern Co", "SO", "Georgia Power", "Alabama Power",
                 "Mississippi Power", "Southern Power"),
        is_integrated_generator=True,
        cik="0000092122",                       # The Southern Company
        foundation_ein="58-2407627",            # Southern Company Charitable Foundation
        foundation_name="Southern Company Charitable Foundation",
        eia_op_ids=(7140, 195, 12686),          # Ga Power, Ala Power, Miss Power
        egrid_operator_names=(
            "Georgia Power Company",
            "Alabama Power Company",
            "Mississippi Power Company",
        ),
        state_puc_codes=("GA-PSC", "AL-PSC", "MS-PSC"),
        notes="Integrated generator across the Southeast.",
    ),
}


def resolve(name_or_alias: str) -> Optional[Company]:
    """Resolve a free-text company name to a Company record.  Case-insensitive,
    matches both canonical names and aliases."""
    needle = name_or_alias.strip().lower()
    for c in REGISTRY.values():
        if c.name.lower() == needle:
            return c
        if any(a.lower() == needle for a in c.aliases):
            return c
    # Loose match — useful for "Consolidated Edison Inc" vs "Consolidated Edison"
    for c in REGISTRY.values():
        all_names = (c.name,) + c.aliases
        if any(needle in n.lower() or n.lower() in needle for n in all_names):
            return c
    return None
