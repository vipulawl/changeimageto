from datetime import datetime, timedelta
import config

_client = None


def _get_client():
    global _client
    if _client:
        return _client
    if not config.GOOGLE_GSC_CREDENTIALS_FILE or not config.GSC_SITE_URL:
        return None
    try:
        from googleapiclient.discovery import build
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_file(
            config.GOOGLE_GSC_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
        )
        _client = build("searchconsole", "v1", credentials=credentials)
        return _client
    except Exception:
        return None


def get_top_queries(days: int = 28) -> list[dict]:
    """Returns queries sorted by impressions. Low CTR + high impressions = opportunity."""
    client = _get_client()
    if not client:
        return []

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        response = client.searchanalytics().query(
            siteUrl=config.GSC_SITE_URL,
            body={
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["query"],
                "rowLimit": 50,
                "orderBy": [{"fieldName": "impressions", "sortOrder": "DESCENDING"}]
            }
        ).execute()

        return [
            {
                "query": row["keys"][0],
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr_pct": round(row.get("ctr", 0) * 100, 2),
                "position": round(row.get("position", 0), 1)
            }
            for row in response.get("rows", [])
        ]
    except Exception as e:
        return [{"error": str(e)}]


def get_page_performance(days: int = 90) -> list[dict]:
    """Returns per-page GSC metrics: clicks, impressions, position, ctr."""
    client = _get_client()
    if not client:
        return []

    end_date = datetime.now().strftime("%Y-%m-%d")
    start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    try:
        response = client.searchanalytics().query(
            siteUrl=config.GSC_SITE_URL,
            body={
                "startDate": start_date,
                "endDate": end_date,
                "dimensions": ["page"],
                "rowLimit": 500,
            }
        ).execute()

        return [
            {
                "page": row["keys"][0],
                "clicks": int(row.get("clicks", 0)),
                "impressions": int(row.get("impressions", 0)),
                "ctr": round(row.get("ctr", 0) * 100, 2),
                "position": round(row.get("position", 0), 1),
            }
            for row in response.get("rows", [])
        ]
    except Exception as e:
        return [{"error": str(e)}]


def get_rising_queries(days: int = 14) -> list[dict]:
    """Compare recent 7 days vs previous 7 days to find rising queries."""
    client = _get_client()
    if not client:
        return []

    now = datetime.now()
    recent_end = now.strftime("%Y-%m-%d")
    recent_start = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    prev_end = (now - timedelta(days=8)).strftime("%Y-%m-%d")
    prev_start = (now - timedelta(days=days)).strftime("%Y-%m-%d")

    def fetch(start, end):
        try:
            r = client.searchanalytics().query(
                siteUrl=config.GSC_SITE_URL,
                body={"startDate": start, "endDate": end, "dimensions": ["query"], "rowLimit": 100}
            ).execute()
            return {row["keys"][0]: row.get("impressions", 0) for row in r.get("rows", [])}
        except Exception:
            return {}

    recent = fetch(recent_start, recent_end)
    previous = fetch(prev_start, prev_end)

    rising = []
    for query, impressions in recent.items():
        prev_impressions = previous.get(query, 0)
        if prev_impressions > 0:
            growth = (impressions - prev_impressions) / prev_impressions
            if growth > 0.2:
                rising.append({"query": query, "recent_impressions": impressions, "growth_pct": round(growth * 100, 1)})

    return sorted(rising, key=lambda x: x["growth_pct"], reverse=True)[:20]
