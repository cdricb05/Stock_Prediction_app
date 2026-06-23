# Phase 7-I - APPROVED controlled pilot live collection (Windows PowerShell).
# Review the dry-run candidate universe + collection plan FIRST. This script sets the
# one approval gate and runs the bounded pilot (<= 300 tickers, rate-limited, free
# sources only). Large data is written under D:\Stock_Prediction_app_data\phase7i_broad_universe\;
# only committed-safe summaries land in the repo. It does NOT commit or push.
#
# Pre-req: the price collector dependency must be present (see dependency_check.csv).
#   python -c "import yfinance"   # must succeed; if not, install it manually first.

Set-Location 'C:\Users\binis\Stock_Prediction_app_push'
$env:PHASE7I_LIVE_APPROVED = 'YES'
python -m research.run_phase7i_controlled_broad_universe_data_build
Remove-Item Env:\PHASE7I_LIVE_APPROVED
