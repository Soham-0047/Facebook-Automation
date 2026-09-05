#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
INDIA TECH NEWS - FACEBOOK PAGE BOT  .  v5.3
=============================================
Single-file bot: fetches Indian tech news from many free sources, scores it,
writes a human-sounding post (English + optional Bengali companion), renders a
designed 1080x1350 image card, and publishes to a Facebook Page.

WHAT'S NEW IN v5.3 (top-rated content + sharper images)
  CONTENT QUALITY
    * Source-authority tiers: ET / Moneycontrol / Mint / Inc42 / YourStory ...
      get a quality bonus; gadget-deal SEO farms (gadgetsnow, 91mobiles,
      smartprix ...) get penalised - the same story from a better outlet wins.
    * SEO-junk firewall: "Top 10 / best under Rs X / deals / buying guide /
      unboxing / roundup / newsletter" listicles, viral-bait and digest
      headlines are heavily penalised - the page posts NEWS, not filler.
    * Money-magnitude scoring: amounts normalised to USD-millions, so a
      $1.2B round outranks a $5M one ("500 million users" no longer
      masquerades as money).
    * Freshness decay after 24h (stale stories sink), undated-article
      penalty, ALL-CAPS tabloid + empty-question headline penalties, and
      non-India global stories need 3+ corroborating outlets.
    * Google News India-edition topic feeds (Business + Technology top
      stories) joined the fan-out - genuinely top-ranked news gets
      corroborated and rises naturally.
    * Default posting bar raised 8 -> 9 (env: MIN_ENGAGEMENT_SCORE_TO_POST).
  IMAGES
    * Two accent palettes per category, rotated per post - the same topic
      never looks identical on back-to-back cards.
    * Soft drop shadows behind every photo-card headline, cinematic vignette
      on full-bleed / giant-stat posters, accent glow behind the big number.
  Earlier: v5.2 (6 layouts, stat posters, photo anti-repeat, calmer Bengali),
  v5.1 (Groq model-line migration), v5.0 (multi-source fan-out + scoring).
  IMAGES carry-over
    * 1080x1350 portrait cards, 6 rotating layouts; entity-aware Pexels
      search, vividness-ranked multi-candidate pick, auto enhancement,
      smart top-biased crop, cinematic scrims, category chip, auto-fit
      headline, accent bar, source line, optional page handle.
    * Designer fallback (duotone gradient + rotating motifs) when no photo.
    * 2x supersampling + LANCZOS downscale for crisp text; UUID filenames.
  STATE
    * state.json persists dedup keys, rotation counters, Bengali daily cap.
      Syncs cadence + Bengali day-count from the Page itself as a backup.
      Commit state.json from GitHub Actions (workflow file provided).

USAGE
    python bot.py                    # normal run (fetch -> score -> post)
    python bot.py --dry-run          # no posting; writes ./preview/
    python bot.py --bengali-preview  # force one Bengali companion preview
    python bot.py --preview-image    # render today's best card (no LLM, no post)
    python bot.py --no-image         # text-only post
    python bot.py --self-test        # offline checks + sample cards
    python bot.py --verbose          # debug logging

ENV / SECRETS  (Google Colab userdata OR environment; GitHub Actions secrets)
    FB_PAGE_ACCESS_TOKEN, FB_PAGE_ID     required to post
    NEWS_API_KEY                          recommended
    GROQ_API_KEY, GEMINI_API_KEY          at least one required
    PEXELS_API_KEY                        strongly recommended (photo cards)
    GNEWS_API_KEY                         optional extra source
    PAGE_HANDLE                           optional, e.g. "@indiatechdaily"
    POST_LINK_AS_FIRST_COMMENT            optional "true"/"false" (default false)
    MIN_ENGAGEMENT_SCORE_TO_POST          optional (default 9)
    BENGALI_PROBABILITY / BENGALI_MAX_PER_DAY / QUIET_HOURS / BOT_STATE_PATH
