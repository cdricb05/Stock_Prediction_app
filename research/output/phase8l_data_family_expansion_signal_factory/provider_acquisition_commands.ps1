# Phase 8-L provider acquisition commands (Windows PowerShell).
# COMMITTED-SAFE: placeholders only. Replace <your_key> with your own key in YOUR shell.
# Never commit a real key. Set the variable in your session, then re-run the factory.
#
# First subscription recommendation: FMP (broad earnings surprise + analyst revisions).
# Always exhaust the FREE sources (FINRA short interest, GDELT news, SEC EDGAR filings) first.

# FMP (~22-29 (Starter)) — financialmodelingprep.com/developer/docs : /earnings-surprises, /analyst-estimates, /earning_calendar, /press-releases, /transcript
#   status: not set
$env:FMP_API_KEY = "<your_key>"

# Finnhub (~0 (free tier) / paid for history) — finnhub.io/docs/api : /stock/earnings, /company-news, /news-sentiment, /stock/transcripts, /stock/short-interest
#   status: not set
$env:FINNHUB_API_KEY = "<your_key>"

# AlphaVantage (~0 (free tier, 25/day) / ~50 premium) — alphavantage.co/documentation : EARNINGS, NEWS_SENTIMENT, HISTORICAL_OPTIONS
#   status: not set
$env:ALPHAVANTAGE_API_KEY = "<your_key>"

# EODHD (~20-80 by bundle) — eodhd.com/financial-apis : /calendar/earnings, /fundamentals
#   status: not set
$env:EODHD_API_KEY = "<your_key>"

# Benzinga (enterprise (quote required)) — docs.benzinga.io : /news, /calendar/earnings, /calendar/ratings
#   status: not set
$env:BENZINGA_API_KEY = "<your_key>"

# Intrinio (options packages (quote required)) — docs.intrinio.com : /options/prices, /options/expirations (IV/Greeks)
#   status: not set
$env:INTRINIO_API_KEY = "<your_key>"

# Polygon (~29-199 by tier) — polygon.io/docs : /v3/snapshot/options, /v2/reference/news
#   status: not set
$env:POLYGON_API_KEY = "<your_key>"

# Tiingo (~10) — tiingo.com/documentation : /tiingo/news, /tiingo/fundamentals
#   status: not set
$env:TIINGO_API_KEY = "<your_key>"

# Quandl (dataset-dependent) — data.nasdaq.com (Nasdaq Data Link) : short-interest datasets
#   status: not set
$env:QUANDL_API_KEY = "<your_key>"

# NewsAPI (~0 dev (no history) / ~449 business) — newsapi.org/docs : /v2/everything (recent window only)
#   status: not set
$env:NEWSAPI_KEY = "<your_key>"

# FINRA: FREE / no key required (cdn.finra.org/equity/regsho (daily) ; finra.org consolidated short interest (biweekly)).
# GDELT: FREE / no key required (api.gdeltproject.org/api/v2/doc/doc (recent window, entity-level)).
# SEC_EDGAR: FREE / no key required (data.sec.gov : /submissions, Form 4 (insider), 13F, 8-K/10-Q/10-K).
# SimFin: FREE / no key required (simfin.com (bulk fundamentals; local CSV)).
