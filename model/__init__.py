"""Phase 2 model layer (offline / research preparation).

This package holds the rebuilt signal / confidence / risk engine described in
``docs/phase2_model_rebuild_plan.md``. Phase 2A ships only the point-in-time
feature builder and labeled-dataset assembler (``model.features``).

Hard rules honored by everything in this package:
- It imports neither ``api_server`` nor Paper Trader.
- It never writes to the production database and performs no schema migration.
- DB access (when used) is confined to functions so the pure feature math is
  unit-testable without a database.
- It never fabricates data. Feature families without a real point-in-time
  source (volume when absent, sector, earnings, news, macro) are *declared but
  disabled*, emitting ``None`` rather than invented values.
"""
