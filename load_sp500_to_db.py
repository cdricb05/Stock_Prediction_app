# load_sp500_to_db.py
# Robust S&P500 (plus ETFs) daily/range loader for PostgreSQL

import os
import sys
import time
import argparse
from datetime import datetime, date, timedelta
from typing import List, Tuple, Optional

import pandas as pd
import yfinance as yf
from sqlalchemy import create_engine, text

DEFAULT_DB_URL = os.getenv("DB_URL", "postgresql://postgres:Adam2015@localhost:5432/stock_data")
HEARTBEAT_PATH = r"C:\StockJobs\last_daily_heartbeat.txt"

engine = create_engine(DEFAULT_DB_URL, pool_pre_ping=True)

def log(msg): print(msg, flush=True)
def warn(msg): print(f"[WARN] {msg}", flush=True)
def err(msg): print(f"[ERROR] {msg}", flush=True)

def is_business_day(d: date) -> bool:
    return d.weekday() < 5

def prev_business_day(ref: Optional[date] = None) -> date:
    d = (ref or date.today()) - timedelta(days=1)
    while not is_business_day(d):
        d -= timedelta(days=1)
    return d

def get_window(start_str: Optional[str], end_str: Optional[str]) -> Tuple[Optional[date], Optional[date]]:
    s = datetime.strptime(start_str, "%Y-%m-%d").date() if start_str else None
    e = datetime.strptime(end_str, "%Y-%m-%d").date() if end_str else None
    if s and e and e <= s:
        raise ValueError("--end must be after --start (exclusive end)")
    return s, e

