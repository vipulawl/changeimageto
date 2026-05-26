import re
import config
from storage.db import get_all_post_memory

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


class DedupChecker:
    def __init__(self):
        self.threshold = config.DEDUP_THRESHOLD

    def check(self, title: str, keyword: str) -> tuple[bool, str, dict | None]:
        """
        Returns (is_duplicate, reason, nearest_match).
        is_duplicate is True when similarity >= threshold.
        """
        query_tokens = _tokenize(f"{title} {keyword}")
        posts = get_all_post_memory()

        best_score = 0.0
        best_match = None

        for p in posts:
            post_tokens = _tokenize(
                f"{p.get('title', '')} {p.get('keyword', '')} {p.get('semantic_fingerprint', '')}"
            )
            score = _jaccard(query_tokens, post_tokens)
            if score > best_score:
                best_score = score
                best_match = p

        if best_score >= self.threshold:
            return (
                True,
                f"Similarity {best_score:.2f} >= threshold {self.threshold} vs '{best_match['title']}'",
                best_match,
            )
        if best_score >= self.threshold * 0.75:
            return (
                False,
                f"Near-duplicate (similarity {best_score:.2f}) vs '{best_match['title'] if best_match else 'none'}' — proceed with caution",
                best_match,
            )
        return (False, f"No duplicate found (max similarity {best_score:.2f})", None)

    def score_penalty(self, title: str, keyword: str) -> float:
        """Returns a dedup penalty (0.0–0.3) to subtract from scheduler score."""
        _, _, match = self.check(title, keyword)
        if match:
            query_tokens = _tokenize(f"{title} {keyword}")
            post_tokens = _tokenize(
                f"{match.get('title', '')} {match.get('keyword', '')} {match.get('semantic_fingerprint', '')}"
            )
            score = _jaccard(query_tokens, post_tokens)
            return min(score * 0.4, 0.3)
        return 0.0
