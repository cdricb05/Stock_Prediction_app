"""Planner protocol + the Phase 29A bounded deterministic planner.

The planner never computes returns and never executes anything: it only
emits declarative, schema-validated experiment specifications over the
approved dimensions, ordered cheapest/most-diagnostic first, deduplicated,
and capped by the campaign budget. Phase 29B may add an LLM planner behind
the same protocol; the tool layer stays the only calculator either way.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List, Optional, Sequence

from . import schemas
from .memory import build_hypothesis_record, spec_hash

# The scientific baseline is the RAW monthly target reconstruction: buffer 0.0
# is the exact configuration that reproduces the owned reference
# historical_book_returns.csv 120/120 months (family_backtest.BASELINE_PARAMS).
# The live engine's EXIT_BUFFER_FRACTION 0.20 belongs to the OPERATIONAL
# holdings/action layer (churn hysteresis, mismatches the reference 111/120
# months) and is studied separately as a sensitivity via hyp_exit_buffer.
BASELINE_CELL = {
    "blend": (0.50, 0.50),
    "top_n": 25,
    "sector_treatment": "sector_cap",
    "exit_buffer_fraction": 0.0,
}


class Planner:
    """Protocol: Phase 29B planners must implement these two methods."""

    def generate_hypotheses(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        raise NotImplementedError

    def plan_experiments(
        self,
        config: Dict[str, Any],
        *,
        seen_spec_hashes: Optional[Sequence[str]] = None,
        max_experiments: Optional[int] = None,
        today: Optional[_dt.date] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError


_HYPOTHESES = (
    {
        "hypothesis_id": "hyp_blend_balance",
        "diagnosis": "The frozen 50/50 fundamental/momentum weighting was chosen "
        "for simplicity, not optimality; the cost-adjusted optimum may sit "
        "elsewhere on the approved 30/70..70/30 grid.",
        "proposed_change": {"axis": "blend", "values": "approved blend pairs"},
        "expected_benefit": "higher net SPY excess with comparable IC stability",
        "falsification_condition": "no non-50/50 blend beats the baseline net "
        "excess with rank-IC support across subperiods",
        "priority": 1,
    },
    {
        "hypothesis_id": "hyp_book_size",
        "diagnosis": "Top-25 concentrates idiosyncratic risk; Top-50 may trade "
        "a little return for materially better stability and capacity.",
        "proposed_change": {"axis": "top_n", "values": [25, 50]},
        "expected_benefit": "lower drawdown/volatility per unit excess return",
        "falsification_condition": "Top-50 gives up net excess without reducing "
        "drawdown or turnover",
        "priority": 2,
    },
    {
        "hypothesis_id": "hyp_sector_treatment",
        "diagnosis": "The 25% known-sector cap is a blunt control; raw exposes "
        "concentration, within-sector demeaning may control it at the score level.",
        "proposed_change": {"axis": "sector_treatment", "values": list(schemas.SECTOR_TREATMENTS)},
        "expected_benefit": "similar excess with lower peak sector concentration",
        "falsification_condition": "raw concentrates beyond limits or "
        "sector-neutral destroys the momentum leg's contribution",
        "priority": 3,
    },
    {
        "hypothesis_id": "hyp_exit_buffer",
        "diagnosis": "Monthly full re-ranking churns the book; the live engine's "
        "0.20 exit-buffer hysteresis may cut turnover with little return give-up.",
        "proposed_change": {"axis": "exit_buffer_fraction", "values": [0.0, 0.20]},
        "expected_benefit": "lower turnover -> better survival at 25/50 bps",
        "falsification_condition": "the buffer's return give-up exceeds its cost "
        "saving at every cost level",
        "priority": 2,
    },
    {
        "hypothesis_id": "hyp_interaction",
        "diagnosis": "Single-axis wins may not compose; interactions between "
        "blend, size, sector treatment and buffer need explicit coverage.",
        "proposed_change": {"axis": "combined", "values": "grid remainder"},
        "expected_benefit": "identify a robust plateau, not a knife-edge cell",
        "falsification_condition": "no multi-axis cell beats the best "
        "single-axis deviation after costs",
        "priority": 4,
    },
    {
        "hypothesis_id": "hyp_cost_robustness",
        "diagnosis": "Any winner must survive the full 12.5/25/50 bps-per-side "
        "ladder; cost fragility is a rejection, not a footnote.",
        "proposed_change": {"axis": "cost_ladder", "values": list(schemas.APPROVED_COST_BPS_PER_SIDE)},
        "expected_benefit": "candidates whose edge is real after realistic costs",
        "falsification_condition": "positive at 12.5 bps but non-positive at 50 bps",
        "priority": 1,
        # no grid cells of its own: its evidence is the cost ladder computed
        # inside every completed cell's metric battery
        "evidence_channel": "per_cell_cost_ladder",
    },
    {
        "hypothesis_id": "hyp_universe_robustness",
        "diagnosis": "The reconstruction universe is broader than the live "
        "eligibility filter; a real edge should survive both views.",
        "proposed_change": {"axis": "universe", "values": list(schemas.APPROVED_UNIVERSES)},
        "expected_benefit": "evidence the edge is not a thin-liquidity artifact",
        "falsification_condition": "excess disappears under live eligibility/ADV",
        "priority": 3,
        # no grid cells of its own: only the robustness battery of a retained
        # candidate (run_universe_sensitivity) can test it
        "evidence_channel": "robustness_battery",
    },
)


class BoundedDeterministicPlanner(Planner):
    def generate_hypotheses(self, config: Dict[str, Any]) -> List[Dict[str, Any]]:
        return [
            build_hypothesis_record(
                hypothesis_id=h["hypothesis_id"],
                diagnosis=h["diagnosis"],
                proposed_change=h["proposed_change"],
                expected_benefit=h["expected_benefit"],
                falsification_condition=h["falsification_condition"],
                priority=h["priority"],
                evidence_channel=h.get("evidence_channel", "grid_cells"),
            )
            for h in _HYPOTHESES
        ]

    def plan_experiments(
        self,
        config: Dict[str, Any],
        *,
        seen_spec_hashes: Optional[Sequence[str]] = None,
        max_experiments: Optional[int] = None,
        today: Optional[_dt.date] = None,
    ) -> Dict[str, Any]:
        dims = config["research_dimensions"]
        data_cutoff = config["data"]["data_cutoff"]
        seed = int(config.get("random_seed", 29))
        primary_cost = float(config.get("baseline", {}).get("cost_bps_per_side", 25.0))
        budget = int(config["budgets"]["max_primary_experiments"])
        allowed = min(budget, max_experiments) if max_experiments else budget
        seen = set(seen_spec_hashes or [])

        blends = [tuple(b) for b in dims.get("blend_weights", [])]
        sizes = list(dims.get("portfolio_sizes", []))
        sectors = list(dims.get("sector_treatments", []))
        buffers = [float(b) for b in dims.get("exit_buffer_fractions", [])]
        rebalances = list(dims.get("rebalance_treatments", ["monthly"]))
        overlays = list(dims.get("defensive_overlays", ["off"]))

        planned: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []
        deduplicated: List[str] = []

        def _mk_spec(blend, top_n, sector, buf, rebalance="monthly", overlay="off"):
            fw, mw = blend
            eid = "exp_f%02dm%02d_top%d_%s_b%02d" % (
                round(fw * 100),
                round(mw * 100),
                top_n,
                {"raw": "raw", "sector_cap": "cap", "sector_neutral": "sn"}.get(sector, sector),
                round(buf * 100),
            )
            if rebalance != "monthly":
                eid += "_" + rebalance
            if overlay != "off":
                eid += "_" + overlay
            ndev = sum(
                (
                    blend != BASELINE_CELL["blend"],
                    top_n != BASELINE_CELL["top_n"],
                    sector != BASELINE_CELL["sector_treatment"],
                    abs(buf - BASELINE_CELL["exit_buffer_fraction"]) > 1e-12,
                )
            )
            est = 1.0 + 0.5 * (top_n == 50) + 0.5 * (sector == "sector_neutral") + 0.3 * (buf > 0)
            if blend != BASELINE_CELL["blend"]:
                hyp = "hyp_blend_balance" if ndev == 1 else "hyp_interaction"
            elif top_n != BASELINE_CELL["top_n"]:
                hyp = "hyp_book_size" if ndev == 1 else "hyp_interaction"
            elif sector != BASELINE_CELL["sector_treatment"]:
                hyp = "hyp_sector_treatment" if ndev == 1 else "hyp_interaction"
            elif abs(buf - BASELINE_CELL["exit_buffer_fraction"]) > 1e-12:
                hyp = "hyp_exit_buffer" if ndev == 1 else "hyp_interaction"
            else:
                hyp = "hyp_interaction"
            if ndev > 1:
                hyp = "hyp_interaction"
            return {
                "experiment_id": eid,
                "candidate_id": "cand_" + eid[4:],
                "baseline_model": schemas.BASELINE_MODEL_ID,
                "candidate_family": schemas.CANDIDATE_FAMILY,
                "model_params": {"fundamental_weight": fw, "momentum_weight": mw},
                "portfolio_params": {
                    "top_n": top_n,
                    "sector_treatment": sector,
                    "rebalance": rebalance,
                    "exit_buffer_fraction": buf,
                    "defensive_overlay": overlay,
                    "min_adv_dollar": 1.0e7,
                },
                "data_cutoff": data_cutoff,
                "universe": "mhz_reconstruction",
                "evaluation_horizons": ["1m"],
                "cost_bps_per_side": primary_cost,
                "robustness_tests": list(schemas.APPROVED_ROBUSTNESS_TESTS),
                "compute_estimate": {"est_seconds": est, "cost_class": "medium"},
                "random_seed": seed,
                "stop_conditions": {
                    "max_runtime_seconds": config["budgets"].get(
                        "experiment_timeout_seconds", 900
                    )
                },
                "hypothesis_id": hyp,
                "_n_deviations": ndev,
            }

        # one structured-rejection probe per configured unsupported value, so
        # the campaign record shows WHY those dimensions produced no results
        for rb in rebalances:
            if rb not in schemas.SUPPORTED_REBALANCE_TREATMENTS:
                probe = _mk_spec(BASELINE_CELL["blend"], 25, "sector_cap", 0.0, rebalance=rb)
                verdict = schemas.validate_experiment_spec(
                    {k: v for k, v in probe.items() if not k.startswith("_")}, today=today
                )
                rejected.append(
                    {
                        "experiment_id": probe["experiment_id"],
                        "reason": "UNSUPPORTED_COMBINATION",
                        "violations": verdict["violations"],
                    }
                )
        for ov in overlays:
            if ov not in schemas.SUPPORTED_DEFENSIVE_OVERLAYS:
                probe = _mk_spec(BASELINE_CELL["blend"], 25, "sector_cap", 0.0, overlay=ov)
                verdict = schemas.validate_experiment_spec(
                    {k: v for k, v in probe.items() if not k.startswith("_")}, today=today
                )
                rejected.append(
                    {
                        "experiment_id": probe["experiment_id"],
                        "reason": "UNSUPPORTED_COMBINATION",
                        "violations": verdict["violations"],
                    }
                )

        candidates: List[Dict[str, Any]] = []
        for blend in blends:
            for top_n in sizes:
                for sector in sectors:
                    for buf in buffers:
                        cell = {
                            "blend": blend,
                            "top_n": top_n,
                            "sector_treatment": sector,
                            "exit_buffer_fraction": buf,
                        }
                        spec = _mk_spec(blend, top_n, sector, buf)
                        if cell == BASELINE_CELL:
                            deduplicated.append(
                                "%s (identical to the validated baseline)" % spec["experiment_id"]
                            )
                            continue
                        candidates.append(spec)

        # cheap + most diagnostic first: fewest deviations, then est cost, then id
        candidates.sort(
            key=lambda s: (s["_n_deviations"], s["compute_estimate"]["est_seconds"], s["experiment_id"])
        )

        seen_ids = set()
        for spec in candidates:
            clean = {k: v for k, v in spec.items() if not k.startswith("_")}
            verdict = schemas.validate_experiment_spec(clean, today=today)
            if not verdict["accepted"]:
                rejected.append(
                    {
                        "experiment_id": spec["experiment_id"],
                        "reason": "UNSUPPORTED_COMBINATION"
                        if verdict["unsupported"]
                        else "INVALID_SPEC",
                        "violations": verdict["violations"],
                    }
                )
                continue
            sh = spec_hash(clean)
            if sh in seen or spec["experiment_id"] in seen_ids:
                deduplicated.append(spec["experiment_id"])
                continue
            if len(planned) >= allowed:
                rejected.append(
                    {
                        "experiment_id": spec["experiment_id"],
                        "reason": "BUDGET_EXHAUSTED",
                        "violations": [],
                    }
                )
                continue
            seen.add(sh)
            seen_ids.add(spec["experiment_id"])
            clean["spec_hash"] = sh
            clean["plan_rank"] = len(planned) + 1
            planned.append(clean)

        return {
            "planned": planned,
            "rejected": rejected,
            "deduplicated": deduplicated,
            "budget": {"allowed": allowed, "configured": budget, "planned": len(planned)},
        }


__all__ = ["BASELINE_CELL", "BoundedDeterministicPlanner", "Planner"]
