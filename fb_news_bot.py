#!/usr/bin/env python3
"""
Enhanced Facebook News Bot - Stability AI REST API Version
Automatically posts detailed Indian tech news with AI-generated images to a Facebook page.

Hardened version:
- Secrets loaded from environment variables / .env (never hardcoded)
- Retries with backoff + timeouts on every network call
- Real logging (console + rotating file) instead of print()
- Graceful degradation: falls back to a text-only post if image gen fails
- Content generator produces varied, less "templated" post copy across SIX
  distinct structural shapes (hot-take, question-hook, mini-story, stat-first,
  listicle, contrarian) -- not just varied wording inside one fixed skeleton
- Image cards use a real, relevant stock photo (Pexels, free tier). Query
  selection is now entity-aware: a small hint table translates recognizable
  companies/products/agencies in the headline (e.g. "Ola Electric", "ISRO",
  "Zomato") into concrete, photographable Pexels queries ("electric scooter
  showroom", "rocket launch pad", "food delivery bike rider") BEFORE falling
  back to the generic category query -- literal brand-name searches on a
  stock library mostly return nothing or unrelated logo mockups, so this is
  what actually gets a photo that looks like it belongs to the story instead
  of "generic tech photo #4". Photos are picked from several query variants,
  filtered by resolution, rendered with a vignette + legibility scrim and
  optional brand tag, at 2x supersampling then downscaled for crisp,
  high-definition text -- flat gradient card and AI photo providers remain
  as fallbacks.
- News extraction pulls from several topic-specific queries per run (not one
  broad query), scores candidates on source quality, freshness decay, and
  clickbait/press-release patterns, and actively rotates topic categories so
  the page doesn't post five AI stories in a row
- Config validation on startup so it fails fast with a clear message
"""

import os
import re
import sys
import json
import time
import glob
import uuid
import random
import signal
import logging
import argparse
import urllib.robotparser
from urllib.parse import urlparse
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from PIL import Image, ImageDraw, ImageFont, ImageFilter
import textwrap

try:
    import trafilatura  # optional -- enables full-article-text extraction
    HAS_TRAFILATURA = True
except ImportError:
    HAS_TRAFILATURA = False

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv is optional -- if it's not installed we just rely on real env vars
    pass


def _get_secret(name):
    """Resolve a secret from, in order: Colab's Secrets manager (the key icon
    in the left sidebar), then a regular environment variable. Never hardcode
    the value itself in the notebook or this file."""
    try:
        from google.colab import userdata  # only exists inside Colab
        try:
            val = userdata.get(name)
            if val:
                return val
        except Exception:
            pass  # secret not set in Colab's Secrets panel -- fall through
    except ImportError:
        pass  # not running in Colab
    return os.getenv(name)


# ========================================
# CONFIGURATION (from environment / Colab secrets only)
# ========================================
FB_PAGE_ACCESS_TOKEN = _get_secret("FB_PAGE_ACCESS_TOKEN")
FB_PAGE_ID = _get_secret("FB_PAGE_ID")
NEWS_API_KEY = _get_secret("NEWS_API_KEY")
STABILITY_API_KEY = _get_secret("STABILITY_API_KEY")
GROQ_API_KEY = _get_secret("GROQ_API_KEY")  # optional -- enables the smarter narrative writer
GEMINI_API_KEY = _get_secret("GEMINI_API_KEY")  # optional -- second free provider, also does free image gen
PEXELS_API_KEY = _get_secret("PEXELS_API_KEY")  # optional -- free-tier real stock photos for the news card
PAGE_BRAND_TAG = _get_secret("PAGE_BRAND_TAG")  # optional -- small brand label drawn on the card, e.g. "TechIndia Daily"

# Ordered fallback list of free Groq models -- tried in order, first success wins.
# Free-tier model rosters on Groq change without much notice (models get deprecated,
# renamed, or rate-limited differently), so hardcoding one model is fragile. If the
# top one 404s, is decommissioned, or gets rate-limited, we just try the next.
# Check console.groq.com/docs/models if all of these ever start failing at once.
GROQ_MODEL_FALLBACKS = [
    "llama-3.3-70b-versatile",   # best quality/reasoning of the free options
    "openai/gpt-oss-120b",       # strong general-purpose alternative
    "qwen/qwen3-32b",            # decent quality, different rate-limit bucket
    "llama-3.1-8b-instant",      # smallest/fastest, most generous rate limits -- last resort
]

# Gemini free tier, tried after Groq is exhausted -- a different provider entirely,
# so an outage/quota exhaustion on Groq's side doesn't take the whole feature down.
GEMINI_TEXT_MODEL_FALLBACKS = [
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]
GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"  # free image gen, ~500/day, no billing required

# ========================================
# NEWS CARD GRAPHICS
# ========================================
# Two card styles share one renderer:
#   1. Photo card (primary, if PEXELS_API_KEY is set): a real, topically
#      relevant stock photo with a dark legibility scrim + headline overlay.
#      This is what actually makes a scroll-stopping, "not obviously a bot"
#      looking post -- flat gradient cards read as template-y fast.
#   2. Gradient card (fallback): the original Pillow gradient background,
#      themed by category. Free, instant, unlimited, no network dependency
#      beyond a one-time font/photo download.
# Both are rendered at 2x supersampling then downscaled with LANCZOS, which
# is what gives noticeably crisper ("high-definition") text edges than
# drawing straight at output size.
CARD_W, CARD_H = 1200, 630
SUPERSAMPLE = 2

FONT_CACHE_DIR = os.path.join("/tmp/news_bot", "fonts")
FONT_URLS = {
    "bold": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Bold.ttf",
    "semibold": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-SemiBold.ttf",
    "medium": "https://raw.githubusercontent.com/google/fonts/main/ofl/poppins/Poppins-Medium.ttf",
}
SYSTEM_FONT_FALLBACK_PATTERNS = {
    "bold": ["DejaVuSans-Bold.ttf"],
    "semibold": ["DejaVuSans-Bold.ttf"],
    "medium": ["DejaVuSans.ttf"],
}

# (keywords to match in the title, kicker label shown on the card, top gradient
# color, bottom gradient color, Pexels search query) -- first match wins, checked
# in this order. The Pexels query is deliberately generic/visual (not literal
# headline text) since stock-photo search works far better on concepts than
# on specific proper nouns.
CATEGORY_THEMES = [
    (["ai", "artificial intelligence", "machine learning", "chatgpt", "llm", "openai"],
     "ARTIFICIAL INTELLIGENCE", (76, 29, 149), (124, 58, 237), "artificial intelligence technology"),
    (["startup", "funding", "raises", "valuation", "venture capital", "seed round"],
     "STARTUP & FUNDING", (154, 52, 18), (234, 88, 12), "startup office team"),
    (["fintech", "payment", "upi", "banking", "paytm", "credit"],
     "FINTECH", (6, 78, 59), (16, 185, 129), "digital payment finance"),
    (["ecommerce", "e-commerce", "retail", "shopping", "flipkart", "amazon", "myntra"],
     "E-COMMERCE", (131, 24, 67), (219, 39, 119), "online shopping delivery"),
    (["electric vehicle", "ev", "battery", "ola electric", "auto", "car"],
     "ELECTRIC & AUTO", (12, 74, 110), (14, 165, 233), "electric vehicle charging"),
    (["space", "isro", "satellite", "rocket", "spacex"],
     "SPACE", (30, 27, 75), (79, 70, 229), "rocket launch space"),
    (["mobile", "smartphone", "app", "gadget", "iphone", "android"],
     "MOBILE & GADGETS", (17, 94, 89), (20, 184, 166), "smartphone technology closeup"),
    (["healthcare", "medical", "health"],
     "HEALTHCARE", (127, 29, 29), (220, 38, 38), "healthcare technology hospital"),
]
DEFAULT_THEME = ("TECH NEWS", (30, 58, 138), (59, 130, 246), "technology india city")

# Last-resort Pexels queries if even the category query comes up empty/low-res --
# broad enough to almost always return something usable.
PEXELS_GENERIC_FALLBACKS = ["technology office India", "modern technology abstract"]

# ----------------------------------------------------------------------------
# ENTITY -> VISUAL QUERY HINTS
# ----------------------------------------------------------------------------
# Searching Pexels for a literal brand/agency name ("Ola Electric", "Zomato",
# "ISRO") mostly returns nothing, or generic office/logo-mockup filler --
# stock libraries are photographed around concepts, not corporate identities.
# This table maps a recognizable name in the headline to a concrete,
# *photographable* scene that's actually true to the story, so the picked
# photo looks like it belongs to this specific article rather than being an
# interchangeable "tech news #4" stock shot. Checked before the generic
# CATEGORY_THEMES query; first match wins. Deliberately keyed on lowercase
# word-boundary terms (matched with the same _kw_match helper used
# elsewhere) so it composes cleanly with everything else in the file.
ENTITY_VISUAL_HINTS = [
    (["ola electric", "ather", "electric scooter", "e-scooter"], "electric scooter showroom"),
    (["tata motors", "electric car", "ev charging"], "electric car charging station"),
    (["isro", "satellite", "rocket", "spacex", "chandrayaan", "gaganyaan"], "rocket launch pad"),
    (["zomato", "swiggy", "food delivery"], "food delivery bike rider city"),
    (["flipkart", "amazon", "myntra", "ecommerce", "e-commerce"], "warehouse delivery packages"),
    (["paytm", "upi", "phonepe", "digital payment", "fintech"], "mobile phone payment scan"),
    (["reliance jio", "jio", "airtel", "vodafone", "telecom", "5g"], "mobile network tower city"),
    (["chatgpt", "openai", "gemini ai", "llm", "generative ai"], "person using laptop chatbot"),
    (["nvidia", "chip", "semiconductor", "processor"], "computer chip circuit board macro"),
    (["ipo", "stock exchange", "nse", "bse", "sensex", "nifty"], "stock market trading screen"),
    (["layoffs", "hiring", "jobs"], "office desk laptop work"),
    (["data breach", "hack", "cyberattack", "cybersecurity"], "cybersecurity lock code screen"),
    (["drone"], "drone flying outdoor"),
    (["robot", "robotics", "automation"], "industrial robot arm factory"),
    (["smartphone", "iphone", "android phone"], "hand holding smartphone screen"),
    (["startup funding", "venture capital", "seed round", "series a", "series b"], "startup team meeting whiteboard"),
    (["hospital", "healthcare", "medical device", "telemedicine"], "hospital technology doctor"),
    (["agritech", "farmer", "agriculture"], "farmer field technology india"),
    (["edtech", "online learning", "e-learning"], "student laptop online class"),
]


