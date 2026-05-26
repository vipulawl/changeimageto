# Blogging Agent

A fully autonomous AI-powered blogging pipeline. It researches topics, writes and edits posts, monitors performance, and corrects underperforming content — all running on GitHub Actions with zero local setup required after the initial config.

## What it does

```
Strategy → Research → Dedup Check → Write → Edit → PR (merge = publish)
                ↑                                        ↓
           Scheduler ←──── Performance Monitor ←── Post Memory Index
                                    ↓
                           Self-Correction Agent
```

The pipeline has 8 components that work together:

| Component | What it does | When it runs |
|---|---|---|
| **Strategy Agent** | Interviews you about your blog, scrapes competitors, builds a content strategy | Once (or when you update strategy) |
| **Research Agent** | Finds 3–5 topics from GSC, GA4, competitors, keyword discovery | On demand / scheduled |
| **Writer + Editor** | Writes a full SEO article then edits it for quality | Per topic |
| **Smart Scheduler** | Reads signals (queue size, ramp count, GSC opportunity) and decides `publish / wait / research` | Every 6 hours via Actions |
| **Memory Layer** | Indexes every published post for internal link suggestions and dedup checks | On every publish |
| **Deduplication** | Blocks duplicate topics from entering the queue; warns before writing near-duplicates | On research + write |
| **Performance Monitor** | Snapshots GSC + GA4 data for every post; flags underperformers | Daily via Actions |
| **Self-Correction** | Reviews flagged posts and decides: rewrite / retitle / requeue / wait | Weekly via Actions |

---

## Quick start

### 1. Clone and install

```bash
git clone https://github.com/vipulawl/blogging-agent
cd blogging-agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

The minimum viable config (free tier, no Google APIs):

```env
PROVIDER=groq
GROQ_API_KEY=your_key_here        # free at console.groq.com
BLOG_NICHE=SaaS marketing
TARGET_AUDIENCE=early-stage startup founders
BLOG_TONE=informative and practical
APPROVAL_MODE=pr
CONTENT_DIR=posts/                # match your site framework
```

### 3. Build your strategy (run once)

```bash
python main.py strategy
```

This runs a 6-question terminal interview about your blog, then the Strategy Agent researches competitors, maps content gaps, and stores a strategy that guides every future research run.

### 4. Run the pipeline

```bash
python main.py research     # find 3–5 topics
python main.py write        # write + edit the top topic
python main.py review       # approve/reject in terminal (if APPROVAL_MODE=cli)
```

With `APPROVAL_MODE=pr` (recommended): after `write`, a GitHub PR is opened. Merging it publishes the post to your site. Closing rejects it.

---

## Fully autonomous mode (GitHub Actions)

After setup, the entire pipeline runs itself. No local process needed.

### How to set up

1. Fork or push this repo to GitHub
2. Add your secrets and variables in **Settings → Secrets and variables → Actions**:

**Secrets** (sensitive):
| Secret | Description |
|---|---|
| `GROQ_API_KEY` | Your Groq API key |

**Variables** (non-sensitive):
| Variable | Example |
|---|---|
| `PROVIDER` | `groq` |
| `BLOG_NICHE` | `SaaS marketing` |
| `TARGET_AUDIENCE` | `early-stage startup founders` |
| `BLOG_TONE` | `informative and practical` |
| `GSC_SITE_URL` | `https://yoursite.com/` |
| `GA4_PROPERTY_ID` | `123456789` |
| `CONTENT_DIR` | `posts/` |
| `GOOGLE_CREDENTIALS_JSON` | Your service account JSON (as a single-line string) |

3. The following workflows activate automatically:

| Workflow | Schedule | What it does |
|---|---|---|
| `blog-pipeline.yml` | Mon–Fri 9am UTC + manual | research → write → PR |
| `schedule.yml` | Every 6 hours | Evaluates signals, triggers write or research |
| `monitor.yml` | Daily 8am UTC | Snapshots GSC + GA4, flags underperformers |
| `correct.yml` | Monday 10am UTC | Reviews flagged posts, applies corrections |
| `blog-strategy.yml` | Manual only | Builds/updates your content strategy |

The SQLite database is committed back to the repo after each run (`[skip ci]` commit) so state persists across Actions runs.

---

## All CLI commands

