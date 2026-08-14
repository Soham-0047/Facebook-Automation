#!/usr/bin/env python3
"""
Enhanced Facebook News Bot - Stability AI REST API Version
Automatically posts detailed Indian tech news with AI-generated images to a Facebook page.

Hardened version:
- Secrets loaded from environment variables / .env (never hardcoded)
- Retries with backoff + timeouts on every network call
- Real logging (console + rotating file) instead of print()
- Graceful degradation: falls back to a text-only post if image gen fails
- Content generator produces varied, less "templated" post copy
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
from PIL import Image, ImageDraw, ImageFont
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
# NEWS CARD GRAPHICS (primary image method)
# ========================================
# Locally-rendered headline cards via Pillow -- free, instant, unlimited, and
# structurally incapable of the mangled-face/garbled-text problems that make
# photorealistic AI image generation look "ugly" so often. Fonts are Google's
# open-license Poppins family, fetched once and cached; falls back to
# whatever DejaVu font the OS has if the download ever fails (no network
# dependency at that point).
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
# color, bottom gradient color) -- first match wins, checked in this order.
CATEGORY_THEMES = [
    (["ai", "artificial intelligence", "machine learning", "chatgpt", "llm", "openai"],
     "ARTIFICIAL INTELLIGENCE", (76, 29, 149), (124, 58, 237)),
    (["startup", "funding", "raises", "valuation", "venture capital", "seed round"],
     "STARTUP & FUNDING", (154, 52, 18), (234, 88, 12)),
    (["fintech", "payment", "upi", "banking", "paytm", "credit"],
     "FINTECH", (6, 78, 59), (16, 185, 129)),
    (["ecommerce", "e-commerce", "retail", "shopping", "flipkart", "amazon", "myntra"],
     "E-COMMERCE", (131, 24, 67), (219, 39, 119)),
    (["electric vehicle", "ev", "battery", "ola electric", "auto", "car"],
     "ELECTRIC & AUTO", (12, 74, 110), (14, 165, 233)),
    (["space", "isro", "satellite", "rocket", "spacex"],
     "SPACE", (30, 27, 75), (79, 70, 229)),
    (["mobile", "smartphone", "app", "gadget", "iphone", "android"],
     "MOBILE & GADGETS", (17, 94, 89), (20, 184, 166)),
    (["healthcare", "medical", "health"],
     "HEALTHCARE", (127, 29, 29), (220, 38, 38)),
]
DEFAULT_THEME = ("TECH NEWS", (30, 58, 138), (59, 130, 246))

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
# STABILITY_API_KEY is intentionally NOT required -- image generation now
# falls back to Gemini (if GEMINI_API_KEY is set) and then to Pollinations.ai,
# which needs no key at all. Posting still works with zero image providers
# configured; it just goes out as a text-only post.


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

    def _already_posted(self, article):
        key = self._dedup_key(article)
        title = article.get('title', '').strip()
        # also check the raw title against older entries saved before this
        # dedup scheme existed, so upgrading the script doesn't cause re-posts
        return key in self.posted_today or title in self.posted_today

    def _engagement_score(self, article):
        """Rank candidate articles by how likely they are to actually catch
        attention -- recognizable names, funding/launch/record-type events,
        and numbers in the headline consistently outperform generic coverage.
        This decides WHICH already-fetched, already-recent article to post,
        not anything about the content itself."""
        title_lower = article.get('title', '').lower()
        score = sum(weight for kw, weight in ENGAGEMENT_KEYWORDS.items() if _kw_match(title_lower, kw))
        if any(ch.isdigit() for ch in title_lower):
            score += 1  # "raises $200M", "40 cities" -- concrete numbers read as more credible/catchy
        if len(article.get('title', '')) < 70:
            score += 1  # short punchy headlines work better as a card title
        return score

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
    def get_indian_tech_news(self):
        try:
            url = "https://newsapi.org/v2/everything"
            params = {
                'q': 'India technology OR Indian tech OR India AI OR India startup OR Indian innovation OR Digital India',
                'apiKey': NEWS_API_KEY,
                'language': 'en',
                'sortBy': 'publishedAt',
                'pageSize': 20,
                'excludeDomains': 'reddit.com',
            }
            resp = self.session.get(url, params=params, timeout=REQUEST_TIMEOUT)

            if resp.status_code == 401:
                logger.error("NewsAPI rejected the API key (401). Check NEWS_API_KEY.")
                return []
            if resp.status_code == 429:
                logger.warning("NewsAPI rate limit hit. Skipping this run.")
                return []
            resp.raise_for_status()

            articles = resp.json().get('articles', [])
            filtered = [
                a for a in articles
                if a.get('title') and a.get('title') != '[Removed]'
                and a.get('description') and len(a.get('description', '')) > 50
                and a.get('url')
            ]
            logger.info("Found %d usable articles out of %d returned.", len(filtered), len(articles))
            return filtered

        except requests.exceptions.Timeout:
            logger.error("NewsAPI request timed out.")
        except requests.exceptions.RequestException as e:
            logger.error("NewsAPI request failed: %s", e)
        except (ValueError, KeyError) as e:
            logger.error("Unexpected NewsAPI response format: %s", e)
        return []

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

    # -------------------- image generation --------------------
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

    # -------------------- news card graphics (primary image method) --------------------
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

    def _category_theme(self, title):
        text_lower = title.lower()
        for keywords, label, top_rgb, bottom_rgb in CATEGORY_THEMES:
            if any(_kw_match(text_lower, kw) for kw in keywords):
                return label, top_rgb, bottom_rgb
        return DEFAULT_THEME

    def _fit_headline(self, draw, text, max_width, max_lines, start_size=78, min_size=38):
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

    def generate_news_card(self, article):
        """Render a clean 1200x630 headline card locally: gradient background
        themed to the story's category, a kicker pill, the (auto-fitted)
        headline, and a source/date footer. No network dependency beyond a
        one-time font download, no rate limits, no risk of the garbled-text/
        mangled-face problems photorealistic AI generators are prone to."""
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

            kicker, top_rgb, bottom_rgb = self._category_theme(title)

            W, H = 1200, 630
            img = self._gradient_image(W, H, top_rgb, bottom_rgb)
            overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
            odraw = ImageDraw.Draw(overlay)
            odraw.ellipse([W - 350, -150, W + 250, 350], fill=(255, 255, 255, 18))
            odraw.ellipse([-200, H - 300, 250, H + 200], fill=(255, 255, 255, 14))
            img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
            draw = ImageDraw.Draw(img)

            pill_font = self._font("semibold", 26)
            bbox = draw.textbbox((0, 0), kicker, font=pill_font)
            pill_w = bbox[2] - bbox[0] + 56
            draw.rounded_rectangle([70, 70, 70 + pill_w, 126], radius=28, fill=(255, 255, 255, 255))
            draw.text((98, 84), kicker, font=pill_font, fill=top_rgb)

            font, wrapped = self._fit_headline(draw, title, max_width=W - 144, max_lines=4)
            line_h = int(font.size * 1.18)
            area_top, area_bottom = 160, H - 140
            total_h = line_h * len(wrapped)
            y = max(area_top, area_top + ((area_bottom - area_top) - total_h) // 2)
            for line in wrapped:
                draw.text((72, y), line, font=font, fill=(255, 255, 255))
                y += line_h

            medium = self._font("medium", 26)
            draw.line([(72, H - 110), (W - 72, H - 110)], fill=(255, 255, 255, 80), width=2)
            draw.text((72, H - 85), f"{source}  •  {date_str}", font=medium, fill=(235, 230, 255))

            os.makedirs("/tmp/news_bot", exist_ok=True)
            path = f"/tmp/news_bot/card_{int(time.time())}_{uuid.uuid4().hex[:8]}.png"
            img.save(path)
            if os.path.getsize(path) < 1000:
                return None
            logger.info("News card generated: %s (theme=%s)", path, kicker)
            return path

        except Exception as e:
            logger.warning("News card generation failed (%s) -- falling back to AI photo providers.", e)
            return None

    def generate_ai_image(self, article):
        """Primary path: a locally-rendered news card (generate_news_card) --
        free, instant, unlimited, no risk of AI-photo artifacts. Only if that
        somehow fails does this fall through to AI photo providers: Stability
        AI (needs credits) -> Gemini Nano Banana (free, ~500/day) ->
        Pollinations (fully free, no key). Returns None only if literally
        everything fails, in which case the caller posts text-only."""
        card_path = self.generate_news_card(article)
        if card_path:
            return card_path

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
    def _build_narrative_prompts(self, title, description, wiki_fact=None, full_text=None):
        """Shared prompt-building for whichever LLM provider ends up handling
        the request. A randomly picked persona + an explicit list of banned
        AI-cliche phrases does more for "doesn't sound like AI" than any
        amount of generic 'be creative' instruction."""
        persona = random.choice(self.personas)
        banned = ", ".join(f'"{p}"' for p in random.sample(self.banned_patterns, k=6))

        if full_text:
            length_instruction = (
                "Write two short paragraphs (roughly 100-170 words total). The first should explain "
                "what's actually going on, pulling specific concrete details from the article body "
                "below -- names, numbers, quotes if present. The second should be your own angle: "
                "what a casual reader would miss, a tension, a comparison, an implication."
            )
        else:
            length_instruction = "Write one sharp paragraph (40-65 words)."

        system_prompt = (
            f"You are {persona}, writing for a Facebook tech-news page. {length_instruction} "
            "Ground everything strictly in the source material given -- never invent statistics, dates, "
            "quotes, or claims that aren't in it. If the source material is thin, say less rather than "
            "pad with generic claims. You may use the optional background fact if it's relevant, and say "
            "plainly if you're connecting it speculatively.\n\n"
            f"Do not use these phrases or anything that reads like them: {banned}. "
            "Also avoid: starting more than one sentence with 'This', overusing em-dashes, rhetorical "
            "questions used as filler, and neat rule-of-three lists. Vary your sentence length -- mix "
            "one short punchy sentence with a longer one. Use contractions. Sound like a specific person "
            "with an opinion, not a summary engine. No emojis, no hashtags, no bullet points, no markdown."
        )

        user_prompt = f"Headline: {title}\n"
        if full_text:
            user_prompt += f"Full article text:\n{full_text}\n"
        else:
            user_prompt += f"Description (only source available): {description}\n"
        if wiki_fact:
            user_prompt += f"\nOptional background fact (Wikipedia, use only if genuinely relevant): {wiki_fact}"

        max_tokens = 340 if full_text else 160
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

    def get_ai_narrative(self, title, description, wiki_fact=None, full_text=None):
        """Top-level narrative generator: Groq first (usually fastest + best
        free rate limits), then Gemini as a fully independent second provider,
        then None so the caller drops to the canned pool. A post always goes
        out no matter how many of these are unavailable."""
        system_prompt, user_prompt, max_tokens = self._build_narrative_prompts(
            title, description, wiki_fact, full_text
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

    def create_engaging_post(self, article):
        """Build a post that reads like a person wrote it, not a template.
        Structure and phrasing are randomized per call so consecutive posts
        don't look identical in shape."""
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
        opener = random.choice(self.openers)
        closer = random.choice(self.closers)

        # Try for a genuinely interesting, fact-grounded take first.
        # Falls back to the canned pool if Groq/Gemini aren't configured or fail --
        # the post always goes out either way.
        full_text = self.fetch_full_article_text(article.get('url'))
        wiki_fact = self.get_wikipedia_fact(self._guess_entity(title))
        ai_narrative = self.get_ai_narrative(title, description, wiki_fact, full_text)
        analysis = ai_narrative or random.choice(self.analysis_pool)

        # When full_text was available AND the AI actually used it, the narrative's
        # first paragraph already explains what's going on in more depth than the
        # 200-char NewsAPI snippet -- showing both means saying the same thing twice.
        # Only show the raw snippet when the AI narrative is the short single-paragraph
        # version (no full_text) or the canned fallback, where it's genuinely additive.
        used_long_form_narrative = bool(ai_narrative) and bool(full_text)

        parts = []

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

        # Post the most attention-grabbing unposted story, not just the newest one.
        candidates.sort(key=self._engagement_score, reverse=True)
        logger.info(
            "Ranked %d candidate(s) by engagement score. Top pick (score=%d): %s",
            len(candidates), self._engagement_score(candidates[0]), candidates[0].get('title', '')[:70],
        )

        posted_count = 0
        consecutive_fb_failures = 0
        MAX_CONSECUTIVE_FAILURES = 2  # if FB rejects 2 in a row, it's the token/config, not the articles

        for i, article in enumerate(candidates, 1):
            title = article.get('title', '').strip()
            logger.info("Processing (%d/%d, score=%d): %s", i, len(candidates), self._engagement_score(article), title[:70])

            image_path = self.generate_ai_image(article)
            if not image_path:
                logger.warning("Falling back to a text-only post for this article.")

            post_content = self.create_engaging_post(article)

            if self.post_to_facebook(post_content, image_path):
                self.posted_today.append(self._dedup_key(article))
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
        article = candidates[0]
        print(f"[DRY RUN] Would post about (engagement score={bot._engagement_score(article)}): {article.get('title')}\n")
        print(bot.create_engaging_post(article))
        card_path = bot.generate_news_card(article)
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
            print(f"  - {article[:60]}...")
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
            print(f"  - {article[:60]}...")
    else:
        bot.run_once()


if __name__ == "__main__":
    main()