import re
from storage.db import save_post_memory, get_all_post_memory

_STOPWORDS = {"the", "and", "for", "are", "was", "that", "with", "this", "from",
              "you", "your", "how", "what", "why", "when", "which", "will",
              "can", "not", "but", "all", "has", "have", "been", "more", "its"}


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if len(w) >= 3 and w not in _STOPWORDS}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class PostIndex:
    def add_post(self, slug: str, title: str, keyword: str, tags: list,
                 content: str, published_at: str = None) -> None:
        summary = content[:500]
        word_count = len(content.split())
        tokens = _tokenize(f"{title} {keyword} {' '.join(tags)} {content[:2000]}")
        fingerprint = " ".join(sorted(tokens)[:50])
        save_post_memory(
            slug=slug,
            title=title,
            keyword=keyword,
            tags=tags,
            summary=summary,
            semantic_fingerprint=fingerprint,
            published_at=published_at,
            word_count=word_count,
        )

    def find_similar(self, keyword: str = None, title: str = None, top_n: int = 5) -> list[dict]:
        query_tokens = _tokenize(f"{keyword or ''} {title or ''}")
        posts = get_all_post_memory()
        scored = []
        for p in posts:
            post_tokens = _tokenize(p.get("semantic_fingerprint", ""))
            score = _jaccard(query_tokens, post_tokens)
            if score > 0:
                scored.append({**p, "similarity": round(score, 3)})
        return sorted(scored, key=lambda x: x["similarity"], reverse=True)[:top_n]

    def get_link_candidates(self, topic_keywords: list[str], top_n: int = 5) -> list[dict]:
        query_tokens = _tokenize(" ".join(topic_keywords))
        posts = get_all_post_memory()
        scored = []
        for p in posts:
            post_tokens = _tokenize(
                f"{p.get('title', '')} {p.get('keyword', '')} {p.get('semantic_fingerprint', '')}"
            )
            score = _jaccard(query_tokens, post_tokens)
            if score > 0.05:
                scored.append({
                    "slug": p["slug"],
                    "title": p["title"],
                    "keyword": p["keyword"],
                    "summary": p.get("summary", "")[:200],
                    "relevance": round(score, 3),
                })
        return sorted(scored, key=lambda x: x["relevance"], reverse=True)[:top_n]