def _entity_visual_query(title_lower):
    """Return the first matching concrete, photographable query for a
    recognizable name/topic in the headline, or None if nothing matches --
    caller falls back to the category-level query in that case."""
    for keywords, visual_query in ENTITY_VISUAL_HINTS:
        if any(_kw_match(title_lower, kw) for kw in keywords):
            return visual_query
    return None

# ========================================
# NEWS EXTRACTION -- topic buckets, quality signals
# ========================================
# One broad NewsAPI query systematically under-covers whole categories --
# whichever generic terms happen to trend on a given day dominate all 20
# results, so space/fintech/EV stories rarely surface even though they're
# exactly the kind of thing CATEGORY_THEMES is built to showcase. Querying
# each topic bucket separately and merging the results gives real category
# coverage instead of one algorithm's idea of "trending". Kept small enough
# (7 buckets x ~4 runs/day = well under NewsAPI's 100 req/day free cap).
NEWS_QUERY_BUCKETS = [
    "India artificial intelligence OR India AI OR ChatGPT India",
    "Indian startup funding OR India venture capital raises",
    "India fintech OR UPI OR digital payments India",
    "India ecommerce OR Flipkart OR Amazon India OR Myntra",
    "India electric vehicle OR EV India OR Ola Electric",
    "ISRO OR India space OR India satellite launch",
    "Digital India OR Indian technology innovation",
]
NEWS_BUCKET_PAGE_SIZE = 8

# Outlets whose reporting tends to be substantive rather than aggregated/
# re-churned -- a small nudge toward stories worth reading, not a hard filter.
SOURCE_QUALITY_BONUS = {
    "techcrunch": 3, "the verge": 2, "reuters": 3, "bloomberg": 3,
    "economic times": 2, "the economic times": 2, "livemint": 2, "mint": 2,
    "moneycontrol": 2, "inc42": 2, "yourstory": 2, "business standard": 2,
    "hindustan times": 1, "times of india": 1, "ndtv": 1, "the hindu": 2,
    "financial express": 1, "cnbc": 2, "forbes india": 2,
}
# Wire-service press-release feeds -- technically "news", but almost always
# unedited PR copy rather than reported stories. Downweighted, not excluded,
# since occasionally the underlying announcement is genuinely the story.
LOW_QUALITY_SOURCE_PENALTY = {
    "prnewswire": 4, "businesswire": 4, "globenewswire": 4, "einpresswire": 3,
}
# Classic clickbait framing -- gets penalized hard rather than excluded
# outright, since NewsAPI descriptions sometimes trip these on legitimate
# stories (e.g. a headline genuinely about a "shocking" fraud case).
CLICKBAIT_PATTERN = re.compile(
    r"you won.?t believe|shocking|jaw-dropping|goes viral|top \d+ (?:reasons|ways|tips)|"
    r"this is why|number \d+ will|what happened next",
    re.IGNORECASE,
)
MAX_ARTICLE_AGE_HOURS = 72  # older than this reads as stale even if never posted before

# ========================================
# TOPIC ENGAGEMENT SCORING
# ========================================
# Weighted keywords used to rank same-day candidate articles by how likely
# they are to actually get engagement -- recognizable names, funding/launch
# events, and numbers in the headline consistently outperform generic
# coverage. Used to pick WHICH of the ~20 fetched articles to post, not to
# fabricate anything about it.
ENGAGEMENT_KEYWORDS = {
    "openai": 3, "chatgpt": 3, "gemini": 2, "google": 2, "meta": 2, "apple": 2,
    "microsoft": 2, "amazon": 2, "tesla": 2, "nvidia": 2, "spacex": 2,
    "reliance": 2, "tata": 2, "adani": 2, "flipkart": 2, "zomato": 2,
    "swiggy": 2, "paytm": 2, "ola": 2, "infosys": 1, "tcs": 1, "wipro": 1,
    "isro": 3, "funding": 2, "raises": 3, "valuation": 2, "ipo": 3,
    "acquires": 2, "acquisition": 2, "unveils": 1, "launches": 1,
    "record": 2, "billion": 2, "crore": 1, "layoffs": 2, "ban": 2,
    "hack": 2, "breach": 2, "ai": 2, "artificial intelligence": 2,
    "robot": 1, "self-driving": 2,
}

POSTED_FILE = os.getenv("POSTED_FILE", "posted_articles.json")
RECENT_CATEGORY_WINDOW = 4  # how many past posts count toward the "don't repeat this category" penalty


def _kw_match(text_lower, keyword):
    """Word-boundary keyword match -- plain substring checks are dangerous
    with short keywords: 'ai' matches inside 'raises', 'said', 'maintain';
    'ban' matches inside 'banking', 'urban'; 'app' matches inside 'Apple',
    'happy'. Requires the keyword not be immediately preceded/followed by
    another alphanumeric character, which correctly handles multi-word
    keywords and keywords with internal hyphens too."""
    pattern = re.compile(r'(?<![a-z0-9])' + re.escape(keyword.strip()) + r'(?![a-z0-9])')
    return bool(pattern.search(text_lower))


LOG_FILE = os.getenv("LOG_FILE", "news_bot.log")
REQUEST_TIMEOUT = 30  # seconds, applied to every outbound call

REQUIRED_VARS = {
    "FB_PAGE_ACCESS_TOKEN": FB_PAGE_ACCESS_TOKEN,
    "FB_PAGE_ID": FB_PAGE_ID,
    "NEWS_API_KEY": NEWS_API_KEY,
}
# STABILITY_API_KEY / PEXELS_API_KEY are intentionally NOT required -- image
# generation falls back through Pexels -> local gradient card -> Stability ->
# Gemini -> Pollinations.ai, the last of which needs no key at all. Posting
# still works with zero image providers configured; it just goes out as a
# text-only post.


# ========================================
# LOGGING
# ========================================
logger = logging.getLogger("news_bot")
logger.setLevel(logging.INFO)

_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", "%H:%M:%S"))

_file = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
_file.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))

logger.addHandler(_console)
logger.addHandler(_file)


def validate_config():
    """Fail fast and clearly if secrets are missing, instead of failing deep in a request."""
    missing = [name for name, val in REQUIRED_VARS.items() if not val]
    if missing:
        logger.error("Missing required environment variables: %s", ", ".join(missing))
        logger.error("Set them in your shell or in a .env file (see .env.example). Exiting.")
        raise SystemExit(1)
    if not PEXELS_API_KEY:
        logger.info(
            "PEXELS_API_KEY not set -- news cards will use flat gradient backgrounds instead of "
            "real stock photos. Get a free key at pexels.com/api for noticeably more eye-catching cards."
        )


def make_session(total_retries=3, backoff_factor=1.5):
    """A requests session that automatically retries GET requests on transient
    failures (connection errors, 429, and 5xx) with exponential backoff.

    POST is deliberately NOT auto-retried (this matches urllib3's own default
    -- POST isn't idempotent). Retrying a POST blindly is dangerous for the
    Facebook publish call specifically: if the response times out AFTER
    Facebook has already created the post server-side, an automatic retry
    would publish the same content twice. For the AI/image-generation POSTs,
    we already have multi-model/multi-provider fallback chains that handle
    failures more intelligently than a blind retry-the-same-request would
    anyway, so nothing is lost by excluding POST here."""
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],  # POST intentionally excluded -- see docstring
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


# ========================================
# POST SHAPES
# ========================================
# The single biggest AI "tell" isn't word choice, it's structure: opener ->
# headline -> description -> analysis -> closer -> source/date -> link ->
# hashtags, every single time. Six distinct shapes below vary *what comes
# first*, *what's omitted*, and *what the LLM is asked to produce* so
# consecutive posts don't share a recognizable skeleton. Each shape has a
# weight (relative pick frequency) and its own narrative instruction that
# gets appended to the persona system prompt.
POST_SHAPES = {
    "hot_take": {
        "weight": 3,
        "narrative_instruction": (
            "Structure: open with a single blunt one-line verdict on this news -- your take, stated "
            "plainly, no hedging ('This is a big deal because...' / 'Don't buy the hype on this one.' "
            "style, but in your own words). Then, in the same flow, back it up with 2-3 sentences of "
            "concrete reasoning grounded in the source material. Do not restate the headline as your "
            "opening line."
        ),
    },
    "question_hook": {
        "weight": 2,
        "narrative_instruction": (
            "Structure: open with one genuine, specific question a reader would actually wonder about "
            "this story (not a rhetorical throwaway) -- e.g. what it means for a specific group, whether "
            "it will actually work, who benefits. Then answer it directly using the source material. "
            "The question must be answerable by what follows, not just a hook that goes nowhere."
        ),
    },
    "mini_story": {
        "weight": 2,
        "narrative_instruction": (
            "Structure: write it as a compact narrative arc -- what happened, then the immediate "
            "complication or consequence, then why it matters. Use plain sequencing (first this, then "
            "this) rather than a list. Keep it grounded strictly in the source material."
        ),
    },
    "stat_first": {
        "weight": 2,
        "narrative_instruction": (
            "Structure: the reader will see a standalone number/stat pulled from this story right "
            "before your text, so do NOT repeat that exact figure as your opening words -- start instead "
            "by explaining what that number actually means in practice, then broaden into the fuller "
            "story."
        ),
    },
    "listicle": {
        "weight": 2,
        "narrative_instruction": (
            "Structure: produce exactly three short, punchy takeaways about this story (each under 16 "
            "words, each a complete standalone thought, no numbering yourself). Separate the three "
            "takeaways with ' || ' and nothing else -- no intro line, no closing line, just "
            "'takeaway one || takeaway two || takeaway three'."
        ),
    },
    "contrarian": {
        "weight": 1,
        "narrative_instruction": (
            "Structure: open by naming, in one short clause, the obvious/expected reaction to this news "
            "-- then pivot hard with 'but' or 'except' or 'actually' into the less obvious angle that "
            "the obvious reaction misses. The pivot is the whole point; don't bury it."
        ),
    },
}