```bash
# Strategy
python main.py strategy               # interactive setup (first-time)
python main.py strategy --force       # replace existing strategy
python main.py strategy --auto        # CI mode (reads STRATEGY_* env vars)
python main.py show-strategy          # view current strategy

# Content pipeline
python main.py research               # find new topics
python main.py write                  # write top queued topic
python main.py write --topic-id 3    # write specific topic
python main.py review                 # approve/reject drafts in terminal
python main.py pipeline               # research + write + review in one command

# Smart systems
python main.py schedule               # evaluate and decide next action
python main.py schedule --dry-run    # print decision without saving
python main.py schedule --json       # machine-readable output (for CI)
python main.py monitor               # snapshot performance for all posts
python main.py correct               # review flagged posts and apply corrections
python main.py dedup --keyword "X"  # check if a keyword is a duplicate

# Maintenance
python main.py refresh               # refresh stale published articles
python main.py refresh --max 3      # refresh up to 3 articles
python main.py list-topics          # list all topics and status
python main.py list-drafts          # list drafts pending review
```

---

## How each system works

### Strategy Agent

Runs a 6-question interview in the terminal (or reads `STRATEGY_*` env vars in CI mode). Then it:
- Searches DuckDuckGo to find competitors in your niche
- Scrapes competitor sitemaps for recent posts
- Maps content gaps and quick-win keywords
- Stores content pillars that guide every future research run

All subsequent research runs are guided by this strategy.

### Research Agent

