import config

_client = None


def _get_client():
    global _client
    if _client:
        return _client
    if not config.GOOGLE_GA4_CREDENTIALS_FILE or not config.GA4_PROPERTY_ID:
        return None
    try:
        from google.analytics.data_v1beta import BetaAnalyticsDataClient
        from google.oauth2 import service_account
        credentials = service_account.Credentials.from_service_account_file(
            config.GOOGLE_GA4_CREDENTIALS_FILE,
            scopes=["https://www.googleapis.com/auth/analytics.readonly"]
        )
        _client = BetaAnalyticsDataClient(credentials=credentials)
        return _client
    except Exception:
        return None


def get_top_pages(days: int = 28, limit: int = 20) -> list[dict]:
    """Top pages by sessions — understand what content is working."""
    client = _get_client()
    if not client:
        return []

    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, Dimension, Metric, DateRange, OrderBy
        )
        request = RunReportRequest(
            property=f"properties/{config.GA4_PROPERTY_ID}",
            dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
            metrics=[
                Metric(name="sessions"),
                Metric(name="bounceRate"),
                Metric(name="averageSessionDuration"),
            ],
            date_ranges=[DateRange(start_date=f"{days}daysAgo", end_date="today")],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name="sessions"), desc=True)],
            limit=limit,
        )
        response = client.run_report(request)
        return [
            {
                "page_path": row.dimension_values[0].value,
                "page_title": row.dimension_values[1].value,
                "sessions": int(row.metric_values[0].value),
                "bounce_rate_pct": round(float(row.metric_values[1].value) * 100, 1),
                "avg_duration_sec": round(float(row.metric_values[2].value)),
            }
            for row in response.rows
        ]
    except Exception as e:
        return [{"error": str(e)}]


def get_declining_pages(days: int = 28) -> list[dict]:
    """Pages with falling traffic — candidates for refresh articles."""
    client = _get_client()
    if not client:
        return []

    try:
        from google.analytics.data_v1beta.types import (
            RunReportRequest, Dimension, Metric, DateRange, OrderBy
        )

        def fetch(start, end):
            req = RunReportRequest(
                property=f"properties/{config.GA4_PROPERTY_ID}",
                dimensions=[Dimension(name="pagePath"), Dimension(name="pageTitle")],
                metrics=[Metric(name="sessions")],
                date_ranges=[DateRange(start_date=start, end_date=end)],
                limit=50,
            )
            resp = client.run_report(req)
            return {
                row.dimension_values[0].value: {
                    "sessions": int(row.metric_values[0].value),
                    "title": row.dimension_values[1].value,
                }
                for row in resp.rows
            }

        half = days // 2
        recent = fetch(f"{half}daysAgo", "today")
        previous = fetch(f"{days}daysAgo", f"{half + 1}daysAgo")

        declining = []
        for path, data in recent.items():
            prev = previous.get(path, {})
            prev_sessions = prev.get("sessions", 0)
            if prev_sessions > 50 and data["sessions"] < prev_sessions * 0.8:
                decline_pct = round((prev_sessions - data["sessions"]) / prev_sessions * 100, 1)
                declining.append({
                    "page_path": path,
                    "page_title": data["title"],
                    "recent_sessions": data["sessions"],
                    "prev_sessions": prev_sessions,
                    "decline_pct": decline_pct,
                })

        return sorted(declining, key=lambda x: x["decline_pct"], reverse=True)[:10]
    except Exception as e:
        return [{"error": str(e)}]
