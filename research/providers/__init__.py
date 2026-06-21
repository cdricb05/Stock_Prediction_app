"""Research data-provider adapters (Track A quant brain).

This package holds thin, offline-first adapters for EXTERNAL alpha-data providers
used by the Phase 5-E enriched-alpha work. Adapters are import-safe with no
network side effects: constructing or importing them never contacts a provider.
A live call happens only when a caller explicitly passes ``live=True`` AND the
provider's API key is present in the environment.

Secret discipline (hard rule across this package): API keys are read ONLY from
environment variables, are never printed, never written to any artifact, and
never committed. Every persisted URL is redacted.
"""
from __future__ import annotations

from . import fmp_client  # noqa: F401  (re-export for convenience)

__all__ = ["fmp_client"]
