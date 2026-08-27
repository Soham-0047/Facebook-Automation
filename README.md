# India Tech News Bot v5.0

Fully-automated Facebook Page autopilot for Indian tech news: a strong
multi-source fetching engine picks the highest-signal stories, Groq/Gemini
write human-sounding commentary in 6 rotating shapes x 6 personas, and a
designer-grade image pipeline produces 1080x1350 cards that stop the scroll.

Single file. Two dependencies (`requests`, `pillow`; `trafilatura` optional
but recommended). Runs on GitHub Actions free tier, Google Colab, or any
machine with Python 3.9+.

---

## What's new in v5.0

### 1. Much stronger fetching algorithm
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

### 2. Much more attractive images
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
├── bot.py                          # the entire bot (v5.0)
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
| `MIN_ENGAGEMENT_SCORE_TO_POST` | `8` | Quality gate 1-10 |
| `QUIET_HOURS` | `20` | Hours of silence before the gate relaxes |
| `QUIET_RELAX_DROP` | `3` | How much the gate relaxes in quiet mode |
| `BENGALI_PROBABILITY` | `0.5` | Chance a Bengali companion accompanies a post |
| `BENGALI_MAX_PER_DAY` | `2` | Bengali posts per day cap |
| `POST_LINK_AS_FIRST_COMMENT` | `false` | Link-as-comment reach trick |
| `PAGE_HANDLE` | *(empty)* | Watermark on cards, e.g. `@indiatechdaily` |
| `FB_API_VERSION` | `v21.0` | Graph API version |
| `BOT_STATE_PATH` / `BOT_FONT_DIR` | script-relative | Override `state.json` / `fonts/` locations |

## Troubleshooting

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
