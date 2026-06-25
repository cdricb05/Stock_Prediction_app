# Phase 8-F provider acquisition commands (committed-safe; NO secrets).
# Set a real key for the chosen provider, then run the matching adapter. Bounded caps are
# enforced inside each adapter: <= 25 tickers, <= 30 days, <= 500 events. Raw under D:/Stock_Prediction_app_data/external_raw; normalized under D:/Stock_Prediction_app_data/external_normalized.

# A_news_sentiment_x_sensitivity (news_sentiment) via one of: NEWSAPI_KEY;BENZINGA_API_KEY;ALPHAVANTAGE_API_KEY;EODHD_API_KEY;POLYGON_API_KEY
$env:NEWSAPI_KEY = "<YOUR_KEY>"
python "D:/Stock_Prediction_app_data/external_adapters/news_sentiment_adapter.py"   # add --dry-run to preview the plan only

# B_analyst_revision_x_sensitivity (analyst_revision) via one of: FMP_API_KEY;FINNHUB_API_KEY;INTRINIO_API_KEY;ALPHAVANTAGE_API_KEY
$env:FMP_API_KEY = "<YOUR_KEY>"
python "D:/Stock_Prediction_app_data/external_adapters/analyst_revision_adapter.py"   # add --dry-run to preview the plan only

# C_earnings_surprise_x_sensitivity (earnings) via one of: FMP_API_KEY;FINNHUB_API_KEY;ALPHAVANTAGE_API_KEY;EODHD_API_KEY
$env:FMP_API_KEY = "<YOUR_KEY>"
python "D:/Stock_Prediction_app_data/external_adapters/earnings_adapter.py"   # add --dry-run to preview the plan only

# D_options_iv_x_sensitivity (options_iv) via one of: POLYGON_API_KEY;INTRINIO_API_KEY;IEX_CLOUD_API_KEY
$env:POLYGON_API_KEY = "<YOUR_KEY>"
python "D:/Stock_Prediction_app_data/external_adapters/options_iv_adapter.py"   # add --dry-run to preview the plan only

# E_short_interest_x_sensitivity (short_interest) via one of: FINNHUB_API_KEY;FMP_API_KEY;QUANDL_API_KEY
$env:FINNHUB_API_KEY = "<YOUR_KEY>"
python "D:/Stock_Prediction_app_data/external_adapters/short_interest_adapter.py"   # add --dry-run to preview the plan only

# A_news_sentiment_x_sensitivity (transcript_tone) via one of: NEWSAPI_KEY;BENZINGA_API_KEY;ALPHAVANTAGE_API_KEY;EODHD_API_KEY;POLYGON_API_KEY
$env:NEWSAPI_KEY = "<YOUR_KEY>"
python "D:/Stock_Prediction_app_data/external_adapters/transcript_tone_adapter.py"   # add --dry-run to preview the plan only
