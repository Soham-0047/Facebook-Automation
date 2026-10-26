# Trending & Entertainment News Bot v5.8

Fully-automated Facebook Page autopilot for **worldwide trending +
entertainment news**: movies, music, TV & streaming, sports, celebs, gaming,
viral moments, space, and big tech launches. A strong multi-source fetching
engine (Variety, THR, Deadline, Billboard, NME, E! News, Guardian, BBC, IGN,
GameSpot, Space.com, NASA + Google News trending fan-outs) picks the hottest
clean stories, Groq/Gemini write human-sounding commentary in 10 rotating
shapes x 6 personas with built-in engagement hooks, and a designer-grade
image pipeline pulls impressive cinematic photography (concert lights,
stadium floodlights, rocket launches, red carpets) into 8 layouts, 2 formats
with film grain so the feed reads like a real page, not a template. The page
is now a game show too: ~1 in 3 posts is an interactive special - trivia
quizzes, THIS OR THAT battles, guess-the-title riddles - and ~1 in 5 photo
posts becomes a swipeable multi-photo carousel. The page even talks back: a
scheduled engagement pass replies to fan comments in the page's own voice.

**Never-fail automation:** every run path exits 0 - the GitHub Actions job
stays green even when Facebook, Groq, or every news source hiccups. Real
problems (broken token, LLM outage) write `.run_alert`, which the workflow
converts into an auto GitHub issue *without* failing the run. And if both
LLM providers are down, a hand-written template caption keeps the page
posting instead of going silent.

**Family-friendly by construction:** an explicit-content firewall filters
every headline, every LLM draft, every trivia question, and keeps all image
queries inside curated safe banks. No explicit content, no graphic gore -
ever.

Single file. Two dependencies (`requests`, `pillow`; `trafilatura` optional
but recommended). Runs on GitHub Actions free tier, Google Colab, or any
machine with Python 3.9+.

---

## What's new in v5.8 (AI image engine + editorial pipeline)

### AI-generated cards - free, keyless, unique (the big one)
- Integrated **Pollinations.ai serving the open-source FLUX model**: a
  text-to-image GET endpoint that needs **no API key, no signup, no card**.
- ~45% of photo cards are now **generated from the headline itself** - a
  beat-aware cinematic prompt (12 category style banks x rotating camera
  flavors x lighting keys) produces art no other page can repost.
- The free tier allows ~1 image / 30s per IP: one patient retry on 402, then
  a **silent fallback to the stock-photo (Pexels) flow**. The AI engine can
  never fail a run - it can only improve it.
- Anonymous output (~686x858) is upscaled with LANCZOS + unsharp to the
  full 1080x1350 / 1080x1080 canvas.
- **NSFW firewall extended to the image prompts** (headline + assembled
  prompt both gated) and the service's own `safe=true` filter is on.
- **Transparency:** AI cards carry an `AI-GENERATED` source-line label,
  matching Facebook's AI-content best practice.
- Tunable: `AI_IMAGE_ENABLED`, `AI_IMAGE_PROBABILITY`, `AI_IMAGE_TIMEOUT`,
  `AI_IMAGE_RETRY_WAIT`.

### Editorial pipeline (drastically better captions)
- Every caption is now written **twice**: the persona drafts it, then a
  second "ruthless viral editor" LLM pass tightens the hook, deletes filler
  and makes one idea pop - under 12-word openers, no new facts.
- A 0-10 **hook heuristic** (short opener, question, concrete number, punchy
  sentence length, reader address) scores draft vs rewrite; the rewrite is
  kept **only if it scores >= the draft**. The editor can never make a post
  worse, and an editor crash just leaves the draft standing.
- Turn it off with `EDITOR_PASS_ENABLED=false`.

### Tier-3 keyless LLM (the chain never dies)
- Third provider in the chain: **pollinations.ai text** (free, no key).
  Reached only when Groq AND Gemini are both down - three independent
  providers must fail in the same run for the page to fall back to
  template captions.

### Blockbuster colour grade
- ~35% of stock photos get a subtle **teal-orange cinematic grade**
  (Hollywood-style: cool shadows, warm highlights). AI-generated art skips
  it - FLUX ships cinematic already. Tunable: `CINEMATIC_GRADE_PROBABILITY`.

