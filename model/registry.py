"""Phase 2D — model artifact / version registry (offline design only).

Defines a small, **JSON-serializable** description of a trained offline model so a
run is reproducible and auditable: which model, on what feature set, over which
window, with which calibration / interval methods, and how it scored against the
plan §8 promotion gates. This is **offline / research only** — it imports neither
``api_server`` nor Paper Trader, never writes to the production database, and does
not touch the live service. stdlib + numpy only — no sklearn, no xgboost, **no
pickle** (plain JSON, so the artifact is human-readable and safe to load).

The metadata maps cleanly onto a future ``model_registry`` row, but **no database
table is created or modified in this phase** (that is Phase 2E, behind the
``PREDICTOR_USE_MODEL_V2`` flag).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Bump if the metadata structure changes in a backward-incompatible way.
SCHEMA_VERSION = "phase2d-registry-v1"

# The required metadata fields, in canonical order (used by docs/tests).
METADATA_FIELDS = [
    "model_version",
    "feature_set_version",
    "created_at",
    "training_source",
    "training_start_date",
    "training_end_date",
    "horizon_days",
    "model_name",
    "calibration_method",
    "interval_method",
    "decision_gate_summary",
    "metrics_summary",
]


# --------------------------------------------------------------------------- #
# JSON safety: make numpy scalars / NaN / Inf strictly serializable
# --------------------------------------------------------------------------- #
def jsonsafe(obj: Any) -> Any:
    """Recursively convert ``obj`` into strictly-JSON-serializable values.

    numpy scalars -> python scalars; NaN / Inf -> ``None``; dates -> ISO strings.
    Applying this before constructing metadata guarantees a clean round-trip with
    ``allow_nan=False`` (so the artifact is valid, portable JSON).
    """
    if obj is None or isinstance(obj, (str, bool, int)):
        return obj
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    # numpy scalars expose .item(); fall through to the primitive branches above.
    if hasattr(obj, "item") and not isinstance(obj, (list, tuple, dict)):
        try:
            return jsonsafe(obj.item())
        except (ValueError, TypeError):
            return str(obj)
    if isinstance(obj, dict):
        return {str(k): jsonsafe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [jsonsafe(v) for v in obj]
    return str(obj)


# --------------------------------------------------------------------------- #
# Version helpers (deterministic, no outcomes used)
# --------------------------------------------------------------------------- #
def feature_set_version(feature_cols: List[str], prefix: str = "phase2a") -> str:
    """Stable id for a feature set: ``<prefix>-<count>f-<hash8>``.

    The hash is over the sorted feature names, so the same feature set always
    yields the same version string and any change is visible in the id.
    """
    names = sorted(str(c) for c in feature_cols)
    h = hashlib.sha1("|".join(names).encode("utf-8")).hexdigest()[:8]
    return f"{prefix}-{len(names)}f-{h}"


def model_version(model_name: str, feature_set_ver: str,
                  created_at: Optional[dt.datetime] = None,
                  prefix: str = "phase2") -> str:
    """Deterministic-ish model version id ``<prefix>-<model>-<date>-<hash6>``."""
    created_at = created_at or dt.datetime.now()
    day = created_at.strftime("%Y%m%d")
    h = hashlib.sha1(f"{model_name}|{feature_set_ver}|{day}".encode("utf-8")
                     ).hexdigest()[:6]
    return f"{prefix}-{model_name}-{day}-{h}"


# --------------------------------------------------------------------------- #
# Metadata record
# --------------------------------------------------------------------------- #
@dataclass
class ModelVersionMetadata:
    """JSON-serializable description of one offline model version."""
    model_version: str
    feature_set_version: str
    created_at: str
    training_source: str
    training_start_date: Optional[str]
    training_end_date: Optional[str]
    horizon_days: int
    model_name: str
    calibration_method: str
    interval_method: str
    decision_gate_summary: Dict[str, Any] = field(default_factory=dict)
    metrics_summary: Dict[str, Any] = field(default_factory=dict)
    # Helpful, non-required extras (clearly secondary to METADATA_FIELDS).
    schema_version: str = SCHEMA_VERSION
    is_synthetic: bool = True

    def to_dict(self) -> Dict[str, Any]:
        """Strictly-JSON-safe dict (numpy/NaN sanitized)."""
        return jsonsafe(asdict(self))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False,
                          allow_nan=False)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ModelVersionMetadata":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})

    @classmethod
    def from_json(cls, s: str) -> "ModelVersionMetadata":
        return cls.from_dict(json.loads(s))

    def write_json(self, path: str, indent: int = 2) -> None:
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.to_json(indent=indent))

    @classmethod
    def read_json(cls, path: str) -> "ModelVersionMetadata":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())


def build_metadata(*, model_name: str, feature_cols: List[str],
                   training_source: str, training_start_date: Optional[dt.date],
                   training_end_date: Optional[dt.date], horizon_days: int,
                   calibration_method: str, interval_method: str,
                   decision_gate_summary: Dict[str, Any],
                   metrics_summary: Dict[str, Any], is_synthetic: bool = True,
                   created_at: Optional[dt.datetime] = None
                   ) -> ModelVersionMetadata:
    """Assemble a :class:`ModelVersionMetadata` with derived version ids."""
    created_at = created_at or dt.datetime.now()
    fsv = feature_set_version(feature_cols)
    mv = model_version(model_name, fsv, created_at=created_at)
    return ModelVersionMetadata(
        model_version=mv,
        feature_set_version=fsv,
        created_at=created_at.isoformat(timespec="seconds"),
        training_source=training_source,
        training_start_date=(training_start_date.isoformat()
                             if training_start_date else None),
        training_end_date=(training_end_date.isoformat()
                           if training_end_date else None),
        horizon_days=int(horizon_days),
        model_name=model_name,
        calibration_method=calibration_method,
        interval_method=interval_method,
        decision_gate_summary=jsonsafe(decision_gate_summary),
        metrics_summary=jsonsafe(metrics_summary),
        is_synthetic=bool(is_synthetic),
    )