STAT_PATTERN = re.compile(
    r'(\$\s?\d[\d,]*(?:\.\d+)?\s?(?:million|billion|m|bn|k)?|'
    r'₹\s?\d[\d,]*(?:\.\d+)?\s?(?:crore|lakh|cr)?|'
    r'\d[\d,]*(?:\.\d+)?\s?%|'
    r'\d[\d,]*(?:\.\d+)?\s?(?:million|billion|crore|lakh|cities|users|million users))',
    re.IGNORECASE,
)


# ========================================
# ENHANCED NEWS BOT CLASS
# ========================================
class EnhancedNewsBot:
    def __init__(self):
        self.session = make_session()
        self.posted_today = self.load_posted_articles()
        self._stop = False
        self._font_paths = None   # resolved lazily on first card render
        self._font_cache = {}     # (weight, size) -> ImageFont instance

        # --- Persona voices for LLM-generated content -------------------
        # Randomly picking a persona per post is what actually kills the
        # "sounds like AI" sameness -- one fixed system prompt produces one
        # fixed cadence no matter how creative the instructions sound.
        self.personas = [
            "a sharp, slightly skeptical industry analyst who's seen a hundred of these announcements and calls out hype when the substance doesn't match it",
            "a curious explainer who gets genuinely excited about how things work and wants the reader to understand the mechanics, not just the headline",
            "a dry, understated observer who prefers a clean concrete detail over an adjective, and lets irony do the work instead of exclamation points",
            "a builder/engineer type who cares about implementation reality -- what's actually hard here, what could break, what's genuinely new engineering-wise",
            "a market-watcher who instinctively connects this to money, competition, and who wins or loses from it",
            "a plainspoken local reporter who explains why this actually matters to an ordinary reader in India, not to investors",
        ]

        # Phrases and patterns that are dead giveaways of generic LLM output.
        # Naming them explicitly and telling the model to avoid them works
        # far better than vague instructions like "be creative."
        self.banned_patterns = [
            "In today's fast-paced world", "In the ever-evolving landscape",
            "Moreover", "Furthermore", "It's worth noting that", "In conclusion",
            "Let's dive in", "unpack", "game-changer", "revolutionize", "revolutionary",
            "not only... but also", "In an exciting development", "as we navigate",
            "This begs the question", "at the end of the day", "when it comes to",
            "In a world where", "It goes without saying", "signals a shift",
            "underscores the importance", "paves the way", "stay tuned",
        ]

        # --- Fallback pools (used only if BOTH Groq and Gemini fail/aren't
        # configured) -- last line of defense so a post still goes out. ---
        self.openers = [
            "Here's something worth your attention today.",
            "Quick one for the tech-watchers in the room:",
            "This crossed our feed and it's worth a closer look.",
            "Not every headline moves the needle -- this one might.",
            "Catching up on India's tech scene? Start here.",
            None,  # sometimes just start with the headline, no opener
        ]

        self.analysis_pool = [
            "The bigger question is how fast this scales beyond the pilot stage -- announcements are cheap, execution is where most of these stories actually get decided.",
            "What's notable isn't the headline number so much as the timing: it lands right as the broader market is recalibrating expectations around this exact space.",
            "Worth watching whether this holds up under real-world load, or whether it's the kind of announcement that ages well mostly in the press release.",
            "The interesting part is second-order: who has to react to this, and how quickly, to stay competitive.",
            "It's easy to read this as isolated news. It's more useful read alongside everything else happening in the same sector this quarter.",
            "This is one of those developments where the near-term reaction and the long-term impact will probably look pretty different a year from now.",
        ]

        self.closers = [
            "Curious what people closer to this space think -- drop a comment.",
            "Would love to hear if this matches what you're seeing on the ground.",
            "Save this one if you want to track how it plays out.",
            "Feel free to share this with someone who'd find it useful.",
            "What's your read on this -- overhyped or genuinely big?",
            None,
        ]

        self.topic_emojis = {
            'ai': '🤖', 'artificial intelligence': '🤖', 'machine learning': '🧠',
            'startup': '🚀', 'funding': '💰', 'investment': '💵',
            'mobile': '📱', 'smartphone': '📱', 'app': '📲',
            'internet': '🌐', 'online': '💻', 'digital': '💻',
            'fintech': '💳', 'payment': '💳', 'banking': '🏦',
            'ecommerce': '🛒', 'shopping': '🛍️', 'retail': '🏪',
            'electric': '⚡', 'vehicle': '🚗', 'car': '🚗',
            'space': '🚀', 'satellite': '🛰️', 'rocket': '🚀',
            'healthcare': '🏥', 'medical': '💊', 'health': '❤️',
            'education': '📚', 'learning': '🎓', 'student': '👨‍🎓',
        }

        self.hashtag_pool = [
            "#TechIndia", "#IndianTech", "#Innovation", "#DigitalIndia",
            "#TechNews", "#IndianStartup", "#Technology", "#StartupIndia",
            "#India", "#Trending",
        ]

        if not HAS_TRAFILATURA:
            logger.info("trafilatura not installed -- full-article scraping disabled, "
                        "will use NewsAPI descriptions only. `pip install trafilatura` to enable it.")

    # -------------------- dedup --------------------
    @staticmethod
    def _normalize_title(title):
        """Lowercase, strip punctuation/whitespace so near-duplicate titles
        (different casing, trailing '...', re-crawled with a tweaked headline)
        still dedup correctly."""
        return re.sub(r'[^a-z0-9]+', ' ', title.lower()).strip()

    def _dedup_key(self, article):
        """Title alone isn't a reliable dedup key -- outlets republish the same
        story with slightly different headlines, and NewsAPI itself sometimes
        returns the same article twice with different casing. Combine
        normalized title + URL for a much sturdier key."""
        norm_title = self._normalize_title(article.get('title', ''))
        url = (article.get('url') or '').split('?')[0].rstrip('/')
        return f"{norm_title}::{url}"

    @staticmethod
    def _posted_display(item):
        """Human-readable line for --stats output -- handles both the new
        dict entries (with category/date) and old plain-string entries."""
        if isinstance(item, dict):
            cat = f" [{item['category']}]" if item.get('category') else ""
            return f"{item.get('title', item.get('key', '?'))[:60]}{cat}"
        return f"{str(item)[:60]}"

    def _posted_keys(self):
        """Dedup keys regardless of storage format -- old entries are plain
        strings (pre-category-tracking), new entries are dicts. Keeping both
        readable means upgrading the script never causes re-posts of
        already-published stories."""
        keys = set()
        for item in self.posted_today:
            if isinstance(item, dict):
                keys.add(item.get('key', ''))
                if item.get('title'):
                    keys.add(item['title'])
            else:
                keys.add(item)
        return keys

    def _recent_categories(self, n=RECENT_CATEGORY_WINDOW):
        """Category labels (e.g. 'ARTIFICIAL INTELLIGENCE') from the last n
        posts that have category data recorded. Old string-only entries are
        silently skipped -- they predate category tracking."""
        cats = [item.get('category') for item in self.posted_today if isinstance(item, dict) and item.get('category')]
        return cats[-n:]

    def _already_posted(self, article):
        key = self._dedup_key(article)
        title = article.get('title', '').strip()
        posted = self._posted_keys()
        # also check the raw title against older entries saved before this
        # dedup scheme existed, so upgrading the script doesn't cause re-posts
        return key in posted or title in posted

    def _engagement_score(self, article):
        """Rank candidate articles by how likely they are to actually catch
        attention -- recognizable names, funding/launch/record-type events,
        and numbers in the headline consistently outperform generic coverage,
        with adjustments for source quality, freshness, and topic diversity
        against what the page just posted. This decides WHICH already-fetched
        candidate to post, never anything about the content itself."""
        title_lower = article.get('title', '').lower()
        score = sum(weight for kw, weight in ENGAGEMENT_KEYWORDS.items() if _kw_match(title_lower, kw))
        if any(ch.isdigit() for ch in title_lower):
            score += 1  # "raises $200M", "40 cities" -- concrete numbers read as more credible/catchy
        if len(article.get('title', '')) < 70:
            score += 1  # short punchy headlines work better as a card title

        source_name = (article.get('source', {}).get('name') or '').lower()
        for name, bonus in SOURCE_QUALITY_BONUS.items():
            if name in source_name:
                score += bonus
                break
        for name, penalty in LOW_QUALITY_SOURCE_PENALTY.items():
            if name in source_name:
                score -= penalty
                break

        age = self._article_age_hours(article)
        if age is not None:
            if age <= 6:
                score += 2
            elif age <= 12:
                score += 1
            elif age > 48:
                score -= 2

        category, _, _, _ = self._category_theme(article.get('title', ''))
        recent = self._recent_categories()
        if recent:
            repeats = recent.count(category)
            score -= repeats * 2  # each recent repeat of this exact category makes it less attractive to post again

        return score

    def _pick_candidate(self, candidates):
        """Bias toward the highest-scoring stories without being 100%
        deterministic. Always picking the single top-scored article means
        that on days with a runaway headline, every run for hours posts
        about the same predictable pick and the page's feed starts to feel
        formulaic in *what* it covers, not just how it's worded. Weighted-
        random over the top few keeps it genuinely curated (still skewed
        hard toward the most engaging stories) while adding real variety."""
        ranked = sorted(candidates, key=self._engagement_score, reverse=True)
        pool = ranked[:min(3, len(ranked))]
        weights = [self._engagement_score(a) + 1 for a in pool]  # +1 so a zero-score article is still pickable
        choice = random.choices(pool, weights=weights, k=1)[0]
        logger.info(
            "Weighted pick from top %d candidate(s) (scores=%s): %s",
            len(pool), [self._engagement_score(a) for a in pool], choice.get('title', '')[:70],
        )
        return choice

    # -------------------- persistence --------------------
    def load_posted_articles(self):
        if os.path.exists(POSTED_FILE):
            try:
                with open(POSTED_FILE, "r") as f:
                    data = json.load(f)
                    return data if isinstance(data, list) else []
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Could not read %s (%s) -- starting fresh.", POSTED_FILE, e)
                return []
        return []

    def save_posted_articles(self):
        try:
            tmp_path = POSTED_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self.posted_today, f)
            os.replace(tmp_path, POSTED_FILE)  # atomic write, avoids corruption mid-crash
        except OSError as e:
            logger.error("Failed to save posted-articles file: %s", e)

    # -------------------- news fetching --------------------
    def _fetch_bucket(self, query):
        """Fetch one topic-bucket query. Isolated so one bucket's failure
        (rate limit, transient error) doesn't take down the whole run --
        the other buckets still contribute candidates."""
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': query,
                'apiKey': NEWS_API_KEY,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': NEWS_BUCKET_PAGE_SIZE,
                'excludeDomains': 'reddit.com',
            }
            resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 401:
                logger.error("NewsAPI rejected the API key (401). Check NEWS_API_KEY.")
                return None  # signal "stop trying" -- a bad key fails every bucket identically
            if resp.status_code == 429:
                logger.warning("NewsAPI rate limit hit on bucket %r -- skipping this bucket.", query[:40])
                return []
            resp.raise_for_status()
            return resp.json().get('articles', [])

        except requests.exceptions.Timeout:
            logger.warning("NewsAPI request timed out on bucket %r.", query[:40])
        except requests.exceptions.RequestException as e:
            logger.warning("NewsAPI request failed on bucket %r: %s", query[:40], e)
        except (ValueError, KeyError) as e:
            logger.warning("Unexpected NewsAPI response format on bucket %r: %s", query[:40], e)
        return []

    @staticmethod
    def _is_low_quality_title(title):
        """Clickbait framing gets filtered outright (not just penalized) --
        it doesn't match the page's voice regardless of how the story scores
        otherwise. ALL-CAPS titles (excluding short acronym-only headlines)
        are usually spammy aggregator re-posts."""
        if CLICKBAIT_PATTERN.search(title):
            return True
        letters = [c for c in title if c.isalpha()]
        if len(letters) > 12 and sum(1 for c in letters if c.isupper()) / len(letters) > 0.7:
            return True
        return False

    def _article_age_hours(self, article):
        try:
            published = datetime.fromisoformat(article.get('publishedAt', '').replace('Z', '+00:00'))
            now = datetime.now(published.tzinfo)
            return (now - published).total_seconds() / 3600
        except (ValueError, TypeError):
            return None

    def get_indian_tech_news(self):
        """Pulls from several topic-specific queries (see NEWS_QUERY_BUCKETS)
        instead of one broad query, merges + dedups by URL, and filters out
        stale, malformed, or clickbait/press-release-flavored results before
        they ever reach scoring."""
        seen_urls = set()
        merged = []
        for query in NEWS_QUERY_BUCKETS:
            articles = self._fetch_bucket(query)
            if articles is None:
                return []  # bad API key -- no point hitting the remaining buckets
            for a in articles:
                url = (a.get('url') or '').split('?')[0].rstrip('/')
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    merged.append(a)

        filtered = []
        for a in merged:
            title = a.get('title', '')
            if not title or title == '[Removed]':
                continue
            if not a.get('description') or len(a.get('description', '')) <= 50:
                continue
            if not a.get('url'):
                continue
            if self._is_low_quality_title(title):
                continue
            age = self._article_age_hours(a)
            if age is not None and age > MAX_ARTICLE_AGE_HOURS:
                continue
            filtered.append(a)

        logger.info(
            "Found %d usable article(s) after merging %d bucket queries and filtering (raw merged=%d).",
            len(filtered), len(NEWS_QUERY_BUCKETS), len(merged),
        )
        return filtered

    # -------------------- full article scraping (optional, best-effort) --------------------
    SCRAPER_USER_AGENT = "Mozilla/5.0 (compatible; NewsBot/1.0; +https://example.com/bot)"

    def _robots_allow(self, url):
        """Politely check robots.txt before scraping. Defaults to allowing
        the fetch if robots.txt is unreachable or malformed -- we don't want
        a broken robots.txt on some random news site to break the whole run."""
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch(self.SCRAPER_USER_AGENT, url)
        except Exception:
            return True

    def fetch_full_article_text(self, url):
        """Best-effort fetch of the actual article body, so the LLM has real
        substance to work with instead of a 200-character NewsAPI snippet.
        Returns None on ANY failure (blocked, paywalled, timeout, parse error,
        non-HTML content, trafilatura not installed) -- this is a quality
        enhancement, never a requirement for posting."""
        if not HAS_TRAFILATURA or not url:
            return None
        try:
            if not self._robots_allow(url):
                logger.info("robots.txt disallows scraping %s -- using description only.", url)
                return None

            headers = {"User-Agent": self.SCRAPER_USER_AGENT}
            resp = self.session.get(url, headers=headers, timeout=15, stream=True)
            if resp.status_code != 200:
                return None

            content_type = resp.headers.get("Content-Type", "")
            if "html" not in content_type.lower():
                logger.info("Non-HTML content-type %r at %s -- skipping scrape.", content_type, url)
                return None

            # Cap how much we read -- some pages are enormous, and we only need
            # the article body, not megabytes of surrounding page weight.
            MAX_BYTES = 3_000_000
            raw = resp.raw.read(MAX_BYTES + 1, decode_content=True)
            if len(raw) > MAX_BYTES:
                logger.info("Page at %s exceeds size cap -- skipping scrape.", url)
                return None
            html = raw.decode(resp.encoding or "utf-8", errors="ignore")

            extracted = trafilatura.extract(html, include_comments=False, include_tables=False)
            if not extracted or len(extracted) < 200:
                return None
            return extracted[:4000]  # cap length -- plenty of context, keeps API calls cheap and fast

        except requests.exceptions.RequestException:
            return None  # many sites block scrapers (403) -- expected, not an error worth logging loudly
        except Exception as e:
            logger.info("Article scraping failed for %s (%s) -- using description only.", url, e)
            return None

    # -------------------- image generation: AI photo providers (last resort) --------------------
    def _save_image_bytes(self, content, source_label):
        """Shared validation + save logic for any image provider's response."""
        os.makedirs("/tmp/news_bot", exist_ok=True)
        if len(content) < 1000:
            logger.error("%s image response is suspiciously small (%d bytes) -- treating as failure.", source_label, len(content))
            return None
        img_path = f"/tmp/news_bot/img_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
        with open(img_path, "wb") as f:
            f.write(content)
        logger.info("%s image saved: %s (%d bytes)", source_label, img_path, len(content))
        return img_path

    def _stability_image(self, prompt):
        if not STABILITY_API_KEY:
            return None
        try:
            url = "https://api.stability.ai/v2beta/stable-image/generate/core"
            headers = {"Authorization": f"Bearer {STABILITY_API_KEY}", "Accept": "image/*"}
            files = {
                "prompt": (None, prompt),
                "output_format": (None, "png"),
                "aspect_ratio": (None, "16:9"),
            }
            resp = self.session.post(url, headers=headers, files=files, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 401:
                logger.error("Stability AI rejected the API key (401). Check STABILITY_API_KEY.")
                return None
            if resp.status_code == 429:
                logger.warning("Stability AI rate limit/credits hit -- trying next image provider.")
                return None
            if resp.status_code != 200:
                logger.warning("Stability AI error %s: %s -- trying next image provider.", resp.status_code, resp.text[:300])
                return None
            if not resp.headers.get("Content-Type", "").startswith("image/"):
                logger.warning("Stability AI returned non-image content-type -- trying next image provider.")
                return None

            return self._save_image_bytes(resp.content, "Stability AI")
        except requests.exceptions.Timeout:
            logger.warning("Stability AI request timed out -- trying next image provider.")
        except requests.exceptions.RequestException as e:
            logger.warning("Stability AI request failed (%s) -- trying next image provider.", e)
        return None

    def _gemini_image(self, prompt):
        """Free image gen via Gemini's Nano Banana model -- roughly 500/day
        free, no billing required. Response comes back as inline base64."""
        if not GEMINI_API_KEY:
            return None
        try:
            import base64
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_IMAGE_MODEL}:generateContent"
            resp = self.session.post(
                url,
                headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
                timeout=REQUEST_TIMEOUT,
            )
            if resp.status_code in (401, 403):
                logger.error("Gemini rejected the API key (%s) for image gen. Check GEMINI_API_KEY.", resp.status_code)
                return None
            if resp.status_code == 429:
                logger.warning("Gemini image quota hit -- trying next image provider.")
                return None
            if resp.status_code != 200:
                logger.warning("Gemini image error %s: %s -- trying next image provider.", resp.status_code, resp.text[:200])
                return None

            parts = resp.json()["candidates"][0]["content"]["parts"]
            for part in parts:
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    image_bytes = base64.b64decode(inline["data"])
                    return self._save_image_bytes(image_bytes, "Gemini")
            logger.warning("Gemini response had no image data -- trying next image provider.")
        except requests.exceptions.Timeout:
            logger.warning("Gemini image request timed out -- trying next image provider.")
        except (requests.exceptions.RequestException, KeyError, IndexError, ValueError) as e:
            logger.warning("Gemini image call failed (%s) -- trying next image provider.", e)
        return None

    def _pollinations_image(self, prompt):
        """Completely free, keyless image generation -- no signup at all.
        Last-resort provider: lower control over quality/consistency than
        Stability or Gemini, but it means the bot can still ship an image
        even if you've configured zero paid/keyed image providers."""
        try:
            import urllib.parse as _urlparse
            encoded = _urlparse.quote(prompt)
            url = f"https://image.pollinations.ai/prompt/{encoded}"
            params = {"width": 1280, "height": 720, "nologo": "true"}
            resp = self.session.get(url, params=params, timeout=45)  # this provider can be slow under load

            if resp.status_code != 200:
                logger.warning("Pollinations error %s -- no image provider succeeded, falling back to text-only.", resp.status_code)
                return None
            if not resp.headers.get("Content-Type", "").startswith("image/"):
                logger.warning("Pollinations returned non-image content -- falling back to text-only.")
                return None

            return self._save_image_bytes(resp.content, "Pollinations")
        except requests.exceptions.Timeout:
            logger.warning("Pollinations request timed out -- falling back to text-only.")
        except requests.exceptions.RequestException as e:
            logger.warning("Pollinations request failed (%s) -- falling back to text-only.", e)
        return None

    # -------------------- real stock photo (Pexels, free tier) --------------------
    MIN_PHOTO_WIDTH = 1600  # below this, upscaling to fill the 2x-supersampled card looks soft

    def _pexels_search_once(self, query):
        """One Pexels search call. Returns a list of photo objects (possibly
        empty) or None on a hard failure (bad key / rate limit) that should
        stop further query attempts this run."""
        try:
            resp = self.session.get(
                "https://api.pexels.com/v1/search",
                headers={"Authorization": PEXELS_API_KEY},
                params={"query": query, "orientation": "landscape", "size": "large", "per_page": 8},
                timeout=15,
            )
            if resp.status_code == 401:
                logger.error("Pexels rejected the API key (401). Check PEXELS_API_KEY.")
                return None
            if resp.status_code == 429:
                logger.warning("Pexels rate limit hit -- falling back to gradient card.")
                return None
            if resp.status_code != 200:
                logger.warning("Pexels search error %s on query %r.", resp.status_code, query)
                return []
            return resp.json().get("photos", [])
        except requests.exceptions.Timeout:
            logger.warning("Pexels request timed out on query %r.", query)
        except (requests.exceptions.RequestException, ValueError, KeyError) as e:
            logger.warning("Pexels lookup failed on query %r (%s).", query, e)
        return []

    def _pexels_photo(self, queries):
        """Try each query in `queries` (most specific first) until one
        returns a usable, sufficiently high-resolution photo. A single fixed
        category query often comes up empty or low-res for narrower topics --
        trying a couple of fallbacks (entity-specific first, then still
        on-topic, then a generic tech/India shot as a last resort)
        meaningfully raises the hit rate for a real, *relevant* photo instead
        of falling through to the flat gradient card. Returns raw image
        bytes, or None if every query comes up empty."""
        if not PEXELS_API_KEY:
            return None
        for query in queries:
            if not query:
                continue
            photos = self._pexels_search_once(query)
            if photos is None:
                return None  # bad key / rate limit -- no point trying more queries
            good = [p for p in photos if p.get("width", 0) >= self.MIN_PHOTO_WIDTH]
            pool = good or photos
            if not pool:
                continue

            # Among qualifying photos, mildly prefer higher resolution but
            # keep some randomness so the same story-category doesn't always
            # surface the identical stock photo run after run.
            pool.sort(key=lambda p: p.get("width", 0), reverse=True)
            top_pool = pool[:min(4, len(pool))]
            photo = random.choice(top_pool)
            src_url = photo.get("src", {}).get("large2x") or photo.get("src", {}).get("large")
            if not src_url:
                continue

            try:
                img_resp = self.session.get(src_url, timeout=20)
                if img_resp.status_code != 200:
                    continue
                if not img_resp.headers.get("Content-Type", "").startswith("image/"):
                    continue
                logger.info("Pexels photo matched on query %r (%dpx wide).", query, photo.get("width", 0))
                return img_resp.content
            except requests.exceptions.Timeout:
                logger.warning("Pexels image download timed out for query %r.", query)
                continue
            except requests.exceptions.RequestException:
                continue
        return None

    # -------------------- shared card rendering --------------------
    def _resolve_fonts(self):
        """Download and cache the Poppins font files once per process. Any
        font that fails to download gets a None entry, so _font() knows to
        search for a system fallback instead of crashing."""
        if self._font_paths is not None:
            return self._font_paths
        paths = {}
        try:
            os.makedirs(FONT_CACHE_DIR, exist_ok=True)
        except OSError:
            pass
        for weight, url in FONT_URLS.items():
            local_path = os.path.join(FONT_CACHE_DIR, os.path.basename(url))
            try:
                if not os.path.exists(local_path):
                    resp = self.session.get(url, timeout=10)
                    resp.raise_for_status()
                    with open(local_path, "wb") as f:
                        f.write(resp.content)
                paths[weight] = local_path
            except Exception as e:
                logger.info("Could not fetch %s font (%s) -- will use a system font instead.", weight, e)
                paths[weight] = None
        self._font_paths = paths
        return paths

    def _find_system_font(self, patterns):
        for base_dir in ("/usr/share/fonts", "/usr/local/share/fonts", "/usr/local/lib"):
            for pattern in patterns:
                matches = glob.glob(os.path.join(base_dir, "**", pattern), recursive=True)
                if matches:
                    return matches[0]
        return None

    def _font(self, weight, size):
        key = (weight, size)
        if key in self._font_cache:
            return self._font_cache[key]
        path = self._resolve_fonts().get(weight)
        if not path:
            path = self._find_system_font(SYSTEM_FONT_FALLBACK_PATTERNS.get(weight, ["DejaVuSans-Bold.ttf"]))
        try:
            font = ImageFont.truetype(path, size) if path else ImageFont.load_default()
        except Exception:
            font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    @staticmethod
    def _gradient_image(w, h, top_rgb, bottom_rgb):
        img = Image.new("RGB", (w, h), top_rgb)
        draw = ImageDraw.Draw(img)
        for y in range(h):
            t = y / h
            r = int(top_rgb[0] + (bottom_rgb[0] - top_rgb[0]) * t)
            g = int(top_rgb[1] + (bottom_rgb[1] - top_rgb[1]) * t)
            b = int(top_rgb[2] + (bottom_rgb[2] - top_rgb[2]) * t)
            draw.line([(0, y), (w, y)], fill=(r, g, b))
        return img

    @staticmethod
    def _cover_fit(img, w, h):
        """Resize+crop a photo to exactly fill w x h (like CSS
        background-size: cover), so real photos of any aspect ratio slot
        cleanly into the card without distortion or letterboxing."""
        src_ratio = img.width / img.height
        dst_ratio = w / h
        if src_ratio > dst_ratio:
            new_h = h
            new_w = int(h * src_ratio)
        else:
            new_w = w
            new_h = int(w / src_ratio)
        img = img.resize((new_w, new_h), Image.LANCZOS)
        left = (new_w - w) // 2
        top = (new_h - h) // 2
        return img.crop((left, top, left + w, top + h))

    def _category_theme(self, title):
        text_lower = title.lower()
        for keywords, label, top_rgb, bottom_rgb, pexels_query in CATEGORY_THEMES:
            if any(_kw_match(text_lower, kw) for kw in keywords):
                return label, top_rgb, bottom_rgb, pexels_query
        return DEFAULT_THEME

    def _fit_headline(self, draw, text, max_width, max_lines, start_size, min_size):
        """Shrinks the font until the headline wraps within max_lines, so a
        short punchy title renders big and a long one still fits cleanly
        instead of overflowing the card."""
        size = start_size
        while size >= min_size:
            font = self._font("bold", size)
            avg_char_w = draw.textlength("Xg", font=font) / 2 or 1
            wrap_width = max(10, int(max_width / avg_char_w))
            wrapped = textwrap.wrap(text, width=wrap_width)
            if len(wrapped) <= max_lines:
                return font, wrapped
            size -= 4
        font = self._font("bold", min_size)
        avg_char_w = draw.textlength("Xg", font=font) / 2 or 1
        wrap_width = max(10, int(max_width / avg_char_w))
        wrapped = textwrap.wrap(text, width=wrap_width)[:max_lines]
        if wrapped:
            wrapped[-1] = wrapped[-1].rstrip() + "…"
        return font, wrapped

    def _render_card(self, base_img, title, kicker, accent_rgb, source, date_str, scrim=False):
        """Draw the pill + headline + footer onto a base image that is
        already at supersampled (2x) resolution, then downscale to the final
        1200x630 with LANCZOS for crisp anti-aliased text. `base_img` is
        either a gradient background or a real photo (already cover-fit);
        when it's a photo, `scrim=True` adds a bottom-up dark gradient first
        so white text stays legible over unpredictable image content."""
        W, H = base_img.width, base_img.height
        scale = W / CARD_W  # supersample factor, so all pixel constants below stay proportional
        img = base_img.convert("RGBA")

        if scrim:
            # subtle vignette first -- darkens the extreme corners of a real
            # photo so it reads as considered photography rather than a raw
            # stock crop, independent of the text-legibility scrim below.
            # A bright ellipse mask (white=keep photo, black=darken) blurred
            # at the edges gives a soft falloff instead of a hard ring.
            mask = Image.new("L", (W, H), 0)
            mdraw = ImageDraw.Draw(mask)
            mdraw.ellipse([-int(W * 0.15), -int(H * 0.25), int(W * 1.15), int(H * 1.25)], fill=255)
            mask = mask.filter(ImageFilter.GaussianBlur(radius=int(70 * scale)))
            corner_dark = Image.new("RGBA", (W, H), (0, 0, 0, 110))
            transparent = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            vignette_layer = Image.composite(transparent, corner_dark, mask)
            img = Image.alpha_composite(img, vignette_layer)

            grad = Image.new("L", (1, H), color=0)
            for y in range(H):
                t = y / H
                # legibility scrim: mostly transparent at top, strong at bottom
                grad.putpixel((0, y), int(235 * max(0, (t - 0.25) / 0.75) ** 1.4))
            alpha = grad.resize((W, H))
            dark = Image.new("RGBA", (W, H), (10, 10, 20, 255))
            dark.putalpha(alpha)
            img = Image.alpha_composite(img, dark)
            # also a soft top-left glow so the kicker pill has contrast even
            # over a bright sky/background photo
            top_grad = Image.new("L", (1, H), color=0)
            for y in range(H):
                t = y / H
                top_grad.putpixel((0, y), int(120 * max(0, (0.3 - t) / 0.3)))
            top_alpha = top_grad.resize((W, H))
            top_dark = Image.new("RGBA", (W, H), (0, 0, 0, 255))
            top_dark.putalpha(top_alpha)
            img = Image.alpha_composite(img, top_dark)
        else:
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            odraw.ellipse([W - int(W * 0.29), -int(H * 0.24), W + int(W * 0.21), int(H * 0.55)], fill=(255, 255, 255, 18))
            odraw.ellipse([-int(W * 0.17), H - int(H * 0.48), int(W * 0.21), H + int(H * 0.32)], fill=(255, 255, 255, 14))
            img = Image.alpha_composite(img, overlay)

        draw = ImageDraw.Draw(img)

        pill_font = self._font("semibold", int(26 * scale))
        bbox = draw.textbbox((0, 0), kicker, font=pill_font)
        pill_w = bbox[2] - bbox[0] + int(56 * scale)
        pad = int(70 * scale)
        pill_h0, pill_h1 = int(70 * scale), int(126 * scale)
        pill_fill = (255, 255, 255, 235) if scrim else (255, 255, 255, 255)
        draw.rounded_rectangle([pad, pill_h0, pad + pill_w, pill_h1], radius=int(28 * scale), fill=pill_fill)
        draw.text((pad + int(28 * scale), pill_h0 + int(14 * scale)), kicker, font=pill_font, fill=accent_rgb)

        font, wrapped = self._fit_headline(
            draw, title, max_width=W - int(144 * scale), max_lines=4,
            start_size=int(78 * scale), min_size=int(38 * scale),
        )
        line_h = int(font.size * 1.18)
        area_top, area_bottom = int(160 * scale), H - int(155 * scale)
        total_h = line_h * len(wrapped)
        y = max(area_top, area_top + ((area_bottom - area_top) - total_h) // 2)
        shadow_offset = max(1, int(2 * scale)) if scrim else 0
        for line in wrapped:
            if shadow_offset:
                draw.text((int(72 * scale) + shadow_offset, y + shadow_offset), line, font=font, fill=(0, 0, 0, 140))
            draw.text((int(72 * scale), y), line, font=font, fill=(255, 255, 255))
            y += line_h

        medium = self._font("medium", int(26 * scale))
        draw.line(
            [(int(72 * scale), H - int(110 * scale)), (W - int(72 * scale), H - int(110 * scale))],
            fill=(255, 255, 255, 90), width=max(1, int(2 * scale)),
        )
        draw.text((int(72 * scale), H - int(85 * scale)), f"{source}  •  {date_str}", font=medium, fill=(235, 230, 255))

        if PAGE_BRAND_TAG:
            # small, unobtrusive bottom-right brand mark -- consistent
            # branding across every post is what makes a page feel like a
            # real outlet rather than a one-off bot account
            brand_font = self._font("semibold", int(22 * scale))
            bbox = draw.textbbox((0, 0), PAGE_BRAND_TAG, font=brand_font)
            brand_w = bbox[2] - bbox[0]
            draw.text(
                (W - int(72 * scale) - brand_w, H - int(85 * scale)),
                PAGE_BRAND_TAG, font=brand_font, fill=(255, 255, 255, 210),
            )

        # downscale from 2x supersample to final output size -- this is what
        # gives noticeably sharper, higher-definition text/edges than
        # rendering directly at 1200x630
        final = img.convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)
        return final

    def generate_photo_card(self, article):
        """Primary image path: a real, topically relevant Pexels photo with
        a legibility scrim and the headline overlaid, rendered at 2x
        supersampling. Query order: entity-specific hint first (if the
        headline names something in ENTITY_VISUAL_HINTS), then the broader
        category query and its 'closeup' variant, then generic fallbacks.
        Returns None (falls through to the gradient card) if
        PEXELS_API_KEY isn't set or no suitable photo is found for any
        query in the chain."""
        try:
            title = article.get('title', '').strip()
            if not title:
                return None
            kicker, top_rgb, bottom_rgb, category_query = self._category_theme(title)
            entity_query = _entity_visual_query(title.lower())

            queries = []
            if entity_query:
                queries.append(entity_query)
            queries += [category_query, f"{category_query} closeup"] + PEXELS_GENERIC_FALLBACKS

            photo_bytes = self._pexels_photo(queries)
            if not photo_bytes:
                return None

            import io
            photo = Image.open(io.BytesIO(photo_bytes)).convert("RGB")
            base = self._cover_fit(photo, CARD_W * SUPERSAMPLE, CARD_H * SUPERSAMPLE)

            source = article.get('source', {}).get('name', 'Tech News')
            try:
                date_obj = datetime.fromisoformat(article.get('publishedAt', '').replace('Z', '+00:00'))
                date_str = date_obj.strftime('%b %d, %Y')
            except ValueError:
                date_str = datetime.now().strftime('%b %d, %Y')

            final = self._render_card(base, title, kicker, top_rgb, source, date_str, scrim=True)

            os.makedirs("/tmp/news_bot", exist_ok=True)
            path = f"/tmp/news_bot/photocard_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
            final.save(path, quality=95)
            if os.path.getsize(path) < 1000:
                return None
            logger.info(
                "Photo card generated: %s (theme=%s, entity_query=%r, source=Pexels)",
                path, kicker, entity_query,
            )
            return path
        except Exception as e:
            logger.info("Photo card generation failed (%s) -- falling back to gradient card.", e)
            return None

    def generate_news_card(self, article):
        """Fallback image path: locally-rendered gradient card, no network
        dependency beyond a one-time font download, no rate limits, no risk
        of the garbled-text/mangled-face problems photorealistic AI
        generators are prone to. Also rendered at 2x supersampling."""
        try:
            title = article.get('title', '').strip()
            if not title:
                return None
            source = article.get('source', {}).get('name', 'Tech News')
            try:
                date_obj = datetime.fromisoformat(article.get('publishedAt', '').replace('Z', '+00:00'))
                date_str = date_obj.strftime('%b %d, %Y')
            except ValueError:
                date_str = datetime.now().strftime('%b %d, %Y')

            kicker, top_rgb, bottom_rgb, _query = self._category_theme(title)
            base = self._gradient_image(CARD_W * SUPERSAMPLE, CARD_H * SUPERSAMPLE, top_rgb, bottom_rgb)
            final = self._render_card(base, title, kicker, top_rgb, source, date_str, scrim=False)

            os.makedirs("/tmp/news_bot", exist_ok=True)
            path = f"/tmp/news_bot/card_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
            final.save(path)
            if os.path.getsize(path) < 1000:
                return None
            logger.info("Gradient news card generated: %s (theme=%s)", path, kicker)
            return path

        except Exception as e:
            logger.warning("News card generation failed (%s) -- falling back to AI photo providers.", e)
            return None

    def generate_ai_image(self, article):
        """Image fallback chain, best quality/reliability first:
        1. Real Pexels stock photo (entity-aware query) + headline overlay
           (needs PEXELS_API_KEY)
        2. Locally-rendered gradient card (free, unlimited, no key needed)
        3. Stability AI photorealistic gen (needs credits)
        4. Gemini Nano Banana (free, ~500/day)
        5. Pollinations (fully free, no key)
        Returns None only if literally everything fails, in which case the
        caller posts text-only."""
        return (
            self.generate_photo_card(article)
            or self.generate_news_card(article)
            or self._ai_photo_fallback(article)
        )

    def _ai_photo_fallback(self, article):
        title = article.get('title', '').strip() if isinstance(article, dict) else str(article)
        prompt = (
            f"{title}, ultra-realistic, professional photography, cinematic lighting, "
            f"8k, high detail, Indian context, modern technology, vibrant colors, "
            f"no text, no watermark, no logos, single clear subject, natural hands and faces"
        )
        return (
            self._stability_image(prompt)
            or self._gemini_image(prompt)
            or self._pollinations_image(prompt)
        )

    # -------------------- real-fact lookup (Wikipedia, free, no key) --------------------
    def _guess_entity(self, title):
        """Best-effort extraction of a likely organization/product name from the
        headline, e.g. 'Zomato', 'ISRO', 'Ola Electric'. Looks for runs of
        capitalized words -- not perfect, but good enough to try a lookup."""
        candidates = re.findall(r'\b([A-Z][a-zA-Z0-9&]*(?:\s+[A-Z][a-zA-Z0-9&]*){0,2})\b', title)
        skip = {"India", "Indian", "The", "This", "New", "AI"}
        for c in candidates:
            if c not in skip and len(c) > 2:
                return c
        return None

    def get_wikipedia_fact(self, entity):
        """Pull one real, sourced sentence from Wikipedia's public summary API.
        No API key required. Returns None on any failure -- this is a nice-to-have,
        never something that should block a post."""
        if not entity:
            return None
        try:
            search_url = "https://en.wikipedia.org/w/api.php"
            params = {
                "action": "query", "list": "search", "srsearch": entity,
                "format": "json", "srlimit": 1,
            }
            r = self.session.get(search_url, params=params, timeout=10)
            r.raise_for_status()
            results = r.json().get("query", {}).get("search", [])
            if not results:
                return None
            page_title = results[0]["title"]

            summary_url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(page_title)}"
            r2 = self.session.get(summary_url, timeout=10)
            if r2.status_code != 200:
                return None
            extract = r2.json().get("extract", "")
            if not extract:
                return None
            first_sentence = extract.split(". ")[0].strip().rstrip(".") + "."
            return first_sentence if len(first_sentence) < 220 else None
        except (requests.exceptions.RequestException, ValueError, KeyError):
            return None  # fine to skip -- this is a bonus, not core functionality

    # -------------------- smarter narrative (Groq, optional) --------------------
    def _build_narrative_prompts(self, title, description, wiki_fact=None, full_text=None, shape="hot_take"):
        """Shared prompt-building for whichever LLM provider ends up handling
        the request. A randomly picked persona + an explicit list of banned
        AI-cliche phrases + a per-shape structural instruction is what
        produces genuinely different-looking posts run to run, instead of
        the same skeleton in different words."""
        persona = random.choice(self.personas)
        banned = ", ".join(f'"{p}"' for p in random.sample(self.banned_patterns, k=6))
        shape_instruction = POST_SHAPES.get(shape, POST_SHAPES["hot_take"])["narrative_instruction"]

        if shape == "listicle":
            length_instruction = "Follow the structure instruction exactly for length and format."
        elif full_text:
            length_instruction = "Aim for roughly 100-170 words total, pulling specific concrete details from the article body below -- names, numbers, quotes if present."
        else:
            length_instruction = "Aim for 40-65 words."

        system_prompt = (
            f"You are {persona}, writing for a Facebook tech-news page. {length_instruction}\n\n"
            f"{shape_instruction}\n\n"
            "Ground everything strictly in the source material given -- never invent statistics, dates, "
            "quotes, or claims that aren't in it. If the source material is thin, say less rather than "
            "pad with generic claims. You may use the optional background fact if it's relevant, and say "
            "plainly if you're connecting it speculatively.\n\n"
            f"Do not use these phrases or anything that reads like them: {banned}. "
            "Also avoid: overusing em-dashes, rhetorical questions used as filler (unless the structure "
            "instruction specifically asked for a question), and neat rule-of-three lists (unless the "
            "structure instruction asked for exactly three). Vary your sentence length -- mix one short "
            "punchy sentence with a longer one. Use contractions. Sound like a specific person with an "
            "opinion, not a summary engine. No emojis, no hashtags, no markdown, no bullet characters "
            "unless the structure instruction explicitly asks for the ' || ' separator."
        )

        user_prompt = f"Headline: {title}\n"
        if full_text:
            user_prompt += f"Full article text:\n{full_text}\n"
        else:
            user_prompt += f"Description (only source available): {description}\n"
        if wiki_fact:
            user_prompt += f"\nOptional background fact (Wikipedia, use only if genuinely relevant): {wiki_fact}"

        max_tokens = 340 if full_text and shape != "listicle" else 180
        return system_prompt, user_prompt, max_tokens

    def get_groq_narrative(self, system_prompt, user_prompt, max_tokens):
        """Tries each model in GROQ_MODEL_FALLBACKS in order, returns on first
        success. Returns None if GROQ_API_KEY isn't set, the key is invalid,
        or every model fails -- caller moves on to the next provider."""
        if not GROQ_API_KEY:
            return None

        for model in GROQ_MODEL_FALLBACKS:
            try:
                resp = self.session.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": model,
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        "temperature": 0.95,
                        "max_tokens": max_tokens,
                    },
                    timeout=REQUEST_TIMEOUT,
                )

                if resp.status_code == 401:
                    logger.error("Groq rejected the API key (401). Check GROQ_API_KEY. Skipping Groq entirely.")
                    return None  # bad key won't work for any model -- no point trying the rest

                if resp.status_code == 429:
                    logger.warning("Groq rate limit hit on %s -- trying next fallback model.", model)
                    continue

                if resp.status_code == 404:
                    logger.warning("Groq model %s not found (likely deprecated) -- trying next fallback model.", model)
                    continue

                if resp.status_code != 200:
                    logger.warning("Groq error %s on %s: %s -- trying next fallback model.",
                                    resp.status_code, model, resp.text[:200])
                    continue

                text = resp.json()["choices"][0]["message"]["content"].strip()
                if text:
                    logger.info("Groq narrative generated using %s (%d chars).", model, len(text))
                    return text
                logger.warning("Groq returned an empty response on %s -- trying next fallback model.", model)

            except requests.exceptions.Timeout:
                logger.warning("Groq request timed out on %s -- trying next fallback model.", model)
            except (requests.exceptions.RequestException, KeyError, IndexError, ValueError) as e:
                logger.warning("Groq call failed on %s (%s) -- trying next fallback model.", model, e)

        logger.warning("All Groq fallback models failed.")
        return None

    def get_gemini_narrative(self, system_prompt, user_prompt, max_tokens):
        """Second free provider, tried only if Groq isn't configured or fails
        entirely. Different company, different outage/quota surface -- this is
        what actually makes the 'AI service' layer resilient rather than just
        having four models from one vendor."""
        if not GEMINI_API_KEY:
            return None

        for model in GEMINI_TEXT_MODEL_FALLBACKS:
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                resp = self.session.post(
                    url,
                    headers={"x-goog-api-key": GEMINI_API_KEY, "Content-Type": "application/json"},
                    json={
                        "system_instruction": {"parts": [{"text": system_prompt}]},
                        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                        "generationConfig": {"temperature": 0.95, "maxOutputTokens": max_tokens},
                    },
                    timeout=REQUEST_TIMEOUT,
                )

                if resp.status_code in (401, 403):
                    logger.error("Gemini rejected the API key (%s). Check GEMINI_API_KEY. Skipping Gemini entirely.", resp.status_code)
                    return None

                if resp.status_code == 429:
                    logger.warning("Gemini rate limit hit on %s -- trying next fallback model.", model)
                    continue

                if resp.status_code != 200:
                    logger.warning("Gemini error %s on %s: %s -- trying next fallback model.",
                                    resp.status_code, model, resp.text[:200])
                    continue

                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                if text:
                    logger.info("Gemini narrative generated using %s (%d chars).", model, len(text))
                    return text
                logger.warning("Gemini returned an empty response on %s -- trying next fallback model.", model)

            except requests.exceptions.Timeout:
                logger.warning("Gemini request timed out on %s -- trying next fallback model.", model)
            except (requests.exceptions.RequestException, KeyError, IndexError, ValueError) as e:
                logger.warning("Gemini call failed on %s (%s) -- trying next fallback model.", model, e)

        logger.warning("All Gemini fallback models failed.")
        return None

    def get_ai_narrative(self, title, description, wiki_fact=None, full_text=None, shape="hot_take"):
        """Top-level narrative generator: Groq first (usually fastest + best
        free rate limits), then Gemini as a fully independent second provider,
        then None so the caller drops to the canned pool. A post always goes
        out no matter how many of these are unavailable."""
        system_prompt, user_prompt, max_tokens = self._build_narrative_prompts(
            title, description, wiki_fact, full_text, shape
        )
        return (
            self.get_groq_narrative(system_prompt, user_prompt, max_tokens)
            or self.get_gemini_narrative(system_prompt, user_prompt, max_tokens)
        )

    # -------------------- content generation --------------------
    def _emoji_for(self, text):
        text_lower = text.lower()
        matches = [emoji for keyword, emoji in self.topic_emojis.items() if _kw_match(text_lower, keyword)]
        if matches:
            return random.choice(list(dict.fromkeys(matches)))  # dedupe while preserving variety
        return random.choice(["🔥", "📰", "⚡"])  # vary the default too instead of always 🔥

    def _clean_description(self, description):
        clean = re.sub(r'\[.*?\]', '', description)
        clean = clean.replace('...', '.').strip()
        return clean

    def _pick_hashtags(self, title):
        title_lower = title.lower()
        tags = set(random.sample(self.hashtag_pool, k=random.randint(3, 5)))
        if _kw_match(title_lower, 'ai') or _kw_match(title_lower, 'artificial intelligence'):
            tags.update(["#ArtificialIntelligence", "#MachineLearning"])
        if 'startup' in title_lower:
            tags.add("#Entrepreneurship")
        if 'mobile' in title_lower or _kw_match(title_lower, 'app'):
            tags.add("#MobileApp")
        tags = list(tags)
        random.shuffle(tags)  # sorted order is a dead giveaway of programmatic generation
        return " ".join(tags)

    def _pick_shape(self, title, description):
        """Weighted-random shape choice, with stat_first only offered when
        the source material actually contains an extractable number -- no
        point promising a stat-led hook we can't back up."""
        candidates = dict(POST_SHAPES)
        if not STAT_PATTERN.search(f"{title} {description}"):
            candidates.pop("stat_first", None)
        shapes = list(candidates.keys())
        weights = [candidates[s]["weight"] for s in shapes]
        return random.choices(shapes, weights=weights, k=1)[0]

    @staticmethod
    def _extract_stat(title, description):
        m = STAT_PATTERN.search(f"{title} {description}")
        return m.group(0).strip() if m else None

    def create_engaging_post(self, article):
        """Build a post that reads like a person wrote it, not a template.
        A shape is chosen per call (hot-take / question-hook / mini-story /
        stat-first / listicle / contrarian), and both the LLM instructions
        AND the surrounding scaffold change per shape -- so consecutive
        posts genuinely differ in structure, not just phrasing."""
        title = article.get('title', '').strip()
        description = self._clean_description(article.get('description', '').strip())
        source = article.get('source', {}).get('name', 'Tech News')
        published_at = article.get('publishedAt', '')

        try:
            date_obj = datetime.fromisoformat(published_at.replace('Z', '+00:00'))
            formatted_date = date_obj.strftime('%B %d, %Y')
        except ValueError:
            formatted_date = 'Today'

        emoji = self._emoji_for(title)
        shape = self._pick_shape(title, description)
        logger.info("Post shape selected: %s", shape)

        full_text = self.fetch_full_article_text(article.get('url'))
        wiki_fact = self.get_wikipedia_fact(self._guess_entity(title))
        ai_narrative = self.get_ai_narrative(title, description, wiki_fact, full_text, shape)
        used_ai = bool(ai_narrative)

        if not used_ai:
            # Canned fallback never knows about shapes -- always reads as a
            # plain analysis paragraph, which is fine since it's the rare
            # last-resort path when both LLM providers are down.
            analysis = random.choice(self.analysis_pool)
            shape = "hot_take"
        else:
            analysis = ai_narrative

        used_long_form_narrative = used_ai and bool(full_text) and shape != "listicle"

        parts = []

        if shape == "listicle" and used_ai and "||" in analysis:
            points = [p.strip(" .") for p in analysis.split("||") if p.strip()]
            parts.append(f"{emoji} {title}")
            parts.append("")
            number_emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]
            for i, point in enumerate(points[:4]):
                parts.append(f"{number_emojis[i]} {point}.")
            parts.append("")

        elif shape == "stat_first":
            stat = self._extract_stat(title, description)
            if stat:
                parts.append(f"{stat.upper() if len(stat) < 12 else stat} -- that's the number driving today's headline.")
                parts.append("")
            parts.append(f"{emoji} {title}")
            parts.append("")
            if description and not used_long_form_narrative:
                parts.append(description if description.endswith('.') else description + '.')
                parts.append("")
            parts.append(analysis)
            parts.append("")

        elif shape in ("hot_take", "question_hook", "contrarian"):
            # LLM output already leads with the hook (verdict / question /
            # pivot) per the shape instruction, so it comes BEFORE the
            # headline here -- the headline functions as a supporting
            # reference rather than the opening line.
            parts.append(analysis)
            parts.append("")
            parts.append(f"{emoji} {title}")
            parts.append("")
            if description and not used_long_form_narrative:
                parts.append(description if description.endswith('.') else description + '.')
                parts.append("")

        else:  # mini_story, or listicle fallback if the delimiter parse failed
            opener = random.choice(self.openers)
            if opener:
                parts.append(opener)
                parts.append("")
            parts.append(f"{emoji} {title}")
            parts.append("")
            if description and not used_long_form_narrative:
                parts.append(description if description.endswith('.') else description + '.')
                parts.append("")
            parts.append(analysis)
            parts.append("")

        # Occasionally surface the raw Wikipedia fact as a standalone "did you know"
        # line -- real, sourced trivia, not something the LLM guessed at.
        if wiki_fact and random.random() < 0.5:
            parts.append(f"📌 Background: {wiki_fact}")
            parts.append("")

        closer = random.choice(self.closers) if shape != "listicle" else "Which of these lands hardest for you?"
        if closer:
            parts.append(closer)
            parts.append("")

        parts.append(f"({source}, {formatted_date})")
        if article.get('url'):
            parts.append(f"🔗 Full story: {article['url']}")
        parts.append("")
        parts.append(self._pick_hashtags(title))

        final_post = "\n".join(parts)

        # Facebook's hard cap is 63,206 characters -- keep well under it
        if len(final_post) > 60000:
            final_post = final_post[:59900].rsplit("\n", 1)[0]

        return final_post

    # -------------------- publishing --------------------
    def post_to_facebook(self, message, image_path=None):
        """Post with an image if available, otherwise a text-only post.
        Returns True/False; never raises."""
        try:
            if image_path and os.path.exists(image_path):
                url = f"https://graph.facebook.com/v18.0/{FB_PAGE_ID}/photos"
                data = {'message': message, 'access_token': FB_PAGE_ACCESS_TOKEN}
                with open(image_path, 'rb') as img_file:
                    files = {'source': img_file}
                    resp = self.session.post(url, data=data, files=files, timeout=REQUEST_TIMEOUT)
            else:
                url = f"https://graph.facebook.com/v18.0/{FB_PAGE_ID}/feed"
                data = {'message': message, 'access_token': FB_PAGE_ACCESS_TOKEN}
                resp = self.session.post(url, data=data, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 200:
                post_id = resp.json().get('id', 'N/A')
                logger.info("Posted to Facebook successfully. Post ID: %s", post_id)
                return True

            # Try to surface Facebook's actual error message
            try:
                err = resp.json().get('error', {})
                logger.error(
                    "Facebook post failed [%s]: %s (code %s)",
                    resp.status_code, err.get('message', resp.text[:300]), err.get('code'),
                )
                if err.get('code') in (190,):  # OAuth token invalid/expired
                    logger.error("Your FB_PAGE_ACCESS_TOKEN appears invalid or expired -- generate a new one.")
            except ValueError:
                logger.error("Facebook post failed [%s]: %s", resp.status_code, resp.text[:300])
            return False

        except requests.exceptions.Timeout:
            logger.error("Facebook post request timed out.")
        except requests.exceptions.RequestException as e:
            logger.error("Facebook post request failed: %s", e)
        finally:
            if image_path and os.path.exists(image_path):
                try:
                    os.remove(image_path)
                except OSError:
                    pass
        return False

    # -------------------- run loop --------------------
    def run_once(self):
        logger.info("Run started at %s", datetime.now().strftime('%Y-%m-%d %H:%M:%S'))

        articles = self.get_indian_tech_news()
        if not articles:
            logger.info("No articles available this run.")
            return 0

        candidates = [a for a in articles if not self._already_posted(a)]
        if not candidates:
            logger.info("No new articles to post -- everything fetched was already posted.")
            return 0

        # Post the most attention-grabbing unposted story, weighted toward
        # (but not locked to) the single top-scored pick -- see _pick_candidate.
        candidates.sort(key=self._engagement_score, reverse=True)
        first_pick = self._pick_candidate(candidates)
        # Try the weighted pick first, then fall through the rest of the
        # ranked list (excluding the one already tried) if it fails to post.
        ordered = [first_pick] + [a for a in candidates if a is not first_pick]

        posted_count = 0
        consecutive_fb_failures = 0
        MAX_CONSECUTIVE_FAILURES = 2  # if FB rejects 2 in a row, it's the token/config, not the articles

        for i, article in enumerate(ordered, 1):
            title = article.get('title', '').strip()
            logger.info("Processing (%d/%d, score=%d): %s", i, len(ordered), self._engagement_score(article), title[:70])

            image_path = self.generate_ai_image(article)
            if not image_path:
                logger.warning("Falling back to a text-only post for this article.")

            post_content = self.create_engaging_post(article)

            if self.post_to_facebook(post_content, image_path):
                category, _, _, _ = self._category_theme(article.get('title', ''))
                self.posted_today.append({
                    "key": self._dedup_key(article),
                    "title": article.get('title', '').strip(),
                    "category": category,
                    "posted_at": datetime.now().isoformat(),
                })
                self.save_posted_articles()
                posted_count += 1
                consecutive_fb_failures = 0
                break  # one article per run, same as before
            else:
                consecutive_fb_failures += 1
                logger.warning("Failed to post (%d consecutive failure(s)).", consecutive_fb_failures)
                if consecutive_fb_failures >= MAX_CONSECUTIVE_FAILURES:
                    logger.error(
                        "Facebook posting failed %d times in a row -- this is almost certainly a token/config "
                        "problem, not bad luck with articles. Stopping this run instead of burning through "
                        "the rest of the article list. Check FB_PAGE_ACCESS_TOKEN.",
                        consecutive_fb_failures,
                    )
                    break

        if posted_count == 0:
            logger.info("No new posts created this run.")
        return posted_count

    def run_continuous(self, interval_hours=4):
        logger.info("Starting continuous mode -- posting every %d hours. Ctrl+C to stop.", interval_hours)

        def _handle_stop(signum, frame):
            logger.info("Stop signal received, shutting down after current run.")
            self._stop = True

        signal.signal(signal.SIGINT, _handle_stop)
        signal.signal(signal.SIGTERM, _handle_stop)

        run_count = 0
        while not self._stop:
            run_count += 1
            logger.info("--- Run #%d ---", run_count)
            try:
                self.run_once()
            except Exception as e:
                # Catch-all so one bad run never kills the whole process
                logger.exception("Unhandled error during run: %s", e)

            if self._stop:
                break

            next_run = datetime.now() + timedelta(hours=interval_hours)
            logger.info("Sleeping until %s", next_run.strftime('%H:%M:%S'))
            # Sleep in short chunks so a stop signal is honored quickly
            slept = 0
            while slept < interval_hours * 3600 and not self._stop:
                time.sleep(min(30, interval_hours * 3600 - slept))
                slept += 30

        logger.info("Bot stopped cleanly.")


# ========================================
# MAIN
# ========================================
def main():
    parser = argparse.ArgumentParser(description="Facebook news bot")
    parser.add_argument("--once", action="store_true", help="Post one article and exit (for cron/GitHub Actions)")
    parser.add_argument("--dry-run", action="store_true", help="Build a post (incl. Groq/Wikipedia calls) and print it, but never call Facebook")
    parser.add_argument("--continuous", action="store_true", help="Run forever, posting every N hours (not for serverless/CI)")
    parser.add_argument("--stats", action="store_true", help="Print how many articles have been posted today")
    parser.add_argument("--interval-hours", type=int, default=4, help="Hours between posts in --continuous mode")
    parser.add_argument(
        "--jitter-seconds", type=int, default=600,
        help="Random delay (0 to this many seconds) before posting, so scheduled runs don't all fire at :00:00 sharp. Default 10 min.",
    )
    args = parser.parse_args()

    validate_config()
    bot = EnhancedNewsBot()

    # If invoked with a flag (cron/CI) OR there's no interactive terminal attached,
    # default to a single non-interactive run instead of blocking on input().
    if args.dry_run:
        articles = bot.get_indian_tech_news()
        if not articles:
            print("No articles fetched -- nothing to preview.")
            return
        candidates = [a for a in articles if not bot._already_posted(a)] or articles
        candidates.sort(key=bot._engagement_score, reverse=True)
        article = bot._pick_candidate(candidates)
        print(f"[DRY RUN] Would post about (engagement score={bot._engagement_score(article)}): {article.get('title')}\n")
        print(bot.create_engaging_post(article))
        card_path = bot.generate_ai_image(article)
        if card_path:
            print(f"\n[DRY RUN] Preview card image saved locally at: {card_path}")
        print("\n[DRY RUN] No Facebook call made.")
        return

    non_interactive = args.once or args.continuous or args.stats or not sys.stdin.isatty()

    if args.continuous:
        bot.run_continuous(interval_hours=args.interval_hours)
        return

    if args.stats:
        print(f"Articles posted today: {len(bot.posted_today)}")
        for article in bot.posted_today[-5:]:
            print(f"  - {bot._posted_display(article)}")
        return

    if non_interactive:
        jitter = random.randint(0, max(0, args.jitter_seconds))
        if jitter:
            logger.info("Jittering start by %ds so this run isn't posting at a perfectly fixed second.", jitter)
            time.sleep(jitter)
        bot.run_once()
        return

    # Interactive fallback (only reached when run directly in a real terminal)
    print("Enhanced Facebook News Bot")
    print("=" * 40)
    print("1. Run once (test mode)")
    print("2. Run continuously (every 4 hours)")
    print("3. Show statistics")
    choice = input("Enter your choice (1-3): ").strip()
    if choice == "1":
        bot.run_once()
    elif choice == "2":
        bot.run_continuous()
    elif choice == "3":
        print(f"Articles posted today: {len(bot.posted_today)}")
        for article in bot.posted_today[-5:]:
            print(f"  - {bot._posted_display(article)}")
    else:
        bot.run_once()


if __name__ == "__main__":
    main()