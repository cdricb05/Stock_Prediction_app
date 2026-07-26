r"""Campaign artifact store: atomic writes, append-only ledgers, locks.

Layout under <artifact_root>\campaigns\<campaign_id>\:
    campaign.json            immutable-ish campaign manifest (atomic overwrite)
    events.jsonl             append-only, chain-hashed event ledger
    hypotheses.jsonl         append-only hypothesis records
    experiment_index.jsonl   append-only experiment index rows
    experiments\<exp_id>\    config.json / provenance.json / metrics.json /
                             gate_results.json / decision.json / report.md
    challengers\             append-only challenger registry (registry.jsonl)
    reports\                 generated campaign reports
    locks\                   campaign.lock single-flight lock
    status.json              heartbeat + operator-visible status (atomic)

Rules enforced here:
- every JSON write is atomic (tmp file + os.replace in the same directory)
- event history is append-only and chain-hashed
- experiment artifacts are immutable once written (identical rewrite is a no-op)
- secret-looking keys are refused before anything reaches disk
- the artifact root may not live inside a git checkout (no datasets into git)
"""

from __future__ import annotations

import errno
import hashlib
import json
import os
import re
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

SCHEMA_VERSION = "29A.1"

CAMPAIGNS_DIR = "campaigns"
CAMPAIGN_MANIFEST = "campaign.json"
EVENTS_LEDGER = "events.jsonl"
HYPOTHESES_LEDGER = "hypotheses.jsonl"
EXPERIMENT_INDEX = "experiment_index.jsonl"
EXPERIMENTS_DIR = "experiments"
CHALLENGERS_DIR = "challengers"
CHALLENGER_REGISTRY = "registry.jsonl"
REPORTS_DIR = "reports"
LOCKS_DIR = "locks"
LOCK_FILE = "campaign.lock"
STATUS_FILE = "status.json"

EXPERIMENT_ARTIFACTS = (
    "config.json",
    "provenance.json",
    "metrics.json",
    "gate_results.json",
    "decision.json",
    "report.md",
)

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|password|passwd|credential|auth[_-]?token|"
    r"access[_-]?token|private[_-]?key|bearer)",
    re.IGNORECASE,
)


class ArtifactStoreError(RuntimeError):
    pass


class CampaignLockedError(ArtifactStoreError):
    pass


class ImmutableArtifactError(ArtifactStoreError):
    pass


class SecretLeakError(ArtifactStoreError):
    pass


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def content_hash(obj: Any) -> str:
    """Deterministic sha256 over canonical JSON."""
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def find_secret_keys(obj: Any, path: str = "$") -> List[str]:
    hits: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            kp = "%s.%s" % (path, k)
            if isinstance(k, str) and _SECRET_KEY_RE.search(k):
                hits.append(kp)
            hits.extend(find_secret_keys(v, kp))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(find_secret_keys(v, "%s[%d]" % (path, i)))
    return hits


def assert_no_secrets(obj: Any) -> None:
    hits = find_secret_keys(obj)
    if hits:
        raise SecretLeakError(
            "refusing to persist secret-looking keys: %s" % ", ".join(sorted(hits))
        )


def _inside_git_checkout(root: Path) -> bool:
    cur = root.resolve()
    for candidate in [cur] + list(cur.parents):
        if (candidate / ".git").exists():
            return True
    return False


def write_json_atomic(path: Path, obj: Any) -> str:
    """Atomically persist ``obj`` as JSON; returns the content hash."""
    assert_no_secrets(obj)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    digest = content_hash(obj)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1, sort_keys=True, default=str)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return digest


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not Path(path).exists():
        return []
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    assert_no_secrets(record)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


