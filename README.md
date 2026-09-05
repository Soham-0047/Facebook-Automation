# India Tech News Bot v5.3.1

Fully-automated Facebook Page autopilot for Indian tech news: a strong
multi-source fetching engine picks the highest-signal stories, Groq/Gemini
write human-sounding commentary in 6 rotating shapes x 6 personas, and a
designer-grade image pipeline produces 1080x1350 cards that stop the scroll.

Single file. Two dependencies (`requests`, `pillow`; `trafilatura` optional
but recommended). Runs on GitHub Actions free tier, Google Colab, or any
machine with Python 3.9+.

---

## What's new in v5.3 (content quality + image polish)

**v5.3.1 touch-up** (found via a live dry-run): weekly recap series that
hide in sentence form ("Between June 01 and June 06, as many as 18 startups
raised..." / "...raised $1.08 Bn from June 22 to June 27") now score -6;
hashtag video-clip headlines ("#WATCH | ...") score -3; social-media
aggregators (LinkedIn / Instagram / X / YouTube / Reddit / Medium) rank at
-2.5 AND no longer count as corroboration for the India gate - so an
ET + Instagram + Facebook cluster is treated as the single outlet it
really is. The CI leaderboard now tags India-gate-blocked stories with
`[gated: no India angle]` so logs never look like a global wire story will
post.

**Problem:** some posts were still mediocre - SEO listicles leaking
through, small deals outranking big ones, global wire stories with no India
angle sneaking in via Indian outlets. Images needed more visual pop.

### Content ranking is now top-rated-first
- **Source authority tiers:** Economic Times / Moneycontrol / Mint score
  +2.5, down to gadgetsnow / 91mobiles / smartprix at -2.0. Authority is
  matched on domain, so good outlets win ties.
- **Money-magnitude scoring:** headlines are normalised to USD millions
  ($1.2B beats $5M, Rs 900 Cr beats Rs 16 Cr, lakh/crore/bn/mn all
  understood). "500 million users" and "under Rs 20,000" price contexts
  are excluded so only real deal-money counts.
- **SEO-junk firewall:** listicles, buying guides, unboxings, price lists
  (-9), roundups/digests (-5) and viral-bait headlines (-6) are structurally
  excluded. M&A "buys" no longer false-positives as clickbait.
- **India gate (content-based):** a story counts as India-relevant only if
  the TEXT mentions Indian places, regulators (RBI/SEBI), crore/UPI terms or
  Indian entities - not merely because an Indian outlet carried it. Global
  stories need 3+ corroborating outlets or they're dropped.
- **Freshness decay + tautness rules:** older than 24h starts losing points
  (up to -3 at 72h), undated articles -1, ALL-CAPS tabloid -2, empty
  question headlines -1.5.
- **Higher bar:** default `MIN_ENGAGEMENT_SCORE_TO_POST` is now **9**
  (was 8). Weak candidates are rejected - the bot posts the next-best
  story instead of mediocre content. The CI log prints a top-8 leaderboard
  so you can see the ranking live.
- **Wider net:** Google News India-edition topic feeds (Business +
  Technology) and extra targeted queries (semiconductors, layoffs) -
  ~900 articles per run now.

### Image polish
- Second accent color per category, rotated per post (more color variety).
- Drop shadows on all photo-card headlines and stat sub-headlines (readable
  on any photo background).
- Cinematic vignette on full-bleed and stat-hero cards; glow shadow behind
  the giant stat number.

---

## What was new in v5.2 (image variety + Bengali frequency)

- **Bengali posts throttled:** `BENGALI_PROBABILITY` 0.18, at most 1 Bengali
  companion per day (roughly 1-2 per week).
- **6 rotating layouts** (full-bleed cinematic, stat-hero giant-number
  poster, magazine split added to the original three). Bengali cards always
  use a different layout than their English sibling.
- **Stat-hero cards:** the headline's biggest number ($234M / Rs 994 Cr /
  4X / 25%) is rendered giant-centered with an accent glow.
- **Photo dedup memory:** the last 10 Pexels photo IDs are remembered in
  `state.json` and avoided - no more same stock photo on every funding post.
- **Designer fallback motifs** rotate 4 ways; JPEG quality raised to 90.

---

## What was new in v5.1 (fixes the Aug 2026 GitHub Actions failures)

**Root cause:** Groq retired the old model lineup for the free tier in
June-August 2026 (`llama-3.3-70b-versatile` shut down Aug 16, 2026;
`qwen/qwen3-32b` and `llama-3.1-8b-instant` decommissioned earlier), so every
LLM call 404'd and the run ended with "LLM commentary failed after retries".
The old code also logged the real API errors only at DEBUG level, so CI logs
showed nothing useful.

- **Migrated to Groq's official replacements:** `openai/gpt-oss-120b` ->
  `qwen/qwen3.6-27b` -> `openai/gpt-oss-20b` (override via `GROQ_MODELS` env,
  comma-separated). Gemini chain: `gemini-2.5-flash` -> `gemini-2.5-flash-lite`
  -> `gemini-flash-latest` (override via `GEMINI_MODELS`).