HARDCODED_SP500 = [
    "A","AAL","AAP","AAPL","ABBV","ABC","ABMD","ABT","ACN","ADBE","ADI","ADM","ADP","ADSK","AEE","AEP",
    "AES","AFL","AIG","AIZ","AJG","AKAM","ALB","ALGN","ALK","ALL","ALLE","AMAT","AMCR","AMD","AME","AMGN",
    "AMP","AMT","AMZN","ANET","ANSS","AON","AOS","APA","APD","APH","APTV","ARE","ATO","AVB","AVGO","AVY",
    "AWK","AXP","AZO","BA","BAC","BALL","BAX","BBWI","BBY","BDX","BEN","BF-B","BIIB","BIO","BK","BKNG",
    "BKR","BLK","BMY","BR","BRK-B","BRO","BSX","BWA","BXP","C","CAG","CAH","CARR","CAT","CB","CBOE","CBRE",
    "CCI","CCL","CDNS","CDW","CE","CEG","CF","CFG","CHD","CHRW","CHTR","CI","CINF","CL","CLX","CMA","CMCSA",
    "CME","CMG","CMI","CMS","CNC","CNP","COF","COO","COP","COST","CPB","CPRT","CPT","CRL","CRM","CSCO",
    "CSGP","CSX","CTAS","CTLT","CTRA","CTSH","CTVA","CVS","CVX","CZR","D","DAL","DD","DE","DFS","DG",
    "DGX","DHI","DHR","DIS","DISH","DLR","DLTR","DOV","DOW","DPZ","DRE","DRI","DTE","DUK","DVA","DVN",
    "DXC","DXCM","EA","EBAY","ECL","ED","EFX","EIX","EL","EMN","EMR","ENPH","EOG","EPAM","EQIX","EQR",
    "EQT","ES","ESS","ETN","ETR","ETSY","EVRG","EW","EXC","EXPD","EXPE","EXR","F","FANG","FAST","FCX",
    "FDS","FDX","FE","FFIV","FIS","FISV","FITB","FLT","FMC","FOX","FOXA","FRC","FRT","FTNT","FTV","GD",
    "GE","GILD","GIS","GL","GLW","GM","GNSS","GOOG","GOOGL","GPC","GPN","GRMN","GS","GWW","HAL","HAS",
    "HBAN","HCA","HD","HES","HIG","HII","HLT","HOLX","HON","HPE","HPQ","HRL","HSIC","HST","HSY","HUM",
    "HWM","IBM","ICE","IDXX","IEX","IFF","ILMN","INCY","INTC","INTU","INVH","IP","IPG","IQV","IR","IRM",
    "ISRG","IT","ITW","IVZ","J","JBHT","JCI","JKHY","JNJ","JNPR","JPM","K","KEY","KEYS","KHC","KIM",
    "KLAC","KMB","KMI","KMX","KO","KR","L","LDOS","LEN","LH","LHX","LIN","LKQ","LLY","LMT","LNC","LNT",
    "LOW","LRCX","LUMN","LUV","LVS","LW","LYB","LYV","MA","MAA","MAR","MAS","MCD","MCHP","MCK","MCO",
    "MDLZ","MDT","MET","META","MGM","MHK","MKC","MKTX","MLM","MMC","MMM","MNST","MO","MOH","MOS","MPC",
    "MPWR","MRK","MRNA","MRO","MS","MSCI","MSFT","MSI","MTB","MTCH","MTD","MU","NCLH","NDAQ","NEE","NEM",
    "NFLX","NI","NKE","NOC","NOW","NRG","NSC","NTAP","NTRS","NUE","NVDA","NVR","NWL","NWS","NWSA","NXPI",
    "O","OKE","OMC","ON","ORCL","ORLY","OXY","PAYC","PAYX","PCAR","PCG","PEAK","PEG","PEP","PFE","PFG",
    "PG","PGR","PH","PHM","PKG","PKI","PLD","PM","PNC","PNR","PNW","POOL","PPG","PPL","PRU","PSA","PSX",
    "PTC","PWR","PXD","QCOM","QRVO","RCL","RE","REG","REGN","RF","RHI","RJF","RL","RMD","ROK","ROL","ROP",
    "ROST","RSG","RTX","SBAC","SBUX","SEDG","SEE","SHW","SIVB","SJM","SLB","SNA","SNPS","SO","SPG","SPGI",
    "SRE","STE","STT","STX","STZ","SWK","SWKS","SYF","SYK","SYY","T","TAP","TDG","TDY","TECH","TEL","TER",
    "TFC","TFX","TGT","TJX","TMO","TMUS","TPR","TRMB","TROW","TRV","TSCO","TSLA","TSN","TT","TTWO","TXN",
    "TXT","TYL","UAL","UDR","UHS","ULTA","UNH","UNP","UPS","URI","USB","V","VFC","VICI","VLO","VMC","VNO",
    "VRSN","VRTX","VTR","VTRS","VZ","WAB","WAT","WBA","WBD","WDC","WEC","WELL","WFC","WHR","WM","WMB",
    "WMT","WRB","WRK","WST","WTW","WY","WYNN","XEL","XOM","XRAY","XYL","YUM","ZBH","ZBRA","ZION","ZTS",
    "SPY", "QQQ", "VOO", "VGT"
]

def fetch_sp500_tickers() -> List[str]:
    # Try 1: GitHub CSV (no lxml needed)
    gh_url = "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents_symbols.txt"
    try:
        tickers_txt = pd.read_csv(gh_url, header=None)
        tickers = [str(x).strip().replace(".", "-") for x in tickers_txt[0].tolist() if str(x).strip()]
        if len(tickers) >= 400:
            out = sorted(set(tickers + ["SPY", "QQQ", "VOO", "VGT"]))
            log(f"[INFO] Tickers loaded from GitHub: {len(out)}")
            return out
    except Exception as e:
        warn(f"GitHub fetch failed: {e}")

    # Try 2: Wikipedia (requires lxml)
    try:
        import lxml  # noqa: F401
        wiki = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
        tables = pd.read_html(wiki)
        if tables:
            symbols = tables[0].iloc[:, 0].astype(str).str.strip().str.replace(".", "-", regex=False).tolist()
            out = sorted(set(symbols + ["SPY", "QQQ", "VOO", "VGT"]))
            log(f"[INFO] Tickers loaded from Wikipedia: {len(out)}")
            return out
    except ImportError:
        warn("lxml not installed, skipping Wikipedia. Run: pip install lxml")
    except Exception as e:
        warn(f"Wikipedia fetch failed: {e}")

    # Try 3: Hardcoded fallback (always works)
    log(f"[INFO] Using hardcoded ticker list: {len(HARDCODED_SP500)} tickers")
    return sorted(HARDCODED_SP500)

