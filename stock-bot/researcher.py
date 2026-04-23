import os
from datetime import datetime, timedelta
from typing import List, Union

import requests


FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"


def _normalize_close_date(close_date: Union[datetime, str]) -> datetime:
    if isinstance(close_date, datetime):
        return close_date

    try:
        return datetime.fromisoformat(close_date)
    except ValueError:
        try:
            return datetime.strptime(close_date, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return datetime.strptime(close_date, "%Y-%m-%d")


def _build_summary(ticker: str, headlines: List[str]) -> str:
    if not headlines:
        return f"No relevant headlines found for {ticker} in the 24h window around close."

    top = headlines[:3]
    numbered = " | ".join(f"{idx + 1}. {title}" for idx, title in enumerate(top))
    return f"Top headlines around close for {ticker}: {numbered}"


def research_failure(ticker: str, close_date: Union[datetime, str]) -> str:
    """
    Fetch and summarize top 3 Finnhub headlines in a 24-hour close window.
    Returns a summary string suitable for storing in the database.
    """
    api_key = os.getenv("FINNHUB_API_KEY")
    if not api_key:
        return "FINNHUB_API_KEY is not configured; unable to research failure context."

    close_dt = _normalize_close_date(close_date)
    from_date = (close_dt - timedelta(hours=24)).strftime("%Y-%m-%d")
    to_date = close_dt.strftime("%Y-%m-%d")

    params = {
        "symbol": ticker.upper(),
        "from": from_date,
        "to": to_date,
        "token": api_key,
    }

    try:
        response = requests.get(FINNHUB_NEWS_URL, params=params, timeout=12)
        response.raise_for_status()
        payload = response.json()

        if not isinstance(payload, list):
            return f"Finnhub response for {ticker} was not in expected format."

        headlines = [item.get("headline", "").strip() for item in payload if item.get("headline")]
        return _build_summary(ticker.upper(), headlines)
    except requests.RequestException as exc:
        return f"Finnhub request failed for {ticker}: {exc}"
    except Exception as exc:
        return f"Unexpected error during failure research for {ticker}: {exc}"
