import streamlit as st
import pandas as pd
import requests
import traceback
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

API_URL = "http://localhost:8000"

MODEL_COLORS = {
    "Naive": "#6b7280",
    "Drift": "#64748b",
    "SMA": "#92400e",
    "LinearTrend": "#0f766e",
    "ETS": "#a21caf",
    "Prophet": "#3b82f6",
    "ARIMA": "#22c55e",
    "XGBoost": "#f59e0b",
    "LSTM": "#8b5cf6",
    "Average": "#111827",
    "Robust Avg": "#1f2937",
}

def colored_label(text: str, color: str) -> str:
    return f"<span style='background:{color};color:white;padding:4px 8px;border-radius:8px'>{text}</span>"

def main():
    st.set_page_config(page_title="Stock Forecast Dashboard", layout="wide")
    st.title("📈 Stock Prediction Dashboard")

    cfg = {}
    with st.sidebar:
        st.markdown("### Backend Status")
        try:
            cfg = requests.get(f"{API_URL}/config", timeout=5).json()
            st.success(
                f"API OK • Live: {cfg.get('allow_live_price')} • LSTM: {cfg.get('enable_lstm')} \n"
                f"Err thresholds: good≤{cfg.get('good_err', 1.0)}%, warn≤{cfg.get('warn_err', 2.0)}% • eps_direction±{(cfg.get('eps_direction',0.003))*100:.2f}%"
            )
        except Exception as e:
            st.error(f"API not reachable: {e}")

    ticker = st.text_input("Enter stock ticker (e.g., AAPL):", value="AAPL").upper().strip()

    if st.button("Run Forecast") and ticker:
        try:
            res = requests.post(f"{API_URL}/predict_all_models/", json={"ticker": ticker}, timeout=180)
            if res.status_code != 200:
                st.error(f"Server returned error {res.status_code}")
                return
            data = res.json()
            if "error" in data:
                st.error(f"API error: {data['error']}")
                return

            # ===== Top summary =====
            st.subheader(f"Summary for {data['ticker']}")
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("DB Last Close", f"${data.get('yesterday_close', '—')}")
            src_map = {"intraday": "Live (1m)", "yf_last_close": "Yahoo Daily Close", "db_last_close": "DB Last Close"}
            price_src_key = data.get("price_source", "")
            price_src = src_map.get(price_src_key, price_src_key or "—")
            cp = data.get("current_price")
            c2.metric(f"Price Used — {price_src}", f"${cp}" if cp is not None else "—")
            c3.metric("As of", data.get("price_as_of") or "—")

            d1 = data.get("ensemble_day1"); d5 = data.get("ensemble_day5")
            d1p = data.get("d1_change_pct"); d5p = data.get("d5_change_pct")
            c4.metric("D+1 target (est. close)", f"${d1 if d1 is not None else '—'}", f"{d1p if d1p is not None else '—'}%")
            c5.metric("D+5 target", f"${d5 if d5 is not None else '—'}", f"{d5p if d5p is not None else '—'}%")

            rec = str(data.get("recommendation", "Hold"))
            rec_color = "#0ea5e9"
            if rec.startswith("Strong Buy"):   rec_color = "#16a34a"
            elif rec == "Buy":                  rec_color = "#22c55e"
            elif rec == "Sell":                 rec_color = "#ef4444"
            elif rec.startswith("Strong Sell"): rec_color = "#b91c1c"
            c6.markdown(colored_label(rec, rec_color), unsafe_allow_html=True)

            # ===== Why this call =====
            st.subheader("Why this recommendation?")
            bullets = [f"- {line}" for line in (data.get("rationale") or [])]
            if not bullets:
                bullets.append("- No rationale computed (unexpected). Please check server logs.")
            st.markdown("\n".join(bullets))

            elig = data.get("eligibility") or {}
            st.caption(
                f"Agreement/Confidence use only **directional** eligible models (exclude 'flat' within ±{(cfg.get('eps_direction',0.003))*100:.2f}%). "
                f"Totals → models: {elig.get('n_total_models',0)}, eligible: {elig.get('n_eligible',0)}, "
                f"bullish: {elig.get('n_bullish',0)}, bearish: {elig.get('n_bearish',0)}, flat excluded: {elig.get('n_flat',0)}."
            )

            # ===== Chart =====
            st.subheader("Forecast Chart")
            fig, ax = plt.subplots(figsize=(14, 6))
            hist_dates = pd.to_datetime(pd.Series(data["hist_dates"]), format="%m/%d/%y", errors="coerce")
            hist_prices = data.get("hist_prices", [])
            ax.plot(hist_dates, hist_prices, label="History → Price Used", linewidth=2, color="#4b5563")

            forecast_dates = pd.to_datetime(pd.Series(data.get("forecast_dates", [])), format="%m/%d/%y", errors="coerce")
            results = data.get("results", {})
            last_date = hist_dates.iloc[-1] if len(hist_dates) else None
            last_price = hist_prices[-1] if len(hist_prices) else None

            for model, preds in results.items():
                if isinstance(preds, list) and len(preds) == 5 and last_date is not None and last_price is not None:
                    xs = pd.concat([pd.Series([last_date]), forecast_dates], ignore_index=True)
                    ys = [last_price] + preds
                    ax.plot(xs, ys, label=model, linestyle="--", linewidth=2, color=MODEL_COLORS.get(model, None))

            avg = data.get("robust_forecast") or data.get("avg_forecast")
            if avg and last_date is not None and last_price is not None:
                xs = pd.concat([pd.Series([last_date]), forecast_dates], ignore_index=True)
                ys = [last_price] + avg
                ax.plot(xs, ys, label="Robust Avg" if data.get("robust_forecast") else "Average",
                        linewidth=3, color=MODEL_COLORS["Robust Avg"])

            ax.set_xlabel("Date"); ax.set_ylabel("Price ($)")
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
            fig.autofmt_xdate()

            all_prices = list(hist_prices)
            for preds in results.values():
                if isinstance(preds, list) and len(preds) == 5:
                    all_prices.extend(preds)
            if avg:
                all_prices.extend(avg)
            if all_prices:
                ymin = min(all_prices) * 0.90; ymax = max(all_prices) * 1.10
                if ymin == ymax:
                    ymin *= 0.95; ymax *= 1.05
                ax.set_ylim(ymin, ymax)
            ax.legend(ncol=2)
            st.pyplot(fig)

            # ===== Per-model table =====
            st.subheader("Per-model predictions (D+1 and D+5)")
            summary = data.get("per_model_summary", [])
            if summary:
                df = pd.DataFrame(summary)
                df = df[["model", "d1", "d5", "d5_change_pct", "direction"]]
                df.columns = ["Model", "D+1 Price", "D+5 Price", "D+5 % vs Price Used", "Direction (5D)"]
                st.dataframe(df, use_container_width=True)
            else:
                st.info("No per-model summary available.")

            # ===== Full 5-day table =====
            st.subheader("Forecast (next 5 business days)")
            rows = data.get("table_rows", [])
            if rows:
                table = pd.DataFrame(rows)
                pref = ["Date","Naive","Drift","SMA","LinearTrend","ETS","Prophet","ARIMA","XGBoost","LSTM","Robust Avg","SPY Ret %","Alpha %","SPY Ratio"]
                cols = [c for c in pref if c in table.columns] + [c for c in table.columns if c not in pref]
                table = table[cols]
                st.dataframe(table, use_container_width=True)
            else:
                st.info("No forecast table rows available.")

            with st.expander("How to read this dashboard (details)", expanded=False):
                st.markdown(f"""
**Top cards**
- **Price Used**: live intraday close when available, else DB last close.
- **D+1/D+5 target**: robust-average ensemble of eligible models (fallback to simple average). The delta shows % vs Price Used.
- **Recommendation**: requires both minimum **agreement** and **confidence**; otherwise defaults to Hold even if price targets move.

**Chart**
- Last ~2 weeks of history connected to each forecast line for continuity.

**Table columns**
- **Naive**: last close carried forward (baseline).
- **Drift**: random-walk with drift using recent average daily log-return.
- **SMA / LinearTrend / ETS / Prophet / ARIMA / XGBoost / LSTM**: individual model forecasts for each of the next 5 business days.
- **Robust Avg**: **median** across eligible models after excluding high-error models (MAE% & RMSE% > warning band).
- **SPY Ret %**: SPY's predicted **return** for that day (not price), used only to compute alpha.
- **Alpha %**: (Ticker return – SPY return). Positive alpha means expected outperformance vs SPY.
- **SPY Ratio**: Ticker forecast / SPY forecast for that day (when both are available).

**Eligibility & consensus**
- Models are eligible for consensus when their backtest errors are within the warning band (≤ {cfg.get('warn_err',2.0):.2f}% RMSE & MAE).
- Directional votes ignore models whose 5-day change is within ±{(cfg.get('eps_direction',0.003))*100:.2f}% of the Price Used.
""")

        except Exception as e:
            st.error(f"Exception: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    main()
