"""Targeted tests for Phase 10-G review-template generator (run_phase10g_review_template).

Verifies the user's explicit asks and the standing safety contract:
  - every candidate defaults to NEEDS_REVIEW
  - NO auto-approval (nothing pre-approved; APPROVE is never written)
  - NO Paper Trader writes, NO orders, NO automation
  - one row per long/short candidate, no fabricated candidates
  - reviewer-input columns ship blank
  - final decision is in the allowed set; allowed/forbidden are disjoint
The source is also scanned for network / order / automation / Paper-Trader-write tokens.
"""
import csv
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from research import run_phase10g_review_template as g10  # noqa: E402

_BOOK_HEADER = ("side,rank_sn,ticker,comp_sn,comp_raw,sector,sector_is_unknown,"
                "sector_repaired,before_review_label,cohort,liquidity_proxy,review_status")
_BOOK_ROWS = [
    "LONG,1,EXPE,8.76583,9.09173,Consumer Discretionary,False,True,LONG,new,319234945.7,PAPER_REVIEW_ONLY",
    "LONG,2,JPM,7.09546,6.45231,Financials,False,False,LONG,old,1614580632.3,PAPER_REVIEW_ONLY",
    "SHORT,1,ZZZA,-6.5,-6.1,Industrials,False,True,SHORT,new,123456789.0,PAPER_REVIEW_ONLY",
    "SHORT,2,ZZZB,-5.4,-5.0,Health Care,False,False,SHORT,old,98765432.0,PAPER_REVIEW_ONLY",
    # a HOLD row that must NOT appear in the long/short review template
    "HOLD,50,MIDD,0.01,0.02,Materials,False,False,HOLD,old,55555555.0,PAPER_REVIEW_ONLY",
]


@pytest.fixture()
def env(tmp_path):
    book = tmp_path / "reranked_paper_review_long_short_book.csv"
    book.write_text("\n".join([_BOOK_HEADER, *_BOOK_ROWS]) + "\n", encoding="utf-8")
    out_dir = tmp_path / "out"
    report = g10.run(book_csv=book, out_dir=out_dir, verbose=False)
    template = out_dir / g10._TEMPLATE_CSV
    with open(template, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {"report": report, "rows": rows, "out_dir": out_dir, "template": template, "book": book}


def test_every_candidate_defaults_to_needs_review(env):
    rows = env["rows"]
    assert rows, "template must have rows"
    assert all(r["review_decision"] == "NEEDS_REVIEW" for r in rows)
    assert env["report"]["default_decision"] == "NEEDS_REVIEW"
    assert env["report"]["all_default_needs_review"] is True
    assert env["report"]["n_needs_review"] == env["report"]["n_candidates"] == len(rows)


def test_no_auto_approval(env):
    rows = env["rows"]
    # nothing pre-approved, and APPROVE is never written by the generator
    assert all(r["approved"].upper() == "FALSE" for r in rows)
    assert all(r["review_decision"] != "APPROVE" for r in rows)
    rep = env["report"]
    assert rep["auto_approval"] is False
    assert rep["n_approved_at_generation"] == 0
    assert "APPROVE" in rep["allowed_human_decisions"]  # human MAY approve later, generator never does


def test_no_orders(env):
    rows = env["rows"]
    assert all(r["order_action"] == "NO_ORDER" for r in rows)
    assert env["report"]["creates_orders"] is False
    assert env["report"]["order_action_all"] == "NO_ORDER"


def test_no_automation_and_no_paper_trader_write(env):
    rep = env["report"]
    assert rep["creates_automation"] is False
    assert rep["wrote_to_paper_trader"] is False
    assert rep["creates_paper_trader_signals"] is False
    assert rep["creates_trade_decisions"] is False
    assert rep["live_trading"] is False and rep["broker_connected"] is False and rep["deploy"] is False
    # output lives under research/output, never under a paper_trader path
    assert "paper_trader" not in str(env["template"]).lower()


def test_one_row_per_long_short_candidate_no_holds(env):
    rows = env["rows"]
    sides = [r["side"].upper() for r in rows]
    assert sides.count("LONG") == 2 and sides.count("SHORT") == 2
    assert "HOLD" not in sides            # holds excluded from the review book
    assert len(rows) == 4
    assert env["report"]["n_long"] == 2 and env["report"]["n_short"] == 2


def test_no_fabricated_candidates(env):
    template_tickers = {r["ticker"] for r in env["rows"]}
    book_tickers = {"EXPE", "JPM", "ZZZA", "ZZZB"}  # the long/short names from the fixture book
    assert template_tickers == book_tickers          # exactly the book's L/S names, nothing invented


def test_reviewer_input_columns_blank(env):
    for r in env["rows"]:
        assert r["reviewer"] == "" and r["reviewed_at"] == ""
        assert r["conviction"] == "" and r["reviewer_notes"] == ""


def test_provenance_carried_from_book(env):
    rep = env["report"]
    cols = rep["template_columns"]
    for c in ("side", "rank_sn", "ticker", "sector", "sector_repaired", "comp_sn"):
        assert c in cols
    expe = next(r for r in env["rows"] if r["ticker"] == "EXPE")
    assert expe["sector"] == "Consumer Discretionary"
    assert expe["sector_repaired"] == "True"
    assert expe["review_status"] == "PAPER_REVIEW_ONLY"


def test_decision_allowed_and_sets_disjoint(env):
    rep = env["report"]
    assert rep["decision"] == "REVIEW_TEMPLATE_READY_FOR_HUMAN_REVIEW"
    assert rep["decision"] in g10.ALLOWED_DECISIONS
    assert set(g10.ALLOWED_DECISIONS).isdisjoint(set(g10.FORBIDDEN_DECISIONS))


def test_blocked_when_book_missing(tmp_path):
    rep = g10.run(book_csv=tmp_path / "does_not_exist.csv", out_dir=tmp_path / "out", verbose=False)
    assert rep["decision"] == "HARD_BLOCKER_REQUIRES_USER_ACTION"
    assert rep["decision"] in g10.ALLOWED_DECISIONS
    assert rep["wrote_to_paper_trader"] is False and rep["creates_orders"] is False


def test_source_has_no_network_order_automation_or_pt_write_tokens():
    src = (_REPO_ROOT / "research" / "run_phase10g_review_template.py").read_text(encoding="utf-8")
    banned = [
        "requests.get", "requests.post", "urllib.request", "http://", "https://", "socket.",
        "create_order", "submit_order", "place_order", "OrderModel", "broker.",
        "session.add", "db.add", ".commit(", "schedule.every", "crontab", "APScheduler",
        "subprocess", "os.system",
    ]
    hits = [tok for tok in banned if tok in src]
    assert not hits, f"source contains banned token(s): {hits}"


def test_real_book_yields_194_needs_review_if_present():
    book = g10._DEFAULT_BOOK
    if not book.exists():
        pytest.skip("10-F-A repaired book not present in this checkout")
    rep = g10.run(verbose=False)
    assert rep["decision"] == "REVIEW_TEMPLATE_READY_FOR_HUMAN_REVIEW"
    assert rep["n_candidates"] == 194 and rep["n_long"] == 97 and rep["n_short"] == 97
    assert rep["all_default_needs_review"] is True
    assert rep["auto_approval"] is False and rep["creates_orders"] is False
