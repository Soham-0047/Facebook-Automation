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

POSTED_FILE = os.getenv("POSTED_FILE", "posted_articles.json")
LOG_FILE = os.getenv("LOG_FILE", "news_bot.log")
REQUEST_TIMEOUT = 30  # seconds, applied to every outbound call

REQUIRED_VARS = {
    "FB_PAGE_ACCESS_TOKEN": FB_PAGE_ACCESS_TOKEN,
    "FB_PAGE_ID": FB_PAGE_ID,
    "NEWS_API_KEY": NEWS_API_KEY,
    "STABILITY_API_KEY": STABILITY_API_KEY,
}


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
    """A requests session that automatically retries on transient failures
    (connection errors, 429, and 5xx) with exponential backoff."""
    session = requests.Session()
    retry = Retry(
        total=total_retries,
        backoff_factor=backoff_factor,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "POST"],
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

        # --- Content variety pools -------------------------------------
        # Instead of one rigid 7-section skeleton every time, we keep several
        # independent pools and mix-and-match so consecutive posts don't
        # read like they came off an assembly line.

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
    def _robots_allow(self, url, user_agent="NewsBot"):
        """Politely check robots.txt before scraping. Defaults to allowing
        the fetch if robots.txt is unreachable or malformed -- we don't want
        a broken robots.txt on some random news site to break the whole run."""
        try:
            parsed = urlparse(url)
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            rp = urllib.robotparser.RobotFileParser()
            rp.set_url(robots_url)
            rp.read()
            return rp.can_fetch(user_agent, url)
        except Exception:
            return True

    def fetch_full_article_text(self, url):
        """Best-effort fetch of the actual article body, so the LLM has real
        substance to work with instead of a 200-character NewsAPI snippet.
        Returns None on ANY failure (blocked, paywalled, timeout, parse error,
        trafilatura not installed) -- this is a quality enhancement, never a
        requirement for posting."""
        if not HAS_TRAFILATURA or not url:
            return None
        try:
            if not self._robots_allow(url):
                logger.info("robots.txt disallows scraping %s -- using description only.", url)
                return None

            headers = {"User-Agent": "Mozilla/5.0 (compatible; NewsBot/1.0; +https://example.com/bot)"}
            resp = self.session.get(url, headers=headers, timeout=15)
            if resp.status_code != 200 or not resp.text:
                return None

            extracted = trafilatura.extract(resp.text, include_comments=False, include_tables=False)
            if not extracted or len(extracted) < 200:
                return None
            return extracted[:4000]  # cap length -- plenty of context, keeps API calls cheap and fast

        except requests.exceptions.RequestException:
            return None  # many sites block scrapers (403) -- expected, not an error worth logging loudly
        except Exception as e:
            logger.info("Article scraping failed for %s (%s) -- using description only.", url, e)
            return None

    # -------------------- image generation --------------------
    def generate_ai_image(self, topic):
        try:
            prompt = (
                f"{topic}, ultra-realistic, professional photography, cinematic lighting, "
                f"8k, high detail, Indian context, modern technology, vibrant colors"
            )
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
                logger.warning("Stability AI rate limit hit -- will fall back to a text-only post.")
                return None
            if resp.status_code != 200:
                logger.error("Stability AI error %s: %s", resp.status_code, resp.text[:300])
                return None

            content_type = resp.headers.get("Content-Type", "")
            if not content_type.startswith("image/"):
                logger.error("Stability AI returned non-image content-type %r -- discarding, falling back to text-only.", content_type)
                return None
            if len(resp.content) < 1000:
                logger.error("Stability AI response body is suspiciously small (%d bytes) -- treating as failure.", len(resp.content))
                return None

            os.makedirs("/tmp/news_bot", exist_ok=True)
            img_path = f"/tmp/news_bot/img_{int(time.time())}.png"
            with open(img_path, "wb") as f:
                f.write(resp.content)
            logger.info("AI image generated: %s (%d bytes)", img_path, len(resp.content))
            return img_path

        except requests.exceptions.Timeout:
            logger.error("Stability AI request timed out.")
        except requests.exceptions.RequestException as e:
            logger.error("Stability AI request failed: %s", e)
        except OSError as e:
            logger.error("Could not write generated image to disk: %s", e)
        return None

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
    def get_groq_narrative(self, title, description, wiki_fact=None, full_text=None):
        """Ask a fast free LLM (Groq) to write a genuinely interesting,
        non-generic take grounded in the real source material. When full_text
        (scraped article body) is available, it's used as the primary source
        and the model is asked for a longer, more substantive 2-paragraph
        piece; otherwise it falls back to writing a shorter single paragraph
        from just the headline/description. Explicitly instructed not to
        invent numbers or claims that aren't in the source material.

        Tries each model in GROQ_MODEL_FALLBACKS in order and returns on the
        first success. Returns None only if GROQ_API_KEY isn't set, the key
        itself is invalid, or every model in the list fails -- in which case
        the caller falls back to the canned analysis pool. This must never
        block a post from going out."""
        if not GROQ_API_KEY:
            return None

        if full_text:
            length_instruction = (
                "Write two short paragraphs (roughly 100-160 words total). The first should explain "
                "what's actually going on and why, pulling specific concrete details from the article "
                "body below. The second should be your own analytical angle -- what most casual readers "
                "would miss, a comparison, a tension, or an implication worth flagging."
            )
        else:
            length_instruction = "Write one short, sharp paragraph (35-55 words)."

        system_prompt = (
            f"You write grounded, factual commentary for a Facebook tech-news page. {length_instruction} "
            "Ground everything strictly in the source material given -- never invent statistics, dates, "
            "quotes, or claims that aren't in it. If the source material is thin, say less rather than "
            "padding with generic claims. You may use the optional background fact if it's relevant, and "
            "say plainly if you're connecting it speculatively. Avoid hype words like 'revolutionary', "
            "'game-changing', 'breakthrough'. Write like a sharp, slightly wry human analyst, not a press "
            "release. No emojis, no hashtags, no bullet points, no markdown formatting."
        )

        user_prompt = f"Headline: {title}\n"
        if full_text:
            user_prompt += f"Full article text:\n{full_text}\n"
        else:
            user_prompt += f"Description (only source available): {description}\n"
        if wiki_fact:
            user_prompt += f"\nOptional background fact (Wikipedia, use only if genuinely relevant): {wiki_fact}"

        max_tokens = 320 if full_text else 150

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
                        "temperature": 0.8,
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
                    logger.info("Groq narrative generated using %s (%d chars, full_text=%s).",
                                model, len(text), bool(full_text))
                    return text
                logger.warning("Groq returned an empty response on %s -- trying next fallback model.", model)

            except requests.exceptions.Timeout:
                logger.warning("Groq request timed out on %s -- trying next fallback model.", model)
            except (requests.exceptions.RequestException, KeyError, IndexError, ValueError) as e:
                logger.warning("Groq call failed on %s (%s) -- trying next fallback model.", model, e)

        logger.warning("All Groq fallback models failed -- falling back to canned analysis pool.")
        return None

    # -------------------- content generation --------------------
    def _emoji_for(self, text):
        text_lower = text.lower()
        for keyword, emoji in self.topic_emojis.items():
            if keyword in text_lower:
                return emoji
        return "🔥"

    def _clean_description(self, description):
        clean = re.sub(r'\[.*?\]', '', description)
        clean = clean.replace('...', '.').strip()
        return clean

    def _pick_hashtags(self, title):
        title_lower = title.lower()
        tags = set(random.sample(self.hashtag_pool, k=4))
        if 'ai' in title_lower or 'artificial intelligence' in title_lower:
            tags.update(["#ArtificialIntelligence", "#MachineLearning"])
        if 'startup' in title_lower:
            tags.add("#Entrepreneurship")
        if 'mobile' in title_lower or 'app' in title_lower:
            tags.add("#MobileApp")
        return " ".join(sorted(tags))

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
        # Falls back to the canned pool if Groq isn't configured or fails --
        # the post always goes out either way.
        full_text = self.fetch_full_article_text(article.get('url'))
        wiki_fact = self.get_wikipedia_fact(self._guess_entity(title))
        analysis = self.get_groq_narrative(title, description, wiki_fact, full_text) or random.choice(self.analysis_pool)

        parts = []

        if opener:
            parts.append(opener)
            parts.append("")

        parts.append(f"{emoji} {title}")
        parts.append("")

        if description:
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

        posted_count = 0
        consecutive_fb_failures = 0
        MAX_CONSECUTIVE_FAILURES = 2  # if FB rejects 2 in a row, it's the token/config, not the articles

        for i, article in enumerate(articles, 1):
            if self._already_posted(article):
                continue

            title = article.get('title', '').strip()
            logger.info("Processing (%d/%d): %s", i, len(articles), title[:70])

            image_path = self.generate_ai_image(title)
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
        article = articles[0]
        print(f"[DRY RUN] Would post about: {article.get('title')}\n")
        print(bot.create_engaging_post(article))
        print("\n[DRY RUN] No image generated, no Facebook call made.")
        return

    non_interactive = args.once or args.continuous or args.stats or not sys.stdin.isatty()

    if args.stats or (non_interactive and not args.once and not args.continuous):
        pass  # fall through to the default single-run behavior below unless --stats was explicit

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