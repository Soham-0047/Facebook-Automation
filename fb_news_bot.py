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
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

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

    # -------------------- persistence --------------------
    def load_posted_articles(self):
        if os.path.exists(POSTED_FILE):
            try:
                with open(POSTED_FILE, "r") as f:
                    return json.load(f)
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
                'pageSize': 15,
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

            os.makedirs("/tmp/news_bot", exist_ok=True)
            img_path = f"/tmp/news_bot/img_{int(time.time())}.png"
            with open(img_path, "wb") as f:
                f.write(resp.content)
            logger.info("AI image generated: %s", img_path)
            return img_path

        except requests.exceptions.Timeout:
            logger.error("Stability AI request timed out.")
        except requests.exceptions.RequestException as e:
            logger.error("Stability AI request failed: %s", e)
        except OSError as e:
            logger.error("Could not write generated image to disk: %s", e)
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
        analysis = random.choice(self.analysis_pool)
        closer = random.choice(self.closers)

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

        if closer:
            parts.append(closer)
            parts.append("")

        parts.append(f"({source}, {formatted_date})")
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
        for i, article in enumerate(articles, 1):
            title = article.get('title', '').strip()
            if title in self.posted_today:
                continue

            logger.info("Processing (%d/%d): %s", i, len(articles), title[:70])

            image_path = self.generate_ai_image(title)
            if not image_path:
                logger.warning("Falling back to a text-only post for this article.")

            post_content = self.create_engaging_post(article)

            if self.post_to_facebook(post_content, image_path):
                self.posted_today.append(title)
                self.save_posted_articles()
                posted_count += 1
                break  # one article per run, same as before
            else:
                logger.warning("Skipping to next article after failed post.")

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
