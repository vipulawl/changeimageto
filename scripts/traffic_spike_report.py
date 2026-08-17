#!/usr/bin/env python3
"""Dump GA4 + GSC traffic for spike analysis. Uses blogging-agent / seo-agent creds."""
from __future__ import annotations

import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _ga4_client():
    from google.analytics.data_v1beta import BetaAnalyticsDataClient
    from google.oauth2 import service_account

    path = (
        os.environ.get("GOOGLE_GA4_CREDENTIALS_FILE")
        or os.environ.get("GA4_CREDENTIALS_FILE")
        or str(ROOT / "gemini-test-487909-7e2ee8971cff.json")
    )
    # blogging-agent CI writes google-credentials-ga4.json
    candidates = [
        path,
        str(ROOT / "scripts/blogging-agent/google-credentials-ga4.json"),
        str(ROOT / "gemini-test-487909-7e2ee8971cff.json"),
    ]
    cred_path = next((p for p in candidates if Path(p).exists()), None)
    if not cred_path:
        raise SystemExit(f"No GA4 credentials found. Tried: {candidates}")
    creds = service_account.Credentials.from_service_account_file(
        cred_path, scopes=["https://www.googleapis.com/auth/analytics.readonly"]
    )
    return BetaAnalyticsDataClient(credentials=creds)


def _gsc_client():
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    candidates = [
        os.environ.get("GOOGLE_GSC_CREDENTIALS_FILE"),
        str(ROOT / "scripts/blogging-agent/google-credentials-gsc.json"),
        str(ROOT / "gemini-test-487909-c351b5b6a299.json"),
    ]
    cred_path = next((p for p in candidates if p and Path(p).exists()), None)
    if not cred_path:
        raise SystemExit("No GSC credentials found")
    creds = service_account.Credentials.from_service_account_file(
        cred_path, scopes=["https://www.googleapis.com/auth/webmasters.readonly"]
    )
    return build("searchconsole", "v1", credentials=creds)