"""

from __future__ import annotations

import argparse
import difflib
import email.utils
import hashlib
import html as _html
import io
import json
import logging
import os
import random
import re
import shutil
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlparse

import requests

try:
    from PIL import Image, ImageDraw, ImageEnhance, ImageFont, ImageStat
    PIL_OK = True
except Exception:
    PIL_OK = False

try:
    import trafilatura
    TRAFILATURA_OK = True
except Exception:
    TRAFILATURA_OK = False

VERSION = "5.3.1"

# ------------------------------------------------------------------ paths & tz
BASE_DIR = Path(__file__).resolve().parent if "__file__" in globals() else Path.cwd()
STATE_PATH = Path(os.getenv("BOT_STATE_PATH", str(BASE_DIR / "state.json")))
FONT_DIR = Path(os.getenv("BOT_FONT_DIR", str(BASE_DIR / "fonts")))
PREVIEW_DIR = BASE_DIR / "preview"
IMAGE_DIR = BASE_DIR / "generated"

IST = timezone(timedelta(hours=5, minutes=30))
UTC = timezone.utc

# ------------------------------------------------------------------ tunables
def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, default)).strip())
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, default)).strip())
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on", "y"}


MIN_ENGAGEMENT_SCORE_TO_POST = _env_int("MIN_ENGAGEMENT_SCORE_TO_POST", 9)
QUIET_HOURS = _env_float("QUIET_HOURS", 20.0)        # hours of silence -> relax the bar
QUIET_RELAX_DROP = _env_int("QUIET_RELAX_DROP", 3)   # how much to relax it
# v5.2: tuned down - 0.5/2 felt spammy on the feed (user feedback)
BENGALI_PROBABILITY = _env_float("BENGALI_PROBABILITY", 0.18)
BENGALI_MAX_PER_DAY = _env_int("BENGALI_MAX_PER_DAY", 1)
POST_LINK_AS_FIRST_COMMENT = _env_bool("POST_LINK_AS_FIRST_COMMENT", False)
PAGE_HANDLE = os.getenv("PAGE_HANDLE", "").strip()   # e.g. "@indiatechdaily"
FB_API_VERSION = os.getenv("FB_API_VERSION", "v21.0")
HTTP_TIMEOUT = _env_int("HTTP_TIMEOUT", 25)

# card geometry (final output is always 1080x1350; rendered at 2x internally)
CARD_W, CARD_H = 1080, 1350
SUPERSAMPLE = _env_int("SUPERSAMPLE", 2)
# 6 rotating layouts - structural variety beats colour tweaks in the feed
CARD_VARIANTS = ["bottom_sheet", "top_banner", "split_card",
                 "full_bleed", "stat_hero", "magazine"]

# ==========================================================================
# SCORING CONFIG - keyword rules (ALL word-boundary safe, case-insensitive)
# ==========================================================================
# (category, points, regex)
_KEYWORD_RULES = [
    ("ipo", 6, r"\bIPOs?\b|\bDRHP\b|\bpublic issue\b|\bmarket debut\b|\bshare sale\b|\bgrey market premium\b|\blisting gains?\b|\bIPO subscription\b"),
    ("ipo", 3, r"\blisting\b|\bgoing public\b|\bNYSE\b|\bNasdaq\b|\bNSE\b|\bBSE\b"),
    ("ma", 6, r"\bacquir(?:e|es|ed|ing)?\b|\bacquisition\b|\btakeover\b|\bmerger\b|\bbuyout\b|\bdivest\w*\b|\bamalgamation\b"),
    ("ma", 3, r"\bstake (?:in|sale|purchase|buyout)\b|\bbuys\b|\bbought\b"),   # v5.3: 'buys' only - bare 'buy' is shopping context
    ("regulatory", 6, r"\bSEBI\b|\bRBI\b|\bCCI\b|\bEnforcement Directorate\b|\bPMLA\b|\bSFIO\b|\bIT raid\b|\bincome tax (?:raid|notice)\b|\bshow-?cause notice\b"),
    ("regulatory", 4, r"\bban(?:s|ned|ning)?\b|\bfine(?:s|d)?\b|\bpenalt\w+\b|\bprobe\b|\bcrackdown\b|\binvestigat\w+\b|\bregulat\w+\b|\banti-?trust\b|\bfraud\b|\bscam\b|\bcheat\w*\b|\bviolat\w+\b"),
    ("unicorn", 6, r"\bunicorns?\b|\bdecacorn\b|\bsoonicorn\b"),
    ("unicorn", 4, r"\bbillion-?dollar (?:valuation|startup|company)\b"),
    ("funding", 4, r"\brais(?:e|es|ed|ing)\b|\bfunding round\b|\bseed round\b|\bseries [a-g]\b(?: funding)?|\bpre-?series [a-g]\b|\bventure (?:funding|capital)\b|\bseed money\b|\bbridge round\b|\bdebt round\b"),
    ("funding", 2, r"\bfund(?:s|ed|ing)?\b|\binvest(?:ment|or|ors)?\b|\bVCs?\b|\bangels?\b"),
    ("layoffs", 5, r"\blayoff\w*\b|\bjob cuts?\b|\bsack(?:s|ed|ing)?\b|\bfired\b|\bdownsiz\w*\b|\brestructur\w*\b|\bresig\w+\b|\bstepping down\b|\bquit(?:s)?\b"),
    ("record", 4, r"\brecord\b|\ball-?time (?:high|low)\b|\bcross(?:es|ed|ing)?\s+\d+\s+(?:crore|lakh|million|billion|mn|bn|cr|users?|subscribers?|customers?|downloads?)\b|\bmilestone\b|\bhighest ever\b|\bfirst-?ever\b"),
    ("profit", 4, r"\bprofit(?:s|able)?\b|\bturn(?:s|ed)? (?:a )?profit\b|\bPAT\b|\bEBITDA\b|\bbreak-?even\b"),
    ("profit", 2, r"\brevenue\b|\bloss(?:es)?\b|\bmargin\w*\b"),
    ("ai", 3, r"\bAIs?\b|\bartificial intelligence\b|\bLLMs?\b|\bChatGPT\b|\bOpenAI\b|\bGPT-?\d\w*\b|\bGemini\b|\bClaude\b|\bgenerative AI\b|\bgen ?ai\b|\bcopilot\w*\b|\bmachine learning\b|\bdeep-?fake\w*\b|\bGPUs?\b"),
    ("gadgets", 2, r"\bsmartphone\w*\b|\blaptops?\b|\bflagships?\b|\bwearable\w*\b|\be-?SIMs?\b|\bearbuds?\b|\btws\b|\bfoldable\w*\b|\btablets?\b"),
    ("ev", 3, r"\belectric (?:scooter|vehicle|car|bike|two-?wheeler|three-?wheeler|SUV|bus)s?\b|\bEVs?\b|\bcharging station\w*\b|\be-?rickshaw\b|\be-?mobility\b"),
    ("space", 4, r"\bISRO\b|\bChandrayaan\b|\bGaganyaan\b|\bAditya-?L1\b|\bNISAR\b|\brocket\w*\b|\bsatellite\w*\b|\blaunch vehicle\b|\bspace mission\b"),
    ("cybersecurity", 4, r"\bhack\w*\b|\bcyber-?(?:attack\w*|crime\w*|security|fraud)\b|\bdata breach\w*\b|\bransomware\b|\bphishing\b|\bmalware\b|\bdata leak\w*\b"),
    ("telecom", 3, r"\bJio\b|\bAirtel\b|\bBharti\b|\bVodafone Idea\b|\btelecom\b|\bspectrum\b|\bStarlink\b|\b5G\b|\b4G\b|\bBSNL\b|\btariff hike\b"),
    ("fintech", 3, r"\bUPI\b|\bfintech\b|\bdigital payment\w*\b|\bRuPay\b|\bQR code\w*\b|\blending\b|\bNBFC\b|\bcredit card\w*\b|\bloan app\w*\b|\binsurtech\b|\bbroking\b|\bmutual fund\w*\b"),
    ("ecommerce", 2, r"\be-?commerce\b|\bquick commerce\b|\b10-?minute delivery\b|\bflash sale\b|\bBig Billion Days\b|\bGreat Indian (?:Festival|Sale)\b|\bdark store\w*\b|\bonline shopping\b"),
]

# (regex, penalty) - penalties applied once per rule unless the penalty is
# small (1), in which case each match costs 1 point, capped at 3.
_NEGATIVE_RULES = [
    (r"\bsponsored\b|\badvertorial\b|\baffiliate\b|\bcoupon\w*\b|\bpromo code\w*\b|\bdiscount\w*\b|\bbest deals?\b|\bdeals of the day\b|\bsale alert\b", 5),
    (r"\bhiring\b|\bjob (?:openings?|listings?|fair)\b|\bwalk-?in\b|\bcareers?\b", 8),
    (r"\bhoroscope\b|\bastrolog\w*\b|\bcricket\b|\bBollywood\b|\bIPL\b|\bbox office\b|\bentertainment\b|\brecipes?\b", 9),
    (r"\breportedly\b|\balleged\w*\b|\brumou?r\w*\b|\bleak\w*\b|\bmight\b|\bexpected to\b|\bplanning to\b|\bslated to\b|\bin talks\b|\bunconfirmed\b", 1),
    # v5.3: SEO/listicle firewall - evergreen deal filler, not news
    (r"\b(?:top|best|worst|cheapest|coolest)\s+\d+\w*\b|\b\d+\s+(?:best|top|must-?haves?)\b|\bunder\s?\u20b9\s?\d[\d,.]*\b|\bbuying guide\b|\bhow to (?:choose|buy|pick|get)\b|\bfull specs?\b|\bunboxing\b|\breview roundup\b|\bprice list\b|\bcoupon codes?\b|\b(?:deal|offer) alert\b", 9),
    # v5.3: digests / roundups / newsletters - yesterday's news sandwiches
    (r"\b(?:roundup|wrap-?up|digest|recap|week in review|this week in tech|top stories of the week|stories of the week|morning brief|newsletter)\b", 5),
    # v5.3.1: date-range recap series ("Between June 01 and June 06, as many
    # as 18 Indian startups raised..." / "...raised $1.08 Bn from June 22 to
    # June 27" - Inc42 weekly tracker in sentence form)
    (r"\b(?:Between|from)\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\s+(?:and|to)\s+", 6),
    # v5.3.1: hashtag video-clip headlines ("#WATCH | ...") - link posts, not stories
    (r"\#(?:WATCH|VIDEO|LIVE|LIVETV)\s*[|\u00bb:\-]?", 3),
    # v5.3: viral-bait headlines
    (r"\b(?:gone viral|goes viral|viral video|you won'?t believe|this one trick|shocking(?:ly)?|jaw-?dropping|mind-?blowing|unmissable|breaking the internet|internet by storm)\b", 6),
]

_MONEY_RE = re.compile(
    r"(?:\u20b9|Rs\.?|INR)\s?\d[\d,.]*\s?(?:crore|crores|cr\b|lakh|lakhs|billion|bn\b|million|mn\b)?"
    r"|\$\s?\d[\d,.]*\s?(?:billion|bn\b|million|mn\b|crore)?"
    r"|\b\d[\d,.]*\s?(?:crore|crores|cr\b|lakh|lakhs)\b"
    r"|\b\d[\d,.]*\s?(?:billion|bn\b|million|mn\b)\b",
    re.I,
)

# v5.3: source-authority tiers - the SAME story from a Tier-1 outlet beats
# an SEO gadget blog. Matched by substring on the article domain.
_DOMAIN_AUTHORITY = {
    # tier 1: India's business/tech incumbents
    "economictimes.indiatimes.com": 2.5,
    "moneycontrol.com": 2.5,
    "livemint.com": 2.5,
    "timesofindia.indiatimes.com": 2.0,
    "business-standard.com": 2.2,
    "hindubusinessline.com": 1.8,
    # tier 2: specialist tech/startup/business outlets
    "inc42.com": 2.0,
    "yourstory.com": 1.8,
    "vccircle.com": 1.8,
    "gadgets.ndtv.com": 1.6,
    "ndtv.com": 1.8,
    "cnbctv18.com": 1.8,
    "businesstoday.in": 1.6,
    "hindustantimes.com": 1.4,
    "indianexpress.com": 1.4,
    "thehindu.com": 1.4,
    "news18.com": 1.2,
    "techcrunch.com": 1.2,
    # penalised: gadget-deal / SEO farms (listicles, coupons, price pages)
    "gadgetsnow.com": -1.5, "91mobiles.com": -2.0, "mysmartprice.com": -2.0,
    "smartprix.com": -2.0, "pricebaba.com": -1.5, "gizbot.com": -1.5,
    "fonearena.com": -1.0, "cashify.in": -1.5, "phonearena.com": -1.0,
    # v5.3.1: social-media aggregators are not publications - weak sourcing,
    # and they must not masquerade as corroboration for the India gate
    "linkedin": -2.5, "instagram": -2.5, "facebook": -2.5, "twitter": -2.5,
    "x.com": -2.5, "youtube": -2.5, "reddit": -2.5, "medium": -1.0,
}

_AUTHORITY_LOOKUP = sorted(_DOMAIN_AUTHORITY.items(), key=lambda kv: -len(kv[0]))

# v5.3.1: social-media aggregators - they rank down AND don't count as
# corroboration (an ET + Instagram + Facebook cluster is really 1 outlet)
_SOCIAL_AGGREGATORS = ("linkedin", "instagram", "facebook", "twitter",
                       "youtube", "reddit", "medium", "t.me", "pinterest", "quora")


def _is_social(domain: str) -> bool:
    d = (domain or "").lower()
    if d == "x.com" or d.endswith(".x.com"):
        return True
    return any(k in d for k in _SOCIAL_AGGREGATORS)


def _domain_authority(domain: str) -> float:
    d = (domain or "").lower()
    if not d:
        return 0.0
    for key, val in _AUTHORITY_LOOKUP:
        if key in d:            # suffix or substring match (m. / tech. subdomains)
            return float(val)
    return 0.0


# category used for the chip label / image theme when several match
_CATEGORY_PRIORITY = [
    "regulatory", "ipo", "ma", "unicorn", "space", "cybersecurity", "layoffs",
    "funding", "fintech", "ev", "telecom", "ai", "ecommerce", "record",
    "profit", "gadgets", "general",
]

# company / entity table: name -> (points, category hint)
_ENTITIES = {
    # conglomerates & IT majors
    "reliance jio": (5, "telecom"), "reliance": (4, "general"), "ambani": (4, "general"),
    "tata digital": (5, "ecommerce"), "tata neu": (5, "ecommerce"), "tata": (3, "general"),
    "adani": (4, "general"),
    "infosys": (4, "general"), "tcs": (4, "general"), "wipro": (4, "general"),
    "hcltech": (4, "general"), "hcl tech": (4, "general"), "tech mahindra": (4, "general"),
    "ltimindtree": (3, "general"), "zoho": (4, "general"), "freshworks": (4, "general"),
    "browserstack": (3, "general"), "postman": (3, "general"),
    # e-commerce & food delivery
    "flipkart": (5, "ecommerce"), "amazon india": (4, "ecommerce"), "amazon": (3, "ecommerce"),
    "zomato": (5, "ecommerce"), "swiggy": (5, "ecommerce"), "zepto": (5, "ecommerce"),
    "blinkit": (4, "ecommerce"), "instamart": (3, "ecommerce"), "bigbasket": (3, "ecommerce"),
    "meesho": (4, "ecommerce"), "myntra": (3, "ecommerce"), "nykaa": (4, "ecommerce"),
    "delhivery": (3, "ecommerce"), "shiprocket": (3, "ecommerce"),
    # fintech
    "paytm": (5, "fintech"), "phonepe": (5, "fintech"), "bharatpe": (4, "fintech"),
    "razorpay": (4, "fintech"), "cashfree": (3, "fintech"), "payu": (3, "fintech"),
    "cred": (4, "fintech"), "mobikwik": (4, "fintech"), "groww": (4, "fintech"),
    "zerodha": (4, "fintech"), "upstox": (3, "fintech"), "angel one": (3, "fintech"),
    "policybazaar": (3, "fintech"), "google pay": (3, "fintech"),
    # edtech
    "byju's": (4, "general"), "byju": (4, "general"), "byjus": (4, "general"),
    "unacademy": (3, "general"), "physics wallah": (3, "general"), "upgrad": (3, "general"),
    # mobility & EV
    "ola electric": (5, "ev"), "ather": (4, "ev"), "tata motors": (3, "ev"),
    "mahindra": (3, "ev"), "hyundai india": (3, "ev"), "rapido": (3, "general"),
    "uber india": (3, "general"), "namma yatri": (3, "general"), "yulu": (3, "ev"),
    # telecom & space
    "airtel": (4, "telecom"), "bharti airtel": (5, "telecom"),
    "vodafone idea": (4, "telecom"), "bsnl": (3, "telecom"),
    "starlink": (4, "telecom"), "oneweb": (3, "telecom"),
    "isro": (5, "space"), "chandrayaan": (5, "space"), "gaganyaan": (5, "space"), "nisar": (4, "space"),
    # global majors with an India angle
    "apple": (3, "gadgets"), "google": (3, "ai"), "microsoft": (3, "ai"),
    "openai": (4, "ai"), "nvidia": (4, "ai"), "tesla": (4, "ev"),
    "samsung": (3, "gadgets"), "xiaomi": (3, "gadgets"), "oneplus": (3, "gadgets"),
    "vivo": (3, "gadgets"), "oppo": (3, "gadgets"), "realme": (3, "gadgets"),
    "nothing phone": (3, "gadgets"), "motorola": (3, "gadgets"),
    "meta": (3, "ai"), "whatsapp": (3, "general"), "youtube": (3, "general"),
    # travel, gaming, health
    "oyo": (4, "general"), "makemytrip": (3, "general"), "ixigo": (3, "general"),
    "dream11": (4, "general"), "nazara": (3, "general"),
    "pharmeasy": (3, "general"), "practo": (3, "general"),
}

# ==========================================================================
# NEWS SOURCES
# ==========================================================================
RSS_FEEDS = [
    ("ET Tech", "https://economictimes.indiatimes.com/tech/rssfeeds/13357270.cms"),
    ("ET Tech Top", "https://tech.economictimes.indiatimes.com/rss/topstories"),
    ("ET Startups", "https://economictimes.indiatimes.com/rssfeeds/2094568912.cms"),
    ("TOI Tech", "https://timesofindia.indiatimes.com/rssfeeds/66949542.cms"),
    ("Gadgets360", "https://gadgets.ndtv.com/rss/news"),
    ("YourStory", "https://yourstory.com/feed"),
    ("Inc42", "https://inc42.com/feed/"),
    ("Moneycontrol Tech", "https://www.moneycontrol.com/rss/technology.xml"),
    ("Livemint Tech", "https://www.livemint.com/rss/technology"),
    ("News18 Tech", "https://www.news18.com/rss/tech.xml"),
    ("Business Standard Tech", "https://www.business-standard.com/rss/technology-102.rss"),
    ("VCCircle", "https://www.vccircle.com/feed"),
]

# free, fresh, no API key - the workhorse for targeted topic fan-out
GOOGLE_NEWS_QUERIES = [
    "India startup funding",
    "India tech IPO",
    "Indian startup unicorn",
    "India technology acquisition merger",
    "India AI artificial intelligence",
    "Indian tech company SEBI RBI",
    # v5.3: extra targeted fan-out
    "India semiconductor chip fab",
    "India tech layoffs",
]

# Bengali-language Google News queries (companion-post grounding + previews)
GOOGLE_NEWS_BN_QUERIES = ["প্রযুক্তি ভারত", "ভারতীয় স্টার্টআপ", "আইপিও ভারত", "কৃত্রিম বুদ্ধিমত্তা"]

# v5.3: Google News India-edition topic feeds - these ARE the top-rated
# stories; joining the fan-out lets corroboration promote real big news.
GOOGLE_NEWS_TOPIC_FEEDS = [
    ("GoogleNews Business", "https://news.google.com/rss/headlines/topic/BUSINESS?hl=en-IN&gl=IN&ceid=IN:en"),
    ("GoogleNews Technology", "https://news.google.com/rss/headlines/topic/TECHNOLOGY?hl=en-IN&gl=IN&ceid=IN:en"),
]

NEWSAPI_EVERYTHING_QUERIES = [
    "India startup funding raise",
    "India IPO DRHP listing",
    "India tech acquisition",
    "India AI artificial intelligence",
]

# ==========================================================================
# IMAGE CONFIG
# ==========================================================================
ENTITY_VISUAL_HINTS = {
    # food & quick commerce
    "zomato": "food delivery rider bike city street",
    "swiggy": "food delivery rider bag bike street",
    "zepto": "delivery rider bike grocery bag city",
    "blinkit": "grocery delivery rider bike city",
    "instamart": "grocery delivery rider bike",
    "bigbasket": "grocery boxes delivery doorstep",
    # e-commerce
    "flipkart": "warehouse workers parcels boxes",
    "amazon": "delivery parcels boxes warehouse",
    "meesho": "online shopping packages delivery home",
    "myntra": "fashion clothes shopping store rack",
    "nykaa": "cosmetics beauty products flatlay",
    "delhivery": "delivery van boxes logistics",
    "shiprocket": "parcel boxes courier",
    # fintech
    "paytm": "person paying smartphone qr code shop",
    "phonepe": "person scanning qr code payment phone",
    "google pay": "person paying smartphone shop counter",
    "bharatpe": "shopkeeper qr code payment counter",
    "razorpay": "digital payment laptop credit card",
    "cred": "credit cards minimal table",
    "mobikwik": "mobile wallet payment phone",
    "groww": "stock market chart phone screen",
    "zerodha": "stock trading chart screens",
    "upstox": "stock chart phone screen",
    "angel one": "stock trading phone screen chart",
    "policybazaar": "insurance documents signing",
    "payu": "online payment laptop card",
    "cashfree": "digital payment phone hand card",
    # edtech
    "byju's": "student studying laptop home",
    "byju": "student studying laptop home",
    "byjus": "student studying laptop home",
    "unacademy": "student online class laptop",
    "physics wallah": "teacher online class whiteboard",
    "upgrad": "professional studying laptop office",
    # mobility & EV
    "ola electric": "electric scooter city street",
    "ather": "electric scooter charging plug",
    "tata motors": "car factory assembly line",
    "mahindra": "suv car showroom",
    "rapido": "bike taxi rider helmet street",
    "uber": "car ride city street night",
    "namma yatri": "auto rickshaw driver street",
    "yulu": "bicycle sharing city street",
    # IT & software
    "infosys": "modern office campus glass building",
    "tcs": "corporate office building glass",
    "wipro": "modern office lobby",
    "hcltech": "software engineers office computers",
    "hcl tech": "software engineers office computers",
    "tech mahindra": "office workers meeting room",
    "ltimindtree": "modern office workspace",
    "zoho": "startup office team working",
    "freshworks": "software team office laptops",
    # telecom
    "jio": "person using smartphone city street",
    "airtel": "telecom tower sunset sky",
    "bharti airtel": "telecom tower sunset sky",
    "vodafone idea": "telecom antenna city skyline",
    "bsnl": "telephone cables street",
    "starlink": "satellite dish night sky",
    "oneweb": "satellite orbit earth",
    # space
    "isro": "rocket launch night",
    "chandrayaan": "moon surface craters",
    "gaganyaan": "astronaut space suit",
    "nisar": "satellite orbit earth",
    # AI & global tech
    "openai": "artificial intelligence robot abstract",
    "nvidia": "computer gpu graphics card closeup",
    "google": "modern tech office colorful",
    "microsoft": "office building glass modern",
    "meta": "virtual reality headset person",
    "apple": "smartphone closeup hand minimal",
    "samsung": "smartphone store display",
    "xiaomi": "smartphone flatlay tech",
    "oneplus": "smartphone closeup dark",
    "vivo": "smartphone camera closeup",
    "oppo": "smartphone hand selfie",
    "realme": "smartphone colorful tech",
    "nothing phone": "minimal tech earbuds white",
    "motorola": "smartphone closeup edge",
    # travel & gaming
    "oyo": "hotel room bed clean",
    "makemytrip": "airport traveler suitcase",
    "ixigo": "train station platform india",
    "dream11": "cricket stadium floodlights",
    "nazara": "esports gaming setup rgb",
    # health & conglomerates
    "pharmeasy": "medicine pills pharmacy shelf",
    "practo": "doctor consultation stethoscope",
    "reliance": "corporate towers city skyline",
    "tata": "heritage corporate building india",
    "adani": "port containers cranes",
    # generic tech nouns
    "data center": "server room blue lights",
    "semiconductor": "silicon wafer chip macro",
    "chip": "circuit board macro",
    "5g": "network antenna city sunset",
}

CATEGORY_IMAGE_QUERIES = {
    "ai": ["robot hand futuristic light", "person using laptop ai interface", "abstract neural lights blue"],
    "funding": ["startup team office whiteboard", "handshake business meeting", "young team working office laptops"],
    "ipo": ["stock market screens numbers", "stock exchange display", "stock market bull statue"],
    "ma": ["business handshake boardroom", "contract signing pen hands", "two office buildings glass"],
    "regulatory": ["government building columns india", "gavel law book court", "documents official stamps"],
    "unicorn": ["startup team celebration office", "confetti office party"],
    "fintech": ["qr code payment phone shop", "digital banking phone hand", "indian currency notes phone"],
    "ev": ["electric scooter city street", "ev charging station car", "electric car charging plug"],
    "gadgets": ["smartphone closeup hand", "laptop desk minimal coffee", "tech gadgets flatlay"],
    "space": ["rocket launch night sky", "satellite orbit earth space", "mission control room screens"],
    "cybersecurity": ["cyber security lock digital", "hacker code screen dark", "padlock circuit board"],
    "telecom": ["telecom tower sunset sky", "network antenna city skyline", "fiber optic cables lights"],
    "ecommerce": ["delivery packages boxes van", "courier parcel doorstep", "warehouse conveyor boxes"],
    "layoffs": ["empty office desks chairs", "person leaving office box belongings"],
    "record": ["celebration team office success", "mountain summit sunrise"],
    "profit": ["stock chart growth green screen", "coins stacked growth plant"],
    "general": ["modern indian city skyline night", "team working office laptops", "abstract technology blue lights"],
}

FALLBACK_IMAGE_QUERIES = [
    "modern city skyline night india",
    "people office laptops working",
    "abstract blue technology lights",
    "india street smartphone person",
]

# per-category design system: chip label + accent colour + designer gradient
# v5.3: accent2 = rotated second palette - same category never looks identical
CATEGORY_THEME = {
    "ai":          {"label": "AI",           "accent": (86, 204, 242),  "accent2": (167, 139, 250), "grad": [(10, 14, 26), (26, 60, 120)]},
    "funding":     {"label": "FUNDING",      "accent": (255, 170, 60),  "accent2": (245, 200, 66),  "grad": [(30, 20, 8), (120, 60, 20)]},
    "ipo":         {"label": "IPO",          "accent": (80, 220, 180),  "accent2": (170, 235, 110), "grad": [(6, 26, 24), (20, 80, 70)]},
    "ma":          {"label": "M&A",          "accent": (200, 160, 255), "accent2": (240, 130, 200), "grad": [(22, 14, 36), (80, 40, 120)]},
    "regulatory":  {"label": "REGULATION",   "accent": (255, 120, 120), "accent2": (255, 170, 95),  "grad": [(30, 10, 12), (110, 30, 40)]},
    "unicorn":     {"label": "UNICORN",      "accent": (255, 140, 220), "accent2": (190, 145, 255), "grad": [(28, 10, 30), (110, 30, 110)]},
    "fintech":     {"label": "FINTECH",      "accent": (120, 220, 140), "accent2": (140, 230, 215), "grad": [(8, 24, 14), (20, 90, 60)]},
    "ev":          {"label": "EV",           "accent": (120, 230, 200), "accent2": (175, 235, 120), "grad": [(6, 24, 20), (20, 80, 70)]},
    "gadgets":     {"label": "GADGETS",      "accent": (255, 200, 90),  "accent2": (255, 150, 120), "grad": [(24, 18, 8), (100, 70, 20)]},
    "space":       {"label": "SPACE",        "accent": (140, 180, 255), "accent2": (180, 160, 255), "grad": [(8, 10, 30), (30, 40, 100)]},
    "cybersecurity": {"label": "CYBERSECURITY", "accent": (110, 230, 120), "accent2": (110, 200, 240), "grad": [(8, 20, 10), (20, 70, 30)]},
    "telecom":     {"label": "TELECOM",      "accent": (255, 150, 100), "accent2": (250, 205, 95),  "grad": [(26, 14, 8), (90, 50, 20)]},
    "ecommerce":   {"label": "ECOMMERCE",    "accent": (255, 120, 160), "accent2": (255, 175, 120), "grad": [(28, 8, 18), (100, 30, 60)]},
    "layoffs":     {"label": "LAYOFFS",      "accent": (255, 120, 120), "accent2": (235, 150, 150), "grad": [(26, 10, 12), (90, 30, 40)]},
    "record":      {"label": "RECORD",       "accent": (255, 210, 90),  "accent2": (255, 240, 130), "grad": [(28, 20, 6), (110, 80, 20)]},
    "profit":      {"label": "BUSINESS",     "accent": (150, 230, 120), "accent2": (200, 240, 120), "grad": [(10, 22, 10), (30, 80, 30)]},
    "general":     {"label": "TECH",         "accent": (110, 180, 255), "accent2": (100, 220, 220), "grad": [(10, 14, 24), (30, 50, 100)]},
}

BN_CATEGORY_LABELS = {
    "funding": "ফান্ডিং", "ipo": "আইপিও", "ma": "অধিগ্রহণ", "regulatory": "রেগুলেশন",
    "unicorn": "ইউনিকর্ন", "fintech": "ফিনটেক", "ev": "ইভি", "gadgets": "গ্যাজেট",
    "space": "স্পেস", "cybersecurity": "সাইবার সিকিউরিটি", "telecom": "টেলিকম",
    "ecommerce": "ই-কমার্স", "layoffs": "ছাঁটাই", "record": "রেকর্ড",
    "profit": "বিজনেস", "ai": "এআই", "general": "টেক",
}

# ==========================================================================
# LOGGING / SECRETS / HTTP / STATE
# ==========================================================================
log = logging.getLogger("india-tech-bot")


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    )
    if not verbose:
        for noisy in ("urllib3", "requests", "PIL"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
    # trafilatura's "discarding data" warnings are noise (we handle the fallback)
    for noisy in ("trafilatura", "htmldate"):
        logging.getLogger(noisy).setLevel(logging.ERROR)


def _get_secret(name: str, default: str = "") -> str:
    """Colab userdata first, then environment variables."""
    try:
        from google.colab import userdata  # type: ignore
        try:
            val = userdata.get(name)
            if val:
                return val
        except Exception:
            pass
    except Exception:
        pass
    return os.environ.get(name, default)


_SESSION = requests.Session()
_SESSION.headers.update({
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 IndiaTechPageBot/" + VERSION),
})


def _http_get(url, *, params=None, headers=None, timeout=None, retries=2):
    """GET with retry/backoff (safe - idempotent). Returns Response or None."""
    timeout = timeout or HTTP_TIMEOUT
    last_err = None
    for attempt in range(retries + 1):
        try:
            r = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.6 * (2 ** attempt) + random.random())
                continue
            if r.status_code != 200:
                log.debug("GET %s -> HTTP %s", url[:110], r.status_code)
                return None
            return r
        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.2 + random.random())
    log.debug("GET failed %s (%s)", url[:110], last_err)
    return None


def _http_get_bytes(url, timeout=25, retries=1):
    r = _http_get(url, timeout=timeout, retries=retries)
    return r.content if r is not None else None


def _http_post(url, *, data=None, json_body=None, files=None, headers=None, timeout=60):
    """Plain POST (LLM calls etc). Single attempt per call."""
    r = _SESSION.post(url, data=data, json=json_body, files=files, headers=headers, timeout=timeout)
    if r.status_code != 200:
        log.debug("POST %s -> HTTP %s %s", url[:110], r.status_code, r.text[:160])
        return None
    try:
        return r.json()
    except Exception:
        return None


# ------------------------------------------------------------------ state
def _default_state() -> dict:
    return {
        "posted_keys": [],        # md5 of normalized title + url (last 400)
        "posted_titles": [],      # normalized titles (last 250) for fuzzy dedup
        "last_post_ts": 0.0,
        "recent_categories": [],  # last 5 posted categories
        "recent_domains": [],     # last 3 posted domains
        "recent_shapes": [],      # last 3 post shapes used
        "recent_photo_ids": [],   # last 10 Pexels photo ids (feed never repeats a photo)
        "recent_personas": [],    # last persona id
        "post_counter": 0,        # drives shape / card-variant rotation
        "bengali_date": "",       # IST date string for the daily Bengali cap
        "bengali_count": 0,
    }


def _load_state() -> dict:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                st = _default_state()
                st.update({k: v for k, v in data.items() if k in st})
                return st
    except Exception as e:
        log.warning("state load failed (%s) - starting fresh", e)
    return _default_state()


def _save_state(state: dict) -> None:
    try:
        state["posted_keys"] = (state.get("posted_keys") or [])[-400:]
        state["posted_titles"] = (state.get("posted_titles") or [])[-250:]
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception as e:
        log.warning("state save failed: %s", e)


def _now_ist() -> datetime:
    return datetime.now(IST)


def _today_ist_str() -> str:
    return _now_ist().strftime("%Y-%m-%d")


# ------------------------------------------------------------------ text utils
_BN_RE = re.compile(r"[\u0980-\u09FF]")
_BN_DIGIT_MAP = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")


def _bn_digits(s) -> str:
    return str(s).translate(_BN_DIGIT_MAP)


def _bengali_ratio(text: str) -> float:
    if not text:
        return 0.0
    n = len(_BN_RE.findall(text))
    return n / max(1, len(text))


def _strip_html(s: str) -> str:
    s = _html.unescape(s or "")
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _normalize_title(t: str) -> str:
    t = _html.unescape(t or "").lower()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


def _strip_gnews_publisher(title: str, publisher: str) -> str:
    if publisher:
        suf = " - " + publisher.strip()
        if title.endswith(suf):
            return title[: -len(suf)].strip()
        # tolerate slight mismatches (e.g. "The Hindu" vs "Hindu")
        if " - " in title:
            head, tail = title.rsplit(" - ", 1)
            if tail.strip() and tail.strip().lower() in publisher.strip().lower():
                return head.strip()
    return title


def _titles_similar(t1: str, t2: str) -> bool:
    """Fuzzy near-duplicate detection across sources."""
    a, b = _normalize_title(t1), _normalize_title(t2)
    if not a or not b:
        return False
    if a == b:
        return True
    ta, tb = set(a.split()), set(b.split())
    if not ta or not tb:
        return False
    overlap = len(ta & tb)
    if overlap < 3:
        return False
    if overlap / min(len(ta), len(tb)) < 0.55:
        return False
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    # headlines sharing >=5 tokens (same story, reworded) get a lower bar
    thresh = 0.55 if overlap >= 5 else 0.62
    return ratio >= thresh


def _dedup_key(art) -> str:
    raw = f"{_normalize_title(art.title)}|{art.url}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _is_duplicate(art, state: dict) -> bool:
    if _dedup_key(art) in set(state.get("posted_keys") or []):
        return True
    for t in state.get("posted_titles") or []:
        if _titles_similar(art.title, t):
            return True
    return False


def _parse_dt(s: str):
    if not s:
        return None
    s = s.strip()
    try:
        dt = email.utils.parsedate_to_datetime(s)
        if dt is not None:
            return dt.astimezone(UTC) if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        pass
    t = s.replace("Z", "+00:00")
    t = t.split("+")[0].split(".")[0].strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(t, fmt).replace(tzinfo=UTC)
        except Exception:
            continue
    return None


def _hours_ago(art) -> str:
    if not art.published_at:
        return "recently"
    h = (datetime.now(UTC) - art.published_at).total_seconds() / 3600
    return f"{max(0, int(h))}h"

# ==========================================================================
# ARTICLE MODEL + FETCHERS
# ==========================================================================
@dataclass
class Article:
    title: str
    url: str
    source: str
    domain: str
    published_at: datetime | None = None
    description: str = ""
    category: str = "general"
    score: float = 0.0
    corroborations: int = 1
    via: str = ""


def _parse_feed_bytes(content: bytes, source_label: str, via: str) -> list:
    """Tolerant RSS 2.0 / Atom / RDF parser (stdlib only, no feedparser dep)."""
    try:
        root = ET.fromstring(content)
    except Exception:
        return []

    def _ln(e):
        return e.tag.rsplit("}", 1)[-1].lower()

    out = []
    for it in (e for e in root.iter() if _ln(e) in ("item", "entry")):
        title = ""
        link = ""
        alt_link = ""
        date_s = ""
        desc = ""
        publisher = ""
        for ch in it:
            n = _ln(ch)
            if n == "title":
                title = (ch.text or "").strip()
            elif n == "link":
                href = (ch.get("href") or ch.text or "").strip()
                if href and href.startswith("http"):
                    if (ch.get("rel") or "alternate") == "alternate":
                        if not alt_link:
                            alt_link = href
                    elif not link:
                        link = href
            elif n in ("pubdate", "published", "updated", "date", "created"):
                date_s = date_s or (ch.text or "").strip()
            elif n in ("description", "summary", "content"):
                desc = desc or _strip_html(ch.text or "")[:400]
            elif n == "source":
                publisher = (ch.text or "").strip()
        link = alt_link or link
        if not title or not link:
            continue
        dom = urlparse(link).netloc.lower().replace("www.", "")
        title_clean = title
        if publisher and "news.google" in dom:
            title_clean = _strip_gnews_publisher(title, publisher)
            dom = publisher.lower().replace(" ", "")[:40] or dom
        out.append(Article(
            title=title_clean, url=link, source=publisher or source_label, domain=dom,
            published_at=_parse_dt(date_s), description=desc, via=via,
        ))
    return out


def _fetch_one_feed(label, url, via):
    r = _http_get(url, timeout=15, retries=1)
    if r is None:
        return []
    return _parse_feed_bytes(r.content, label, via)


def fetch_rss_all(feeds, via="rss") -> list:
    arts = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_fetch_one_feed, lbl, url, via) for lbl, url in feeds]
        for f in as_completed(futs):
            try:
                arts.extend(f.result())
            except Exception:
                pass
    return arts


def fetch_google_news(queries, lang="en") -> list:
    """Google News RSS search: free, fresh, no key - targeted topic fan-out."""
    feeds = []
    for q in queries:
        if lang == "bn":
            url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=bn&gl=IN&ceid=IN:bn"
        else:
            url = f"https://news.google.com/rss/search?q={quote_plus(q)}&hl=en-IN&gl=IN&ceid=IN:en"
        feeds.append((f"GoogleNews", url))
    return fetch_rss_all(feeds, via="google-news")


def _newsapi_article(a: dict, via: str):
    url = a.get("url") or ""
    dom = urlparse(url).netloc.lower().replace("www.", "") if url else ""
    return Article(
        title=(a.get("title") or "").strip(),
        url=url,
        source=(a.get("source") or {}).get("name") or "NewsAPI",
        domain=dom,
        published_at=_parse_dt(a.get("publishedAt") or ""),
        description=_strip_html(a.get("description") or "")[:400],
        via=via,
    )


def fetch_newsapi() -> list:
    key = _get_secret("NEWS_API_KEY")
    if not key:
        log.info("NEWS_API_KEY not set - skipping NewsAPI (RSS still covers you)")
        return []
    base = "https://newsapi.org/v2"
    out = []

    def _call(path, params):
        params = dict(params)
        params["apiKey"] = key
        r = _http_get(base + path, params=params, timeout=20, retries=1)
        if r is None:
            return []
        try:
            return r.json().get("articles") or []
        except Exception:
            return []

    for cat in ("technology", "business"):
        for a in _call("/top-headlines", {"country": "in", "category": cat, "pageSize": 50}):
            out.append(_newsapi_article(a, f"newsapi/{cat}"))
    since = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    for q in NEWSAPI_EVERYTHING_QUERIES:
        for a in _call("/everything", {"q": q, "language": "en", "sortBy": "publishedAt",
                                       "pageSize": 15, "from": since}):
            out.append(_newsapi_article(a, "newsapi/everything"))
    return out


def fetch_hn() -> list:
    """Hacker News (Algolia) - free, no key. India-related tech stories only."""
    try:
        ts = int((datetime.now(UTC) - timedelta(hours=48)).timestamp())
        r = _http_get("https://hn.algolia.com/api/v1/search_by_date",
                      params={"query": "India", "tags": "story", "hitsPerPage": 40,
                              "numericFilters": f"created_at_i>{ts}"},
                      timeout=15, retries=1)
        if r is None:
            return []
        hits = r.json().get("hits", [])
        out = []
        for h in hits:
            t = (h.get("title") or "").strip()
            if not t or (h.get("points") or 0) < 4:
                continue
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            out.append(Article(
                title=t, url=url, source="Hacker News",
                domain=urlparse(url).netloc.lower().replace("www.", ""),
                published_at=datetime.fromtimestamp(int(h.get("created_at_i") or 0), tz=UTC),
                description="", via="hn",
            ))
        return out
    except Exception as e:
        log.debug("HN fetch failed: %s", e)
        return []


def fetch_gnews_api() -> list:
    """Optional GNews.io source (free tier) - used only if GNEWS_API_KEY is set."""
    key = _get_secret("GNEWS_API_KEY")
    if not key:
        return []
    r = _http_get("https://gnews.io/api/v4/search",
                  params={"q": "India (startup OR technology OR IPO OR funding)",
                          "lang": "en", "country": "in", "max": 15, "apikey": key},
                  timeout=20, retries=1)
    if r is None:
        return []
    try:
        items = r.json().get("articles", [])
    except Exception:
        return []
    out = []
    for a in items:
        url = a.get("url") or ""
        if not url:
            continue
        out.append(Article(
            title=(a.get("title") or "").strip(),
            url=url,
            source=(a.get("source") or {}).get("name") or "GNews",
            domain=urlparse(url).netloc.lower().replace("www.", ""),
            published_at=_parse_dt(a.get("publishedAt") or ""),
            description=_strip_html(a.get("description") or "")[:400],
            via="gnews",
        ))
    return out


def fetch_all_articles() -> list:
    seen = set()
    arts = []
    stats = {}

    def _add(label, items):
        n = 0
        for a in items:
            if not a or not a.title or not a.url:
                continue
            if a.title.lower() in ("[removed]", "removed"):
                continue
            if a.url in seen:
                continue
            seen.add(a.url)
            arts.append(a)
            n += 1
        stats[label] = n

    _add("newsapi", fetch_newsapi())
    _add("rss", fetch_rss_all(RSS_FEEDS))
    _add("google-news", fetch_google_news(GOOGLE_NEWS_QUERIES))
    _add("google-top", fetch_rss_all(GOOGLE_NEWS_TOPIC_FEEDS, via="google-top"))
    _add("hackernews", fetch_hn())
    _add("gnews", fetch_gnews_api())
    log.info("Fetched %d unique articles: %s", len(arts),
             ", ".join(f"{k}={v}" for k, v in stats.items()))
    return arts


# ------------------------------------------------------------------ enrichment
def _wikipedia_context(title: str) -> str:
    """One short company intro from Wikipedia (best-effort grounding)."""
    t = title.lower()
    for name in sorted(_ENTITIES, key=len, reverse=True):
        if re.search(r"\b" + re.escape(name) + r"\b", t):
            try:
                r = _http_get("https://en.wikipedia.org/w/api.php",
                              params={"action": "query", "format": "json", "prop": "extracts",
                                      "exintro": 1, "explaintext": 1, "redirects": 1, "titles": name},
                              timeout=12, retries=0)
                if r is None:
                    return ""
                pages = (r.json().get("query") or {}).get("pages") or {}
                for _, p in pages.items():
                    ex = (p.get("extract") or "").strip()
                    if ex and "may refer to" not in ex.lower():
                        return ex[:420]
            except Exception:
                pass
            return ""
    return ""


def _article_grounding(art) -> str:
    """Scrape the article body (trafilatura if available) for the LLM."""
    if "news.google" in (art.domain or ""):
        return ""
    if art.url.lower().endswith(".pdf"):
        return ""
    try:
        r = _http_get(art.url, timeout=15, retries=0)
        if r is None:
            return ""
        page = r.text[:500000]
    except Exception:
        return ""
    if TRAFILATURA_OK:
        try:
            txt = trafilatura.extract(page, url=art.url, include_comments=False,
                                      include_tables=False, favor_recall=True)
            if txt:
                return txt[:1400]
        except Exception:
            pass
    return _strip_html(page)[:800]


# ==========================================================================
# SCORING ENGINE
# ==========================================================================
_COMPILED_RULES = [(cat, pts, re.compile(pat, re.I)) for cat, pts, pat in _KEYWORD_RULES]
_COMPILED_NEGATIVE = [(re.compile(pat, re.I), pts) for pat, pts in _NEGATIVE_RULES]
_COMPILED_ENTITIES = {
    name: (pts, cat, re.compile(r"\b" + re.escape(name) + r"\b", re.I))
    for name, (pts, cat) in _ENTITIES.items()
}


# v5.3: magnitude-aware money scoring - a $1.2B round outranks a $5M one.
# "500 million users" is NOT money, so a nearby user-noun kills the match.
_NON_MONEY_AFTER = re.compile(
    r"\s(?:users?|customers?|subscribers?|downloads?|people|jobs?|employees?|"
    r"units?|devices?|phones?|handsets?|rides?|orders?|transactions?|stores?|"
    r"apps?|videos?|views?|hours?|tonnes?|cars?|scooters?|satellites?)\b", re.I)
_NON_MONEY_BEFORE = re.compile(
    r"(?:users?|customers?|subscribers?|people|downloads?)\s+"
    r"(?:cross(?:es|ed)?|tops?|hits?|reach(?:es|ed)?|surpass\w*)\s*$"
    r"|(?:under|from|starting|priced|costing|just|only)\s*$", re.I)   # price-listing, not a deal amount


def _money_usd_m(raw: str) -> float:
    """One money string ('Rs 4,400 crore', '$234 million') -> USD millions."""
    nm = re.search(r"(\d[\d,]*(?:\.\d+)?)", raw)
    if not nm:
        return 0.0
    try:
        val = float(nm.group(1).replace(",", ""))
    except Exception:
        return 0.0
    low = raw.lower()
    inr = ("\u20b9" in raw) or low.startswith("rs") or low.startswith("inr")
    usd = "$" in raw
    if "crore" in low or re.search(r"\bcrs?\b", low):
        unit = 0.12                       # Rs 1 crore ~ $0.12M
    elif "lakh" in low:
        unit = 0.0012
    elif "billion" in low or re.search(r"\bbn\b", low):
        unit = 1000.0 if (usd or not inr) else 12.0
    elif "million" in low or re.search(r"\bmn\b", low):
        unit = 1.0 if (usd or not inr) else 0.012
    elif usd:
        unit = 1e-6
    elif inr:
        unit = 1.2e-8
    else:
        return 0.0
    return val * unit


def _money_score(text: str) -> float:
    """Largest amount in the text, mapped to points (capped at 4)."""
    best, n = 0.0, 0
    for m in _MONEY_RE.finditer(text):
        tail = text[m.end():m.end() + 18]
        if _NON_MONEY_AFTER.match(tail):
            continue                     # counts/users, not cash
        head = text[max(0, m.start() - 24):m.start()]
        if _NON_MONEY_BEFORE.search(head):
            continue                     # "users cross ..."
        v = _money_usd_m(m.group(0))
        if v > 0:
            n += 1
            best = max(best, v)
    if n == 0:
        return 0.0
    if best >= 500:
        s = 4.0
    elif best >= 50:
        s = 3.2
    elif best >= 5:
        s = 2.4
    elif best >= 0.5:
        s = 1.6
    else:
        s = 1.0
    return min(4.0, s + (0.4 if n >= 2 else 0.0))


def _score_one(a: Article, now: datetime):
    text = f"{a.title}. {a.description}"[:700]
    cat_pts = {}
    for cat, pts, rx in _COMPILED_RULES:
        if rx.search(text):
            cat_pts[cat] = cat_pts.get(cat, 0) + pts
    ent_pts, ent_cat = 0.0, ""
    for name, (pts, cat, rx) in _COMPILED_ENTITIES.items():
        if rx.search(text):
            ent_pts += pts
            if not ent_cat:
                ent_cat = cat
            if ent_pts >= 7:
                break
    ent_pts = min(ent_pts, 7)

    money = _money_score(text)          # v5.3: magnitude-aware

    rec = 0.0
    if a.published_at:
        hrs = (now - a.published_at).total_seconds() / 3600
        if hrs < 0:
            hrs = 0
        # v5.3: fresh <24h scores as before; beyond that stories decay fast
        # (24h -> 0, 72h -> -3) so the page never posts stale news
        if hrs <= 6:
            rec = 3.0
        elif hrs <= 12:
            rec = 2.0
        elif hrs <= 24:
            rec = 1.0
        else:
            rec = -min(3.0, (hrs - 24) / 16.0)
    else:
        rec = -1.0                       # v5.3: undated -> probably stale

    spec = 0.0
    if re.search(r"\d", a.title):
        spec += 1
    if re.search(r"[%\u20b9$]", a.title + " " + a.description):
        spec += 1

    pen = 0.0
    for rx, p in _COMPILED_NEGATIVE:
        m = rx.findall(text)
        if m:
            pen -= float(p if p >= 4 else min(3, len(m)))
    # v5.3: title quality - tabloid ALL-CAPS & empty question hooks
    letters = [c for c in a.title if c.isalpha()]
    if letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.5:
        pen -= 2.0
    if a.title.rstrip().endswith("?") and len(a.title.split()) < 9:
        pen -= 1.5

    base = (sum(min(v, 8.0) for v in cat_pts.values()) + ent_pts + money + rec
            + spec + pen + _domain_authority(a.domain))   # v5.3: outlet tier

    chosen = ""
    for cat in _CATEGORY_PRIORITY:
        if cat_pts.get(cat, 0) >= 2:
            chosen = cat
            break
    if not chosen and ent_cat:
        chosen = ent_cat
    return max(0.0, base), chosen


# v5.3: a story needs an India angle (or 3+ corroborating outlets) to rank.
# The signal must come from the STORY, not just the outlet - ET/Moneycontrol
# carry global wire stories too (Shein, Shanghai IPOs...).
_INDIAN_PLACE_RE = re.compile(
    r"\b(?:india|indian|india'?s|bharat|bengaluru|bangalore|mumbai|delhi|hyderabad|"
    r"pune|chennai|kolkata|gurugram|gurgaon|noida|ahmedabad|jaipur|kochi|coimbatore|"
    r"gujarat|karnataka|maharashtra|tamil nadu|telangana)\b", re.I)
# India-specific vocabulary that only appears in Indian stories
_INDIAN_TERM_RE = re.compile(
    r"\b(?:RBI|SEBI|NSE|BSE|Nifty|Sensex|NIFTY|SENSEX|UPI|NITI Aayog|DRHP|"
    r"crore|crores|lakh|lakhs|rupee|rupees|repo rate|LIC)\b")
# global entities in the entity table (NOT India signals)
_GLOBAL_ENTITY_KEYS = {"apple", "google", "microsoft", "openai", "nvidia", "tesla",
                       "samsung", "xiaomi", "oneplus", "vivo", "oppo", "realme",
                       "nothing phone", "motorola", "meta", "whatsapp", "youtube",
                       "amazon", "oneweb", "starlink", "google pay"}
_INDIAN_ENTITY_RES = [re.compile(r"\b" + re.escape(k) + r"\b", re.I)
                      for k in _ENTITIES if k not in _GLOBAL_ENTITY_KEYS]


def _india_signal(a) -> bool:
    if a.via == "hn":
        return True                    # HN query is India-filtered
    text = f"{a.title} {a.description}"
    if _INDIAN_PLACE_RE.search(text) or _INDIAN_TERM_RE.search(text):
        return True
    if (a.domain or "").endswith(".in"):
        return True
    for rx in _INDIAN_ENTITY_RES:      # an Indian company in the story
        if rx.search(text):
            return True
    return False


def _cluster_articles(articles: list) -> list:
    """Group near-duplicate stories across sources; count distinct outlets."""
    clusters = []
    for a in sorted(articles, key=lambda x: -x.score):
        placed = False
        for c in clusters:
            if _titles_similar(a.title, c[0].title):
                c.append(a)
                placed = True
                break
        if not placed:
            clusters.append([a])
    for c in clusters:
        doms = {x.domain for x in c if not _is_social(x.domain)}
        for a in c:
            a.corroborations = len(doms)
    return clusters


def score_articles(articles: list) -> list:
    now = datetime.now(UTC)
    for a in articles:
        s, cat = _score_one(a, now)
        a.score, a.category = s, cat or "general"
    clusters = _cluster_articles(articles)
    for c in clusters:
        doms = len({x.domain for x in c if not _is_social(x.domain)})
        boost = 4.0 if doms >= 3 else 2.0 if doms == 2 else 0.0
        india_ok = any(_india_signal(x) for x in c)
        for a in c:
            a.india_ok = india_ok      # v5.3: cluster-level India signal
            a.score += boost
            if not india_ok and doms < 3:
                a.score -= 2.5        # v5.3: global story w/o India angle or wide pickup
    return clusters


# ==========================================================================
# SELECTION (threshold + diversity + quiet-page safety valve)
# ==========================================================================
def select_article(clusters: list, state: dict, threshold: float):
    flat = [c[0] for c in clusters if c]

    def effective(a: Article) -> float:
        s = a.score
        rc = state.get("recent_categories") or []
        if rc and a.category == rc[-1]:
            s -= 2.5
        if (rc[-3:] if len(rc) >= 3 else rc).count(a.category) >= 2:
            s -= 3.0
        rd = state.get("recent_domains") or []
        if rd and a.domain == rd[-1]:
            s -= 2.0
        return s

    def eligible(a: Article) -> bool:
        # v5.3: India gate - a story with no India angle may only post if it
        # is huge (3+ corroborating outlets); the page stays Indian tech news
        return getattr(a, "india_ok", True) or a.corroborations >= 3

    quals = [a for a in flat if effective(a) >= threshold and not _is_duplicate(a, state) and eligible(a)]
    if not quals:
        last = state.get("last_post_ts") or 0
        quiet_hrs = (time.time() - last) / 3600 if last else 999.0
        if quiet_hrs >= QUIET_HOURS:
            relaxed = max(4, int(threshold) - QUIET_RELAX_DROP)
            quals = [a for a in flat if effective(a) >= relaxed and not _is_duplicate(a, state) and eligible(a)]
            if quals:
                log.info("Page quiet for %.1fh - relaxing threshold %d -> %d",
                         quiet_hrs, int(threshold), relaxed)
    if not quals:
        return None
    quals.sort(key=lambda a: (effective(a), a.corroborations), reverse=True)
    return quals[0]

# ==========================================================================
# AI NARRATIVE SYSTEM (Groq primary, Gemini fallback; shapes + personas)
# ==========================================================================
# Aug 2026: Groq retired llama-3.3-70b-versatile / qwen3-32b / llama-3.1-8b-instant
# (free tier). Official replacements below; override via GROQ_MODELS env (comma-separated).
GROQ_MODELS = [m.strip() for m in os.getenv(
    "GROQ_MODELS", "openai/gpt-oss-120b,qwen/qwen3.6-27b,openai/gpt-oss-20b").split(",") if m.strip()]
GEMINI_MODELS = [m.strip() for m in os.getenv(
    "GEMINI_MODELS", "gemini-2.5-flash,gemini-2.5-flash-lite,gemini-flash-latest").split(",") if m.strip()]
_LLM_KEY_WARNED = set()   # "key missing" logged once per provider, not per attempt

PERSONAS = [
    {"id": "arjun", "desc": "Arjun, 29, product manager at a Bengaluru startup. Dry humour, allergic to hype, quotes numbers to make points."},
    {"id": "meera", "desc": "Meera, 34, ex-finance journalist in Mumbai who now runs a small creator page. Loves a good chart, explains money stuff simply."},
    {"id": "dev", "desc": "Dev, 41, engineering manager in Pune. Dad energy, pragmatic, has watched a hundred buzzwords die."},
    {"id": "riya", "desc": "Riya, 24, tech enthusiast in Kolkata. Sharp, playful, internet-native, allergic to corporate speak."},
    {"id": "kabir", "desc": "Kabir, 37, consultant in Delhi who has read too many pitch decks. Skeptical of valuation theatre, respects profit."},
    {"id": "ananya", "desc": "Ananya, 31, data scientist in Chennai. Notices the number everyone else skipped and explains it in one line."},
]

# Six STRUCTURALLY different post skeletons - the skeleton changes, not just words.
POST_SHAPES = [
    {"id": "hot_take", "name": "HOT TAKE",
     "scaffold": "Line 1: one blunt opinion or reaction (max 14 words), no setup, no context - something a person blurts out.\n"
                 "Then 2-3 sentences: the ONE fact that justifies the reaction (use a number if there is one).\n"
                 "Last line: a short provocation inviting disagreement (max 8 words)."},
    {"id": "question_hook", "name": "QUESTION HOOK",
     "scaffold": "Line 1: a genuinely curious question TO THE READER about this specific situation (not generic filler like 'is this good or bad?').\n"
                 "Then 2-3 sentences of context with the key fact, answering just enough.\n"
                 "Last line: casually invite answers in comments ('Tell me I'm wrong', 'What would you pick?')."},
    {"id": "mini_story", "name": "MINI STORY",
     "scaffold": "Sentence 1: a tiny concrete scene from the story - a person, a place, a moment, present tense. Do NOT open with 'Imagine this' or 'Picture this'.\n"
                 "Then 2-3 sentences zooming out: what actually happened.\n"
                 "End with 1-2 sentences on what it means for an ordinary person."},
    {"id": "stat_first", "name": "STAT FIRST",
     "scaffold": "Line 1: the single most striking number from the story, with its unit, nothing else.\n"
                 "Then 2-3 sentences: what that number is about, plus one comparison that makes it feel big or small.\n"
                 "Last line: one implication, max 10 words."},
    {"id": "listicle", "name": "THREE THINGS",
     "scaffold": "Line 1: a short claim about why this matters (max 12 words).\n"
                 "Then EXACTLY 3 numbered lines - '1.', '2.', '3.' - each one a self-contained takeaway (max 16 words each).\n"
                 "Last line: one casual closer (max 8 words)."},
    {"id": "contrarian", "name": "CONTRARIAN",
     "scaffold": "Line 1: state the popular take plainly, then disagree in the same breath (e.g. \"Everyone's calling this the future of Indian retail. Honestly? No.\")\n"
                 "Then 2-3 sentences of counter-argument grounded in a fact from the story.\n"
                 "Last: concede ONE fair point to the other side, in a few words."},
]

BANNED_EN_PHRASES = [
    "game changer", "game-changer", "gamechanger", "game-changing", "revolutioniz", "revolutionis",
    "buckle up", "dive in", "dive into", "in a significant development", "testament to",
    "poised to", "in today's", "fast-paced", "ever-evolving", "ever-changing",
    "only time will tell", "it remains to be seen", "seamless", "seamlessly", "netizens",
    "took to social media", "breaking the internet", "storming the internet", "internet by storm",
    "delve", "navigate the landscape", "landscape of", "must-watch", "eye-opening",
    "wake-up call", "double-edged sword", "tip of the iceberg", "in conclusion", "to sum up",
    "as an ai", "cutting-edge", "state-of-the-art", "unprecedented", "in the realm of",
]

BANNED_BN_PHRASES = [
    "সংজ্ঞায়িত", "তাৎপর্যপূর্ণ", "পরিপ্রেক্ষিতে", "উল্লেখযোগ্যভাবে", "উল্লেখযোগ্য",
    "চিন্তার খোরাক", "নজরকাড়া", "নজর কাড়ছে", "আলোচনার ঝড়", "তোলপাড়",
    "অগ্রসরমান", "প্রযুক্তির জগতে", "প্রযুক্তি জগতের", "সর্বশেষ তথ্য অনুযায়ী",
    "এক নতুন যুগের সূচনা", "ইতিহাসে স্বর্ণাক্ষরে", "স্বর্ণাক্ষরে", "তথাকথিত",
]


def _groq_chat(system, user, temperature=1.0, max_tokens=450):
    key = _get_secret("GROQ_API_KEY")
    if not key:
        if "groq" not in _LLM_KEY_WARNED:
            _LLM_KEY_WARNED.add("groq")
            log.warning("GROQ_API_KEY not set - Groq provider disabled")
        return None
    for model in GROQ_MODELS:
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": min(temperature, 2.0),
            "top_p": 0.95,
            # 'max_tokens' is deprecated platform-wide; reasoning models also need
            # generous room or the content comes back empty
            "max_completion_tokens": max(int(max_tokens), 1100),
        }
        if model.startswith("openai/gpt-oss-"):
            body["reasoning_effort"] = "low"
        for attempt in range(3):        # idempotent call -> safe to retry on 429/5xx
            try:
                r = _SESSION.post("https://api.groq.com/openai/v1/chat/completions",
                                  json=body, headers={"Authorization": f"Bearer {key}"},
                                  timeout=60)
                if r.status_code == 200:
                    try:
                        j = r.json()
                    except Exception:
                        log.warning("groq %s: HTTP 200 but non-JSON body", model)
                        break
                    content = ((j.get("choices") or [{}])[0].get("message") or {}).get("content")
                    if content and content.strip():
                        log.debug("groq ok via %s", model)
                        return content.strip()
                    log.warning("groq %s returned empty content - trying next model", model)
                    break
                if r.status_code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(1.5 * (2 ** attempt) + random.random())
                    continue
                log.warning("groq %s -> HTTP %s %s", model, r.status_code,
                            r.text[:180].replace("\n", " "))
                break
            except Exception as e:
                log.warning("groq %s request failed: %s", model, e)
                break
    return None


def _gemini_chat(system, user, temperature=1.0, max_tokens=500):
    key = _get_secret("GEMINI_API_KEY")
    if not key:
        if "gemini" not in _LLM_KEY_WARNED:
            _LLM_KEY_WARNED.add("gemini")
            log.warning("GEMINI_API_KEY not set - Gemini fallback disabled (optional)")
        return None
    for model in GEMINI_MODELS:
        gen_cfg = {"temperature": min(temperature, 2.0), "topP": 0.95,
                   "maxOutputTokens": max(int(max_tokens), 1200)}
        if model in ("gemini-2.5-flash", "gemini-2.5-flash-lite"):
            gen_cfg["thinkingConfig"] = {"thinkingBudget": 0}  # thinking eats the budget
        body = {"system_instruction": {"parts": [{"text": system}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": gen_cfg}
        for attempt in range(3):        # idempotent call -> safe to retry on 429/5xx
            try:
                r = _SESSION.post(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}",
                    json=body, timeout=60)
                if r.status_code == 200:
                    j = r.json()
                    parts = (((j.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
                    text = "".join(p.get("text", "") for p in parts).strip()
                    if text:
                        return text
                    log.warning("gemini %s returned empty content - trying next model", model)
                    break
                if r.status_code in (429, 500, 502, 503) and attempt < 2:
                    time.sleep(1.5 * (2 ** attempt) + random.random())
                    continue
                log.warning("gemini %s -> HTTP %s %s", model, r.status_code,
                            r.text[:180].replace("\n", " "))
                break
            except Exception as e:
                log.warning("gemini %s request failed: %s", model, e)
                break
    return None


def _strip_reasoning(text: str) -> str:
    """Remove <think>...</think> blocks some reasoning models emit inline."""
    if not text:
        return text
    t = re.sub(r"<think>[\s\S]*?</think>\s*", "", text)
    if "<think>" in t:                       # unclosed block
        t = t.split("<think>")[0]
    return t.strip()


def _llm_complete(system, user, temperature=1.0, max_tokens=450):
    text = _groq_chat(system, user, temperature, max_tokens)
    if text:
        return _strip_reasoning(text), "groq"
    text = _gemini_chat(system, user, temperature, max_tokens)
    if text:
        return _strip_reasoning(text), "gemini"
    return None, ""


# ------------------------------------------------------------------ prompts
def _system_prompt(persona):
    return f"""You write Facebook posts for an Indian tech-news page read by smart, casual followers.

