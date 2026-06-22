# Project Charter — S&P 500 Multi-Factor Scoring & Risk-Managed Portfolio System

> **Purpose of this document:** This is the overarching charter for the project. Read it at the start of every session. It defines the philosophy, architecture, phasing, and — critically — the guardrails that keep this system honest. Do not drift from these principles without the project owner explicitly approving the change.

---

## 1. Roles & Operating Principle

- **Project owner (human):** Acts as the Portfolio Manager. Owns the investment philosophy, the risk appetite, the final skeptic role on backtest results, and all judgment calls. Has ~17 years of Financial Services / Capital Markets experience. Thinks in business and P&L terms.
- **Claude Code (agent):** Acts as the quant engineer. Builds essentially all of it — data pipelines, factor computations, normalization, backtest harness, risk decomposition, dashboards. Carries the engineering load.

**The non-negotiable operating rule:** Judgment calls (factor selection, weighting philosophy, backtest interpretation) must NEVER be silently buried inside code and presented as objective. For every such decision, lay out the options, expose the trade-offs, make a recommendation **with reasoning visible**, and let the owner consciously endorse or override. The supervision is not a formality — it is the mechanism that keeps the system from quietly drifting into an impressive-looking black box that loses money.

---

## 2. Core Philosophy (do not violate)

1. **This is a ranking and risk-monitoring system, not a prediction oracle.** It answers "which names are relatively attractive right now" and "what is my consolidated risk" — NOT "what will the market do tomorrow." Single-name directional prediction with confidence is explicitly out of scope and treated as a red flag.

2. **Risk is a property of the whole book, not of individual positions.** Every position decomposes into separable exposures (market beta, rate/duration, sector/factor, idiosyncratic). The goal is to keep the bets the owner has conviction in and strip the ones they don't.

3. **The edge is not magic.** It is the disciplined harvesting of documented factor premia (value, momentum, quality, low-vol) with low cost and tight risk control. Expected outcome is modest, durable outperformance through consistency — not spectacular returns from being right about individual names.

4. **Math is the arbiter.** Every design decision must resolve to out-of-sample, cost-adjusted, regime-decomposed evidence. Neither the owner's enthusiasm nor the agent's bias-toward-pleasing gets the final word — the validation framework does.

5. **Robustness beats peak performance.** A parameter that only works at one precise value is overfit. A parameter that works across a broad plateau is robust. Always report the whole sensitivity surface, never just the best point.

---

## 3. System Architecture

```
DATA LAYER          →  FACTOR LAYER      →  SCORING LAYER     →  RISK LAYER        →  OUTPUT LAYER
(raw ingestion)        (compute signals)    (normalize+weight)   (consolidate)        (rank/monitor/hedge)

FRED, CBOE,            Value, Momentum,     Z-score each         Net beta,            Composite score,
yfinance,             Quality, Volatility,  factor cross-         net duration,        sector-neutral rank,
SEC EDGAR,            Growth, Sentiment     sectionally,         factor tilts,        consolidated risk view,
CFTC                  per ticker            apply weights        hedge overlay        hedge sizing
```

### Two systems, layered (not competitors):
- **System 1 — Ranking engine (the engine):** Scores and ranks all 500 names. Decides WHICH names express a given stance.
- **System 2 — Regime overlay (the throttle):** Reads macro indicators to classify risk-on / neutral / risk-off. Decides HOW MUCH risk to take and WHICH factor buckets to favor. NEVER a standalone oracle — only a posture-adjusting throttle on top of System 1.

```
System 2 (Regime)  →  HOW MUCH risk + WHICH factors
       │
       ▼
System 1 (Ranking) →  WHICH specific names
       │
       ▼
Risk Sizing        →  HOW BIG each position (inverse-vol / risk budgeting)
       │
       ▼
Execution/Rebal    →  WHEN, with cost discipline
```

---

## 4. Factors (single-name level)

| Bucket | Example signals | Source |
|---|---|---|
| Value | P/E, P/B, EV/EBITDA, FCF yield | SEC EDGAR / yfinance |
| Momentum | 12-1 month return, RSI, distance from 200DMA | Price data |
| Quality | ROE, debt/equity, earnings stability, margins | Fundamentals |
| Volatility | Realized vol, beta, downside deviation | Price data |
| Growth | Revenue / EPS growth trajectory | Fundamentals |
| Sentiment/Flow | Analyst revisions, short interest, options skew | Mixed |

**Macro indicators** (VIX term structure, credit spreads HY/IG OAS, yield curve 10Y-3M/10Y-2Y, breadth, MOVE, real yields) do NOT score individual tickers. They drive the **System 2 regime classifier** and modulate factor-bucket weights.

---

## 5. Normalization & Scoring Rules

- Cross-sectional **z-scoring**: standardize each factor across all 500 names so they are comparable (cannot add a raw P/E to an RSI to a credit spread).
- **Winsorize** outliers (cap z-scores at ±3).
- **Sector-neutralize** where appropriate (a tech P/E vs a utility P/E is not comparable).
- **Start with equal weighting across factors. Do NOT optimize weights early.** Equal weight is a hard benchmark to beat and protects against overfitting. Weight optimization only after the validation framework is trusted, and only with the owner's explicit endorsement of the weighting philosophy.

---

## 6. The Backtest Harness — THE MEASURING INSTRUMENT (build this first)

The validation framework is the arbiter everything else is judged against. It should arguably be built **before** the factors, because it is the instrument the whole project depends on.

For every design choice, the harness must report:

