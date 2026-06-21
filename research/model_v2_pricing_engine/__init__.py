"""model_v2 preview pricing engine — Phase 5-A.

A self-contained, offline, preview-only scoring package for S&P 500 (SPY proxy)
movement. Importable with no database, no network, and no paid APIs so it can be
served by the GCP prediction service in Phase 5-B.

Public API:
    score_market_v2(ticker="SPY") -> dict   # primary market-direction entrypoint
    score_ticker_v2(ticker)       -> dict   # per-ticker direction

Both return the model_v2 prediction contract and are preview-only:
orders/automation/broker-execution are always disabled.
"""
from __future__ import annotations

from .scorer import (
    ALLOWED_PREDICTIONS,
    ALLOWED_REGIMES,
    DEFAULT_HORIZON,
    MODEL_NAME,
    MODEL_VERSION,
    score_market_v2,
    score_ticker_v2,
)

__all__ = [
    "score_market_v2",
    "score_ticker_v2",
    "MODEL_NAME",
    "MODEL_VERSION",
    "DEFAULT_HORIZON",
    "ALLOWED_PREDICTIONS",
    "ALLOWED_REGIMES",
]