- **New API params:** `max_completion_tokens` (the old `max_tokens` is
  deprecated platform-wide) with a >= 1100 floor - reasoning models return
  EMPTY content when the budget is too small, which silently killed
  `gpt-oss-120b` before. `reasoning_effort: low` on gpt-oss models for speed.
  Gemini 2.5 models get `thinkingBudget: 0` so thinking can't eat the output.
- **Errors are now VISIBLE:** every failed LLM call logs a WARNING with the
  HTTP status + provider error body ("groq qwen/... -> HTTP 404 ..."), the
  startup line shows which providers have keys, and rejected outputs say WHY
  (banned phrase / word count / link / language ratio).
- **429/5xx retry with backoff** on LLM calls (they're idempotent, unlike FB
  posts, so retrying is safe).
- **`<think>...</think>` blocks** are stripped from reasoning-model output.
- **Trafilatura log noise fixed** (the confusing `discarding data: None`
  warning is gone; the article URL is now passed to the parser).

### What was already in v5.0

#### 1. Much stronger fetching algorithm
- **Multi-source fan-out (parallel, fault-isolated):** NewsAPI
  (top-headlines + everything queries) + 11 direct RSS feeds
  (ET Tech, TOI Tech, Inc42, YourStory, Medianama, TelecomTalk, ...) +
  6 Google News RSS topic queries (free, no key) + Hacker News Algolia +
  optional GNews. One dead source never kills the run.
- **Scoring engine:** weighted word-boundary keyword rules (~20 categories:
  IPO, M&A, regulatory, unicorn, funding, layoffs, AI, EV, space, cyber,
  telecom, fintech, ...), an ~85-entity bonus table, money-magnitude regex
  (the bigger the number, the bigger the boost), recency decay, and
  vagueness/junk penalties. Only stories clearing the bar get posted.
- **Cross-source corroboration:** fuzzy title clustering detects the same
  story appearing in multiple sources and boosts it - real signal, not
  single-outlet noise.
- **Tuned fuzzy dedup:** SequenceMatcher 0.62 (0.55 with >= 5 shared
  tokens) + token-overlap prefilter, on top of the exact key
  (normalized title + URL). No repeats, no near-duplicates.
- **Selection guardrails:** threshold gate (default 8/10), quiet-page
  safety valve (page silent too long -> bar relaxes), category/domain
  diversity penalties so the page never looks like one outlet's feed.

#### 2. Much more attractive images
- **1080x1350 portrait cards** (max feed real estate), rendered at 2x and
  LANCZOS-downscaled for print-sharp text.
- **3 rotating layouts** so the feed never looks templated:
  `bottom_sheet` (photo top, headline panel bottom), `top_banner`,
  `split_card`.
- **Smart photo picking:** entity-aware Pexels search, multi-candidate
  ranking by resolution/aspect then vividness (saturation + contrast),
  auto color/contrast/sharpness enhance, smart top-biased crop.
- **Cinematic presentation:** category chip, auto-fit Poppins headline
  (never overflows, emoji stripped), accent bar, source line, optional
  page-handle watermark, layered scrims for guaranteed text contrast.
- **Designer fallback card** when no photo fits: duotone gradient + dot
  grid + rings - still on-brand, never a flat gray box.
- **Bengali cards** with Noto Sans Bengali + Bengali digits/date.

### Also improved
- Groq 4-model fallback chain -> Gemini 2-model fallback (plain REST, no
  SDK), output validation + retry, banned-phrase lists (English + Bengali).
- Grounded generation: trafilatura full-text + Wikipedia + Bengali Google
  News snippets fed to the LLM (less hallucination, more specifics).
- Facebook hardening: GET-only auto-retry; publish POSTs are single-attempt
  so a timeout can never double-post; state syncs cadence + Bengali daily
  count from the page itself.
- UUID filenames (no silent overwrites), `state.json` committed back to
  the repo by the included workflow so dedup memory survives across runs.

---

## Package contents

```
india-tech-news-bot/
├── bot.py                          # the entire bot (v5.3.1)
├── requirements.txt
├── README.md
├── .env.example
├── fonts/                          # Poppins + Noto Sans Bengali (bundled,
│                                   # bot re-downloads automatically if absent)
├── sample_cards/                   # real renders from the offline self-test
└── .github/workflows/post-news.yml # scheduled GitHub Actions runner
```

---

## Quick start (local / Colab)

```bash
pip install -r requirements.txt

# 1. Offline sanity check - no keys needed, renders sample cards
python bot.py --self-test

# 2. Full dress rehearsal - fetches real news, writes real captions,
#    saves cards + captions to ./preview/ ... but never posts
python bot.py --dry-run

# 3. Go live
export FB_PAGE_ACCESS_TOKEN=... FB_PAGE_ID=... NEWS_API_KEY=...
export GROQ_API_KEY=... PEXELS_API_KEY=...   # GEMINI_API_KEY optional
python bot.py
```