| Question | Mathematical answer it must produce |
|---|---|
| Does this factor add value? | Marginal contribution to risk-adjusted return, out-of-sample, after costs |
| How sensitive to parameter X? | Full parameter sensitivity surface — smooth plateau vs knife-edge |
| Is the weighting right? | OOS Sharpe, max drawdown, turnover, robustness across sub-periods, per scheme |
| Is the result real or luck? | Deflated Sharpe ratio, count of configurations tested, significance after multiple-testing correction |
| Will it survive regime change? | Performance decomposed BY regime / sub-period, not just aggregate |
| Does it survive reality? | Net of realistic transaction costs, slippage, turnover |

### Three mandatory anti-self-deception safeguards:
1. **Multiple-testing discipline.** Track how many configurations have been tested. Apply deflated Sharpe / multiple-testing corrections. The more you search, the more you fool yourself.
2. **Out-of-sample is a spent budget.** Maintain a true holdout touched ONCE. Use walk-forward / **purged cross-validation with embargo periods** to prevent train/test leakage.
3. **Stationarity cannot be assumed.** The 2022 stock-bond correlation flip is the canonical warning: a 20-year negative correlation reversed. Robustness across sub-regimes beats a single headline Sharpe.

### Avoid lookahead bias structurally:
Build a **point-in-time database** — every data point timestamped as of when it was actually knowable. Store the *vintage* of fundamental data so the backtest only ever sees what would have been available at decision time.

---

## 7. Risk Decomposition & Hedging Layer

**Concept:** Net exposure accounting via a factor exposure matrix. Every position (equity or hedge) decomposes into its contribution to each risk factor; sum across the book:
- Net portfolio beta = Σ (weight × beta)
- Net portfolio duration = Σ (weight × duration contribution)

Hedges are simply positions with **negative** contributions to the factors to be neutralized. Size them so net exposure on each unwanted dimension lands where desired (often ~zero for risks with no view; fully intact for conviction bets). This is the logic behind risk parity and portable alpha: separate alpha (selection) from beta (market exposure), dial each independently.

**Regime-dependence of hedges (critical nuance):** Treasuries hedge equities against *growth / risk-off* shocks but can AMPLIFY losses in *inflation / rate* shocks (both fall together). Stock-bond correlation is itself regime-dependent. Hedge ratios must therefore be regime-aware (ties back to System 2).

| Risk to offset | Hedge instrument | Mechanism |
|---|---|---|
| Market beta | S&P futures (ES), SPY puts, inverse ETF | Reduce net market exposure |
| Rate / duration | UST futures (ZN, ZB), TLT | Offset discount-rate sensitivity |
| Sector concentration | Sector ETFs (long/short) | Neutralize industry bets |
| Tail risk | OTM index puts, VIX calls | Convex protection |
| Single-name | Pair trade within sector | Isolate idiosyncratic alpha |

---

## 8. Risk Appetite — Operationalized as Constraints (not predictions)

| Lever | Conservative | Aggressive |
|---|---|---|
| Net exposure | 40–60% | 90–100%+ |
| Position count | 40–60 names | 15–25 names |
| Factor tilt | Quality, low-vol | Momentum, growth |
| Vol target (annualized) | 8–10% | 18–25% |
| Max position | 2–3% | 6–8% |
| Sector cap vs benchmark | ±5% | unconstrained |

---

## 9. Roadmap / Phasing

- **Phase 0 — Backtest harness (build FIRST):** Validation framework, purged cross-validation with embargo, the full metrics suite above. This is the measuring instrument.
- **Phase 1 — Data foundation (2–3 wks):** Clean, reliable ingestion. Free sources first (yfinance, FRED, SEC EDGAR). Point-in-time PostgreSQL DB with data vintages. Unglamorous and the most important phase.
- **Phase 2 — Single-factor computation (2 wks):** Compute each raw factor per ticker per date; cross-sectional z-score; winsorize; sector-neutralize.
- **Phase 3 — Composite scoring (1–2 wks):** Combine into bucket and composite scores. EQUAL WEIGHT first. Output ranked list of 500.
- **Phase 4 — Backtesting & validation (4+ wks, the long pole):** Top vs bottom rank divergence, OOS only, ruthless about costs/turnover/crowding. Project lives or dies here.
- **Phase 4.5 — Consolidated risk view:** Dashboard of net beta, net duration, sector concentration, factor tilts across the whole book. Valuable on its own even with zero hedging — institutional-grade, CFO-minded artifact.
- **Phase 5 — Regime overlay (System 2):** Macro indicators → risk-on/neutral/risk-off classifier → dynamic factor-weight tilts.
- **Phase 6 — Hedge overlay:** Neutralize unwanted net exposures using the risk decomposition layer.
- **Phase 7 — Regime-aware hedging:** Vary hedge ratios by regime (since stock-bond correlation flips).
- **Phase 8 — Monitoring & alerting:** Daily score refresh, ranking changes, exposure drift. Turns research project into usable tool.

**Sequencing principle:** Build System 1 fully and trust it over months before adding System 2 as a throttle. You cannot hedge a book whose exposures you cannot yet measure — build the thing that MEASURES net exposure before the thing that NEUTRALIZES it.

---

## 10. The One Irreducible Human Judgment

Optimization can map the robustness-vs-return trade-off frontier perfectly. It CANNOT decide where on that frontier the owner should sit — that depends on the owner's actual utility function and what a drawdown would mean for them. The math presents the frontier with total clarity; the owner picks the point. This is the honest boundary of what the system can decide for itself.

---

## 11. Environment Notes

- Owner runs Windows + WSL2, 32GB RAM, GTX 1650, files on D: drive.
- PostgreSQL already in use (preferred backend).
- Free data sources prioritized initially: yfinance, FRED API, SEC EDGAR, CBOE, CME FedWatch, CFTC COT.

---

*End of charter. If a proposed change contradicts Sections 1, 2, or 6, stop and get explicit owner approval before proceeding.*
