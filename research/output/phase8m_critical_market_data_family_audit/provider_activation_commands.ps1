# Phase 8-M provider activation commands (PLACEHOLDER ONLY - never paste a real
# key into a committed file). Set the key in your CURRENT PowerShell session only;
# the audit reads it from the environment and never writes it to disk.
#
# FMP is the recommended first provider (one key unlocks the most critical families).
#   $env:FMP_API_KEY = '<PASTE_FMP_KEY_HERE>'
#
# Conditional / later providers (only if a signal needs them - exhaust free first):
#   $env:INTRINIO_API_KEY = '<PASTE_INTRINIO_KEY_HERE>'   # options IV/skew (not yet needed)
#   $env:FINNHUB_API_KEY  = '<PASTE_FINNHUB_KEY_HERE>'    # short interest depth (FINRA free first)
#
# Free sources need NO key: FINRA (short interest), GDELT (news), SEC EDGAR (filings).
#
# After setting FMP_API_KEY, run the bounded live entitlement probe:
#   python research/run_phase8m_critical_market_data_family_audit.py --live
