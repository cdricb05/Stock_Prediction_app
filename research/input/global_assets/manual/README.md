# Manual Global ETF Price Data Intake (Phase 3-W)

Phase 3-V could not download these price histories automatically (the free Stooq CSV
endpoint blocked automated access). Phase 3-W lets you supply the data **manually** from a
legitimate source you already have access to.

This phase calls **no network**. It only reads CSV files you place in this folder. All
price data must be **real** - do not fabricate rows.

## Where to put files

Place CSV files directly in this folder:

    research/input/global_assets/manual/

## Accepted file layouts

1. **One file per ticker**, named `<TICKER>.csv` - e.g. `SPY.csv`, `QQQ.csv`, `TLT.csv`.
2. **One combined file** named `global_etf_prices.csv` with a `ticker` column.

The `README.md` and `manual_global_etf_price_template.csv` files are ignored by the scan.

## Accepted column variants (header row, case-insensitive)

    Date, Open, High, Low, Close, Adj Close, Volume
    date, open, high, low, close, adjusted_close, volume
    ticker, date, open, high, low, close, adjusted_close, volume
    ticker, date, adjusted_open, adjusted_high, adjusted_low, adjusted_close, volume

`adjusted_close` uses `adjusted_close`/`Adj Close` if present, otherwise `close`.

## Acceptable sources (manual only)

- A browser-downloaded CSV from a legitimate historical price page.
- A broker export.
- A data-vendor export you already have access to.

Do not use paid API automation or any network fetch - this phase is manual intake only.

## Target tickers (22)

    SPY QQQ IWM EFA EEM VGK EWJ FXI SHY IEF TLT TIP LQD HYG GLD SLV DBC USO UUP FXE FXY VIXY

After adding files, re-run:

    python -B research/run_phase3w_manual_global_etf_data_import.py
