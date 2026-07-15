"""Targeted tests for Phase 13-G Part A - Current Alpha Universe Integrity Audit.

The runner is fully offline (reads the frozen 10-L panel + the 13-A package + the owned Norgate
PIT S&P 500 membership panel only), so this suite makes ZERO network calls and touches no Paper
Trader state. It mixes one real offline integration run with deterministic unit tests on the
membership / shadow / decision helpers (synthetic inputs).
"""
import csv
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

STEM = "phase13g_current_alpha_universe_integrity_audit"
RUNNER = REPO / "research" / f"run_{STEM}.py"
DOCS = REPO / "docs" / f"{STEM}_v1.md"
OUT_DIR = REPO / "research" / "output" / STEM
OUT_JSON = OUT_DIR / f"{STEM}.json"
MEMBERSHIP_CSV = OUT_DIR / "current_alpha_universe_membership.csv"

from research import run_phase13g_current_alpha_universe_integrity_audit as m  # noqa: E402


# --------------------------------------------------------------------------- #
# Integration: the real offline run against owned/local data.
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def result():
    proc = subprocess.run([sys.executable, str(RUNNER)], cwd=str(REPO),
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"runner failed: {proc.stdout}\n{proc.stderr}"
    # the live EODHD key must never be echoed by an offline audit
    key = os.environ.get("EODHD_API_KEY") or ""
    if key:
        assert key not in proc.stdout and key not in proc.stderr
    with open(OUT_JSON, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_runner_compiles():
    py_compile.compile(str(RUNNER), doraise=True)


def test_docs_exist():
    assert DOCS.exists()
    text = DOCS.read_text(encoding="utf-8").lower()
    assert "s&p 500" in text and "shadow" in text and "champion" in text


def test_phase_and_offline(result):
    assert result["phase"] == "13-G"
    assert result["offline"] is True
    assert result["performs_network"] is False
    assert result["eodhd_key_required"] is False


def test_traces_actual_universe_not_sp500(result):
    # It must trace the ACTUAL (broader) universe and must NOT falsely claim S&P 500.
    assert result["latest_ranked_count"] == 234
    assert result["is_strict_sp500_universe"] is False
    assert result["decision"] == "CURRENT_UNIVERSE_BROADER_KEEP_CHAMPION"
    assert result["decision"] in m.ALLOWED_UNIVERSE_DECISIONS
    assert "phase8v" in result["validated_alpha_universe_name"]


def test_membership_breakdown_sums(result):
    mem = result["latest_cross_section_membership"]
    assert mem["n_ranked"] == 234
    assert (mem["confirmed_sp500"] + mem["not_confirmed_sp500"]
            + mem["unknown_membership"]) == 234
    # a material fraction are NOT confirmed S&P 500 -> this is why it is broader
    assert mem["not_confirmed_sp500"] > 0
    assert 0.0 < mem["confirmed_fraction"] < 1.0


def test_champion_reproduces_frozen_10d(result):
    # panel-integrity: the champion side must reproduce the frozen 10-D composite_sn baseline
    champ = result["current_champion"]
    assert abs(champ["ic_t_63d"] - 2.665) <= 0.25
    assert abs(champ["net_25bps"] - 0.00401) <= 0.0015
    assert abs(champ["net_50bps"] - 0.00095) <= 0.0015
    assert abs(champ["turnover"] - 0.6115) <= 0.10


def test_shadow_is_separate_from_champion(result):
    champ = result["current_champion"]
    shadow = result["sp500_shadow"]
    assert shadow is not None
    # distinct objects with distinct labels + distinct coverage (filtered subset)
    assert shadow["label"] != champ["label"]
    assert shadow["coverage_rows_scoreable"] < champ["coverage_rows_scoreable"]
    assert result["sp500_shadow_decision"] in m.ALLOWED_SHADOW_DECISIONS


def test_shadow_uses_same_formula_champion_unchanged(result):
    # alpha formula / rank is unchanged: the champion is preserved and not reweighted/retuned
    assert result["champion_preserved"] is True
    assert result["changed_champion_ranks"] is False
    assert result["reweighted_or_retuned_champion"] is False
    assert "no reweight" in result["shadow_method_note"].lower()


def test_safety_flags(result):
    assert result["creates_orders"] is False
    assert result["creates_automation"] is False
    assert result["creates_broker_connection"] is False
    assert result["wrote_to_paper_trader"] is False
    assert result["live_trading"] is False
    assert result["uses_paid_data"] is False


def test_membership_csv(result):
    assert MEMBERSHIP_CSV.exists()
    with open(MEMBERSHIP_CSV, "r", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 234
    for col in m._MEMBERSHIP_CSV_HEADER:
        assert col in rows[0]
    # at least one confirmed and one not-confirmed name present in the CSV
    statuses = {r["sp500_membership_status"] for r in rows}
    assert m.MEMB_CONFIRMED in statuses
    assert (m.MEMB_NOT in statuses) or (m.MEMB_NOT_IN_SUPERSET in statuses)


# --------------------------------------------------------------------------- #
# Unit tests: deterministic membership / shadow / decision logic (synthetic).
# --------------------------------------------------------------------------- #
def _write_membership(tmp_path):
    # active names as plain columns, one delist-suffixed column that must be skipped
    p = tmp_path / "membership.csv"
    with open(p, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "AAA", "BBB", "OLD-201501"])
        w.writerow(["2020-01-31", "1.0", "0.0", "1.0"])
        w.writerow(["2020-02-29", "1.0", "1.0", "0.0"])
        w.writerow(["2020-03-31", "0.0", "1.0", "0.0"])
    return p


def test_load_membership_skips_delist_suffix(tmp_path):
    mem, err = m.load_membership(_write_membership(tmp_path))
    assert err is None
    assert mem.available
    assert mem.in_superset("AAA") and mem.in_superset("BBB")
    assert not mem.in_superset("OLD-201501")  # delist-suffixed identity is not matched
    assert mem.n_dates == 3


def test_member_asof_is_point_in_time(tmp_path):
    mem, _ = m.load_membership(_write_membership(tmp_path))
    # strictly PIT: latest month-end <= rebalance date
    assert mem.member_asof("AAA", "2020-01-15") is None       # before first month-end
    assert mem.member_asof("AAA", "2020-02-10") is True        # resolves to 2020-01-31 row (1.0)
    assert mem.member_asof("BBB", "2020-02-10") is False       # 2020-01-31 row (0.0)
    assert mem.member_asof("BBB", "2020-03-15") is True        # 2020-02-29 row (1.0)
    assert mem.member_asof("AAA", "2020-04-01") is False       # 2020-03-31 row (0.0)
    assert mem.member_asof("ZZZ", "2020-04-01") is None        # not in superset


def test_classify_membership_buckets(tmp_path):
    mem, _ = m.load_membership(_write_membership(tmp_path))
    rows = [
        {"rank": 1, "ticker": "AAA", "sector": "X", "composite_sn": 2.0},   # 2020-01-31 row: 1.0
        {"rank": 2, "ticker": "BBB", "sector": "X", "composite_sn": 1.0},   # 2020-01-31 row: 0.0
        {"rank": 3, "ticker": "FOREIGN", "sector": "X", "composite_sn": 0.5},  # not in superset
    ]
    # 2020-02-15 resolves strictly-PIT to the 2020-01-31 month-end row
    classified, counts = m.classify_membership(rows, mem, "2020-02-15")
    by_ticker = {r["ticker"]: r["sp500_membership_status"] for r in classified}
    assert by_ticker["AAA"] == m.MEMB_CONFIRMED
    assert by_ticker["BBB"] == m.MEMB_NOT
    assert by_ticker["FOREIGN"] == m.MEMB_NOT_IN_SUPERSET
    assert counts[m.MEMB_CONFIRMED] == 1


def test_classify_membership_unavailable_is_unknown():
    mem = m.Membership([], {})
    rows = [{"rank": 1, "ticker": "AAA", "sector": "X", "composite_sn": 2.0}]
    classified, counts = m.classify_membership(rows, mem, "2020-03-01")
    assert classified[0]["sp500_membership_status"] == m.MEMB_UNKNOWN
    assert counts[m.MEMB_UNKNOWN] == 1


def test_decide_universe_thresholds():
    dec_all, _ = m.decide_universe(100, {m.MEMB_CONFIRMED: 100})
    assert dec_all == m.DEC_CONFIRMED_SP500
    dec_mixed, _ = m.decide_universe(234, {m.MEMB_CONFIRMED: 194})
    assert dec_mixed == m.DEC_BROADER


def test_decide_shadow_weaker_vs_ready():
    champ = {"net_25bps": 0.004, "n_quarters": 40}
    mem = m.Membership(["2020-01-31"], {"AAA": [1.0]})
    weaker = {"net_25bps": 0.0001, "n_quarters": 40}
    dec, _ = m.decide_shadow(mem, champ, weaker)
    assert dec == m.SHADOW_REJECTED
    stronger = {"net_25bps": 0.006, "n_quarters": 40}
    dec2, _ = m.decide_shadow(mem, champ, stronger)
    assert dec2 == m.SHADOW_READY


def test_decide_shadow_insufficient_when_no_membership():
    champ = {"net_25bps": 0.004, "n_quarters": 40}
    dec, _ = m.decide_shadow(m.Membership([], {}), champ, None)
    assert dec == m.SHADOW_INSUFFICIENT


def test_equity_metrics_known_series():
    eq = m._equity_metrics([0.10, -0.05, 0.02])
    # compounded 1.10 * 0.95 * 1.02 - 1 = 0.0659
    assert abs(eq["cumulative_return"] - 0.0659) <= 1e-3
    assert eq["max_drawdown"] <= 0.0
    assert eq["n_quarters"] == 3
    empty = m._equity_metrics([])
    assert empty["cumulative_return"] is None and empty["n_quarters"] == 0
