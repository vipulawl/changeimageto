"""
Always-on GSC + GA4 fetch for research. Code-driven (not LLM-dependent).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import config
from tools.ga4 import get_top_pages, get_declining_pages


@dataclass
class ResearchSignals:
    gsc_configured: bool = False
    ga4_configured: bool = False
    gsc_queries: list[dict] = field(default_factory=list)
    gsc_rising: list[dict] = field(default_factory=list)
    gsc_opportunities: list[dict] = field(default_factory=list)
    ga4_top_pages: list[dict] = field(default_factory=list)
    ga4_declining: list[dict] = field(default_factory=list)
    ga4_top_blog_pages: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary_for_prompt(self) -> str:
        lines = [
            "=== PRE-FETCHED GSC + GA4 DATA (already loaded — use these signals) ===",
            f"GSC site: {config.GSC_SITE_URL or '(not set)'}",
            f"GA4 property: {config.GA4_PROPERTY_ID or '(not set)'}",
        ]
        if self.errors:
            lines.append("Errors: " + "; ".join(self.errors))
        if self.gsc_opportunities:
            lines.append("\nTop GSC opportunities (high impressions, weak position/CTR):")
            for q in self.gsc_opportunities[:12]:
                lines.append(
                    f"  - \"{q['query']}\" | imp={q['impressions']} clicks={q['clicks']} "
                    f"pos={q['position']} ctr={q['ctr_pct']}%"
                )
        if self.gsc_rising:
            lines.append("\nRising GSC queries:")
            for q in self.gsc_rising[:8]:
                lines.append(f"  - \"{q['query']}\" +{q.get('growth_pct', 0)}% impressions")
        if self.ga4_top_blog_pages:
            lines.append("\nTop GA4 blog pages (last 28 days):")
            for p in self.ga4_top_blog_pages[:10]:
                lines.append(
                    f"  - {p['page_path']} | sessions={p['sessions']} "
                    f"bounce={p.get('bounce_rate_pct', '?')}%"
                )
        if self.ga4_declining:
            lines.append("\nGA4 declining pages (refresh candidates):")
            for p in self.ga4_declining[:6]:
                lines.append(
                    f"  - {p['page_path']} | -{p.get('decline_pct', 0)}% sessions "
                    f"({p.get('prev_sessions', 0)} → {p.get('recent_sessions', 0)})"
                )
        if not self.gsc_opportunities and not self.ga4_top_blog_pages:
            lines.append("\n(No analytics rows returned — check credentials / property access.)")
        lines.append(
            "\nREQUIRED: Call save_topic for at least 3 topics. "
            "Prioritize GSC opportunities above, then strategy quick wins / content gaps."
        )
        return "\n".join(lines)


def _is_blog_path(path: str) -> bool:
    return "/blog/" in (path or "")


def _score_gsc_opportunity(row: dict) -> float:
    if row.get("error"):
        return -1
    impressions = row.get("impressions", 0)
    position = row.get("position", 100)
    ctr = row.get("ctr_pct", 0)
    if impressions < 20:
        return 0
    score = min(impressions / 200, 1.0) * 0.4
    if 5 <= position <= 20:
        score += 0.35
    elif 20 < position <= 40:
        score += 0.2
    if ctr < 3 and impressions >= 50:
        score += 0.25
    return score


def fetch_research_signals() -> ResearchSignals:
    signals = ResearchSignals()
    signals.gsc_configured = bool(config.GOOGLE_CREDENTIALS_FILE and config.GSC_SITE_URL)
    signals.ga4_configured = bool(config.GOOGLE_CREDENTIALS_FILE and config.GA4_PROPERTY_ID)

    if signals.gsc_configured:
        try:
            signals.gsc_queries = get_top_queries(days=28) or []
            if signals.gsc_queries and signals.gsc_queries[0].get("error"):
                signals.errors.append(f"GSC queries: {signals.gsc_queries[0]['error']}")
                signals.gsc_queries = []
            signals.gsc_rising = get_rising_queries(days=14) or []
            if signals.gsc_rising and signals.gsc_rising[0].get("error"):
                signals.gsc_rising = []
            scored = sorted(
                [r for r in signals.gsc_queries if not r.get("error")],
                key=_score_gsc_opportunity,
                reverse=True,
            )
            signals.gsc_opportunities = [r for r in scored if _score_gsc_opportunity(r) > 0.15][:20]
        except Exception as e:
            signals.errors.append(f"GSC fetch failed: {e}")

    if signals.ga4_configured:
        try:
            signals.ga4_top_pages = get_top_pages(days=28, limit=30) or []
            if signals.ga4_top_pages and signals.ga4_top_pages[0].get("error"):
                signals.errors.append(f"GA4 top pages: {signals.ga4_top_pages[0]['error']}")
                signals.ga4_top_pages = []
            signals.ga4_declining = get_declining_pages(days=28) or []
            if signals.ga4_declining and signals.ga4_declining[0].get("error"):
                signals.ga4_declining = []
            signals.ga4_top_blog_pages = [
                p for p in signals.ga4_top_pages if _is_blog_path(p.get("page_path", ""))
            ]
        except Exception as e:
            signals.errors.append(f"GA4 fetch failed: {e}")

    return signals


def _title_from_keyword(keyword: str) -> str:
    k = keyword.strip()
    if not k:
        return "Image Editing Guide"
    if len(k) <= 60 and k[0].isupper():
        return k[:60]
    return k.title()[:60]


def _brief_from_signal(keyword: str, source: str, extra: str = "") -> str:
    return (
        f"Primary keyword: {keyword}\n"
        f"Source: {source}\n"
        f"{extra}\n"
        "Angle: practical how-to for ChangeImageTo.com readers (ecommerce sellers, designers, creators).\n"
        "Sections: intro problem, step-by-step, tool recommendations (link ChangeImageTo tools), FAQ.\n"
        "Word count: 1200-1800."
    )


def seed_topics_from_signals(signals: ResearchSignals, strategy: dict | None, min_topics: int = 3) -> list[int]:
    """Deterministic fallback — save topics from GSC/GA4/strategy when the LLM saved none."""
    from dedup import DedupChecker
    from storage.db import get_all_topics, save_topic

    existing = get_all_topics(status="queued")
    if len(existing) >= min_topics:
        return []

    saved_ids: list[int] = []
    dedup = DedupChecker()
    seen_keywords: set[str] = set()

    def try_save(title: str, keyword: str, brief: str, source: str, priority: float) -> None:
        if len(saved_ids) + len(existing) >= min_topics:
            return
        kw = keyword.lower().strip()
        if not kw or kw in seen_keywords:
            return
        is_dup, _, _ = dedup.check(title, keyword)
        if is_dup:
            return
        topic_id = save_topic(title, keyword, brief, source=source, priority_score=priority)
        saved_ids.append(topic_id)
        seen_keywords.add(kw)

    for i, row in enumerate(signals.gsc_opportunities):
        kw = row.get("query", "").strip()
        if not kw:
            continue
        extra = (
            f"GSC signal: {row.get('impressions', 0)} impressions, "
            f"position {row.get('position', '?')}, CTR {row.get('ctr_pct', '?')}%."
        )
        try_save(
            _title_from_keyword(kw),
            kw,
            _brief_from_signal(kw, "gsc_opportunity", extra),
            "gsc_opportunity",
            0.85 - i * 0.03,
        )

    for i, row in enumerate(signals.gsc_rising):
        kw = row.get("query", "").strip()
        if not kw:
            continue
        try_save(
            _title_from_keyword(kw),
            kw,
            _brief_from_signal(kw, "gsc_rising", f"Rising query: +{row.get('growth_pct', 0)}% impressions."),
            "gsc_rising",
            0.75 - i * 0.02,
        )

    if strategy:
        for kw in strategy.get("quick_wins", [])[:5]:
            kw = str(kw).strip()
            if kw:
                try_save(
                    _title_from_keyword(kw),
                    kw,
                    _brief_from_signal(kw, "strategy_quick_win"),
                    "strategy_quick_win",
                    0.7,
                )
        for gap in strategy.get("content_gaps", [])[:3]:
            gap = str(gap).strip()
            if gap:
                try_save(
                    _title_from_keyword(gap),
                    gap,
                    _brief_from_signal(gap, "strategy_content_gap"),
                    "strategy_content_gap",
                    0.65,
                )

    # Image-editing fallbacks if analytics returned nothing
    fallbacks = [
        ("Shopify Image Size Guide for Faster Storefronts", "shopify image size guide"),
        ("Best Free Background Removal Tools Online", "best background removal tools"),
        ("How to Blur Image Background for Product Photos", "blur image background product photos"),
    ]
    for title, kw in fallbacks:
        try_save(title, kw, _brief_from_signal(kw, "fallback"), "fallback", 0.5)

    return saved_ids
