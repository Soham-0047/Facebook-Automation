# India Tech News Bot v5.4

Fully-automated Facebook Page autopilot for Indian tech news: a strong
multi-source fetching engine picks the highest-signal stories, Groq/Gemini
write human-sounding commentary in 6 rotating shapes x 6 personas, and a
designer-grade image pipeline produces 1080x1350 cards that stop the scroll.

Single file. Two dependencies (`requests`, `pillow`; `trafilatura` optional
but recommended). Runs on GitHub Actions free tier, Google Colab, or any
machine with Python 3.9+.

---

## What's new in v5.4 (topic variety + smarter crops + crash-safe automation)

### Content
- **Hard daily topic diversity:** max 2 posts per category per day, and never
  the same category back-to-back. The 4 daily posts now cover 4 different
  beats instead of 4 funding roundups of the same flavour. Counters live in
  `state.json` (7-day rolling window); tune or disable via
  `MAX_PER_CATEGORY_PER_DAY`.
- **Clickbait firewall:** shout-words (`THIS SHOCKING MOVE`), punctuation
  spam (`!!!`) and bait hooks ("you won't believe", "here's why") now sink
  below clean newsroom headlines, even from tier-1 outlets.
- **Smart-tag safety net:** when the LLM ships a post with zero hashtags,
  up to 2 clean topical tags (category + company, e.g. `#StartupFunding
  #Blinkit`) are appended deterministically. Fires only as a fallback, so
  LLM-curated tags are never doubled. Disable with `SMART_TAGS=false`.

### Images
- **Entropy-aware crops:** instead of a blind center + top-bias crop, the
  bot now measures edge energy on a downscaled grayscale and keeps the most
  detail-rich window - faces, logos, products and skylines stay in frame.
  A soft center pull prevents slamming the crop to a photo edge; any error
  falls back to the v5.3 crop.