REQUIRED_COLS = ["date", "open", "high", "low", "close", "adj_close", "volume"]

def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    rename_map = {
        "Date": "date", "Adj Close": "adj_close", "Open": "open",
        "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
    }
    df.rename(columns=rename_map, inplace=True)
    df.columns = [c[0].lower() if isinstance(c, tuple) else str(c).lower() for c in df.columns]
    if "adj_close" not in df.columns and "close" in df.columns:
        df["adj_close"] = df["close"]
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for c in ["open", "high", "low", "close", "adj_close"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    if "volume" in df.columns:
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce")
    return df

def validate_frame(df: pd.DataFrame) -> bool:
    return df is not None and not df.empty and set(REQUIRED_COLS).issubset(df.columns)

def yf_daily_period(symbol: str, days: int = 12) -> pd.DataFrame:
    df = yf.download(symbol, period=f"{days}d", interval="1d", auto_adjust=False, progress=False, threads=True)
    if df is None or df.empty:
        return pd.DataFrame()
    return normalize_columns(df.reset_index())

def yf_range(symbol: str, start: date, end: date) -> pd.DataFrame:
    df = yf.download(symbol, start=start, end=end, interval="1d", auto_adjust=False, progress=False, threads=True)
    if df is not None and not df.empty:
        return normalize_columns(df.reset_index())
    warn(f"yfinance empty for {symbol} in range [{start}..{end}); trying Ticker.history()")
    try:
        hist = yf.Ticker(symbol).history(start=start, end=end, interval="1d", auto_adjust=False)
        if hist is not None and not hist.empty:
            return normalize_columns(hist.reset_index())
    except Exception as e:
        warn(f"Ticker.history failed for {symbol}: {e}")
    dfp = yf_daily_period(symbol, days=30)
    if dfp is not None and not dfp.empty:
        mask = (dfp["date"].dt.date >= start) & (dfp["date"].dt.date < end)
        return dfp.loc[mask].copy()
    return pd.DataFrame()

def upsert_block(ticker: str, block: pd.DataFrame) -> Tuple[int, int]:
    if block.empty:
        return (0, 0)
    block = block.copy()
    block["ticker"] = ticker
    block = block[["ticker"] + REQUIRED_COLS].dropna(subset=["date"]).drop_duplicates(subset=["ticker", "date"])
    n_days = block["date"].dt.date.nunique()
    start = block["date"].min()
    end = block["date"].max()
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM stock_prices WHERE ticker = :t AND date >= :start AND date <= :end"),
            {"t": ticker, "start": start, "end": end}
        )
        block.to_sql("stock_prices", conn, if_exists="append", index=False)
    return (len(block), n_days)

def process_ticker_daily(ticker: str, target_day: date) -> Tuple[str, str]:
    try:
        df = yf_daily_period(ticker, days=12)
        if df.empty:
            return ("no-data", "yfinance: empty period")
        df["__d"] = df["date"].dt.date
        one = df[df["__d"] == target_day].drop(columns=["__d"])
        if one.empty:
            return ("no-data", f"no row for {target_day}")
        if not validate_frame(one):
            return ("error", "missing required columns")
        wrote, ndays = upsert_block(ticker, one)
        return ("ok", f"{wrote}r/{ndays}d")
    except Exception as e:
        return ("error", f"{type(e).__name__}: {e}")