You are writing as: {persona['desc']}

The #1 rule: sound like a real human posting to friends - never like AI, never like a corporate page, never like a press release.

Style rules:
- Contractions everywhere (it's, doesn't, they're).
- Vary rhythm: mix longer sentences with very short ones (under 5 words).
- Plain conversational Indian English is welcome ("honestly", "no joke", "worth noting").
- Facts over adjectives. If a number exists, use it.
- Never invent facts. Only use what's in the story you're given.
- No markdown, no bold, no links, no emoji spam (0-1 emoji max).
- Max 2 hashtags, only if they feel natural.
- Don't start with the headline - the image card already shows it.
- At most one em dash in the whole post.

BANNED (never use, in any form): {", ".join(BANNED_EN_PHRASES)}

Output only the post text."""


def _user_prompt(art, shape, grounding, wiki, extra=""):
    blocks = [f"STORY\nHeadline: {art.title}\nSource: {art.source} ({art.domain}), published ~{_hours_ago(art)} ago"]
    if art.description:
        blocks.append(f"Description: {art.description}")
    if grounding:
        blocks.append(f"Story details (scraped from the article):\n{grounding[:900]}")
    if wiki:
        blocks.append(f"Context about a company mentioned:\n{wiki}")
    task = (
        "\n\nYOUR TASK\n"
        f"Write the Facebook post following this structure EXACTLY:\n"
        f"SHAPE - {shape['name']}:\n{shape['scaffold']}\n\n"
        "Length: 40-110 words. Plain text only. No markdown, no links. "
        "Don't repeat the headline verbatim. Use ONLY facts from the story - never invent "
        "numbers or names. End with 0-2 natural hashtags at most. "
        f"Output ONLY the post text - no title, no labels, no explanation.{extra}"
    )
    return "\n\n".join(blocks) + task


def _clean_post_text(text, bengali=False):
    """Validate + sanitize LLM output. Returns cleaned text or None (reject)."""
    if not text:
        return None
    t = text.strip()
    t = re.sub(r"^(?:post|caption|facebook post|final post|output)\s*[:\-\u2013\u2014]\s*", "", t, flags=re.I)
    if len(t) >= 2 and t[0] in "\"'" and t[-1] == t[0]:
        t = t[1:-1]
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"^#{1,4}\s*", "", t, flags=re.M)
    t = re.sub(r"^\s*[-*\u2022]\s+", "- ", t, flags=re.M)
    t = re.sub(r"\n{3,}", "\n\n", t).strip()
    if t.count("\u2014") > 1:
        t = t.replace("\u2014", ", ")
    low = t.lower()
    banned = BANNED_BN_PHRASES if bengali else BANNED_EN_PHRASES
    for p in banned:
        if p in low:
            return None
    if "http://" in t or "https://" in t or "www." in t:
        return None
    if re.search(r"\b(as an ai|language model)\b", low):
        return None
    words = t.split()
    if not (18 <= len(words) <= 180):
        return None
    if bengali and _bengali_ratio(t) < 0.30:
        return None
    if not bengali and _bengali_ratio(t) > 0.08:
        return None
    if len(re.findall(r"#[\w\u0980-\u09FF]+", t)) > 4:
        return None
    return t


def _reject_reason(text, bengali=False):
    """Mirror of _clean_post_text checks - returns WHY text would be rejected."""
    t = (text or "").strip()
    if not t:
        return "empty output"
    low = t.lower()
    banned = BANNED_BN_PHRASES if bengali else BANNED_EN_PHRASES
    for p in banned:
        if p in low:
            return f"banned phrase '{p}'"
    if "http://" in t or "https://" in t or "www." in t:
        return "contains a link"
    if re.search(r"\b(as an ai|language model)\b", low):
        return "AI self-reference"
    words = t.split()
    if not (18 <= len(words) <= 180):
        return f"word count {len(words)} outside 18-180"
    if bengali and _bengali_ratio(t) < 0.30:
        return "not Bengali enough"
    if not bengali and _bengali_ratio(t) > 0.08:
        return "unexpected Bengali text"
    if len(re.findall(r"#[\w\u0980-\u09FF]+", t)) > 4:
        return "too many hashtags"
    return "unknown"


def generate_commentary(art, shape, persona, grounding="", wiki=""):
    """3 attempts: new persona + stricter note each time. Returns (text, meta)."""
    for i in range(3):
        p = persona if i == 0 else random.choice(PERSONAS)
        extra = "" if i == 0 else (
            "\nIMPORTANT: your previous attempt was rejected for banned cliches or bad length. "
            "Re-read the banned list and be careful.")
        text, provider = _llm_complete(
            _system_prompt(p), _user_prompt(art, shape, grounding, wiki, extra),
            temperature=1.0 + 0.15 * i, max_tokens=450,
        )
        if not text:
            continue
        cleaned = _clean_post_text(text, bengali=False)
        if cleaned:
            return cleaned, {"provider": provider, "persona": p["id"]}
        log.warning("attempt %d/3: LLM output rejected (%s) - retrying with a new persona",
                    i + 1, _reject_reason(text, bengali=False))
    return None, {}


# ------------------------------------------------------------------ Bengali
def _bengali_system_prompt(persona):
    return f"""তুমি একটা ভারতীয় টেক-নিউজ ফেসবুক পেজের হয়ে বাংলায় পোস্ট লেখো। পাঠক: বাংলাভাষী তরুণ-তরুণী, প্রযুক্তি আর স্টার্টআপে আগ্রহী।

তুমি এই চরিত্রে লিখছ: {persona['desc']}

সবচেয়ে বড় নিয়ম: লেখাটা যেন মনে হয় একজন সাধারণ মানুষ নিজের ভাষায় লিখেছে - কখনও নিউজ বুলেটিন নয়, অনুবাদ নয়, এআই-লেখা নয়।

ভাষার নিয়ম:
- কলকাতার কথ্য বাংলার টোন: 'হচ্ছে', 'দিচ্ছে', 'করেই', 'একদম', 'সোজা কথায়' - এই ধরনের প্রাকৃতিক রূপ।
- প্রযুক্তির ইংরেজি শব্দ বাংলা অক্ষরে লেখো: স্টার্টআপ, ফান্ডিং, আইপিও, অ্যাপ, ইউজার - এগুলোই স্বাভাবিক।
- ছোট ছোট বাক্য। দু-একটা খুব ছোট বাক্য রাখো (৩-৪ শব্দের)।
- সংস্কৃতঘেঁষা ভারী শব্দ একেবারে নিষিদ্ধ।
- শুধু গল্পে দেওয়া তথ্য ব্যবহার করো, বানাবে না।
- কোনো লিংক, মার্কডাউন নয়। ইমোজি ০-১টা। হ্যাশট্যাগ সর্বোচ্চ ২টা।
- হেডলাইন হুবহু কপি করবে না।

নিষিদ্ধ শব্দ/বাক্যাংশ (কোনো রূপেই নয়): {", ".join(BANNED_BN_PHRASES)}

আউটপুট ফরম্যাট (ঠিক এভাবে):
প্রথমে পোস্টের টেক্সট।
তারপর নতুন লাইনে: CARD: <ছবির কার্ডের জন্য ৬-১০ শব্দের বাংলা হেডলাইন>

এই দুটো ছাড়া আর কিছু লিখো না।"""


def _bengali_user_prompt(art, shape, grounding, bn_snippets):
    blocks = [f"গল্প:\nহেডলাইন: {art.title}\nসূত্র: {art.source} ({art.domain}), প্রায় {_hours_ago(art)} আগে"]
    if art.description:
        blocks.append(f"বিবরণ: {art.description}")
    if grounding:
        blocks.append(f"গল্পের খতিয়ান (স্ক্র্যাপ করা):\n{grounding[:700]}")
    if bn_snippets:
        blocks.append("বাংলা মিডিয়ার হেডলাইন (সূত্র হিসেবে): " + " | ".join(bn_snippets))
    task = (
        "\n\nকাজ:\n"
        f"নিচের গঠন exactly মেনে পুরো পোস্টটা প্রাকৃতিক, কথ্য বাংলায় লেখো -\n"
        f"গঠন ({shape['name']}):\n{shape['scaffold']}\n\n"
        "দৈর্ঘ্য: ৩৫-১০০ শব্দ। শেষে সর্বোচ্চ ২টা হ্যাশট্যাগ। "
        "শুধু ফাইনাল পোস্ট + CARD লাইন আউটপুট করো।"
    )
    return "\n\n".join(blocks) + task


def _parse_bn_output(text):
    card = ""
    keep = []
    for l in text.strip().splitlines():
        m = re.match(r"^\s*CARD\s*[:\uff1a]\s*(.+)$", l.strip(), flags=re.I)
        if m:
            card = m.group(1).strip()
        else:
            keep.append(l)
    return "\n".join(keep).strip(), card


def generate_bengali_post(art, grounding=""):
    """Returns (post_text, card_headline, provider) or (None, None, '')."""
    try:
        snippets = [a.title for a in fetch_google_news(GOOGLE_NEWS_BN_QUERIES[:2], "bn")[:4]]
    except Exception:
        snippets = []
    persona = random.choice(PERSONAS)
    shape = POST_SHAPES[random.randrange(4)]  # first 4 shapes work well in Bengali
    for i in range(3):
        text, provider = _llm_complete(
            _bengali_system_prompt(persona),
            _bengali_user_prompt(art, shape, grounding, snippets),
            temperature=1.0 + 0.15 * i, max_tokens=520,
        )
        if not text:
            break
        post, card = _parse_bn_output(text)
        cleaned = _clean_post_text(post, bengali=True)
        if cleaned and card:
            return cleaned, card[:90], provider
        log.warning("Bengali attempt %d/3 rejected (%s)", i + 1,
                    _reject_reason(post, bengali=True) if not cleaned else "missing CARD line")
        persona = random.choice(PERSONAS)
    return None, None, ""


def _pick_shape(state):
    last = (state.get("recent_shapes") or [""])[-1]
    idx = state.get("post_counter", 0) % len(POST_SHAPES)
    if POST_SHAPES[idx]["id"] == last:
        idx = (idx + 1) % len(POST_SHAPES)
    return POST_SHAPES[idx]


def _pick_persona(state):
    last = (state.get("recent_personas") or [""])[-1]
    pool = [p for p in PERSONAS if p["id"] != last] or PERSONAS
    return random.choice(pool)

# ==========================================================================
# IMAGE PIPELINE - fonts, Pexels picking, layout primitives
# ==========================================================================
if PIL_OK:
    _RESAMPLE = getattr(Image, "Resampling", Image)

_FONT_FILES = {
    "Poppins-Bold.ttf": "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Bold.ttf",
    "Poppins-SemiBold.ttf": "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-SemiBold.ttf",
    "Poppins-Medium.ttf": "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Medium.ttf",
    "Poppins-Regular.ttf": "https://github.com/google/fonts/raw/main/ofl/poppins/Poppins-Regular.ttf",
    "NotoSansBengali.ttf": "https://github.com/google/fonts/raw/main/ofl/notosansbengali/NotoSansBengali%5Bwdth%2Cwght%5D.ttf",
}
_fonts_ready = False
_FONT_CACHE = {}


def _ensure_fonts() -> bool:
    """Download Poppins + Noto Sans Bengali once (falls back to system fonts)."""
    global _fonts_ready
    if _fonts_ready or not PIL_OK:
        return _fonts_ready
    try:
        FONT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:
        return False

    def _dl(fname, url):
        try:
            data = _http_get_bytes(url, timeout=25)
            if data and len(data) > 20000:
                (FONT_DIR / fname).write_bytes(data)
                return True
        except Exception:
            pass
        return False

    missing = [f for f in _FONT_FILES if not (FONT_DIR / f).exists()]
    if missing:
        log.info("Downloading %d font files...", len(missing))
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(lambda f: _dl(f, _FONT_FILES[f]), missing))
    have_pop = (FONT_DIR / "Poppins-Bold.ttf").exists()
    have_bn = (FONT_DIR / "NotoSansBengali.ttf").exists()
    log.info("Fonts ready: Poppins=%s NotoBengali=%s", have_pop, have_bn)
    _fonts_ready = True
    return True


def _font(size, weight="regular", bengali=False):
    if not PIL_OK:
        return None
    key = (int(size), weight, bengali)
    if key in _FONT_CACHE:
        return _FONT_CACHE[key]
    font = None
    if bengali:
        p = FONT_DIR / "NotoSansBengali.ttf"
        if p.exists():
            try:
                font = ImageFont.truetype(str(p), int(size))
                if weight in ("bold", "semibold"):
                    try:
                        font.set_variation_by_name("Bold")
                    except Exception:
                        pass
            except Exception:
                font = None
    else:
        names = {"bold": "Poppins-Bold.ttf", "semibold": "Poppins-SemiBold.ttf",
                 "medium": "Poppins-Medium.ttf", "regular": "Poppins-Regular.ttf"}
        p = FONT_DIR / names.get(weight, "Poppins-Regular.ttf")
        if p.exists():
            try:
                font = ImageFont.truetype(str(p), int(size))
            except Exception:
                font = None
    if font is None:
        candidates = []
        if bengali:
            candidates.append("/usr/share/fonts/truetype/noto-bengali/NotoSansBengali-Regular.ttf")
        candidates.append({
            "bold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "semibold": "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "medium": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "regular": "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        }.get(weight, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
        for c in candidates:
            if c and os.path.exists(c):
                try:
                    font = ImageFont.truetype(c, int(size))
                    break
                except Exception:
                    continue
    if font is None:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None
    _FONT_CACHE[key] = font
    return font


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    if not words:
        return [""]
    lines = []
    cur = words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if draw.textlength(trial, font=font) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def _fit_headline(draw, text, weight, max_width, max_lines, start, min_size, bengali=False):
    """Shrink-to-fit headline. Returns (lines, font, line_height, total_h, size)."""
    size = start
    while size >= min_size:
        font = _font(size, weight, bengali)
        if font is None:
            break
        lines = _wrap_text(draw, text, font, max_width)
        lh = int(size * 1.22)
        if len(lines) <= max_lines:
            return lines, font, lh, lh * len(lines), size
        size -= 3
    font = _font(min_size, weight, bengali)
    lines = _wrap_text(draw, text, font, max_width) if font is not None else [text]
    lines = lines[: max_lines + 2]
    lh = int(min_size * 1.22)
    return lines, font, lh, lh * len(lines), min_size


def _tracked_width(draw, text, font, tracking=1.15):
    return sum(draw.textlength(ch, font=font) for ch in text) * tracking


def _draw_tracked(draw, xy, text, font, fill, tracking=1.15):
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=font, fill=fill)
        x += draw.textlength(ch, font=font) * tracking


def _txt(d, xy, text, font, fill=(255, 255, 255, 255), shadow=True):
    """Text with a soft drop shadow - headline stays readable on ANY photo."""
    if font is None:
        return
    if shadow:
        off = max(2, int(getattr(font, "size", 24) / 26))
        d.text((xy[0] + off, xy[1] + off), text, font=font, fill=(0, 0, 0, 150))
    d.text(xy, text, font=font, fill=fill)


def _vignette(size, strength=64):
    """Cinematic corner darkening (RGBA overlay or None)."""
    try:
        if not hasattr(Image, "radial_gradient"):
            return None
        w, h = size
        mask = Image.radial_gradient("L").resize((w, h), _RESAMPLE.BILINEAR)
        mask = mask.point(lambda p: int(p * strength / 255))
        vig = Image.new("RGBA", (w, h), (0, 0, 0, 255))
        vig.putalpha(mask)
        return vig
    except Exception:
        return None


def _draw_chip(draw, x, y, text, accent, size, bengali=False):
    """Pill-shaped category chip: dark glass fill + accent outline + accent text."""
    if not text:
        return y, 0
    font = _font(size, "semibold", bengali)
    if font is None:
        return y, 0
    bbox = draw.textbbox((0, 0), text, font=font)
    th = bbox[3] - bbox[1]
    tw = _tracked_width(draw, text, font, 1.18)
    pad_x = int(size * 0.85)
    chip_h = int(size * 2.0)
    box_w = int(tw) + 2 * pad_x
    draw.rounded_rectangle([x, y, x + box_w, y + chip_h], radius=chip_h // 2,
                           fill=(8, 10, 18, 165), outline=accent + (255,),
                           width=max(2, size // 11))
    _draw_tracked(draw, (x + pad_x, y + (chip_h - th) // 2 - bbox[1]),
                  text, font, accent + (255,), 1.18)
    return y + chip_h, box_w


def _vertical_gradient(size, rgb, a_start, a_end, curve=1.7):
    w, h = size
    g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        a = int(a_start + (a_end - a_start) * (t ** curve))
        a = max(0, min(255, a))
        d.line([(0, y), (w, y)], fill=rgb + (a,))
    return g


def _diag_gradient(w, h, c1, c2):
    base = Image.new("RGB", (64, 64))
    px = base.load()
    for y in range(64):
        for x in range(64):
            t = (x + y) / 126.0
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))
    return base.resize((w, h), _RESAMPLE.BILINEAR)


def _horizontal_gradient(size, rgb, a_start, a_end, curve=1.7):
    w, h = size
    g = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(g)
    for x in range(w):
        t = x / max(1, w - 1)
        a = int(a_start + (a_end - a_start) * (t ** curve))
        a = max(0, min(255, a))
        d.line([(x, 0), (x, h)], fill=rgb + (a,))
    return g


def _smart_crop(img, tw, th):
    """Aspect crop with a slight top bias (subjects usually sit high)."""
    w, h = img.size
    target = tw / th
    if w / h > target:
        nw = int(round(h * target))
        x0 = (w - nw) // 2
        return img.crop((x0, 0, x0 + nw, h))
    nh = int(round(w / target))
    y0 = int((h - nh) * 0.28)
    return img.crop((0, y0, w, y0 + nh))


def _enhance_photo(img):
    """Tasteful 'pop' so cards stand out in the feed without looking fake."""
    try:
        img = ImageEnhance.Color(img).enhance(1.12)
        img = ImageEnhance.Contrast(img).enhance(1.06)
        img = ImageEnhance.Sharpness(img).enhance(1.12)
    except Exception:
        pass
    return img


def _vividness(img):
    """Prefer bright, colourful, contrasty photos (dull grey images kill reach)."""
    try:
        small = img.convert("RGB").resize((96, 96))
        hsv = small.convert("HSV")
        sat = ImageStat.Stat(hsv.split()[1]).mean[0] / 255.0
        con = ImageStat.Stat(small.convert("L")).stddev[0] / 128.0
        return sat * 1.4 + min(con, 0.5)
    except Exception:
        return 0.5


# ------------------------------------------------------------------ Pexels
def _pexels_search(query, orientation="portrait", per_page=12):
    key = _get_secret("PEXELS_API_KEY")
    if not key:
        return []
    params = {"query": query, "per_page": per_page, "size": "large", "locale": "en-US"}
    if orientation:
        params["orientation"] = orientation
    r = _http_get("https://api.pexels.com/v1/search", params=params,
                  headers={"Authorization": key}, timeout=20, retries=1)
    if r is None:
        return []
    try:
        return r.json().get("photos", [])
    except Exception:
        return []


_HINT_REGEX_CACHE = None


def _image_queries_for(art):
    global _HINT_REGEX_CACHE
    if _HINT_REGEX_CACHE is None:
        _HINT_REGEX_CACHE = [
            (re.compile(r"\b" + re.escape(k) + r"\b", re.I), v)
            for k, v in ENTITY_VISUAL_HINTS.items()
        ]
    text = (art.title + " " + art.description).lower()
    out = []
    for rx, hint in _HINT_REGEX_CACHE:
        if rx.search(text):
            out.append(hint)
    cat_q = CATEGORY_IMAGE_QUERIES.get(art.category) or CATEGORY_IMAGE_QUERIES["general"]
    out.extend(cat_q)
    out.append(cat_q[0] + " closeup")
    out.extend(FALLBACK_IMAGE_QUERIES)
    seen = set()
    dedup = []
    for q in out:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            dedup.append(q)
    return dedup[:6]


def _download_photo(p):
    src = p.get("src") or {}
    url = src.get("large2x") or src.get("large") or src.get("original")
    if not url:
        return None
    try:
        data = _http_get_bytes(url, timeout=25)
        if not data:
            return None
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None


def _pick_photo(art, avoid_ids=None):
    """Query ladder -> rank candidates -> download top 3 -> keep most vivid.
    avoid_ids: Pexels photo ids used recently, so the feed never repeats a photo.
    Returns (PIL.Image, photographer, photo_id) or None."""
    if not PIL_OK:
        return None
    avoid = avoid_ids or set()
    target = CARD_W / CARD_H
    for query in _image_queries_for(art):
        photos = _pexels_search(query, "portrait")
        if not photos:
            photos = _pexels_search(query, None)
        if not photos:
            continue

        def _rank(p):
            w, h = p.get("width", 0) or 0, p.get("height", 0) or 0
            if w < 800 or h < 800:
                return None
            aspect = w / max(1, h)
            size = min(w, h)
            return size - abs(aspect - target) * 2200

        ranked = []
        for p in photos:
            r = _rank(p)
            if r is not None:
                ranked.append((r, p))
        if not ranked:
            continue
        ranked.sort(key=lambda x: x[0], reverse=True)
        if avoid:
            fresh = [rp for rp in ranked if rp[1].get("id") not in avoid]
            if fresh:
                ranked = fresh   # prefer unseen photos; reuse rather than fail

        cand = []
        with ThreadPoolExecutor(max_workers=3) as ex:
            futs = {ex.submit(_download_photo, p): p for _, p in ranked[:3]}
            for f in as_completed(futs):
                try:
                    img = f.result()
                except Exception:
                    img = None
                if img is not None:
                    cand.append((img, (futs[f].get("photographer") or "Pexels"),
                                 futs[f].get("id")))
        if not cand:
            continue
        cand.sort(key=lambda c: _vividness(c[0]), reverse=True)
        log.info("Photo found for query '%s' (%d candidates)", query, len(cand))
        return cand[0]
    return None


_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF"
    "\U00002B00-\U00002BFF\U0000FE0F\U00002190-\U000021FF\U00002700-\U000027BF]+")


def _card_headline(title):
    t = _strip_html(title or "").strip()
    t = re.sub(r"\s*\|\s*[^|]{2,32}$", "", t)          # trailing " | SiteName"
    t = re.sub(r"^#\w+\s*[|:\-\u2013\u2014]\s*", "", t)  # leading "#WATCH |" junk
    t = _EMOJI_RE.sub("", t)                             # fonts can't render emoji
    t = re.sub(r"\s{2,}", " ", t).strip(" \u00b7|-")
    if len(t) > 118:
        t = t[:115].rsplit(" ", 1)[0] + "\u2026"
    return t


# ---- giant-stat extraction (feeds the stat_hero card layout) --------------
_STAT_CURRENCY_RE = re.compile(
    r"(\u20b9|\bRs\b\.?|\bUS\s*\$|\$)\s?(\d[\d,]*(?:\.\d+)?)\s*(billion|bn|crore|cr|lakh|million|mn|thousand|k)?\b", re.I)
_STAT_BARE_RE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s+(million|billion|crore|lakh)\b", re.I)
_STAT_PCT_RE = re.compile(r"\b(\d[\d,]*(?:\.\d+)?)\s?%")
_STAT_MULT_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s?[xX]\b")


def _extract_stat(text):
    """Pull the most eye-catching number out of a headline.
    Returns (display, raw_span) or (None, None).
    '\u20b952,000 crore profit' -> ('\u20b952,000 Cr', '\u20b952,000 crore')."""
    t = text or ""
    m = _STAT_CURRENCY_RE.search(t)
    if m:
        cur = "\u20b9" if ("\u20b9" in m.group(1) or m.group(1).lower().startswith("rs")) else "$"
        units = {"billion": "B", "bn": "B", "crore": " Cr", "cr": " Cr",
                 "lakh": " L", "million": "M", "mn": "M", "thousand": "K", "k": "K"}
        u = units.get((m.group(3) or "").lower(), "")
        return f"{cur}{m.group(2)}{u}", m.group(0)
    m = _STAT_BARE_RE.search(t)
    if m:
        units = {"million": "M", "billion": "B", "crore": " Cr", "lakh": " L"}
        return f"{m.group(1)}{units[m.group(2).lower()]}", m.group(0)
    m = _STAT_PCT_RE.search(t)
    if m:
        return f"{m.group(1)}%", m.group(0)
    m = _STAT_MULT_RE.search(t)
    if m:
        return f"{m.group(1)}X", m.group(0)
    return None, None


_BN_MONTHS = ["জানুয়ারি", "ফেব্রুয়ারি", "মার্চ", "এপ্রিল", "মে", "জুন",
              "জুলাই", "আগস্ট", "সেপ্টেম্বর", "অক্টোবর", "নভেম্বর", "ডিসেম্বর"]


def _source_date_line(art, bengali=False):
    src = (art.source or art.domain or "Tech").upper()[:28]
    now = _now_ist()
    if bengali:
        return f"{src} \u00b7 {_bn_digits(now.day)} {_BN_MONTHS[now.month - 1]}"
    return f"{src} \u00b7 {now.strftime('%d %b')}"

# ==========================================================================
# CARD RENDERERS - 6 rotating layouts, 2x supersampled
# ==========================================================================
def _render_photo_card(photo, art, variant, headline, source_line, bengali=False, stat=None, variant_idx=0):
    """Stock photo + branded overlay. variant in CARD_VARIANTS; stat -> giant number poster."""
    if variant == "stat_hero" and not stat:
        variant = "full_bleed"                      # defensive fallback
    S = max(1, SUPERSAMPLE)
    W, H = CARD_W * S, CARD_H * S
    M = int(72 * S)
    theme = CATEGORY_THEME.get(art.category, CATEGORY_THEME["general"])
    # v5.3: two accent palettes per category, rotated per post
    accent = theme["accent"] if variant_idx % 2 == 0 else theme.get("accent2", theme["accent"])

    # ---- base image
    if variant == "split_card":
        split = int(H * 0.54)
        panel = tuple(int(c * 0.78) for c in theme["grad"][0])
        top_img = _smart_crop(photo, W, split).resize((W, split), _RESAMPLE.LANCZOS)
        top_img = _enhance_photo(top_img)
        base = Image.new("RGBA", (W, H), panel + (255,))
        base.paste(top_img.convert("RGB"), (0, 0))
        fade = _vertical_gradient((W, int(90 * S)), panel, 0, 255, curve=2.2)
        base.alpha_composite(fade, (0, split - int(90 * S)))
    elif variant == "magazine":
        split_x = int(W * 0.56)
        panel = tuple(int(c * 0.72) for c in theme["grad"][0])
        base = Image.new("RGBA", (W, H), panel + (255,))
        side_w = W - split_x
        side = _smart_crop(photo, side_w, H).resize((side_w, H), _RESAMPLE.LANCZOS)
        base.paste(_enhance_photo(side).convert("RGB"), (split_x, 0))
    else:
        base = _smart_crop(photo, W, H).resize((W, H), _RESAMPLE.LANCZOS)
        base = _enhance_photo(base).convert("RGBA")

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # ---- scrims (text legibility on ANY photo)
    if variant == "bottom_sheet":
        y0 = int(H * 0.40)
        overlay.alpha_composite(_vertical_gradient((W, H - y0), (8, 10, 18), 0, 247, curve=1.9), (0, y0))
        overlay.alpha_composite(_vertical_gradient((W, int(H * 0.15)), (8, 10, 18), 150, 0), (0, 0))
    elif variant == "top_banner":
        overlay.alpha_composite(_vertical_gradient((W, int(H * 0.66)), (8, 10, 18), 248, 0, curve=1.15), (0, 0))
        bh = int(H * 0.14)
        overlay.alpha_composite(_vertical_gradient((W, bh), (8, 10, 18), 0, 140), (0, H - bh))
    elif variant == "split_card":
        overlay.alpha_composite(_vertical_gradient((W, int(H * 0.15)), (8, 10, 18), 140, 0), (0, 0))
    elif variant == "full_bleed":
        overlay.alpha_composite(_vertical_gradient((W, H), (8, 10, 18), 150, 215, curve=1.0), (0, 0))
        overlay.alpha_composite(
            _vertical_gradient((W, int(H * 0.18)), (8, 10, 18), 0, 175, curve=1.6),
            (0, H - int(H * 0.18)))
        vig = _vignette((W, H), 58)             # v5.3: cinematic corners
        if vig is not None:
            overlay.alpha_composite(vig)
    elif variant == "magazine":
        split_x = int(W * 0.56)
        side_w = W - split_x
        overlay.alpha_composite(
            _vertical_gradient((side_w, int(H * 0.16)), (8, 10, 18), 120, 0), (split_x, 0))
        overlay.alpha_composite(
            _vertical_gradient((side_w, int(H * 0.16)), (8, 10, 18), 0, 130, curve=1.6),
            (split_x, H - int(H * 0.16)))
    else:  # stat_hero - heavy cinematic darken so the giant number pops
        overlay.alpha_composite(_vertical_gradient((W, H), (8, 10, 18), 200, 240, curve=1.1), (0, 0))
        vig = _vignette((W, H), 72)             # v5.3: poster-style corners
        if vig is not None:
            overlay.alpha_composite(vig)

    # ---- chip (top-left) + handle
    chip_size = int(25 * S)
    label = (BN_CATEGORY_LABELS if bengali else {}).get(art.category) or theme["label"]
    _, chip_h = _draw_chip(d, M, M, label, accent, chip_size, bengali)
    if PAGE_HANDLE and variant not in ("top_banner", "stat_hero", "magazine"):
        hf = _font(int(25 * S), "semibold", bengali)
        if hf is not None:
            hw = _tracked_width(d, PAGE_HANDLE, hf, 1.12)
            d.text((W - M - int(hw), M + int(10 * S)), PAGE_HANDLE,
                   font=hf, fill=(255, 255, 255, 210))

    # ---- headline + accent bar + source line per layout
    if variant == "bottom_sheet":
        src_font = _font(int(27 * S), "medium", bengali)
        src_y = H - M - int(36 * S)
        if src_font is not None:
            d.text((M, src_y), source_line, font=src_font, fill=(255, 255, 255, 190))
        bar_y = src_y - int(52 * S)
        d.rounded_rectangle([M, bar_y, M + int(96 * S), bar_y + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        avail_bottom = bar_y - int(30 * S)
        avail_top = int(H * 0.44)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 5, int(74 * S), int(42 * S), bengali)
        y = avail_bottom - th
        for ln in lines:
            _txt(d, (M, y), ln, font)
            y += lh

    elif variant == "top_banner":
        top = M + chip_h + int(48 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 5, int(74 * S), int(42 * S), bengali)
        y = top
        for ln in lines:
            _txt(d, (M, y), ln, font)
            y += lh
        y += int(28 * S)
        d.rounded_rectangle([M, y, M + int(96 * S), y + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        y += int(36 * S)
        src_font = _font(int(27 * S), "medium", bengali)
        if src_font is not None:
            d.text((M, y), source_line, font=src_font, fill=(255, 255, 255, 195))
        if PAGE_HANDLE:
            hf = _font(int(25 * S), "semibold", bengali)
            if hf is not None:
                hw = _tracked_width(d, PAGE_HANDLE, hf, 1.12)
                d.text((W - M - int(hw), H - M - int(34 * S)), PAGE_HANDLE,
                       font=hf, fill=(255, 255, 255, 215))

    elif variant == "split_card":
        split = int(H * 0.54)
        d.rectangle([0, split - int(3 * S), W, split], fill=accent + (255,))
        top = split + int(44 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 5, int(66 * S), int(40 * S), bengali)
        y = top
        for ln in lines:
            _txt(d, (M, y), ln, font)
            y += lh
        y += int(30 * S)
        d.rounded_rectangle([M, y, M + int(96 * S), y + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        src_font = _font(int(26 * S), "medium", bengali)
        if src_font is not None:
            d.text((M, H - M - int(36 * S)), source_line, font=src_font, fill=(255, 255, 255, 185))

    elif variant == "full_bleed":
        # editorial: accent bar ABOVE the headline, upper-third block, photo breathes
        top = int(H * 0.34)
        d.rounded_rectangle([M, top, M + int(96 * S), top + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        y = top + int(42 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 5, int(78 * S), int(44 * S), bengali)
        for ln in lines:
            _txt(d, (M, y), ln, font)
            y += lh
        src_font = _font(int(27 * S), "medium", bengali)
        if src_font is not None:
            d.text((M, H - M - int(36 * S)), source_line, font=src_font, fill=(255, 255, 255, 200))

    elif variant == "magazine":
        # asymmetric editorial split: dark text panel left, full-height photo right
        split_x = int(W * 0.56)
        d.rectangle([split_x - int(3 * S), 0, split_x, H], fill=accent + (255,))
        panel_w = split_x - M - int(28 * S)
        top = M + chip_h + int(46 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", panel_w, 7, int(56 * S), int(32 * S), bengali)
        y = top
        for ln in lines:
            _txt(d, (M, y), ln, font)
            y += lh
        y += int(26 * S)
        d.rounded_rectangle([M, y, M + int(88 * S), y + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        src_font = _font(int(24 * S), "medium", bengali)
        if src_font is not None:
            d.text((M, H - M - int(34 * S)), source_line, font=src_font, fill=(255, 255, 255, 185))
        if PAGE_HANDLE:
            hf = _font(int(24 * S), "semibold", bengali)
            if hf is not None:
                hw = _tracked_width(d, PAGE_HANDLE, hf, 1.12)
                d.text((split_x + int(24 * S), H - M - int(34 * S)), PAGE_HANDLE,
                       font=hf, fill=(255, 255, 255, 200))

    else:  # stat_hero - the scroll-stopper: giant number, poster symmetry
        stat_font = None
        size = int(240 * S)
        while size >= int(96 * S):
            f = _font(size, "bold", False)
            if f is None:
                break
            if d.textlength(stat, font=f) <= W - 2 * M:
                stat_font = f
                break
            size -= int(8 * S)
        if stat_font is None:
            stat_font = _font(int(96 * S), "bold", False)
        cy = int(H * 0.36)
        d.ellipse([W // 2 - int(360 * S), cy - int(210 * S),
                   W // 2 + int(360 * S), cy + int(210 * S)], fill=accent + (30,))
        d.ellipse([W // 2 - int(230 * S), cy - int(140 * S),
                   W // 2 + int(230 * S), cy + int(140 * S)], fill=accent + (26,))
        bbox = stat_font.getbbox(stat)
        stat_h = bbox[3] - bbox[1]
        tw = d.textlength(stat, font=stat_font)
        sx = (W - tw) / 2 - bbox[0]
        sy = cy - stat_h / 2 - bbox[1]
        d.text((sx + int(5 * S), sy + int(5 * S)), stat,
               font=stat_font, fill=accent + (110,))            # v5.3: accent glow
        d.text((sx, sy), stat, font=stat_font, fill=(255, 255, 255, 255))
        y = cy + stat_h // 2 + int(46 * S)
        bar_w = min(int(tw * 0.7), int(320 * S))
        d.rounded_rectangle([int((W - bar_w) / 2), y, int((W + bar_w) / 2), y + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        y += int(48 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "semibold", W - 2 * M, 4, int(52 * S), int(34 * S), bengali)
        for ln in lines:
            if font is not None:
                lw = d.textlength(ln, font=font)
                _txt(d, ((W - lw) / 2, y), ln, font, (255, 255, 255, 245))
            y += lh
        src_font = _font(int(26 * S), "medium", bengali)
        if src_font is not None:
            sw = d.textlength(source_line, font=src_font)
            d.text(((W - sw) / 2, H - M - int(36 * S)), source_line,
                   font=src_font, fill=(255, 255, 255, 200))

    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out.resize((CARD_W, CARD_H), _RESAMPLE.LANCZOS)


def _render_designer_card(art, headline, source_line, bengali=False, variant_idx=0, stat=None):
    """No-photo fallback that still looks designed: duotone gradient + one of
    four rotating background motifs + full typography system; optional giant stat."""
    S = max(1, SUPERSAMPLE)
    W, H = CARD_W * S, CARD_H * S
    M = int(76 * S)
    theme = CATEGORY_THEME.get(art.category, CATEGORY_THEME["general"])
    # v5.3: two accent palettes per category, rotated per post
    accent = theme["accent"] if variant_idx % 2 == 0 else theme.get("accent2", theme["accent"])
    base = _diag_gradient(W, H, theme["grad"][0], theme["grad"][1]).convert("RGBA")

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # ---- rotating background motif (feed variety even without photos)
    m = variant_idx % 4
    if m == 0:      # dot grid + rings
        step = int(56 * S)
        r = int(2 * S)
        for gy in range(int(36 * S), H, step):
            for gx in range(int(36 * S), W, step):
                d.ellipse([gx, gy, gx + r, gy + r], fill=(255, 255, 255, 15))
        cx, cy = (W - int(150 * S), int(120 * S)) if variant_idx % 8 < 4 \
            else (int(150 * S), H - int(200 * S))
        d.ellipse([cx - int(210 * S), cy - int(210 * S), cx + int(210 * S), cy + int(210 * S)],
                  outline=(255, 255, 255, 24), width=int(2 * S))
        d.ellipse([cx - int(140 * S), cy - int(140 * S), cx + int(140 * S), cy + int(140 * S)],
                  fill=accent + (16,))
    elif m == 1:    # diagonal pinstripes
        for x0 in range(-H, W, int(84 * S)):
            d.line([(x0, H), (x0 + H, 0)], fill=(255, 255, 255, 13), width=int(2 * S))
    elif m == 2:    # halftone wave (dots grow downward)
        step = int(74 * S)
        for gy in range(int(30 * S), H, step):
            t = gy / H
            r = int((1.5 + 3.2 * t) * S)
            for gx in range(int(30 * S), W, step):
                d.ellipse([gx, gy, gx + r, gy + r], fill=(255, 255, 255, 17))
    else:           # translucent circle cluster + corner ring
        for (dx, dy, rr, aa) in ((-60, -40, 260, 22), (120, 60, 170, 18), (260, 180, 100, 14)):
            d.ellipse([W - int(dx * S) - int(rr * S), H - int(dy * S) - int(rr * S),
                       W - int(dx * S) + int(rr * S), H - int(dy * S) + int(rr * S)],
                      fill=accent + (aa,))
        d.ellipse([int(60 * S) - int(130 * S), int(80 * S) - int(130 * S),
                   int(60 * S) + int(130 * S), int(80 * S) + int(130 * S)],
                  outline=(255, 255, 255, 26), width=int(2 * S))

    # chip
    chip_size = int(25 * S)
    label = (BN_CATEGORY_LABELS if bengali else {}).get(art.category) or theme["label"]
    _draw_chip(d, M, M, label, accent, chip_size, bengali)

    if stat:
        # ---- giant stat poster
        stat_font = None
        size = int(230 * S)
        while size >= int(90 * S):
            f = _font(size, "bold", False)
            if f is None:
                break
            if d.textlength(stat, font=f) <= W - 2 * M:
                stat_font = f
                break
            size -= int(8 * S)
        if stat_font is None:
            stat_font = _font(int(90 * S), "bold", False)
        cy = int(H * 0.35)
        bbox = stat_font.getbbox(stat)
        stat_h = bbox[3] - bbox[1]
        tw = d.textlength(stat, font=stat_font)
        d.ellipse([W // 2 - int(330 * S), cy - int(200 * S),
                   W // 2 + int(330 * S), cy + int(200 * S)], fill=accent + (20,))
        d.text(((W - tw) / 2 - bbox[0], cy - stat_h / 2 - bbox[1]), stat,
               font=stat_font, fill=(255, 255, 255, 255))
        y = cy + stat_h // 2 + int(44 * S)
        bar_w = min(int(tw * 0.7), int(300 * S))
        d.rounded_rectangle([int((W - bar_w) / 2), y, int((W + bar_w) / 2), y + int(8 * S)],
                            radius=int(4 * S), fill=accent + (255,))
        y += int(46 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "semibold", W - 2 * M, 4, int(50 * S), int(32 * S), bengali)
        for ln in lines:
            if font is not None:
                lw = d.textlength(ln, font=font)
                _txt(d, ((W - lw) / 2, y), ln, font, (255, 255, 255, 245))
            y += lh
    else:
        # ---- standard headline block
        top = int(H * 0.30)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 6, int(84 * S), int(46 * S), bengali)
        y = top
        for ln in lines:
            if font is not None:
                d.text((M, y), ln, font=font, fill=(255, 255, 255, 252))
            y += lh
        y += int(30 * S)
        d.rounded_rectangle([M, y, M + int(110 * S), y + int(9 * S)],
                            radius=int(4 * S), fill=accent + (255,))

    # source + handle
    src_font = _font(int(26 * S), "medium", bengali)
    if src_font is not None:
        d.text((M, H - M - int(34 * S)), source_line, font=src_font, fill=(255, 255, 255, 190))
    if PAGE_HANDLE:
        hf = _font(int(25 * S), "semibold", bengali)
        if hf is not None:
            hw = _tracked_width(d, PAGE_HANDLE, hf, 1.12)
            d.text((W - M - int(hw), H - M - int(34 * S)), PAGE_HANDLE,
                   font=hf, fill=(255, 255, 255, 205))

    out = Image.alpha_composite(base, overlay).convert("RGB")
    return out.resize((CARD_W, CARD_H), _RESAMPLE.LANCZOS)


def render_card(art, state=None, bengali=False, card_headline=None):
    """Full pipeline: Pexels photo card or designer fallback -> JPEG path."""
    if not PIL_OK:
        log.warning("Pillow unavailable - skipping image card")
        return None
    _ensure_fonts()
    if card_headline is None:
        card_headline = _card_headline(art.title)
    src_line = _source_date_line(art, bengali)
    # Bengali companion always gets a DIFFERENT layout than its English sibling
    variant_idx = (state or {}).get("post_counter", 0) + (1 if bengali else 0)
    variant = CARD_VARIANTS[variant_idx % len(CARD_VARIANTS)]

    # giant-stat treatment (English cards; headline drops the number to avoid saying it twice)
    stat = raw = None
    if not bengali:
        stat, raw = _extract_stat(card_headline)
    if variant == "stat_hero":
        if not stat:
            variant = "magazine" if variant_idx % 2 else "full_bleed"
        elif raw:
            stripped = re.sub(r"\s{2,}", " ", card_headline.replace(raw, " ")).strip(" ,.:-\u2013\u2014|")
            if len(stripped) >= 18:
                card_headline = stripped
    # editorial uppercase treatment for the two magazine-style layouts
    if not bengali and variant in ("full_bleed", "magazine"):
        card_headline = card_headline.upper()

    photo = None
    avoid = set((state or {}).get("recent_photo_ids") or [])
    try:
        photo = _pick_photo(art, avoid)
    except Exception as e:
        log.debug("photo pick failed: %s", e)

    # remember the photo so the next 10 cards never reuse it
    if photo is not None and state is not None and photo[2]:
        used = list(state.get("recent_photo_ids") or []) + [photo[2]]
        state["recent_photo_ids"] = used[-10:]

    try:
        if photo is not None:
            img = _render_photo_card(photo[0], art, variant, card_headline, src_line, bengali, stat, variant_idx)
            kind = "photo"
        else:
            img = _render_designer_card(art, card_headline, src_line, bengali, variant_idx,
                                        stat if variant == "stat_hero" else None)
            kind = "designer"
    except Exception as e:
        log.warning("card render failed (%s) - designer fallback", e)
        img = _render_designer_card(art, card_headline, src_line, bengali, variant_idx,
                                    stat if variant == "stat_hero" else None)
        kind = "designer"

    try:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = IMAGE_DIR / f"card_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
        img.save(path, "JPEG", quality=90, optimize=True, progressive=True)
        log.info("Card rendered: %s (%s / variant=%s%s)", path.name, kind, variant,
                 f" / stat={stat}" if variant == "stat_hero" and stat else "")
        return path
    except Exception as e:
        log.error("card save failed: %s", e)
        return None

# ==========================================================================
# FACEBOOK GRAPH API  (POSTs are NEVER retried - duplicate-post risk)
# ==========================================================================
def _fb_base():
    return f"https://graph.facebook.com/{FB_API_VERSION}"


def _fb_creds():
    return _get_secret("FB_PAGE_ACCESS_TOKEN"), _get_secret("FB_PAGE_ID")


def _fb_post_once(url, data=None, files=None, timeout=90):
    """Single-attempt Graph POST. Retrying POSTs can duplicate page posts."""
    r = _SESSION.post(url, data=data, files=files, timeout=timeout)
    try:
        j = r.json()
    except Exception:
        j = {}
    if r.status_code >= 300 or j.get("error"):
        msg = (j.get("error") or {}).get("message", "")[:220]
        raise RuntimeError(f"FB POST failed (HTTP {r.status_code}): {msg}")
    return j


def fb_get_recent_posts(limit=25):
    tok, pid = _fb_creds()
    if not tok or not pid:
        return []
    r = _http_get(f"{_fb_base()}/{pid}/posts",
                  params={"fields": "message,created_time", "limit": limit,
                          "access_token": tok},
                  timeout=20, retries=1)
    if r is None:
        return []
    try:
        return r.json().get("data", [])
    except Exception:
        return []


def _sync_state_from_facebook(state):
    """Page itself is the source of truth for cadence + Bengali daily count."""
    posts = fb_get_recent_posts()
    if not posts:
        return
    latest = 0.0
    today = _today_ist_str()
    bn_today = 0
    for p in posts:
        try:
            dt = datetime.strptime(p.get("created_time", ""), "%Y-%m-%dT%H:%M:%S%z")
            ts = dt.timestamp()
        except Exception:
            continue
        latest = max(latest, ts)
        try:
            local = datetime.fromtimestamp(ts, IST)
            if local.strftime("%Y-%m-%d") == today and _bengali_ratio(p.get("message") or "") > 0.25:
                bn_today += 1
        except Exception:
            pass
    if latest > state.get("last_post_ts", 0):
        state["last_post_ts"] = latest
    if state.get("bengali_date") != today:
        state["bengali_date"] = today
        state["bengali_count"] = bn_today
    else:
        state["bengali_count"] = max(state.get("bengali_count", 0), bn_today)
    quiet_h = (time.time() - latest) / 3600 if latest else -1
    log.info("FB sync: %d recent posts | last post %.1fh ago | Bengali today: %d",
             len(posts), quiet_h, state.get("bengali_count", 0))


def publish_photo_post(image_path, message):
    tok, pid = _fb_creds()
    if not tok or not pid:
        raise RuntimeError("FB_PAGE_ACCESS_TOKEN / FB_PAGE_ID missing")
    with open(image_path, "rb") as f:
        img_bytes = f.read()
    files = {"source": (image_path.name, img_bytes, "image/jpeg")}
    j = _fb_post_once(f"{_fb_base()}/{pid}/photos",
                      data={"access_token": tok, "message": message}, files=files, timeout=120)
    return j.get("post_id") or j.get("id")


def publish_text_post(message):
    tok, pid = _fb_creds()
    if not tok or not pid:
        raise RuntimeError("FB_PAGE_ACCESS_TOKEN / FB_PAGE_ID missing")
    j = _fb_post_once(f"{_fb_base()}/{pid}/feed",
                      data={"access_token": tok, "message": message}, timeout=60)
    return j.get("id")


def publish_comment(post_id, text):
    tok, _ = _fb_creds()
    if not tok:
        return None
    j = _fb_post_once(f"{_fb_base()}/{post_id}/comments",
                      data={"access_token": tok, "message": text}, timeout=30)
    return j.get("id")


# ==========================================================================
# ORCHESTRATION
# ==========================================================================
def _update_state_after_post(state, art, shape_id, persona_id):
    state["posted_keys"] = (state.get("posted_keys") or []) + [_dedup_key(art)]
    state["posted_titles"] = (state.get("posted_titles") or []) + [_normalize_title(art.title)]
    state["last_post_ts"] = time.time()
    for key, val, cap in (("recent_categories", art.category, 5),
                          ("recent_domains", art.domain, 3),
                          ("recent_shapes", shape_id, 3),
                          ("recent_personas", persona_id, 2)):
        lst = list(state.get(key) or [])
        lst.append(val)
        state[key] = lst[-cap:]
    state["post_counter"] = (state.get("post_counter", 0) or 0) + 1


def _should_post_bengali(state):
    today = _today_ist_str()
    if state.get("bengali_date") != today:
        state["bengali_date"] = today
        state["bengali_count"] = 0
    if state.get("bengali_count", 0) >= BENGALI_MAX_PER_DAY:
        return False
    return random.random() < BENGALI_PROBABILITY


def _save_preview(text_files, image_files):
    d = PREVIEW_DIR / _now_ist().strftime("%Y%m%d_%H%M%S")
    d.mkdir(parents=True, exist_ok=True)
    for name, content in (text_files or {}).items():
        (d / name).write_text(content, encoding="utf-8")
    for name, path in (image_files or {}).items():
        if path and Path(path).exists():
            shutil.copy(path, d / name)
    log.info("Preview saved to %s", d)
    return d


def run(args) -> int:
    log.info("=== India Tech Page Bot v%s | %s ===", VERSION,
             _now_ist().strftime("%d %b %Y, %H:%M IST"))
    if not PIL_OK:
        log.warning("Pillow not installed - posts will be text-only")

    state = _load_state()
    try:
        _sync_state_from_facebook(state)
    except Exception as e:
        log.debug("FB sync skipped: %s", e)
    log.info("LLM providers: Groq=%s, Gemini=%s",
             "ready" if _get_secret("GROQ_API_KEY") else "MISSING KEY (set GROQ_API_KEY!)",
             "ready" if _get_secret("GEMINI_API_KEY") else "not set (optional fallback)")

    # ---- 1. fetch
    articles = fetch_all_articles()
    if not articles:
        log.warning("No articles fetched from any source - aborting run")
        _save_state(state)
        return 1

    # ---- 2. score + log the leaderboard
    clusters = score_articles(articles)
    flat = sorted([c[0] for c in clusters if c], key=lambda a: a.score, reverse=True)
    log.info("Scored %d stories -> %d unique clusters. Top candidates:", len(articles), len(clusters))
    for a in flat[:8]:
        # v5.3.1: flag India-gate-blocked stories so the CI leaderboard never
        # looks like a global wire story will post (it won't)
        gate = "" if (getattr(a, "india_ok", True) or a.corroborations >= 3) \
            else "  [gated: no India angle]"
        log.info("   %5.1f  %-13s %s%s", a.score, a.category, a.title[:78], gate)
    threshold = args.threshold if (getattr(args, "threshold", None)) else MIN_ENGAGEMENT_SCORE_TO_POST

    # ---- 3. fast paths: --preview-image / --bengali-preview
    if getattr(args, "preview_image", False):
        art = select_article(clusters, state, max(5, threshold - 2)) or (flat[0] if flat else None)
        if art is None:
            log.warning("nothing to render")
            return 1
        card = render_card(art, state=state)
        _save_preview({"headline.txt": art.title + "\n" + art.url}, {"card.jpg": card} if card else {})
        return 0

    if getattr(args, "bengali_preview", False):
        art = select_article(clusters, state, max(4, threshold - 2)) or (flat[0] if flat else None)
        if art is None:
            log.warning("nothing to preview")
            return 1
        grounding = _article_grounding(art)
        post, card_h, provider = generate_bengali_post(art, grounding)
        if not post:
            log.error("Bengali generation failed")
            return 1
        card = render_card(art, state=state, bengali=True, card_headline=card_h)
        _save_preview({"bengali.txt": post}, {"bengali_card.jpg": card} if card else {})
        log.info("BENGALI PREVIEW (provider=%s):\n%s", provider, post)
        return 0

    # ---- 4. select
    art = select_article(clusters, state, threshold)
    if art is None:
        log.info("No story cleared the bar (threshold %d) - skipping. Quality > filler.", threshold)
        _save_state(state)
        return 0
    log.info("Selected: [%.1f | %s] %s (%s, via %s, %d sources)",
             art.score, art.category, art.title, art.source, art.via, art.corroborations)

    # ---- 5. write
    shape = _pick_shape(state)
    persona = _pick_persona(state)
    grounding = ""
    try:
        grounding = _article_grounding(art)
    except Exception as e:
        log.debug("grounding failed: %s", e)
    wiki = ""
    try:
        wiki = _wikipedia_context(art.title)
    except Exception as e:
        log.debug("wiki failed: %s", e)
    commentary, meta = generate_commentary(art, shape, persona, grounding, wiki)
    if not commentary:
        log.error("LLM commentary failed after retries - skipping run (better silent than bad). "
                  "Check the WARNING lines above for the exact API error; usual suspects: "
                  "invalid/expired key, rate limit, or a renamed model (override via GROQ_MODELS env).")
        _save_state(state)
        return 1
    log.info("Commentary ready (provider=%s, shape=%s, persona=%s):\n%s",
             meta.get("provider"), shape["id"], meta.get("persona"), commentary)

    # ---- 6. image
    image_path = None
    if not getattr(args, "no_image", False) and PIL_OK:
        try:
            image_path = render_card(art, state=state)
        except Exception as e:
            log.warning("image render failed (%s) - continuing without image", e)

    # ---- 7. publish (or preview)
    if getattr(args, "dry_run", False):
        files = {"message.txt": commentary}
        imgs = {"card.jpg": image_path} if image_path else {}
        if _should_post_bengali(state):
            try:
                bn_post, bn_card_h, _ = generate_bengali_post(art, grounding)
                if bn_post:
                    bn_img = render_card(art, state=state, bengali=True, card_headline=bn_card_h)
                    files["bengali.txt"] = bn_post
                    if bn_img:
                        imgs["bengali_card.jpg"] = bn_img
            except Exception as e:
                log.debug("bengali preview failed: %s", e)
        _save_preview(files, imgs)
        log.info("DRY RUN complete - nothing published.")
        return 0

    try:
        post_id = publish_photo_post(image_path, commentary) if image_path else publish_text_post(commentary)
        log.info("Published post id=%s", post_id)
    except Exception as e:
        log.error("Publish failed: %s - state NOT updated (will retry next run)", e)
        _save_state(state)
        return 1

    # optional reach trick: link as first comment instead of post body
    if POST_LINK_AS_FIRST_COMMENT and post_id and art.url:
        try:
            publish_comment(post_id, f"Full story: {art.url}")
            log.info("Article link posted as first comment")
        except Exception as e:
            log.warning("Link comment failed (non-fatal): %s", e)

    _update_state_after_post(state, art, shape["id"], meta.get("persona", ""))

    # ---- 8. Bengali companion track
    if _should_post_bengali(state):
        try:
            bn_post, bn_card_h, bn_provider = generate_bengali_post(art, grounding)
            if bn_post:
                bn_img = render_card(art, state=state, bengali=True, card_headline=bn_card_h)
                bn_id = publish_photo_post(bn_img, bn_post) if bn_img else publish_text_post(bn_post)
                state["bengali_count"] = state.get("bengali_count", 0) + 1
                state["bengali_date"] = _today_ist_str()
                log.info("Bengali companion published (id=%s, provider=%s)", bn_id, bn_provider)
        except Exception as e:
            log.warning("Bengali companion failed (non-fatal): %s", e)

    _save_state(state)
    log.info("Run complete. post=%s", post_id)
    return 0


# ==========================================================================
# OFFLINE SELF-TEST (no keys, no posting)
# ==========================================================================
def _synthetic_photo(w=1600, h=2000):
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        t = y / h
        d.line([(0, y), (w, y)], fill=(int(30 + 180 * t), int(20 + 60 * t), int(120 + 100 * (1 - t))))
    for i in range(6):
        x = (i * 371) % w
        cy = (i * 577) % h
        r = 120 + (i * 97) % 300
        d.ellipse([x - r, cy - r, x + r, cy + r], outline=(255, 255, 255), width=8)
    return img


def _self_test() -> int:
    log.info("Running self-test (offline, no keys needed)...")
    failures = []

    # text utils
    n = _normalize_title("Zomato  Swiggy: \u20b9500 Cr deal \u2014 done!")
    if n != "zomato swiggy 500 cr deal done":
        failures.append(f"normalize_title -> {n!r}")
    if not _titles_similar("Zomato acquires Blinkit for Rs 4,400 crore",
                           "Zomato buys Blinkit in Rs 4,400 crore deal"):
        failures.append("titles_similar should be True for dupes")
    if _titles_similar("Infosys announces new AI platform", "Zomato acquires Blinkit"):
        failures.append("titles_similar should be False for unrelated")
    if _bengali_ratio("এটি একটি বাংলা বাক্য") < 0.5:
        failures.append("bengali_ratio detection")
    if _bn_digits("27") != "২৭":
        failures.append("bn_digits")
    if _card_headline("Big funding news | Inc42").endswith("Inc42"):
        failures.append("card_headline should strip '| SiteName'")

    # stat extraction (feeds the stat_hero card)
    stat_cases = [
        ("Jio posts \u20b952,000 crore profit", "\u20b952,000 Cr"),
        ("Sarvam raises $234 million", "$234M"),
        ("Startup valued at $1.5 billion", "$1.5B"),
        ("Ola raises Rs 16 crore", "\u20b916 Cr"),
        ("Profit skyrockets 4X YoY", "4X"),
        ("Users cross 500 million", "500M"),
        ("Tariff hike of 25% announced", "25%"),
    ]
    for src, want in stat_cases:
        got, _raw = _extract_stat(src)
        if got != want:
            failures.append(f"extract_stat({src!r}) -> {got!r}, want {want!r}")
    if _extract_stat("New smartphone launched today")[0] is not None:
        failures.append("extract_stat should find nothing in a stat-less headline")

    # scoring
    now = datetime.now(UTC)
    a1 = Article("Paytm parent One97 raises $1.1 billion ahead of IPO listing",
                 "https://x.com/a", "T", "t.com", now)
    a2 = Article("Best smartphone deals and coupons this week",
                 "https://x.com/b", "T", "t.com", now)
    a3 = Article("Local man wins chess tournament",
                 "https://x.com/c", "T", "t.com", None)
    score_articles([a1, a2, a3])
    if not (a1.score >= 8 and a1.category in ("ipo", "funding", "fintech")):
        failures.append(f"scoring strong story: {a1.score} {a1.category}")
    if a2.score >= 8:
        failures.append(f"junk leaked through: {a2.score}")
    if a3.score >= 4:
        failures.append(f"irrelevant scored too high: {a3.score}")

    # v5.3: content-quality firewall - SEO listicles die, tier-1 wins
    a4 = Article("Top 10 best 5G smartphones under \u20b920,000 to buy this week",
                 "https://x.com/d", "T", "gadgetsnow.com", now)
    a5 = Article("Top 10 best 5G smartphones under \u20b920,000 to buy this week",
                 "https://x.com/e", "T", "economictimes.indiatimes.com", now)
    a6 = Article("Reliance acquires Disney's India business for \u20b970,000 crore",
                 "https://x.com/f", "T", "economictimes.indiatimes.com", now)
    big = Article("Zeta raises $1.2 billion", "https://x.com/g", "T", "inc42.com", now)
    small = Article("Zeta raises $5 million", "https://x.com/h", "T", "inc42.com", now)
    score_articles([a4, a5, a6, big, small])
    if a5.score >= 9:
        failures.append(f"SEO listicle leaked through a tier-1 outlet: {a5.score}")
    if a4.score >= 9:
        failures.append(f"SEO listicle leaked through: {a4.score}")
    if a6.score < 12:
        failures.append(f"mega-deal scored too low: {a6.score}")
    if big.score <= small.score:
        failures.append(f"money magnitude not ranked: big={big.score} small={small.score}")
    if _domain_authority("economictimes.indiatimes.com") <= 0:
        failures.append("domain authority table broken")
    if not _india_signal(a6):
        failures.append("india signal detection broken")

    # dedup + selection safety valve
    st = _default_state()
    st["posted_titles"] = [_normalize_title(a1.title)]
    if select_article([[a1]], st, 1) is not None:
        failures.append("duplicate should be rejected")

    # image pipeline (offline: synthetic photo, no Pexels)
    if PIL_OK:
        _ensure_fonts()
        out_dir = PREVIEW_DIR / "selftest"
        out_dir.mkdir(parents=True, exist_ok=True)
        art = Article("Reliance Jio posts record \u20b952,000 crore profit as users cross 500 million",
                      "https://example.com/jio", "Test Feed", "example.com", now, category="telecom")
        img = _render_designer_card(art, _card_headline(art.title), "TEST FEED \u00b7 TODAY", False, 0)
        img.save(out_dir / "designer_card.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"designer card size {img.size}")
        photo = _synthetic_photo()
        for i, v in enumerate(CARD_VARIANTS):
            img = _render_photo_card(photo, art, v, _card_headline(art.title), "TEST FEED \u00b7 TODAY")
            img.save(out_dir / f"photo_card_{i + 1}_{v}.jpg", "JPEG", quality=90)
            if img.size != (CARD_W, CARD_H):
                failures.append(f"photo card {v} size {img.size}")
        # v5.3: accent rotation must actually change the render
        imgA = _render_photo_card(photo, art, "split_card", "Accent rotation check",
                                  "S", False, None, 0)
        imgB = _render_photo_card(photo, art, "split_card", "Accent rotation check",
                                  "S", False, None, 1)
        if imgA.tobytes() == imgB.tobytes():
            failures.append("accent rotation produced identical cards")

        # giant-stat poster (photo + designer versions)
        img = _render_photo_card(photo, art, "stat_hero",
                                 "Jio posts record profit as users cross 500 million",
                                 "TEST FEED \u00b7 TODAY", stat="\u20b952,000 Cr")
        img.save(out_dir / "photo_card_stat_hero.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"stat hero card size {img.size}")
        img = _render_designer_card(art, "Jio posts record profit as users cross 500 million",
                                    "TEST FEED \u00b7 TODAY", False, 2, stat="\u20b952,000 Cr")
        img.save(out_dir / "designer_card_stat.jpg", "JPEG", quality=90)
        if (FONT_DIR / "NotoSansBengali.ttf").exists():
            img = _render_designer_card(art, "জিও-র রেকর্ড লাভ, ৫০ কোটি ইউজার",
                                        "টেস্ট ফিড \u00b7 আজ", True, 1)
            img.save(out_dir / "designer_card_bengali.jpg", "JPEG", quality=90)
        else:
            log.info("Bengali font not downloaded - skipping BN card test")
        log.info("Sample cards written to %s", out_dir)
    else:
        log.warning("Pillow missing - image tests skipped")

    if failures:
        for f in failures:
            log.error("FAIL: %s", f)
        log.error("Self-test FAILED (%d failures)", len(failures))
        return 1
    log.info("Self-test PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="India Tech News Facebook Page Bot")
    ap.add_argument("--dry-run", action="store_true", help="no posting; save previews to ./preview/")
    ap.add_argument("--bengali-preview", action="store_true", help="force one Bengali companion preview")
    ap.add_argument("--preview-image", action="store_true", help="render the best card only (no LLM, no post)")
    ap.add_argument("--no-image", action="store_true", help="publish text-only post")
    ap.add_argument("--self-test", action="store_true", help="offline checks + sample cards")
    ap.add_argument("--threshold", type=int, default=None, help="override MIN_ENGAGEMENT_SCORE_TO_POST")
    ap.add_argument("--verbose", action="store_true", help="debug logging")
    args = ap.parse_args()
    _setup_logging(args.verbose)
    if args.self_test:
        return _self_test()
    return run(args)


if __name__ == "__main__":
    sys.exit(main())