- **Dynamic headline sizing:** short punchy headlines ("Jio posts record
  profit") render up to +14pt bigger - poster mode for the feed; long
  headlines start smaller and wrap gracefully.
- **Contrast-aware scrims:** the darkening scrim under the headline now
  measures the actual photo luminance in the text zone and adapts - busy
  bright photos get a stronger scrim, already-dark photos keep breathing.

### Automation (GitHub Actions)
- **Crash-safe by construction:** the selected story's dedup key is written
  to `state.json` BEFORE the Facebook upload starts. If the workflow times
  out or dies mid-upload, the retry pass can structurally never double-post.
- **Safe retry:** the workflow retries a crashed run once after 90s - but
  ONLY on a real crash (detected via a `.run_ok` completion sentinel).
  Clean skips ("nothing cleared the bar") and clean failures (LLM down) are
  left for the next scheduled run instead of hammering a dead API.
- **Failure alerts:** failed runs auto-open a GitHub issue (deduplicated -
  one open issue at a time), so you get an email the moment the bot breaks
  instead of discovering silent gaps days later.
- **Engagement tracker:** a second workflow (`insights.yml`, 2x daily) runs
  `python bot.py --insights`, pulls reactions/comments/shares for recent
  posts into `state.json`, and logs a 7-day engagement leaderboard plus the
  best-performing IST hour - tune your cron schedule on data, not vibes.

### History
- **v5.3 / v5.3.1** - source-authority tiers, money-magnitude scoring,
  SEO-junk firewall, content-based India gate (3-outlet corroboration),
  freshness decay, threshold raised to 9, recap-series / `#WATCH` /
  social-aggregator penalties, gated-leaderboard logging.
- **v5.2** - 6 rotating layouts, stat posters, photo anti-repeat memory,
  calmer Bengali frequency.
- **v5.1** - Groq free-tier model-line migration with automatic fallbacks.
- **v5.0** - multi-source fan-out (NewsAPI + RSS + Google News + HN + Gnews),
  cross-source clustering + dedup, engagement scoring.

---

## Package contents

```
.
├── bot.py                              # the entire bot (v5.4)
├── requirements.txt
├── README.md
├── env.example                         # copy to .env and fill in
├── tests/                              # offline test suite (mock LLM, scrims)
├── fonts/                              # Poppins + Noto Sans Bengali (bundled,
│                                       # bot re-downloads automatically if absent)
├── sample_cards/                       # real renders from the offline self-test
└── .github/workflows/
    ├── post-news.yml                   # scheduled poster (4x/day IST, retry + alerts)
    ├── insights.yml                    # engagement tracker (2x/day IST)
    └── tests.yml                       # CI: self-test + 20 mock tests on every push
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

1. Push this repo (keep both files under `.github/workflows/`).
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
   `PAGE_HANDLE` (watermark on cards), `POST_LINK_AS_FIRST_COMMENT`
   (`true` = link in first comment), `MAX_PER_CATEGORY_PER_DAY`
   (topic diversity cap, default 2; `0` disables).
4. Done. What runs automatically:
   - **Post workflow** - 4x/day IST (08:30, 12:30, 18:00, 21:30), retries
     once on a real crash, opens an auto-deduplicated issue on failure, and
     commits `state.json` back after every run so dedup memory persists.
   - **Insights workflow** - 2x/day IST (16:00, 23:45), pulls engagement
     into `state.json` and logs the leaderboard. Check the run logs (or
     `state.json -> hour_eng`) to see which posting hours perform best.
   - **Manual run** - Actions -> India Tech Page Bot -> Run workflow.

---

## CLI flags

| Flag | What it does |
|---|---|
| `--dry-run` | Full pipeline, saves previews to `./preview/`, never posts |
| `--bengali-preview` | Force one Bengali companion preview |
| `--preview-image` | Render the best card only (no LLM calls, no post) |
| `--no-image` | Publish text-only posts |
| `--self-test` | Offline checks + sample cards, no keys needed |
| `--insights` | Pull engagement stats into `state.json` + log leaderboard (no posting) |
| `--threshold N` | Override the engagement gate for one run |
| `--verbose` | Debug logging |

## Tuning knobs (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `MIN_ENGAGEMENT_SCORE_TO_POST` | `9` | Quality gate |
| `MAX_PER_CATEGORY_PER_DAY` | `2` | v5.4 topic diversity cap (`0` = off) |
| `SMART_TAGS` | `true` | v5.4 hashtag safety net |
| `QUIET_HOURS` | `20` | Hours of silence before the gate relaxes |
| `QUIET_RELAX_DROP` | `3` | How much the gate relaxes in quiet mode |
| `BENGALI_PROBABILITY` | `0.18` | Chance a Bengali companion accompanies a post |
| `BENGALI_MAX_PER_DAY` | `1` | Bengali posts per day cap |
| `POST_LINK_AS_FIRST_COMMENT` | `false` | Link-as-comment reach trick |
| `PAGE_HANDLE` | *(empty)* | Watermark on cards, e.g. `@indiatechdaily` |
| `FB_API_VERSION` | `v21.0` | Graph API version |
| `GROQ_MODELS` | *(official replacements)* | Comma-separated Groq model chain - edit when Groq deprecates models |
| `GEMINI_MODELS` | `gemini-2.5-flash,...` | Comma-separated Gemini model chain |
| `BOT_STATE_PATH` / `BOT_FONT_DIR` | script-relative | Override `state.json` / `fonts/` locations |

## Troubleshooting

- **"LLM commentary failed after retries"** - the WARNING lines right above
  show the exact HTTP status + provider error. `HTTP 404 ... model does not
  exist` means Groq decommissioned a model again: set `GROQ_MODELS` to the
  current list from console.groq.com/docs/models. `HTTP 401` = re-issue the
  key.
- **"Bot run failed - needs attention" issue opened** - that's the v5.4
  alert doing its job. Open the linked run log; the most common causes are
  listed in the issue body. Fix, close the issue, done.
- **Insights workflow exits with "No posts/engagement data"** - the
  `FB_PAGE_ACCESS_TOKEN` secret is missing/expired, or the page has no
  posts yet. Posting is unaffected.
- **"groq ... returned empty content"** - reasoning model burned its token
  budget; the bot auto-falls to the next model, and the >= 1100 token floor
  already makes this rare.
- **"Fonts ready: Poppins=False"** - offline machine. Ship the bundled
  `fonts/` folder next to `bot.py` (already the case here) or set
  `BOT_FONT_DIR`.
- **Zero posts / "nothing cleared the bar"** - normal on slow news days;
  the quiet valve kicks in after `QUIET_HOURS`. For testing use
  `--threshold 5` or `--dry-run`.
- **NewsAPI 429** - free tier is 100 req/day; the bot falls back to the
  keyless RSS + Google News + HN sources, so posting continues even with
  quota exhausted.
- **Facebook token errors** - re-issue a long-lived Page token (Graph API
  Explorer -> Page -> generate; extend 60 days) and update the secret.
- **Duplicate state on GitHub Actions** - make sure the workflow's
  "Persist state.json" step ran (`contents: write` is already set). Note
  v5.4 also marks stories in-flight before upload, so a failed run can
  never repost the same story.

Happy posting! The bot logs every decision (scores, clustering, gates,
diversity filters) - run with `--verbose` once to watch it think.
