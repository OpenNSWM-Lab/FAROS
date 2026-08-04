from pathlib import Path

# Venue templates
VENUE_CONFIGS = {
    "icml": {"name": "ICML", "bibstyle": "icml2025", "docclass_opts": ""},
    "neurips": {"name": "NeurIPS", "bibstyle": "plainnat", "docclass_opts": ""},
    "iclr": {"name": "ICLR", "bibstyle": "iclr2025_conference", "docclass_opts": ""},
    "acl": {"name": "ACL", "bibstyle": "acl_natbib", "docclass_opts": ""},
    "generic": {"name": "Generic", "bibstyle": "plainnat", "docclass_opts": "12pt"},
    "challenge_cup": {"name": "挑战杯模版", "bibstyle": "plainnat", "docclass_opts": "12pt"},
}

PAPER_TYPES = [
    "algorithm", "application", "survey", "benchmark", "system", "security", "position"
]

# Quality thresholds
MIN_REFERENCES = 25
MIN_ALGORITHMS = 2
MIN_EQUATIONS = 4
MIN_TABLES = 3
MIN_FIGURES = 4
MIN_SECTION_CHARS = 300

TEMPLATE_ROOT = Path(__file__).resolve().parents[4] / "templates" / "latex"
