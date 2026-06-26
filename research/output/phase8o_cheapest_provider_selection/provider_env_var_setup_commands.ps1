# Phase 8-O provider env-var setup (PLACEHOLDER ONLY - never paste a real key into a
# committed file). Set the key in your CURRENT PowerShell session only; this phase
# reads presence from the environment and never writes a key to disk.
#
# Phase 8-N proved the current FMP plan is INSUFFICIENT (all six critical families
# entitlement-PARTIAL). Cheapest path = free-tier alternatives, earnings first:
#
# 1) CHEAPEST FIRST - Alpha Vantage (earnings surprises/calendar; free tier 25 req/day):
#   $env:ALPHAVANTAGE_API_KEY = '<PASTE_ALPHAVANTAGE_KEY_HERE>'
#
# 2) SECOND - Finnhub (analyst estimates/recommendations/price targets/grades; free trends):
#   $env:FINNHUB_API_KEY = '<PASTE_FINNHUB_KEY_HERE>'
#
# Optional single-bundle alternative (only if a single paid source is preferred):
#   $env:EODHD_API_KEY = '<PASTE_EODHD_KEY_HERE>'
#
# FMP Premium upgrade is the LAST-RESORT fallback (re-uses FMP_API_KEY). FMP Ultimate
# is NOT justified - do not buy it.
#
# After setting a key, re-run the bounded live probe of the keyed provider(s):
#   python research/run_phase8o_cheapest_provider_selection.py --live
