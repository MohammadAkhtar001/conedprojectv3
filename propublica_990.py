"""
Extractor package.

Importing this module triggers registration of every extractor against
the metric → extractors map in extractors.base.EXTRACTOR_PRIORITY.

Order of imports here matters only for tie-breaking when two extractors
declare the same base_confidence — earlier imports get tried first.  We
list them best-source-first.
"""

from . import sec_edgar          # noqa: F401  (revenue)
from . import propublica_990     # noqa: F401  (charitable_giving, foundation_assets)
from . import epa_egrid          # noqa: F401  (carbon_emissions)
from . import eia_reliability    # noqa: F401  (saidi)
from . import jd_power           # noqa: F401  (customer_satisfaction)
from . import csr_report         # noqa: F401  (renewable_pct, energy_assistance, etc.)

from .base import EXTRACTOR_PRIORITY    # re-export for orchestrator