class CampaignLock:
    def __init__(self, lock_path: Path, owner: str):
        self.lock_path = Path(lock_path)
        self.owner = owner
        self._held = False

    def acquire(self) -> "CampaignLock":
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "owner": self.owner,
            "pid": os.getpid(),
            "host": socket.gethostname(),
            "acquired_at": _now_iso(),
            "heartbeat_at": _now_iso(),
        }
        try:
            fd = os.open(str(self.lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                existing: Any = None
                try:
                    existing = read_json(self.lock_path)
                except Exception:
                    existing = {"unreadable": True}
                raise CampaignLockedError(
                    "campaign is locked by another process: %s" % json.dumps(existing)
                )
            raise
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1, sort_keys=True)
        self._held = True
        return self

    def heartbeat(self) -> None:
        if not self._held:
            return
        try:
            doc = read_json(self.lock_path)
        except Exception:
            doc = {"owner": self.owner, "pid": os.getpid()}
        doc["heartbeat_at"] = _now_iso()
        write_json_atomic(self.lock_path, doc)

    def release(self) -> None:
        if self._held:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass
            self._held = False

    def __enter__(self) -> "CampaignLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()


class ArtifactStore:
    """Filesystem-backed store for one artifact root (many campaigns)."""

    def __init__(self, artifact_root: str):
        root = Path(artifact_root)
        if _inside_git_checkout(root):
            raise ArtifactStoreError(
                "artifact_root %s is inside a git checkout; campaign artifacts "
                "and datasets must live outside the repository" % root
            )
        self.root = root
        self.campaigns_root = root / CAMPAIGNS_DIR

    # ---- layout -----------------------------------------------------------
    def campaign_dir(self, campaign_id: str) -> Path:
        _validate_id(campaign_id, "campaign_id")
        return self.campaigns_root / campaign_id

    def experiment_dir(self, campaign_id: str, experiment_id: str) -> Path:
        _validate_id(experiment_id, "experiment_id")
        return self.campaign_dir(campaign_id) / EXPERIMENTS_DIR / experiment_id

    def ensure_campaign_layout(self, campaign_id: str) -> Path:
        base = self.campaign_dir(campaign_id)
        for sub in (EXPERIMENTS_DIR, CHALLENGERS_DIR, REPORTS_DIR, LOCKS_DIR):
            (base / sub).mkdir(parents=True, exist_ok=True)
        return base

    def list_campaigns(self) -> List[str]:
        if not self.campaigns_root.exists():
            return []
        return sorted(
            p.name
            for p in self.campaigns_root.iterdir()
            if p.is_dir() and (p / CAMPAIGN_MANIFEST).exists()
        )

    # ---- manifest / status ------------------------------------------------
    def write_manifest(self, campaign_id: str, manifest: Dict[str, Any]) -> str:
        return write_json_atomic(
            self.campaign_dir(campaign_id) / CAMPAIGN_MANIFEST, manifest
        )

    def read_manifest(self, campaign_id: str) -> Dict[str, Any]:
        path = self.campaign_dir(campaign_id) / CAMPAIGN_MANIFEST
        if not path.exists():
            raise ArtifactStoreError("unknown campaign_id: %s" % campaign_id)
        return read_json(path)

    def write_status(self, campaign_id: str, status_doc: Dict[str, Any]) -> None:
        doc = dict(status_doc)
        doc["updated_at"] = _now_iso()
        doc["schema_version"] = SCHEMA_VERSION
        write_json_atomic(self.campaign_dir(campaign_id) / STATUS_FILE, doc)

    def read_status(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        path = self.campaign_dir(campaign_id) / STATUS_FILE
        if not path.exists():
            return None
        return read_json(path)

    # ---- append-only ledgers ---------------------------------------------
    def append_event(
        self, campaign_id: str, kind: str, payload: Dict[str, Any]
    ) -> Dict[str, Any]:
        path = self.campaign_dir(campaign_id) / EVENTS_LEDGER
        rows = read_jsonl(path)
        prev_hash = rows[-1]["row_hash"] if rows else "GENESIS"
        core = {
            "seq": len(rows) + 1,
            "kind": kind,
            "payload": payload,
            "prev_hash": prev_hash,
        }
        core["row_hash"] = content_hash(core)
        core["ts"] = _now_iso()
        append_jsonl(path, core)
        return core

    def read_events(self, campaign_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.campaign_dir(campaign_id) / EVENTS_LEDGER)

    def verify_event_chain(self, campaign_id: str) -> Dict[str, Any]:
        rows = self.read_events(campaign_id)
        prev = "GENESIS"
        for i, row in enumerate(rows):
            core = {
                "seq": row.get("seq"),
                "kind": row.get("kind"),
                "payload": row.get("payload"),
                "prev_hash": row.get("prev_hash"),
            }
            if row.get("prev_hash") != prev or content_hash(core) != row.get(
                "row_hash"
            ):
                return {"intact": False, "first_bad_row": i + 1, "rows": len(rows)}
            prev = row.get("row_hash")
        return {"intact": True, "rows": len(rows)}

    def append_hypothesis(self, campaign_id: str, record: Dict[str, Any]) -> None:
        append_jsonl(self.campaign_dir(campaign_id) / HYPOTHESES_LEDGER, record)

    def read_hypotheses(self, campaign_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.campaign_dir(campaign_id) / HYPOTHESES_LEDGER)

    def append_experiment_index(
        self, campaign_id: str, record: Dict[str, Any]
    ) -> None:
        append_jsonl(self.campaign_dir(campaign_id) / EXPERIMENT_INDEX, record)

    def read_experiment_index(self, campaign_id: str) -> List[Dict[str, Any]]:
        return read_jsonl(self.campaign_dir(campaign_id) / EXPERIMENT_INDEX)

    # ---- immutable experiment artifacts ----------------------------------
    def write_experiment_artifact(
        self,
        campaign_id: str,
        experiment_id: str,
        name: str,
        obj: Any,
    ) -> str:
        if name not in EXPERIMENT_ARTIFACTS:
            raise ArtifactStoreError("unknown experiment artifact name: %s" % name)
        path = self.experiment_dir(campaign_id, experiment_id) / name
        if name.endswith(".md"):
            if path.exists():
                if path.read_text(encoding="utf-8") == obj:
                    return content_hash(obj)
                raise ImmutableArtifactError(
                    "experiment artifact already exists and differs: %s" % path
                )
            write_text_atomic(path, obj)
            return content_hash(obj)
        digest = content_hash(obj)
        if path.exists():
            existing = read_json(path)
            if content_hash(existing) == digest:
                return digest  # identical rewrite: idempotent no-op
            raise ImmutableArtifactError(
                "experiment artifact already exists and differs: %s" % path
            )
        return write_json_atomic(path, obj)

    def read_experiment_artifact(
        self, campaign_id: str, experiment_id: str, name: str
    ) -> Any:
        path = self.experiment_dir(campaign_id, experiment_id) / name
        if not path.exists():
            return None
        if name.endswith(".md"):
            return path.read_text(encoding="utf-8")
        return read_json(path)

    # ---- challenger registry ---------------------------------------------
    def challenger_registry_path(self, campaign_id: str) -> Path:
        return self.campaign_dir(campaign_id) / CHALLENGERS_DIR / CHALLENGER_REGISTRY

    # ---- lock -------------------------------------------------------------
    def lock(self, campaign_id: str, owner: str) -> CampaignLock:
        return CampaignLock(
            self.campaign_dir(campaign_id) / LOCKS_DIR / LOCK_FILE, owner
        )


_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _validate_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ArtifactStoreError(
            "%s must be a safe identifier (got %r)" % (label, value)
        )


__all__ = [
    "ArtifactStore",
    "ArtifactStoreError",
    "CampaignLock",
    "CampaignLockedError",
    "ImmutableArtifactError",
    "SecretLeakError",
    "append_jsonl",
    "assert_no_secrets",
    "content_hash",
    "find_secret_keys",
    "read_json",
    "read_jsonl",
    "write_json_atomic",
    "write_text_atomic",
    "SCHEMA_VERSION",
]
