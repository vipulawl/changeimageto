import os
from dotenv import load_dotenv

load_dotenv()

# ── Provider ──────────────────────────────────────────────────────────────────
# Options: "openai" | "groq" | "ollama" | "anthropic"
PROVIDER = os.getenv("PROVIDER", "openai")

# OpenAI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")

# Groq (free tier, OpenAI-compatible)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# Ollama (local, OpenAI-compatible)
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:70b")

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

# Resolved model name based on provider
def _resolve_model():
    if PROVIDER == "openai":
        return OPENAI_MODEL
    if PROVIDER == "groq":
        return GROQ_MODEL
    if PROVIDER == "ollama":
        return OLLAMA_MODEL
    return ANTHROPIC_MODEL

MODEL = os.getenv("MODEL") or _resolve_model()

# ── Blog identity ─────────────────────────────────────────────────────────────
BLOG_NICHE = os.getenv("BLOG_NICHE", "")
TARGET_AUDIENCE = os.getenv("TARGET_AUDIENCE", "general readers")
BLOG_TONE = os.getenv("BLOG_TONE", "informative and engaging")
BLOG_LANGUAGE = os.getenv("BLOG_LANGUAGE", "English")

# ── Google APIs (optional) ────────────────────────────────────────────────────
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "")
GOOGLE_GSC_CREDENTIALS_FILE = os.getenv("GOOGLE_GSC_CREDENTIALS_FILE", "") or GOOGLE_CREDENTIALS_FILE
GOOGLE_GA4_CREDENTIALS_FILE = os.getenv("GOOGLE_GA4_CREDENTIALS_FILE", "") or GOOGLE_CREDENTIALS_FILE
GSC_SITE_URL = os.getenv("GSC_SITE_URL", "")
GA4_PROPERTY_ID = os.getenv("GA4_PROPERTY_ID", "")

# ── Publishing ────────────────────────────────────────────────────────────────
# "auto" → commit + push HTML directly (ChangeImageTo static site)
# "pr"   → create a git branch + GitHub PR (merge = publish)
# "cli"  → blocking terminal review prompt
APPROVAL_MODE = os.getenv("APPROVAL_MODE", "pr")

# Publisher adapter: "markdown" (default) or "changeimageto" (static HTML)
PUBLISHER = os.getenv("PUBLISHER", "markdown")

# Path (relative to REPO_DIR) where articles are written
CONTENT_DIR = os.getenv("CONTENT_DIR", "output")

# Absolute path to your site's git repo root (defaults to current working dir)
REPO_DIR = os.getenv("REPO_DIR", "")

# ── Daemon behaviour ──────────────────────────────────────────────────────────
# How often the daemon wakes up to check (minutes)
POLL_INTERVAL_MINUTES = int(os.getenv("POLL_INTERVAL_MINUTES", "60"))
# Trigger research when queued topics drop below this number
MIN_QUEUE_SIZE = int(os.getenv("MIN_QUEUE_SIZE", "3"))
# Max articles written per calendar day
MAX_ARTICLES_PER_DAY = int(os.getenv("MAX_ARTICLES_PER_DAY", "1"))

# ── Smart systems ────────────────────────────────────────────────────────────
# Jaccard similarity threshold above which a topic is considered a duplicate
DEDUP_THRESHOLD = float(os.getenv("DEDUP_THRESHOLD", "0.80"))
# Days after publication before a post is eligible for performance flagging
PERFORMANCE_CHECK_DAYS = int(os.getenv("PERFORMANCE_CHECK_DAYS", "30"))
# Set to "true" in GitHub Actions to execute corrections without terminal prompts
CORRECTION_AUTO_MODE = os.getenv("CORRECTION_AUTO_MODE", "false")
# Max posts under 30 days old before scheduler slows down
MAX_POSTS_IN_RAMP = int(os.getenv("MAX_POSTS_IN_RAMP", "3"))

# ── Legacy ───────────────────────────────────────────────────────────────────
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "output")