On Colab: keep secrets in the Secrets tray (side panel -> key icon) - the
bot reads them automatically via `google.colab.userdata`, falling back to
normal environment variables everywhere else.

## Deploy on GitHub Actions (recommended)

1. Create a repo and push this folder's contents to it (keep
   `.github/workflows/post-news.yml`).
2. Repo **Settings -> Secrets and variables -> Actions -> Secrets**, add:

   | Secret | Required | Notes |
   |---|---|---|
   | `FB_PAGE_ACCESS_TOKEN` | yes | Page token with `pages_manage_posts`, `pages_read_engagement` |
   | `FB_PAGE_ID` | yes | numeric Page ID |
   | `NEWS_API_KEY` | yes | newsapi.org free tier works |
   | `GROQ_API_KEY` | yes | console.groq.com free tier works |
   | `PEXELS_API_KEY` | yes | pexels.com/api, free |
   | `GEMINI_API_KEY` | optional | fallback LLM provider |
   | `GNEWS_API_KEY` | optional | extra source |

3. Optional repo **Variables** (same screen, "Variables" tab):
   `PAGE_HANDLE` (e.g. `@indiatechdaily`, watermark on cards) and
   `POST_LINK_AS_FIRST_COMMENT` (`true` = keep captions clean, link in
   first comment - often better reach).
4. Done. The workflow posts ~4x/day IST (08:30, 12:30, 18:00, 21:30) and
   commits `state.json` back after each run so dedup memory persists.
   **Actions -> India Tech Page Bot -> Run workflow** to trigger a post
   manually anytime.

---

## CLI flags

| Flag | What it does |
|---|---|
| `--dry-run` | Full pipeline, saves previews to `./preview/`, never posts |
| `--bengali-preview` | Force one Bengali companion preview |
| `--preview-image` | Render the best card only (no LLM calls, no post) |
| `--no-image` | Publish text-only posts |
| `--self-test` | Offline checks + sample cards, no keys needed |
| `--threshold N` | Override the engagement gate for one run |
| `--verbose` | Debug logging |

## Tuning knobs (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `MIN_ENGAGEMENT_SCORE_TO_POST` | `9` | Quality gate 1-10 (v5.3 raised the default bar) |
| `QUIET_HOURS` | `20` | Hours of silence before the gate relaxes |
| `QUIET_RELAX_DROP` | `3` | How much the gate relaxes in quiet mode |
| `BENGALI_PROBABILITY` | `0.18` | Chance a Bengali companion accompanies a post (throttled in v5.2) |
| `BENGALI_MAX_PER_DAY` | `1` | Bengali posts per day cap |
| `POST_LINK_AS_FIRST_COMMENT` | `false` | Link-as-comment reach trick |
| `PAGE_HANDLE` | *(empty)* | Watermark on cards, e.g. `@indiatechdaily` |
| `FB_API_VERSION` | `v21.0` | Graph API version |
| `GROQ_MODELS` | *(official replacements)* | Comma-separated Groq model chain - edit when Groq deprecates models again |
| `GEMINI_MODELS` | `gemini-2.5-flash,...` | Comma-separated Gemini model chain |
| `BOT_STATE_PATH` / `BOT_FONT_DIR` | script-relative | Override `state.json` / `fonts/` locations |

## Troubleshooting

- **"LLM commentary failed after retries"** - the WARNING lines right above it
  now show the exact HTTP status + provider error. If you see `HTTP 404 ...
  model does not exist`, Groq decommissioned a model again: set the `GROQ_MODELS`
  env var (repo variable or secret) to the current list from
  console.groq.com/docs/models. If you see `HTTP 401`, re-issue the key.
- **"groq ... returned empty content"** - reasoning model burned its token
  budget; the bot auto-falls to the next model, and the >= 1100 token floor
  already makes this rare.
- **"Node 20 is being deprecated" warning in Actions** - cosmetic; GitHub
  forces actions/checkout@v4 / setup-python@v5 onto Node 24 and they keep
  working. No action needed.
- **"Fonts ready: Poppins=False"** - offline machine. Ship the bundled
  `fonts/` folder next to `bot.py` (already the case in this zip) or set
  `BOT_FONT_DIR`.
- **Zero posts / "nothing cleared the bar"** - normal on slow news days;
  the quiet valve kicks in after `QUIET_HOURS`. For testing use
  `--threshold 5` or `--dry-run`.
- **NewsAPI 429** - the free tier is 100 req/day; the bot already fans out
  leanly and falls back to the keyless RSS + Google News + HN sources, so
  posting continues even with quota exhausted.
- **Facebook token errors** - re-issue a long-lived Page token
  (Graph API Explorer -> Page -> generate; extend 60 days) and update the
  secret.
- **Duplicate state on GitHub Actions** - make sure the workflow's
  "Persist state.json" step ran (`contents: write` permission is already
  set in the included workflow).

Happy posting! The bot logs every decision (scores, clustering, gates) -
run with `--verbose` once to watch it think.