Each research run:
1. Pulls GSC queries with high impressions but weak CTR/position (opportunities you're missing)
2. Checks GA4 for declining pages (refresh candidates)
3. Scrapes each competitor's sitemap for new posts in the last 14 days
4. Runs keyword discovery on pillar topics to find intent-based variations not in GSC
5. Checks Google Trends for rising queries
6. Validates competition via DuckDuckGo SERP before saving a topic
7. **Dedup check**: before saving, checks post memory index — skips topics that are too similar to existing posts

Output: 3–5 topics saved to the queue with full research briefs for the writer.

### Writer + Editor Agents

The Writer Agent:
- Receives the topic + research brief
- **Receives internal link candidates** from the Post Memory index (posts already on your blog that are topically relevant)
- Does 1–2 web searches for fresh data or examples
- Writes the full article in clean markdown (no frontmatter)

The Editor Agent:
- Reads the draft and the original research brief
- Checks SEO (keyword placement, title, meta), structure, clarity, and engagement
- Edits the draft in-place, adds editor notes explaining what changed and why

### Smart Scheduler

Runs every 6 hours on GitHub Actions. Evaluates these signals in order:

1. **Already published today?** → `wait` (max 1 post/day)
2. **Queue < 2 topics?** → `research` (fill the queue first)
3. **Too many posts in ramp?** (posts < 30 days old, default limit: 3) → `wait` (let them gain traction)
4. Otherwise → `publish`, picking the highest-scored queued topic

Topic scoring formula:
```
score = (days_waiting / 7) × 0.3
      + gsc_opportunity_score × 0.3
      + priority_score × 0.4
      - dedup_penalty
```

The `--json` flag outputs the decision as JSON so GitHub Actions can parse it and conditionally trigger the write or research workflow.

Decisions are stored in the `scheduler_decisions` table for audit purposes.

### Memory Layer (Post Index)

Every published post is indexed in the `post_memory` table with:
- Slug, title, keyword, tags
- First 500 characters as a summary
- A semantic fingerprint (top 50 tokens from title + keyword + tags + content)

**Internal link candidates**: before writing, the orchestrator queries the index with the new topic's keywords and injects the most relevant existing posts into the writer's system prompt as link suggestions.

**Dedup checks**: uses Jaccard similarity (`|A∩B| / |A∪B|`) on the tokenized fingerprint. No vector database needed.

### Deduplication

`DedupChecker` checks any candidate topic against the full post memory index.

Returns: `(is_duplicate: bool, reason: str, nearest_match: dict)`

Wired in 3 places:
- **Research Agent**: skips saving a topic if similarity ≥ `DEDUP_THRESHOLD` (default: 0.80)
- **Orchestrator write step**: warns if near-duplicate found; prompts to skip, or proceed
- **Scheduler**: applies a score penalty to near-duplicate topics so they rank lower

Manual check:
```bash
python main.py dedup --keyword "email marketing automation"
```

### Performance Monitor

Runs daily. For each post in the memory index:
1. Queries GSC for clicks, impressions, average position, CTR (last 90 days)
2. Queries GA4 for sessions (last 90 days)
3. Computes a **health score** (0–100):
   - Base: 50
   - Position ≤ 5: +30 | ≤ 10: +20 | ≤ 20: +5 | > 20: −20
   - CTR ≥ 5%: +15
   - Sessions ≥ 100: +15
   - Impressions < 10 after 30+ days: −15
4. Flags underperformers:
   - `low_ranking`: age ≥ 30 days, position > 20, impressions > 50 (visible but not ranking)
   - `low_traffic`: age ≥ 45 days, clicks < 5

Snapshots stored in `performance_snapshots`. Flagged posts feed the Self-Correction agent.

### Self-Correction Agent

Runs weekly (Mondays). For each flagged post:

The agent is given the post's GSC/GA4 data, health score, flag reason, and a summary of the blog's top-performing posts. It decides one of:

| Action | When | What happens |
|---|---|---|
| `rewrite` | Poor position + high impressions (content depth issue) | Hands off to `RefreshAgent` |
| `retitle` | Good position + low CTR (title/meta issue) | Proposes new title + meta description, opens a PR |
| `requeue` | Wrong keyword or intent mismatch | Archives topic, saves new keyword angle to queue |
| `wait` | Too young or borderline data | Sets a `check_after` date, no action |

In **CI mode** (`CORRECTION_AUTO_MODE=true`): executes immediately.  
Locally: prints the decision and asks for confirmation before executing.

All decisions logged in the `correction_log` table.

---

## Approval workflow

The recommended workflow uses GitHub PRs:

```
write → PR opened on branch blog/YYYY-MM-DD-slug
              ↓
       Review in GitHub or Cursor
              ↓
      Merge = publish (Vercel/Netlify auto-deploys)
      Close = reject
```

Set `APPROVAL_MODE=pr` in your `.env`. The PR includes the article preview, keyword, tags, meta description, and editor notes.

For CLI review mode (`APPROVAL_MODE=cli`), run `python main.py review` after writing.

---

## Output format

Articles are saved as `CONTENT_DIR/YYYY-MM-DD-slug.md`:

```yaml
---
title: "Your Article Title"
date: "2026-05-22"
slug: "your-article-slug"
description: "SEO meta description, 150–160 chars"
keyword: "primary keyword"
tags: ["tag1", "tag2", "tag3"]
status: published
---

Article content in markdown...
```

Works with: **Next.js**, **Astro**, **Hugo**, **Jekyll**, **Gatsby**, **Nuxt Content**, and any markdown-based CMS. Set `CONTENT_DIR` to match your framework's content path.

---

## AI model options

| Provider | Model | Cost | Setup |
|---|---|---|---|
| **Groq** (default) | `llama-3.3-70b-versatile` | Free tier | `GROQ_API_KEY` from console.groq.com |
| **Ollama** | Any local model | Free (local) | Install Ollama, pull a model |
| **Anthropic** | `claude-sonnet-4-6` | ~$0.15–$0.40/article | `ANTHROPIC_API_KEY` |

Switch provider in `.env`:
```env
PROVIDER=groq        # or: ollama, anthropic
```

---

## Google APIs setup (optional)

Without Google credentials, the pipeline uses DuckDuckGo only. With them, Research and Monitor use real GSC + GA4 data.

**Create a service account:**
1. [Google Cloud Console](https://console.cloud.google.com) → New project
2. Enable: **Search Console API** and **Google Analytics Data API**
3. IAM → Service Accounts → Create → Create JSON key → save as `google-credentials.json`

**Grant access:**
- **GSC**: Search Console → Settings → Users → Add service account email as Viewer
- **GA4**: Admin → Account Access Management → Add service account email as Viewer

**For GitHub Actions:** paste the entire JSON as a single-line string in the `GOOGLE_CREDENTIALS_JSON` Actions variable.

---

## Configuration reference

### Core

| Variable | Default | Description |
|---|---|---|
| `PROVIDER` | `groq` | AI provider: `groq`, `ollama`, `anthropic` |
| `GROQ_API_KEY` | — | Required if `PROVIDER=groq` |
| `GROQ_MODEL` | `llama-3.3-70b-versatile` | Groq model |
| `OLLAMA_BASE_URL` | `http://localhost:11434/v1` | Ollama endpoint |
| `OLLAMA_MODEL` | `llama3.1:70b` | Ollama model |
| `ANTHROPIC_API_KEY` | — | Required if `PROVIDER=anthropic` |
| `BLOG_NICHE` | — | e.g. `SaaS marketing` |
| `TARGET_AUDIENCE` | `general readers` | e.g. `early-stage startup founders` |
| `BLOG_TONE` | `informative and engaging` | Writing style |
| `BLOG_LANGUAGE` | `English` | Article language |

### Publishing

| Variable | Default | Description |
|---|---|---|
| `APPROVAL_MODE` | `pr` | `pr` = GitHub PR; `cli` = terminal prompt |
| `CONTENT_DIR` | `output` | Path inside your repo for markdown files |
| `REPO_DIR` | *(cwd)* | Absolute path to your site's git root |

### Google APIs

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CREDENTIALS_FILE` | — | Path to service account JSON |
| `GSC_SITE_URL` | — | e.g. `https://yoursite.com/` |
| `GA4_PROPERTY_ID` | — | Numeric property ID |

### Smart systems

| Variable | Default | Description |
|---|---|---|
| `DEDUP_THRESHOLD` | `0.80` | Jaccard similarity above which a topic is blocked as duplicate (0–1) |
| `PERFORMANCE_CHECK_DAYS` | `30` | Minimum post age before performance flags apply |
| `CORRECTION_AUTO_MODE` | `false` | Set `true` in CI to skip terminal confirmation |
| `MAX_POSTS_IN_RAMP` | `3` | Scheduler slows down when more than this many posts are under 30 days old |

### Daemon / scheduling

| Variable | Default | Description |
|---|---|---|
| `POLL_INTERVAL_MINUTES` | `60` | Local daemon check interval |
| `MIN_QUEUE_SIZE` | `3` | Queue minimum before scheduling a write |
| `MAX_ARTICLES_PER_DAY` | `1` | Max articles per calendar day |

---

## Database tables

All state lives in `blogging_agent.db` (SQLite, committed to the repo for Actions persistence).

| Table | Purpose |
|---|---|
| `topics` | Topic queue: queued → writing → editing → pending_approval → published |
| `drafts` | Article drafts with versioned edits and editor notes |
| `strategy` | Active content strategy from the Strategy Agent |
| `competitor_posts` | Seen competitor posts (prevents re-alerting on the same post) |
| `refreshes` | Content refresh history from RefreshAgent |
| `scheduler_decisions` | Full log of every scheduler decision |
| `post_memory` | Index of all published posts with Jaccard fingerprints |
| `performance_snapshots` | Daily GSC + GA4 snapshots per post with health scores |
| `correction_log` | Self-correction decisions and execution timestamps |

---

## File structure

```
blogging-agent/
├── main.py                     # CLI entry point — all commands
├── orchestrator.py             # Pipeline coordination + approval gate
├── config.py                   # Environment config with defaults
├── scheduler.py                # Smart scheduling logic
├── monitor.py                  # Performance monitoring + health scoring
├── dedup.py                    # Jaccard-based deduplication checker
│
├── agents/
│   ├── base.py                 # Agentic loop (Anthropic + OpenAI-compatible)
│   ├── strategy.py             # Strategy Agent — competitor research + planning
│   ├── research.py             # Research Agent — topic discovery
│   ├── writer.py               # Writer Agent — article authoring
│   ├── editor.py               # Editor Agent — quality review + editing
│   ├── refresh.py              # Refresh Agent — updates stale articles
│   └── corrector.py            # Corrector Agent — fixes underperformers
│
├── memory/
│   └── post_index.py           # Post memory index (Jaccard similarity)
│
├── tools/
│   ├── search.py               # DuckDuckGo search
│   ├── serp.py                 # SERP analysis + competitor discovery
│   ├── competitors.py          # Sitemap scraping + post analysis
│   ├── keyword_discovery.py    # Intent-pattern keyword expansion + Google Trends
│   ├── gsc.py                  # Google Search Console (queries + page performance)
│   └── ga4.py                  # Google Analytics 4 (sessions + declining pages)
│
├── storage/
│   └── db.py                   # SQLite schema + all CRUD functions
│
├── .github/workflows/
│   ├── blog-pipeline.yml       # Mon–Fri: research → write → PR
│   ├── blog-strategy.yml       # Manual: build/update content strategy
│   ├── schedule.yml            # Every 6h: scheduling signals → trigger next action
│   ├── monitor.yml             # Daily 8am: performance snapshots
│   └── correct.yml             # Weekly Monday: self-correction agent
│
├── output/                     # Approved markdown articles (gitkeep)
├── .env.example                # All config variables with descriptions
└── blogging_agent.db           # SQLite database (auto-created + committed)
```