def main() -> int:
    from google.analytics.data_v1beta.types import (
        DateRange,
        Dimension,
        Metric,
        OrderBy,
        RunReportRequest,
    )

    prop_id = os.environ.get("GA4_PROPERTY_ID", "505035310")
    site = os.environ.get("GSC_SITE_URL", "sc-domain:changeimageto.com")
    prop = f"properties/{prop_id}"
    today = date.today()
    ga = _ga4_client()
    out: dict = {"generated_at": today.isoformat(), "property": prop_id, "site": site}

    def report(**kwargs):
        return ga.run_report(RunReportRequest(property=prop, **kwargs))

    # Daily trend
    resp = report(
        dimensions=[Dimension(name="date")],
        metrics=[
            Metric(name="sessions"),
            Metric(name="totalUsers"),
            Metric(name="screenPageViews"),
            Metric(name="bounceRate"),
            Metric(name="engagedSessions"),
        ],
        date_ranges=[DateRange(start_date="21daysAgo", end_date="today")],
        order_bys=[OrderBy(dimension=OrderBy.DimensionOrderBy(dimension_name="date"))],
    )
    daily = []
    for row in resp.rows:
        d = row.dimension_values[0].value
        s, u, pv, br, es = [m.value for m in row.metric_values]
        daily.append(
            {
                "date": f"{d[:4]}-{d[4:6]}-{d[6:]}",
                "sessions": int(s),
                "users": int(u),
                "pageviews": int(pv),
                "bounce_pct": round(float(br) * 100, 1),
                "engaged_sessions": int(es),
            }
        )
    out["ga4_daily"] = daily

    def day_slice(start: str, end: str, dims, metrics, limit=40):
        r = report(
            dimensions=[Dimension(name=d) for d in dims],
            metrics=[Metric(name=m) for m in metrics],
            date_ranges=[DateRange(start_date=start, end_date=end)],
            order_bys=[OrderBy(metric=OrderBy.MetricOrderBy(metric_name=metrics[0]), desc=True)],
            limit=limit,
        )
        rows = []
        for row in r.rows:
            item = {dims[i]: row.dimension_values[i].value for i in range(len(dims))}
            for i, m in enumerate(metrics):
                val = row.metric_values[i].value
                if m.lower().endswith("rate"):
                    item[m] = round(float(val) * 100, 1)
                elif "." in val:
                    item[m] = round(float(val), 1)
                else:
                    item[m] = int(val)
            rows.append(item)
        return rows

    out["today_pages"] = day_slice(
        "today",
        "today",
        ["pagePath", "pageTitle"],
        ["sessions", "screenPageViews", "bounceRate", "averageSessionDuration"],
    )
    out["yesterday_pages"] = {
        r["pagePath"]: r["sessions"]
        for r in day_slice("yesterday", "yesterday", ["pagePath"], ["sessions"], limit=50)
    }
    out["today_sources"] = day_slice(
        "today",
        "today",
        ["sessionDefaultChannelGroup", "sessionSourceMedium"],
        ["sessions", "totalUsers", "bounceRate"],
    )
    out["today_countries"] = day_slice("today", "today", ["country"], ["sessions"], limit=20)
    out["today_devices"] = day_slice("today", "today", ["deviceCategory"], ["sessions"])
    out["today_landing_source"] = day_slice(
        "today",
        "today",
        ["landingPage", "sessionSourceMedium"],
        ["sessions"],
        limit=40,
    )
    try:
        out["today_hours"] = day_slice("today", "today", ["hour"], ["sessions"], limit=24)
        out["today_hours"] = sorted(out["today_hours"], key=lambda x: int(x["hour"]))
    except Exception as e:
        out["today_hours_error"] = str(e)

    # Compare page deltas vs yesterday
    deltas = []
    for p in out["today_pages"]:
        y = out["yesterday_pages"].get(p["pagePath"], 0)
        deltas.append(
            {
                "path": p["pagePath"],
                "today": p["sessions"],
                "yesterday": y,
                "delta": p["sessions"] - y,
                "bounce_pct": p.get("bounceRate"),
            }
        )
    out["page_deltas"] = sorted(deltas, key=lambda x: x["delta"], reverse=True)

    # GSC (often delayed)
    try:
        gsc = _gsc_client()
        gsc_daily = []
        r = (
            gsc.searchanalytics()
            .query(
                siteUrl=site,
                body={
                    "startDate": (today - timedelta(days=14)).isoformat(),
                    "endDate": today.isoformat(),
                    "dimensions": ["date"],
                },
            )
            .execute()
        )
        for row in r.get("rows", []):
            gsc_daily.append(
                {
                    "date": row["keys"][0],
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr": round(row.get("ctr", 0) * 100, 2),
                    "position": round(row.get("position", 0), 1),
                }
            )
        out["gsc_daily"] = sorted(gsc_daily, key=lambda x: x["date"])

        for label, start, end in [
            ("latest_available", (today - timedelta(days=2)).isoformat(), (today - timedelta(days=2)).isoformat()),
            ("yesterday", (today - timedelta(days=1)).isoformat(), (today - timedelta(days=1)).isoformat()),
        ]:
            rq = (
                gsc.searchanalytics()
                .query(
                    siteUrl=site,
                    body={
                        "startDate": start,
                        "endDate": end,
                        "dimensions": ["query"],
                        "rowLimit": 25,
                        "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
                    },
                )
                .execute()
            )
            out[f"gsc_queries_{label}"] = [
                {
                    "query": row["keys"][0],
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr": round(row.get("ctr", 0) * 100, 2),
                    "position": round(row.get("position", 0), 1),
                }
                for row in rq.get("rows", [])
            ]
            rp = (
                gsc.searchanalytics()
                .query(
                    siteUrl=site,
                    body={
                        "startDate": start,
                        "endDate": end,
                        "dimensions": ["page"],
                        "rowLimit": 25,
                        "orderBy": [{"fieldName": "clicks", "sortOrder": "DESCENDING"}],
                    },
                )
                .execute()
            )
            out[f"gsc_pages_{label}"] = [
                {
                    "page": row["keys"][0],
                    "clicks": int(row.get("clicks", 0)),
                    "impressions": int(row.get("impressions", 0)),
                    "ctr": round(row.get("ctr", 0) * 100, 2),
                    "position": round(row.get("position", 0), 1),
                }
                for row in rp.get("rows", [])
            ]
    except Exception as e:
        out["gsc_error"] = str(e)

    out_path = Path(os.environ.get("TRAFFIC_REPORT_PATH", "/tmp/traffic_spike_report.json"))
    out_path.write_text(json.dumps(out, indent=2))
    print(f"Wrote {out_path}")

    # Human summary to stdout
    print("\n=== GA4 DAILY ===")
    for r in daily:
        print(
            f"{r['date']}\tsessions={r['sessions']:5d}\tusers={r['users']:5d}\tpv={r['pageviews']:5d}\tbounce={r['bounce_pct']:5.1f}%"
        )
    print("\n=== TODAY TOP PAGES ===")
    for p in out["today_pages"][:20]:
        print(f"{p['sessions']:4d}\t{p.get('bounceRate',0):5.1f}%\t{p['pagePath']}")
    print("\n=== TODAY SOURCES ===")
    for s in out["today_sources"][:15]:
        print(
            f"{s['sessions']:4d}\t{s.get('bounceRate',0):5.1f}%\t{s['sessionDefaultChannelGroup']}\t{s['sessionSourceMedium']}"
        )
    print("\n=== BIGGEST PAGE DELTAS vs YESTERDAY ===")
    for d in out["page_deltas"][:15]:
        print(f"{d['delta']:+5d}\ttoday={d['today']:4d}\tyest={d['yesterday']:4d}\t{d['path']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