### CI: alerts now actually reach you
- **Bug fixed:** the workflows' alert steps (`gh issue create`) were silently
  403-ing because `issues: write` was missing from the job permissions.
  Added to post-news, engage and insights - the "you get an email" promise
  now works.
- engage/insights runs now follow the same **green-but-alerted** pattern as
  the post job: a crash never reddens the badge, it flags a GitHub issue.
- New test suite: `tests/test_ai_features.py` - 44 offline cases covering
  the AI image engine, tier-3 LLM ordering, editorial pass and the
  AI-card render path end-to-end.

## What was new in v5.7 (the engagement engine)

### Interactive specials (~1 in 3 posts, never two in a row, max 2/day)
- **TRIVIA DROP:** clean entertainment trivia from OpenTDB (free, no key)
  on its own neon quiz card - giant '?', lettered option pills. The answer
  is posted as a first comment, so the thread becomes the game.
- **THIS OR THAT:** A/B battle card - two striking photos split diagonally
  behind a giant VS badge; readers pick a side in the comments.
- **GUESS THE TITLE:** 3 clues on a gold mystery card + an emoji riddle in
  the caption; the reveal lands in the comments.
- Guard rails: hard daily cap, no same-kind repeats back-to-back, the same
  NSFW gate as news, and every probability is env-tunable.

### Carousel posts (~1 in 5 photo posts)
- Facebook **multi-photo swipeable carousels**: the designed headline card
  + 2 extra impressive photos from the same query pool (zero extra API
  calls). Carousels reliably out-reach single-photo posts.
- Slide design: enhanced crop, soft scrim, category accent bar, handle
  watermark - reads as one designed object with the card.
- If any upload step fails, the post silently falls back to a single photo
  (unpublished uploads stay invisible on the page - no duplicates, ever).

### More voices
- 2 new post shapes (REACTION SPLIT, GUESS HOOK) and 2 new hooks
  ("no googling", "which side are you on").

### Never-fail automation (the CI stays green)
- **Template-caption fallback:** if Groq AND Gemini are both down, the page
  still posts a varied hand-written caption (6 rotating templates) - a
  quiet page loses reach faster than a plainer post.
- **Every exit is 0:** source outage -> "nothing to do this run"; publish
  failure -> story marked, alert flagged, next run moves on.
- **`.run_alert` -> auto GitHub issue** without failing the job: you get
  the email, the badge stays green.
- Stale-alert cleanup at run start; the alert issue dedupes itself (one
  open issue, new flags become comments).

### What was new in v5.6 (the worldwide entertainment pivot)

#### Content
- **Scope pivot: the page now covers the whole world.** The India tech
  gate is gone, the source list is rebuilt around the global entertainment
  trades + world news, and scoring runs on 11 new beats: VIRAL, GAMING,
  MOVIES, MUSIC, SPORTS, TV, CELEBS, SPACE, TECH, WOW (world records &
  bizarre-but-clean), TRENDING.
- **Fresh source lineup (all live-probed):** Variety, Hollywood Reporter,
  Deadline, IndieWire, Billboard, NME, Guardian Music/World/Culture/Sport/Tech,
  E! News, NY Post Entertainment, TOI Entertainment, BBC News/Sport,
  Space.com, NASA, IGN, GameSpot, PC Gamer, The Verge, Bored Panda + Google
  News WORLD/ENTERTAINMENT/SPORTS/TECHNOLOGY topic feeds and `when:Nd`
  trending queries (viral, box office, trailers, world records, awards).
- **Pop-culture entity table:** ~70 names from Taylor Swift, Messi and
  Marvel to Netflix, PlayStation and SpaceX carry category hints and score
  weight - the page knows what's actually hot.
- **Explicit-content firewall:** NSFW + graphic-gore regex gate at fetch
  time, a second gate on LLM output, and strict family-friendly rules in
  every prompt (posts, Bengali posts, comment replies). Image queries are
  curated safe banks, never derived from raw headline text.
- **Viral is a virtue now:** "gone viral / breaking the internet / millions
  of views" moved from the penalty list to the top of the scoring engine -
  while cheap clickbait ("you won't believe", SHOCKING!!!) still dies.