def process_ticker_range(ticker: str, start: date, end: date) -> Tuple[str, str]:
    try:
        df = yf_range(ticker, start, end)
        if df.empty:
            return ("no-data", "empty after all fetch attempts")
        if not validate_frame(df):
            return ("error", "missing required columns")
        wrote, ndays = upsert_block(ticker, df)
        return ("ok", f"{wrote}r/{ndays}d")
    except Exception as e:
        return ("error", f"{type(e).__name__}: {e}")

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Fetch OHLCV for S&P500+ and load into PostgreSQL (stock_prices).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--daily", action="store_true", help="Daily mode: fetch previous (or chosen) business day.")
    mode.add_argument("--full", action="store_true", help="Full backfill 2010..today (long).")
    p.add_argument("--start", type=str, help="Range start YYYY-MM-DD.")
    p.add_argument("--end", type=str, help="Range end YYYY-MM-DD (exclusive; default today).")
    p.add_argument("--daily-date", type=str, help="Force a specific business day (YYYY-MM-DD) for daily mode.")
    p.add_argument("--ticker", type=str, help="Single ticker symbol.")
    p.add_argument("--limit", type=int, default=0, help="Limit tickers processed (for testing).")
    p.add_argument("--sleep", type=float, default=0.8, help="Sleep seconds between tickers.")
    p.add_argument("--heartbeat", action="store_true", help="Write a heartbeat timestamp to verify scheduler.")
    p.add_argument("--verbose", action="store_true", help="Verbose logs.")
    return p

def main():
    args = build_arg_parser().parse_args()
    if args.full:
        mode = "full"
    elif args.start:
        mode = "range"
    else:
        mode = "daily"

    if args.heartbeat:
        try:
            with open(HEARTBEAT_PATH, "w", encoding="utf-8") as f:
                f.write(f"OK {datetime.now():%Y-%m-%d %H:%M:%S}\n")
        except Exception as e:
            warn(f"Could not write heartbeat: {e}")

    tickers = [args.ticker] if args.ticker else fetch_sp500_tickers()
    if args.limit and args.limit > 0:
        tickers = tickers[:args.limit]

    if mode == "daily":
        target_day = datetime.strptime(args.daily_date, "%Y-%m-%d").date() if args.daily_date else prev_business_day()
        window_msg = f"target_day={target_day}"
    elif mode == "range":
        s, e = get_window(args.start, args.end or date.today().strftime("%Y-%m-%d"))
        target_day = None
        window_msg = f"window=[{s} .. {e})"
    else:
        s, e = date(2010, 1, 1), date.today() + timedelta(days=1)
        target_day = None
        window_msg = f"window=[{s} .. {e})"

    log(f"[INFO] Mode={mode} {window_msg} tickers={len(tickers)}")

    ok = nd = er = 0
    t0 = time.time()
    for i, t in enumerate(tickers, start=1):
        prefix = f"[{i}/{len(tickers)}] {t}"
        try:
            if mode == "daily":
                status, detail = process_ticker_daily(t, target_day)
            elif mode == "range":
                status, detail = process_ticker_range(t, s, e)
            else:
                cur_s = s
                wrote_total = 0
                days_total = 0
                status = "no-data"
                detail = "init"
                while cur_s < e:
                    cur_e = min(cur_s + timedelta(days=31), e)
                    st, de = process_ticker_range(t, cur_s, cur_e)
                    if st == "ok":
                        try:
                            x, y = de.split("r/")
                            wrote_total += int(x)
                            days_total += int(y[:-1])
                            status = "ok"
                        except Exception:
                            status = st
                    elif st == "error":
                        status = "error"
                        detail = de
                        break
                    cur_s = cur_e
                if status == "ok":
                    detail = f"{wrote_total}r/{days_total}d"

            if status == "ok":
                ok += 1
                log(f"{prefix} -> ok:{detail}")
            elif status == "no-data":
                nd += 1
                log(f"{prefix} -> no-data")
            else:
                er += 1
                err(f"{prefix} -> {detail}")
        except KeyboardInterrupt:
            err("Interrupted by user.")
            break
        except Exception as e:
            er += 1
            err(f"{prefix} -> {type(e).__name__}: {e}")

        if args.sleep > 0:
            time.sleep(args.sleep)

    log(f"[DONE] ok={ok} no-data={nd} errors={er} elapsed={round(time.time()-t0,1)}s")

if __name__ == "__main__":
    main()
