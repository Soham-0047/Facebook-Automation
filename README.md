# India Tech News Bot v5.5

Fully-automated Facebook Page autopilot for Indian tech news: a strong
multi-source fetching engine picks the highest-signal stories, Groq/Gemini
write human-sounding commentary in 8 rotating shapes x 6 personas with
built-in engagement hooks, and a designer-grade image pipeline mixes 8
layouts, 2 formats and film grain so the feed reads like a real page, not a
template. The page even talks back: a scheduled engagement pass replies to
fan comments in the page's own voice.

Single file. Two dependencies (`requests`, `pillow`; `trafilatura` optional
but recommended). Runs on GitHub Actions free tier, Google Colab, or any
machine with Python 3.9+.

---

## What's new in v5.5 (the human-touch release)

### Content
- **Interaction hooks:** most posts now end with one cheap action a reader
can complete in two seconds - emoji votes ("Your one-word take: 👍 or 👎?"),
1-10 scales, quick polls, tag-a-friend, fill-in-the-blank. Hooks rotate
with memory (never the same CTA twice in a row), are category-aware, and
are skipped ~25% of the time so the page never feels needy. Tune with
`ENGAGE_HOOK_PROBABILITY`.
- **Two new post shapes:** RECEIPTS (three cold dash-line facts, zero
opinion - the most shareable format on Indian tech pages) and COLD OPEN
(starts mid-conversation, "So Bengaluru just..."). 8 shapes total.
- **Anti-AI-tell hardening:** the banned-phrase list now blocks the current
generation of LLM filler ("let's unpack", "isn't just", "read that again",
"plot twist", ...), and shape selection is weighted-random with a 3-post
memory instead of a predictable modulo rotation.
- **Looser, commercial emoji policy:** 0-3 emoji as visual anchors
(previously 0-1), with the hashtags always moved to the final line after
the hook - the layout real pages use.

### The page talks back
- **Engagement pass (`--engage`):** a new scheduled mode reads fresh
comments on recent posts, replies to the best ones via Groq/Gemini in the
page's voice (40 words max, warm, never generic), and likes a handful
more. Reply memory lives in `state.json`, so no comment is ever answered
twice. This is the single biggest "real page vs bot page" signal - and
Facebook's ranking rewards comment replies with extra reach.

### Images
- **Square format mixing:** ~35% of cards render as 1080x1080 squares
(never two in a row). A feed of only 4:5 cards reads as a template; a
mixed feed reads like a real page. Tune with `SQUARE_CARD_PROBABILITY`.
- **Text-only hot takes:** ~15% of conversational posts ship without an
image card at all - exactly what real pages do between the designed posts.
Tune with `TEXT_POST_PROBABILITY`.
- **Film grain:** every card now carries subtle sensor noise. "Too clean"
flat gradients are the #1 visual tell of AI-generated design; grain kills
it.
- **Two new layouts:** TICKER (broadcast banner: accent band, pulsing dot,
heavy centered headline, dark strip - the loudest card we have) and QUOTE
(editorial pull-quote: duotone photo, cream paper, giant serif quote mark,
DejaVu Serif bundled in `fonts/`). 8 layouts total.
- **Duotone treatments:** full-bleed and magazine cards periodically map
the photo through an accent-tinted duotone ramp, so photo + typography
read as one designed object.

### Fixes
- **`--insights` exits 0 when there's no engagement data** (token missing /
FB hiccup / quiet page). "Nothing to do" was never a failure - but it
marked the workflow red and emailed you. Green is green.

### History
- **v5.4** - hard daily topic diversity, clickbait firewall, smart-tag
safety net, entropy-aware crops, dynamic headline sizing, contrast-aware
scrims, crash-safe in-flight dedup, safe retry, failure-alert issues,
engagement tracker (`--insights`).
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
├── bot.py                              # the entire bot (v5.5)
├── requirements.txt
├── README.md
├── env.example                         # copy to .env and fill in
├── tests/                              # offline test suite (46 mock cases)
├── fonts/                              # Poppins + Noto Sans Bengali + DejaVu Serif
│                                       # (bundled; bot re-downloads if absent)
├── sample_cards/                       # real renders from the offline self-test
└── .github/workflows/
    ├── post-news.yml                   # scheduled poster (4x/day IST, retry + alerts)
    ├── engage.yml                      # comment replies + likes (4x/day IST)
    ├── insights.yml                    # engagement tracker (2x/day IST)
    └── tests.yml                       # CI: self-test + 46 mock tests on every push
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
   (topic diversity cap, default 2; `0` disables), `ENGAGE_MAX_REPLIES`
   (comment replies per engagement pass, default 4),
   `ENGAGE_LIKE_COMMENTS` (comment likes per pass, default 6).
4. Done. What runs automatically:
   - **Post workflow** - 4x/day IST (08:30, 12:30, 18:00, 21:30), retries
     once on a real crash, opens an auto-deduplicated issue on failure, and
     commits `state.json` back after every run so dedup memory persists.
   - **Engagement workflow** - 4x/day IST (07:00, 13:00, 19:00, 01:00),
     replies to fresh fan comments in the page's voice and likes a few
     more. Needs the same secrets as posting (FB token + at least one LLM
     key). Watch `state.json -> replied_comment_ids` grow.
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
| `--engage` | **v5.5:** reply to fresh comments + like fan comments (the page talks back) |
| `--threshold N` | Override the engagement gate for one run |
| `--verbose` | Debug logging |

## Tuning knobs (environment variables, all optional)

| Variable | Default | Meaning |
|---|---|---|
| `MIN_ENGAGEMENT_SCORE_TO_POST` | `9` | Quality gate |
| `MAX_PER_CATEGORY_PER_DAY` | `2` | v5.4 topic diversity cap (`0` = off) |
| `SMART_TAGS` | `true` | v5.4 hashtag safety net |
| `ENGAGE_HOOK_PROBABILITY` | `0.75` | v5.5 share of posts ending with a CTA hook |
| `TEXT_POST_PROBABILITY` | `0.15` | v5.5 share of conversational posts with no image |
| `SQUARE_CARD_PROBABILITY` | `0.35` | v5.5 share of 1080x1080 square cards |
| `ENGAGE_MAX_REPLIES` | `4` | v5.5 comment replies per `--engage` pass |
| `ENGAGE_LIKE_COMMENTS` | `6` | v5.5 comment likes per `--engage` pass |
| `ENGAGE_COMMENTS_PER_POST` | `20` | v5.5 comments scanned per post |
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
  posts yet. v5.5 treats this as exit 0 (nothing to do), so the workflow
  stays green and posting is unaffected.
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