- **Routine politics ranks below entertainment** - fun-first page; only a
  massively corroborated political moment still clears the bar.

#### Images (the "impressive photo" engine)
- **Cinematic query banks per beat:** concert stage lights, stadium
  floodlights, rocket launches at night, red-carpet flashbulbs, aurora,
  neon city streets - every query aims for a photo people stop scrolling
  for.
- **Bigger candidate pools:** 18 photos per query (was 12), top 4
  downloaded and vividness-ranked (was 3).
- **Landscape-friendly ranking:** the resolution floor now accepts dramatic
  wide shots (which crop beautifully) and the aspect penalty was softened
  from 2200 to 1200 - huge cinematic landscapes can beat a mediocre
  portrait.
- **Rotating cinematic modifiers:** each run randomly appends "dramatic",
  "cinematic", "night", "aerial", "neon", "bokeh" or "golden hour" to the
  category queries, so no two days pull identical stock.
- **New entertainment palette:** VIRAL hot-pink, MOVIES gold, MUSIC magenta,
  STREAMING cyan, SPORTS electric green, GAMING violet, WOW yellow -
  magazine-cover energy with the usual accent rotation so no category
  repeats itself visually.

### History
- **v5.6** - worldwide pivot, 11 entertainment beats, NSFW firewall,
  impressive-photo engine, entertainment palette.
- **v5.5** - interaction hooks, `--engage` comment replies, TICKER + QUOTE
  layouts, square mixing, film grain, duotone, banned-phrase hardening.
- **v5.4** - hard daily topic diversity, clickbait firewall, smart-tag
  safety net, entropy-aware crops, dynamic headline sizing, contrast-aware
  scrims, crash-safe in-flight dedup, safe retry, failure-alert issues,
  engagement tracker (`--insights`).
- **v5.3 / v5.3.1** - source-authority tiers, money-magnitude scoring,
  SEO-junk firewall, freshness decay, recap-series / `#WATCH` /
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
├── bot.py                              # the entire bot (v5.6)
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
   - **Manual run** - Actions -> Trending Entertainment Page Bot -> Run workflow.

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
| `SPECIAL_POSTS_ENABLED` | `true` | v5.7 master switch for trivia/VS/riddle specials |
| `TRIVIA_PROBABILITY` | `0.16` | v5.7 share of runs that become a TRIVIA DROP |
| `VS_PROBABILITY` | `0.12` | v5.7 share of runs that become a THIS OR THAT battle |
| `RIDDLE_PROBABILITY` | `0.08` | v5.7 share of runs that become a GUESS THE TITLE riddle |
| `MAX_SPECIALS_PER_DAY` | `2` | v5.7 hard daily cap on interactive specials |
| `CAROUSEL_ENABLED` | `true` | v5.7 multi-photo carousel master switch |
| `CAROUSEL_PROBABILITY` | `0.20` | v5.7 share of photo posts that become carousels |
| `CAROUSEL_MAX_PHOTOS` | `3` | v5.7 slides per carousel (card + N photos) |
| `TEMPLATE_CAPTION_FALLBACK` | `true` | v5.7 keep posting hand-written captions if both LLMs are down |
| `AI_IMAGE_ENABLED` | `true` | v5.8 master switch for AI-generated (FLUX) cards |
| `AI_IMAGE_PROBABILITY` | `0.45` | v5.8 share of photo cards that use AI art (rest stay on Pexels) |
| `AI_IMAGE_TIMEOUT` | `90` | v5.8 seconds to wait for one FLUX generation |
| `AI_IMAGE_RETRY_WAIT` | `32` | v5.8 free-tier busy wait before the single retry |
| `EDITOR_PASS_ENABLED` | `true` | v5.8 second-pass viral-editor rewrite of every caption |
| `POLLINATIONS_TEXT_ENABLED` | `true` | v5.8 keyless tier-3 LLM (only used if Groq AND Gemini fail) |
| `CINEMATIC_GRADE_PROBABILITY` | `0.35` | v5.8 share of stock photos that get the teal-orange movie grade |
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
| `PAGE_HANDLE` | *(empty)* | Watermark on cards, e.g. `@trendingbuzz` |
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
