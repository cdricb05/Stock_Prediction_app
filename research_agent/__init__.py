"""Phase 29A — Autonomous research agent kernel.

Research-only. This package never trades, never creates orders, never touches
the Paper Trader operational state, and never executes arbitrary code. All
experiments are declarative, schema-validated, and bounded to approved
dimensions. Promotion of any candidate beyond SHADOW_ELIGIBLE requires an
explicit human decision outside this package.
"""

SCHEMA_VERSION = "29A.1"

SAFETY_CONTRACT = {
    "research_only": True,
    "operational_model_changed": False,
    "operational_holdings_changed": False,
    "creates_orders": False,
    "broker_execution": False,
    "automation_of_trading": False,
    "promotion_requires_human_approval": True,
    "arbitrary_code_execution": False,
}

__all__ = ["SCHEMA_VERSION", "SAFETY_CONTRACT"]
