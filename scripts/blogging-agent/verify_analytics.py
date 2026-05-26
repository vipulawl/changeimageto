#!/usr/bin/env python3
"""Verify GSC + GA4 credentials work before research/write."""
import sys

import config
from research_signals import fetch_research_signals


def main() -> int:
    print(f"GSC site URL: {config.GSC_SITE_URL or '(missing)'}")
    print(f"GA4 property: {config.GA4_PROPERTY_ID or '(missing)'}")
    print(f"Credentials file: {config.GOOGLE_CREDENTIALS_FILE or '(missing)'}")

    signals = fetch_research_signals()
    print(f"\nGSC configured: {signals.gsc_configured}")
    print(f"GA4 configured: {signals.ga4_configured}")
    print(f"GSC queries fetched: {len(signals.gsc_queries)}")
    print(f"GSC opportunities: {len(signals.gsc_opportunities)}")
    print(f"GA4 top pages: {len(signals.ga4_top_pages)}")
    print(f"GA4 blog pages: {len(signals.ga4_top_blog_pages)}")

    if signals.errors:
        print("\nErrors:")
        for e in signals.errors:
            print(f"  - {e}")

    if not signals.gsc_configured or not signals.ga4_configured:
        print("\nERROR: GSC or GA4 not configured.", file=sys.stderr)
        return 1
    if not signals.gsc_queries and not signals.ga4_top_pages:
        print("\nERROR: Analytics APIs returned no data.", file=sys.stderr)
        return 1

    print("\nAnalytics OK.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
