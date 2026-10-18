#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TRENDING & ENTERTAINMENT NEWS - FACEBOOK PAGE BOT  .  v5.7
=========================================================
Single-file bot: fetches worldwide trending + entertainment news (movies,
music, TV, sports, celebs, gaming, viral moments, space, tech launches) from
many free sources, scores it, writes a human-sounding post (English + optional
Bengali companion), renders a designed 1080x1350 image card with impressive
photography, and publishes to a Facebook Page.

WHAT'S NEW IN v5.7 (engagement engine - the page becomes an experience)
  INTERACTIVE SPECIALS (~1 in 3 posts, never two in a row, capped 2/day)
    * TRIVIA DROP: clean entertainment trivia from OpenTDB (free, no key).
      Custom quiz card (giant '?', lettered options) + the answer is posted
      as a first comment - people answer in the thread.
    * THIS OR THAT: A/B battle card - two striking photos, diagonal split,
      giant VS, readers pick a side in comments.
    * GUESS THE TITLE: 3 text clues on a mystery card, emoji riddle in the
      caption, answer in the first comment.
  CAROUSEL POSTS (~1 in 5 photo posts)
    * Facebook multi-photo swipeable carousels: designed headline card + 2
      extra impressive photos. Carousels get ~2-3x the reach of single-photo
      posts. Falls back to a single-photo post if any upload step fails.
  MORE VOICES
    * 2 new post shapes (REACTION SPLIT / GUESS HOOK) + 2 new hooks.
  NEVER-FAIL AUTOMATION
    * Template-caption fallback: if both Groq and Gemini are down, the page
      still posts a hand-written template caption (page never goes quiet).
    * Every run path now exits 0 (green CI); real problems write .run_alert,
      which the workflow turns into an auto GitHub issue WITHOUT failing
      the job. Fetch/LLM/publish failures degrade gracefully, never crash.
  Earlier: v5.6 (worldwide pivot, NSFW firewall), v5.5 (human touch),
  v5.4 (diversity, crash-safe, insights), v5.3 (authority tiers),
  v5.2 (6 layouts), v5.1 (Groq migration), v5.0 (multi-source fan-out).

WHAT WAS NEW IN v5.6 (worldwide entertainment pivot)
  CONTENT
    * Scope pivot: Indian tech news -> GLOBAL trending & entertainment news.
      All India-gate logic removed - the whole world is in play.
    * New source lineup: Variety, THR, Deadline, Billboard, E! News, NME,
      NY Post Ent, IndieWire, Guardian (culture/music/world/sport/tech),
      BBC (news/sport), Space.com, NASA, IGN, GameSpot, PC Gamer, The Verge,
      Bored Panda + Google News WORLD/ENTERTAINMENT/SPORTS/TECH topic feeds
      and fresh when:Nd trending fan-out queries.
    * 11 entertainment keyword categories (viral/movies/music/tv/celebs/
      sports/gaming/space/tech/odd) + pop-culture entity table (Taylor
      Swift, Messi, Marvel, Netflix, PlayStation, SpaceX...).
    * EXPLICIT-CONTENT FIREWALL: every headline/description passes an NSFW
      regex gate at fetch time; LLM output is validated again post-write;
      prompts carry a strict family-friendly rule. No explicit content,
      no graphic gore - ever.
  IMAGES
    * "Impressive photo" engine: curated cinematic query banks per beat
      (concert lights, stadium floodlights, rocket launches, red carpets,
      neon city, aurora), larger Pexels candidate pools (18/page, top 4
      downloaded), landscape-friendly ranking so dramatic wide shots win,
      and randomised cinematic modifiers so no two days look alike.
  AUTOMATION (unchanged from v5.5)
    * 4 posts/day IST cron, --engage comment-reply pass, --insights
      tracker, crash-safe in-flight dedup, .run_ok sentinel.
  Earlier: v5.5 (human-touch: hooks, engage mode, 8 layouts, grain),
  v5.4 (diversity caps, crash-safe, insights), v5.3 (authority tiers,
  money-magnitude, SEO firewall), v5.2 (6 layouts, stat posters),
  v5.1 (Groq migration), v5.0 (multi-source fan-out).
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
    python bot.py --insights         # engagement stats -> state.json + leaderboard
    python bot.py --engage           # reply to fresh comments + like a few
    python bot.py --verbose          # debug logging

ENV / SECRETS  (Google Colab userdata OR environment; GitHub Actions secrets)
    FB_PAGE_ACCESS_TOKEN, FB_PAGE_ID     required to post
    NEWS_API_KEY                          recommended
    GROQ_API_KEY, GEMINI_API_KEY          at least one required
    PEXELS_API_KEY                        strongly recommended (photo cards)
    GNEWS_API_KEY                         optional extra source
    PAGE_HANDLE                           optional, e.g. "@trendingbuzz"
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

VERSION = "5.7"

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
# v5.4: hard daily topic diversity - the 4 daily posts should cover 4 beats,
# not 4 funding roundups of the same flavour
MAX_PER_CATEGORY_PER_DAY = _env_int("MAX_PER_CATEGORY_PER_DAY", 2)
# v5.4: deterministic hashtag safety net - when the LLM writes a post with ZERO
# hashtags, append up to 2 clean topical tags (category + company) for reach
SMART_TAGS = _env_bool("SMART_TAGS", True)
# v5.5: interaction hooks - poll-style CTAs appended to most (not all) posts.
# This is what commercial pages do: every post invites a cheap action.
ENGAGE_HOOK_PROBABILITY = _env_float("ENGAGE_HOOK_PROBABILITY", 0.75)
# v5.5: occasional text-only hot takes (no image card) - the feed stops
# looking like a wall of identical branded cards; ~1 in 7 posts
TEXT_POST_PROBABILITY = _env_float("TEXT_POST_PROBABILITY", 0.15)
# v5.5: --engage run - reply to fresh comments + like a few more (page feels alive)
ENGAGE_MAX_REPLIES = _env_int("ENGAGE_MAX_REPLIES", 4)
ENGAGE_LIKE_COMMENTS = _env_int("ENGAGE_LIKE_COMMENTS", 6)
ENGAGE_COMMENTS_PER_POST = _env_int("ENGAGE_COMMENTS_PER_POST", 20)
POST_LINK_AS_FIRST_COMMENT = _env_bool("POST_LINK_AS_FIRST_COMMENT", False)
# ---- v5.7: engagement engine tunables --------------------------------
# Interactive specials (trivia / this-or-that / guess-the-title). ~1 in 3
# posts; never two specials in a row; hard cap per day; every one is
# env-overridable so the mix can be tuned without touching code.
SPECIAL_POSTS_ENABLED = _env_bool("SPECIAL_POSTS_ENABLED", True)
TRIVIA_PROBABILITY = _env_float("TRIVIA_PROBABILITY", 0.16)
VS_PROBABILITY = _env_float("VS_PROBABILITY", 0.12)
RIDDLE_PROBABILITY = _env_float("RIDDLE_PROBABILITY", 0.08)
MAX_SPECIALS_PER_DAY = _env_int("MAX_SPECIALS_PER_DAY", 2)
# Carousel posts (Facebook multi-photo): headline card + 2 extra photos.
# Carousels reliably out-reach single-photo posts; ~1 in 5 photo posts.
CAROUSEL_ENABLED = _env_bool("CAROUSEL_ENABLED", True)
CAROUSEL_PROBABILITY = _env_float("CAROUSEL_PROBABILITY", 0.20)
CAROUSEL_MAX_PHOTOS = _env_int("CAROUSEL_MAX_PHOTOS", 3)
# v5.7: if both LLM providers are down, post a hand-written template caption
# instead of skipping the run - a quiet page loses reach faster than a
# slightly plainer post. Disable with TEMPLATE_CAPTION_FALLBACK=false.
TEMPLATE_CAPTION_FALLBACK = _env_bool("TEMPLATE_CAPTION_FALLBACK", True)
PAGE_HANDLE = os.getenv("PAGE_HANDLE", "").strip()   # e.g. "@indiatechdaily"
FB_API_VERSION = os.getenv("FB_API_VERSION", "v21.0")
HTTP_TIMEOUT = _env_int("HTTP_TIMEOUT", 25)

# card geometry (portrait 1080x1350 default; v5.5 also mixes 1080x1080 squares
# so the feed stops looking like a wall of identical-format cards)
CARD_W, CARD_H = 1080, 1350
SQUARE_CARD_PROBABILITY = _env_float("SQUARE_CARD_PROBABILITY", 0.35)
SUPERSAMPLE = _env_int("SUPERSAMPLE", 2)
# 8 rotating layouts - structural variety beats colour tweaks in the feed
CARD_VARIANTS = ["bottom_sheet", "top_banner", "split_card",
                 "full_bleed", "stat_hero", "magazine", "ticker", "quote"]


def _set_card_format(square: bool):
    """v5.5: switch the render canvas between portrait and square. The two
    render functions read CARD_W/CARD_H at call time, so setting the globals
    before dispatch is enough."""
    global CARD_W, CARD_H
    CARD_W, CARD_H = (1080, 1080) if square else (1080, 1350)


def _apply_grain(img, strength=8):
    """v5.5: subtle film grain. 'Too clean' flat gradients are the #1 visual
    tell of AI-generated cards - real design files carry sensor/paper noise."""
    try:
        noise = Image.effect_noise(img.size, 24).convert("L")
        noise_rgb = Image.merge("RGB", (noise, noise, noise))
        return Image.blend(img, noise_rgb, min(0.06, strength / 255.0))
    except Exception:
        return img


def _duotone(img, dark=(12, 14, 24), light=(120, 200, 255)):
    """v5.5: map the photo's luminance through a dark->accent ramp. Editorial
    magazines duotone their feature photos; it also makes photo + typography
    feel like ONE designed object instead of text slapped on stock."""
    try:
        g = img.convert("L")
        lut = []
        for ch in range(3):
            lut.extend(int(dark[ch] + (light[ch] - dark[ch]) * i / 255.0) for i in range(256))
        return g.convert("RGB").point(lut)
    except Exception:
        return img.convert("RGB")

# ==========================================================================
# SCORING CONFIG - keyword rules (ALL word-boundary safe, case-insensitive)
# ==========================================================================
# v5.6: entertainment pivot. 11 beats - the categories the page now covers.
# (category, points, regex)
_KEYWORD_RULES = [
    # ---- VIRAL (the page's identity beat - what's blowing up right now)
    ("viral", 6, r"\b(?:gone viral|goes viral|viral video|viral clip|breaking the internet|internet by storm|millions of views|trending (?:now|worldwide|everywhere|globally)|most-?(?:watched|shared|liked|streamed))\b"),
    ("viral", 3, r"\b(?:blowing up|took over the internet|everyone is (?:talking|posting) about|no one saw (?:this|it) coming|watched [\d,.]+ (?:million|billion|mn|bn) times)\b"),
    # ---- MOVIES
    ("movies", 5, r"\b(?:box office|opening weekend|movie|movies|film|films|sequel|prequel|trailer|teaser|premiere|screening|cinema|cinemas)\b"),
    ("movies", 4, r"\b(?:Oscars?|Academy Award\w*|film festival|Cannes|Venice|Sundance|director|franchise|casting|cast|IMAX|superhero|blockbuster|Bollywood|Hollywood|no-?one opener|post-?credit)\b"),
    # ---- MUSIC
    ("music", 5, r"\b(?:album|world tour|stadium tour|concert|live show|Grammys?|Grammy|Billboard (?:chart|Hot 100|200)|tops? the chart\w*|debut\w* at (?:no\.?|number) ?[0-9]|number-?one)\b"),
    ("music", 3, r"\b(?:singer|rapper|band|K-?pop|setlist|headline\w* (?:show|festival|slot)|sold-?out|encore|vinyl|single drop\w*|chart debut)\b"),
    # ---- TV & STREAMING
    ("tv", 4, r"\b(?:series|season (?:finale|premiere)|show renewed|cancell?ed|Netflix|HBO|Prime Video|Disney\+|Apple TV\+|Hulu|episode|episodes|binge-?watch\w*|streaming (?:debut|hit|record|rights)|reality show|docuseries|season [0-9])\b"),
    # ---- CELEBS (clean celebrity news only - the NSFW firewall runs upstream)
    ("celebs", 4, r"\b(?:celebrit\w+|red carpet|breaks? (?:her|his|their) silence|split|divorce|engaged|wedding|baby (?:news|announcement)|Met Gala|power couple|breakup|announces? pregnancy)\b"),
    ("celebs", 2, r"\b(?:actor|actors|actress\w*|superstar|pop star|rock star|movie star|influencer|rapper'\w*)\b"),
    # ---- SPORTS
    ("sports", 5, r"\b(?:champion|champions|championship|World Cup|Olympics?|Olympic|Grand Slam|finals?|hat-?trick|world record|title (?:win|defense|race|clinch)|trophy|gold medal|record-?breaking|Super Bowl|playoffs?)\b"),
    ("sports", 3, r"\b(?:goal|goals|touchdown|home run|pole position|transfer (?:saga|fee|window|news)|cricket|football|tennis|basketball|F1|Formula 1|NBA|NFL|UFC|WWE|IPL|Premier League|Champions League|La Liga|test match)\b"),
    # ---- GAMING
    ("gaming", 4, r"\b(?:PlayStation[45]?|Xbox|Nintendo|Switch 2|Steam|speedrun|esports?|game (?:launch|release|trailer)|console|patch notes|early access|open beta|video game\w*)\b"),
    ("gaming", 3, r"\b(?:Minecraft|Fortnite|Roblox|Valorant|Call of Duty|Grand Theft Auto|Cyberpunk|Baldur'?s Gate|Zelda|Mario|gameplay reveal|director\??\'?s cut)\b"),
    # ---- SPACE (impressive, uplifting, endlessly shareable)
    ("space", 4, r"\b(?:NASA|SpaceX|rocket|rockets|satellite|satellites|spacewalk|moon|lunar|Mars|asteroid|space station|space mission|ISRO|Chandrayaan|liftoff|spacecraft|space telescope|space launch|astronaut\w*)\b"),
    # ---- TECH (launches, gadgets, AI - the fun side, not the finance side)
    ("tech", 3, r"\b(?:iPhone|Galaxy S[0-9]|foldable|launch event|unveil\w*|OpenAI|ChatGPT|Gemini|robot|robots|drone\w*|MacBook|Apple (?:Watch|Vision)|wearable\w*|AI model|self-?driving|Cybertruck|smart glasses)\b"),
    # ---- ODD / WOW (world records & bizarre-but-clean)
    ("odd", 4, r"\b(?:Guinness World Record|world-?record attempt|bizarre|unusual|weird|strange|oddly satisfying|one in a (?:million|billion)|record attempt|world'?s (?:fastest|tallest|longest|biggest|smallest))\b"),
]

# (regex, penalty) - penalties applied once per rule unless the penalty is
# small (1), in which case each match costs 1 point, capped at 3.
# v5.6: entertainment is CORE now - the old cricket/Bollywood/box-office
# penalties are gone. Ads, SEO farms, horoscopes and clickbait still die.
_NEGATIVE_RULES = [
    (r"\bsponsored\b|\badvertorial\b|\baffiliate\b|\bcoupon\w*\b|\bpromo code\w*\b|\bdiscount\w*\b|\bbest deals?\b|\bdeals of the day\b|\bsale alert\b", 5),
    (r"\bhiring\b|\bjob (?:openings?|listings?|fair)\b|\bwalk-?in\b|\bcareers?\b", 8),
    (r"\bhoroscope\b|\bastrolog\w*\b|\brecipes?\b|\bskincare\b|\bmakeup tutorial\w*\b|\bfashion tips\b|\bworkout plan\b|\bdiet plan\b|\brelationship advice\b", 9),
    # v5.6: lighter than v5.3 - entertainment journalism legitimately leans on
    # 'reportedly'. Still nudges confirmed news above blind items.
    (r"\breportedly\b|\balleged\w*\b|\brumou?r\w*\b|\bmight\b|\bexpected to\b|\bplanning to\b|\bslated to\b|\bin talks\b|\bunconfirmed\b", 1),
    # v5.3: SEO/listicle firewall - evergreen filler, not news
    (r"\b(?:top|best|worst|cheapest|coolest)\s+\d+\w*\b|\b\d+\s+(?:best|top|must-?haves?|movies|shows|songs|games)\b|\bunder\s?\u20b9\s?\d[\d,.]*\b|\bbuying guide\b|\bhow to (?:choose|buy|pick|get)\b|\bfull specs?\b|\bunboxing\b|\breview roundup\b|\bprice list\b|\bcoupon codes?\b|\b(?:deal|offer) alert\b|\branked\b", 9),
    # v5.3: digests / roundups / newsletters - yesterday's news sandwiches
    (r"\b(?:roundup|wrap-?up|digest|recap|week in review|this week in (?:tech|entertainment|music|gaming)|top stories of the week|stories of the week|morning brief|newsletter)\b", 5),
    # v5.3.1: date-range recap series ("Between June 01 and June 06, as many
    # as 18 Indian startups raised..." / "...raised $1.08 Bn from June 22 to
    # June 27" - weekly tracker in sentence form)
    (r"\b(?:Between|from)\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2}\s+(?:and|to)\s+", 6),
    # v5.3: hashtag video-clip headlines ("#WATCH | ...") - link posts, not stories
    (r"\#(?:WATCH|VIDEO|LIVE|LIVETV)\s*[|\u00bb:\-]?", 3),
    # v5.4: clickbait firewall (kept in v5.6 - fun page, not a sleaze page;
    # note the viral terms moved OUT to the positive side)
    (r"\byou won'?t believe\b|\bthis one trick\b|\bdoctors hate\b|\bshocking(?:ly)?\b|\bjaw-?dropping\b|\bmind-?blowing\b|\bunmissable\b|\bclick here\b|\bwatch before\b|\bwhat happens next\b", 6),
    # v5.6: routine party politics ranks below entertainment (fun-first page;
    # only a massively corroborated political moment still clears the bar)
    (r"\b(?:white house|election\w*|senate|senator|parliament|impeach\w*|ballot\w*|voters?|campaign rally|president (?:trump|biden|modi|putin)|donald trump|prime minister narendra modi)\b", 4),
]

# v5.6: EXPLICIT-CONTENT FIREWALL - the page is family-friendly entertainment.
# Hard-rejected at fetch time (title+description), again on LLM output, and the
# image queries are curated safe banks (never derived from raw headline text).
_EXPLICIT_RE = re.compile(
    r"\b(?:porn\w*|xxx|x-?rated|nudes?|naked|nudity|topless|bottomless|onlyfans|"
    r"playboy|stripper\w*|escort (?:service|girl)|brothel\w*|orgy|orgies|threesome|"
    r"nsfw|penis\w*|vagina\w*|genital\w*|hardcore|fetish\w*|bdsm|erotic\w*|obscene|"
    r"sexting|sex (?:tape|scene|ual content|ual assault)|(?:sexual|indecent) assault|"
    r"molest\w*|rap(?:e|ed|ing)\b|behead\w*|decapitat\w*|dismember\w*|disembowel\w*|"
    r"snuff film|gore|gory (?:video|images?|footage)|graphic (?:footage|images?))\b",
    re.I)


def _is_explicit(text: str) -> bool:
    """True if title/description/caption trips the NSFW or graphic-gore gate."""
    return bool(_EXPLICIT_RE.search(text or ""))

_MONEY_RE = re.compile(
    r"(?:\u20b9|Rs\.?|INR)\s?\d[\d,.]*\s?(?:crore|crores|cr\b|lakh|lakhs|billion|bn\b|million|mn\b)?"
    r"|\$\s?\d[\d,.]*\s?(?:billion|bn\b|million|mn\b|crore)?"
    r"|\b\d[\d,.]*\s?(?:crore|crores|cr\b|lakh|lakhs)\b"
    r"|\b\d[\d,.]*\s?(?:billion|bn\b|million|mn\b)\b",
    re.I,
)

# v5.6: source-authority tiers for the entertainment world - the SAME story
# from a Tier-1 outlet beats a gossip blog. Matched by substring on the domain.
_DOMAIN_AUTHORITY = {
    # tier 1: global wires + the entertainment trades
    "apnews.com": 2.6, "reuters.com": 2.6, "nasa.gov": 2.6,
    "bbc.com": 2.5, "bbci.co.uk": 2.5, "theguardian.com": 2.5,
    "variety.com": 2.5, "hollywoodreporter.com": 2.5, "deadline.com": 2.4,
    "nytimes.com": 2.4, "espn.com": 2.4,
    # tier 2: trusted specialists
    "billboard.com": 2.2, "rollingstone.com": 2.2, "news.sky.com": 2.2,
    "skysports.com": 2.2, "washingtonpost.com": 2.2, "cnn.com": 2.2,
    "indiewire.com": 2.0, "ign.com": 2.0, "gamespot.com": 1.8,
    "pcgamer.com": 1.8, "polygon.com": 1.8, "eurogamer.net": 1.8,
    "space.com": 2.0, "nme.com": 1.8, "pitchfork.com": 1.8,
    "arstechnica.com": 1.8, "theverge.com": 2.0, "wired.com": 1.8,
    "nbcnews.com": 2.0, "abcnews.go.com": 2.0, "cbsnews.com": 2.0,
    "ew.com": 1.8, "people.com": 1.8, "etonline.com": 1.6,
    "espncricinfo.com": 2.0, "cricbuzz.com": 1.6, "bleacherreport.com": 1.4,
    "independent.co.uk": 1.6, "usatoday.com": 1.6, "dw.com": 2.0,
    "aljazeera.com": 2.0, "france24.com": 1.8, "scmp.com": 1.6,
    # India (Bollywood/cricket stories legitimately surface here)
    "timesofindia.indiatimes.com": 1.6, "ndtv.com": 1.8, "economictimes.indiatimes.com": 1.6,
    "hindustantimes.com": 1.4, "indianexpress.com": 1.4, "news18.com": 1.2,
    "dnaindia.com": 1.0,
    # gossip tier - real stories, weaker sourcing
    "tmz.com": 1.2, "pagesix.com": 1.2, "usmagazine.com": 1.2,
    "dailymail.co.uk": 0.6,
    "thesun.co.uk": 0.6, "the-sun.com": 0.6, "mirror.co.uk": 0.6,
    # penalised: gadget-deal / SEO farms (listicles, coupons, price pages)
    "gadgetsnow.com": -1.5, "91mobiles.com": -2.0, "mysmartprice.com": -2.0,
    "smartprix.com": -2.0, "pricebaba.com": -1.5, "gizbot.com": -1.5,
    "fonearena.com": -1.0, "cashify.in": -1.5, "phonearena.com": -1.0,
    # v5.3.1: social-media aggregators are not publications - weak sourcing,
    # and they must not masquerade as corroboration
    "linkedin": -2.5, "instagram": -2.5, "facebook": -2.5, "twitter": -2.5,
    "x.com": -2.5, "youtube": -2.5, "reddit": -2.5, "medium": -1.0,
    # viral aggregators - fine as a source of ideas, weak as a citation
    "boredpanda.com": 0.8, "unilad.com": 0.5, "ladbible.com": 0.5,
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
# v5.6: gaming before movies - a GTA/PlayStation trailer is a GAMING story even
# though 'trailer' is a movie word (games carry game-specific keywords too).
_CATEGORY_PRIORITY = [
    "viral", "gaming", "movies", "music", "sports", "tv", "celebs",
    "space", "tech", "odd", "general",
]

# v5.6: pop-culture entity table: name -> (points, category hint)
_ENTITIES = {
    # streaming / studios
    "netflix": (5, "tv"), "disney+": (5, "tv"), "disney": (4, "movies"),
    "hbo": (4, "tv"), "max": (3, "tv"), "prime video": (4, "tv"),
    "apple tv": (4, "tv"), "hulu": (3, "tv"), "marvel": (4, "movies"),
    "dc studios": (4, "movies"), "star wars": (4, "movies"),
    "warner bros": (4, "movies"), "paramount": (3, "movies"),
    "stranger things": (5, "tv"), "squid game": (5, "tv"),
    "avatar": (3, "movies"), "james bond": (4, "movies"),
    "mission impossible": (3, "movies"), "spider-man": (4, "movies"),
    "avengers": (5, "movies"), "barbie": (3, "movies"),
    # music
    "taylor swift": (5, "music"), "beyonce": (5, "music"), "beyoncé": (5, "music"),
    "drake": (4, "music"), "bad bunny": (4, "music"), "bts": (4, "music"),
    "blackpink": (4, "music"), "k-pop": (3, "music"), "coldplay": (4, "music"),
    "bruno mars": (4, "music"), "the weeknd": (4, "music"), "weeknd": (4, "music"),
    "lady gaga": (4, "music"), "billie eilish": (4, "music"),
    "sabrina carpenter": (3, "music"), "ariana grande": (4, "music"),
    "rihanna": (4, "music"), "adele": (4, "music"), "eminem": (4, "music"),
    "spotify": (4, "music"), "coachella": (4, "music"),
    "grammy": (5, "music"), "grammys": (5, "music"),
    "oscar": (5, "movies"), "oscars": (5, "movies"), "academy awards": (5, "movies"),
    "emmy": (5, "tv"), "emmys": (5, "tv"), "golden globe": (4, "movies"),
    "met gala": (4, "celebs"), "cannes": (4, "movies"),
    # sports
    "messi": (5, "sports"), "lionel messi": (5, "sports"),
    "cristiano ronaldo": (5, "sports"), "ronaldo": (5, "sports"),
    "neymar": (4, "sports"), "mbappe": (4, "sports"), "mbapp\u00e9": (4, "sports"),
    "virat kohli": (5, "sports"), "ms dhoni": (4, "sports"), "rohit sharma": (3, "sports"),
    "neeraj chopra": (4, "sports"), "ipl": (4, "sports"),
    "world cup": (5, "sports"), "champions league": (4, "sports"),
    "premier league": (4, "sports"), "super bowl": (5, "sports"),
    "olympics": (4, "sports"), "formula 1": (4, "sports"), "f1": (3, "sports"),
    "max verstappen": (4, "sports"), "lewis hamilton": (4, "sports"),
    "wwe": (4, "sports"), "ufc": (4, "sports"), "wimbledon": (4, "sports"),
    "cricket world cup": (5, "sports"), "ipl auction": (5, "sports"),
    # gaming
    "playstation": (4, "gaming"), "xbox": (4, "gaming"), "nintendo": (4, "gaming"),
    "nintendo switch": (5, "gaming"), "steam": (3, "gaming"),
    "grand theft auto": (4, "gaming"), "gta": (3, "gaming"),
    "minecraft": (3, "gaming"), "fortnite": (3, "gaming"),
    "roblox": (3, "gaming"), "valorant": (2, "gaming"),
    "call of duty": (3, "gaming"), "nintendo switch 2": (5, "gaming"),
    # space & tech
    "nasa": (4, "space"), "spacex": (5, "space"), "isro": (4, "space"),
    "chandrayaan": (5, "space"), "artemis": (4, "space"), "james webb": (4, "space"),
    "elon musk": (4, "tech"), "apple": (3, "tech"), "iphone": (4, "tech"),
    "samsung": (3, "tech"), "openai": (4, "tech"), "chatgpt": (4, "tech"),
    "google": (3, "tech"), "nvidia": (3, "tech"), "tesla": (4, "tech"),
    "meta": (3, "tech"), "youtube": (3, "tv"),
    # celebs (world + India)
    "kim kardashian": (4, "celebs"), "tom cruise": (3, "celebs"),
    "shah rukh khan": (5, "celebs"), "salman khan": (4, "celebs"),
    "deepika padukone": (4, "celebs"), "priyanka chopra": (4, "celebs"),
    "ranbir kapoor": (3, "celebs"), "alia bhatt": (3, "celebs"),
    "prince harry": (3, "celebs"), "meghan": (3, "celebs"),
    "king charles": (4, "celebs"), "kate middleton": (4, "celebs"),
    "royal family": (4, "celebs"),
}

# ==========================================================================
# NEWS SOURCES
# ==========================================================================
# v5.6: worldwide entertainment + trending sources. Every URL live-probed.
# Movies/TV/music/celebs carry the feed; world/space/gaming/tech keep it broad.
RSS_FEEDS = [
    # movies, tv, awards
    ("Variety", "https://variety.com/feed/"),
    ("HollywoodReporter", "https://www.hollywoodreporter.com/feed/"),
    ("Deadline", "https://deadline.com/feed/"),
    ("IndieWire", "https://www.indiewire.com/feed/"),
    # music
    ("Billboard", "https://www.billboard.com/feed/"),
    ("NME", "https://www.nme.com/feed"),
    ("Guardian Music", "https://www.theguardian.com/music/rss"),
    # celebs / pop culture
    ("E! News", "https://eonline.com/feed"),
    ("NYPost Ent", "https://nypost.com/entertainment/feed/"),
    ("TOI Entertainment", "https://timesofindia.indiatimes.com/rssfeeds/1081479906.cms"),
    # sports
    ("BBC Sport", "https://feeds.bbci.co.uk/sport/rss.xml"),
    ("Guardian Sport", "https://www.theguardian.com/sport/rss"),
    # world news
    ("BBC News", "https://feeds.bbci.co.uk/news/rss.xml"),
    ("Guardian World", "https://www.theguardian.com/world/rss"),
    ("Guardian Culture", "https://www.theguardian.com/culture/rss"),
    # space (the most impressive photos on the internet)
    ("Space.com", "https://www.space.com/feeds/all"),
    ("NASA", "https://www.nasa.gov/news-release/feed/"),
    # gaming
    ("IGN", "https://feeds.ign.com/ign/all"),
    ("GameSpot", "https://www.gamespot.com/feeds/news/"),
    ("PC Gamer", "https://www.pcgamer.com/rss/"),
    # tech (launches & gadgets - the fun side)
    ("The Verge", "https://www.theverge.com/rss/index.xml"),
    ("Guardian Tech", "https://www.theguardian.com/uk/technology/rss"),
    # viral / odd / wow
    ("Bored Panda", "https://www.boredpanda.com/feed/"),
]

# free, fresh, no API key - the workhorse for trending topic fan-out.
# v5.6: when:Nd recency windows keep every query fresh + worldwide edition.
GOOGLE_NEWS_QUERIES = [
    "viral when:2d",
    "\"trending\" when:1d",
    "\"box office\" when:3d",
    "gaming when:2d",
    "\"world record\" when:3d",
    "\"breaks silence\" OR \"red carpet\" when:2d",
    "trailer OR teaser when:2d",
    "Grammy OR Oscar OR Emmy when:3d",
]

# Bengali-language Google News queries (companion-post grounding + previews)
GOOGLE_NEWS_BN_QUERIES = ["বিনোদন", "ভাইরাল ভিডিও", "সিনেমা", "ক্রিকেট"]

# v5.6: Google News US-edition topic feeds - these ARE the top-rated stories
# worldwide; joining the fan-out lets corroboration promote real big news.
GOOGLE_NEWS_TOPIC_FEEDS = [
    ("GoogleNews World", "https://news.google.com/rss/headlines/topic/WORLD?hl=en-US&gl=US&ceid=US:en"),
    ("GoogleNews Entertainment", "https://news.google.com/rss/headlines/topic/ENTERTAINMENT?hl=en-US&gl=US&ceid=US:en"),
    ("GoogleNews Sports", "https://news.google.com/rss/headlines/topic/SPORTS?hl=en-US&gl=US&ceid=US:en"),
    ("GoogleNews Technology", "https://news.google.com/rss/headlines/topic/TECHNOLOGY?hl=en-US&gl=US&ceid=US:en"),
]

NEWSAPI_EVERYTHING_QUERIES = [
    "viral video",
    "box office record",
    "world record",
    "celebrity news",
    "trending now",
]

# ==========================================================================
# IMAGE CONFIG
# ==========================================================================
ENTITY_VISUAL_HINTS = {
    # streaming / movies
    "netflix": "cinema screen dark room glow",
    "disney": "movie theater audience silhouette",
    "disney+": "living room tv night popcorn",
    "marvel": "superhero silhouette city night",
    "star wars": "star wars lights silhouette",
    "avengers": "superhero silhouette city night",
    "spider-man": "city skyline dusk dramatic",
    "james bond": "sports car night city lights",
    "hollywood": "hollywood sign los angeles dusk",
    "bollywood": "indian film dance colorful stage",
    "avatar": "3d cinema glasses dark",
    "imax": "imax cinema huge screen",
    # music
    "taylor swift": "stadium concert lights crowd",
    "beyonce": "concert stage spotlight silhouette",
    "beyoncé": "concert stage spotlight silhouette",
    "coldplay": "concert confetti stadium lights",
    "drake": "hip hop concert crowd hands",
    "bts": "kpop concert lightsticks crowd",
    "blackpink": "kpop stage neon lights",
    "k-pop": "kpop concert lightsticks crowd",
    "concert": "concert crowd hands silhouette",
    "spotify": "headphones music neon lights",
    "coachella": "music festival sunset crowd",
    "grammy": "gold gramophone trophy spotlight",
    "grammys": "gold gramophone trophy spotlight",
    "festival": "music festival crowd sunset",
    # celebs & events
    "red carpet": "red carpet photographers flashes night",
    "oscars": "oscars red carpet flashbulbs",
    "oscar": "oscars red carpet flashbulbs",
    "academy awards": "awards ceremony stage spotlight gold",
    "met gala": "met gala red carpet flashes",
    "cannes": "cannes red carpet photographers",
    # sports
    "messi": "soccer stadium night floodlights",
    "lionel messi": "soccer stadium night floodlights",
    "ronaldo": "soccer stadium night floodlights",
    "cristiano ronaldo": "soccer stadium night floodlights",
    "world cup": "soccer world cup trophy confetti",
    "cricket world cup": "cricket stadium floodlights night",
    "cricket": "cricket stadium floodlights night",
    "ipl": "cricket stadium floodlights crowd",
    "virat kohli": "cricket stadium crowd celebration",
    "champions league": "soccer trophy confetti celebration",
    "premier league": "soccer stadium packed crowd",
    "super bowl": "super bowl stadium lights halftime",
    "olympics": "olympic stadium opening ceremony",
    "wimbledon": "tennis court grass sunset",
    "wwe": "wrestling arena lights crowd",
    "ufc": "fight arena octagon lights",
    "formula 1": "f1 race cars night speed",
    "f1": "f1 race car speed motion blur",
    "tour de france": "cycling peloton mountain road",
    # gaming
    "playstation": "gaming controller neon dark",
    "xbox": "gaming controller neon dark",
    "nintendo": "video game console colorful",
    "gta": "neon city street night rain",
    "grand theft auto": "neon city street night rain",
    "minecraft": "voxel blocks landscape sunset",
    "fortnite": "colorful battle royale illustration sky",
    "esports": "esports arena rgb crowd",
    "roblox": "colorful blocks toys play",
    # space
    "nasa": "rocket launch night long exposure",
    "spacex": "falcon rocket launch smoke fire",
    "isro": "rocket launch smoke flames",
    "chandrayaan": "moon surface craters earthrise",
    "moon": "full moon night clouds",
    "mars": "mars surface rover sunset",
    "james webb": "deep space nebula stars",
    "aurora": "aurora borealis night mountains",
    # tech
    "iphone": "smartphone closeup neon bokeh",
    "apple": "minimal tech product studio dark",
    "openai": "artificial intelligence abstract lights",
    "chatgpt": "artificial intelligence abstract lights",
    "tesla": "electric car night city lights",
    "cybertruck": "futuristic car night neon",
    "robot": "humanoid robot studio light",
    "drone": "drone aerial city dusk",
}

# v5.6: IMPRESSIVE query banks - every query aims for a photo people stop
# scrolling for. Dramatic light, crowds, scale, energy. Photo-first strategy.
CATEGORY_IMAGE_QUERIES = {
    "viral": ["drone light show night sky", "crowd celebration confetti rain",
              "colorful powder festival crowd", "flash mob street performance"],
    "movies": ["cinema popcorn film reel macro", "movie theater screen audience silhouette",
               "film set camera crane lights", "hollywood sign los angeles dusk"],
    "music": ["concert stage lights crowd", "music festival night confetti",
              "singer silhouette spotlight microphone", "dj turntables neon smoke"],
    "tv": ["cozy living room tv night", "clapper board film set lights",
           "binge watching popcorn couch night", "premiere event red carpet"],
    "celebs": ["red carpet photographers flashes", "paparazzi cameras night city",
               "award show stage spotlight", "limousine night city lights"],
    "sports": ["stadium floodlights night full", "soccer goal celebration crowd",
               "trophy confetti team lift", "athlete sprint finish dramatic",
               "basketball dunk silhouette"],
    "gaming": ["esports arena rgb crowd", "gaming controller neon lights",
               "gaming setup triple monitor rgb", "retro arcade neon dark"],
    "space": ["rocket launch night long exposure", "earth from orbit sunrise",
              "milky way astrophotography mountains", "astronaut spacewalk earth",
              "aurora borealis night sky"],
    "tech": ["smartphone closeup neon bokeh", "futuristic gadget studio dark",
             "robot arm precision light", "drone aerial city dusk"],
    "odd": ["world record attempt crowd", "guinness world record celebration",
            "bicycle tall weird invention", "giant rubber duck harbor"],
    "general": ["city skyline night neon lights", "crowd cheering silhouette sunset",
                "fireworks night sky burst", "neon street night rain reflections"],
}

FALLBACK_IMAGE_QUERIES = [
    "crowd concert silhouette lights",
    "neon city street night rain",
    "aurora night mountains stars",
    "fireworks city celebration night",
]

# per-category design system: chip label + accent colour + designer gradient
# v5.6: entertainment palette - hotter, punchier, magazine-cover energy.
# accent2 = rotated second palette - same category never looks identical
CATEGORY_THEME = {
    "viral":     {"label": "VIRAL",      "accent": (255, 64, 96),   "accent2": (255, 140, 105), "grad": [(28, 6, 16), (130, 20, 60)]},
    "movies":    {"label": "MOVIES",     "accent": (255, 200, 60),  "accent2": (255, 150, 60),  "grad": [(30, 18, 4), (120, 60, 16)]},
    "music":     {"label": "MUSIC",      "accent": (255, 105, 180), "accent2": (190, 120, 255), "grad": [(24, 8, 34), (100, 20, 90)]},
    "tv":        {"label": "STREAMING",  "accent": (120, 220, 255), "accent2": (140, 190, 255), "grad": [(6, 18, 32), (20, 60, 110)]},
    "celebs":    {"label": "CELEBS",      "accent": (255, 160, 220), "accent2": (255, 110, 160), "grad": [(30, 8, 24), (120, 30, 80)]},
    "sports":    {"label": "SPORTS",     "accent": (80, 255, 160),  "accent2": (170, 240, 80),  "grad": [(6, 26, 14), (16, 90, 50)]},
    "gaming":    {"label": "GAMING",     "accent": (170, 120, 255), "accent2": (90, 200, 255),  "grad": [(20, 10, 38), (70, 20, 130)]},
    "space":     {"label": "SPACE",      "accent": (140, 180, 255), "accent2": (180, 160, 255), "grad": [(8, 10, 30), (30, 40, 100)]},
    "tech":      {"label": "TECH",       "accent": (110, 220, 230), "accent2": (120, 180, 255), "grad": [(8, 18, 24), (20, 60, 80)]},
    "odd":       {"label": "WOW",        "accent": (255, 230, 90),  "accent2": (255, 170, 70),  "grad": [(30, 22, 4), (110, 70, 16)]},
    "general":   {"label": "TRENDING",   "accent": (255, 120, 90),  "accent2": (255, 200, 90),  "grad": [(30, 10, 8), (110, 40, 20)]},
}

BN_CATEGORY_LABELS = {
    "viral": "ভাইরাল", "movies": "সিনেমা", "music": "মিউজিক", "tv": "টিভি",
    "celebs": "সেলিব্রিটি", "sports": "খেলা", "gaming": "গেমিং",
    "space": "স্পেস", "tech": "টেক", "odd": "আশ্চর্য", "general": "ট্রেন্ডিং",
}

# ==========================================================================
# LOGGING / SECRETS / HTTP / STATE
# ==========================================================================
log = logging.getLogger("trending-ent-bot")


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
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 TrendingEntPageBot/" + VERSION),
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
        "category_days": {},      # v5.4: {"2026-09-10": {"funding": 2, ...}} daily topic counts
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

    # v5.6: global entertainment categories (no country pin - worldwide mix)
    for cat in ("entertainment", "sports", "general", "technology"):
        for a in _call("/top-headlines", {"category": cat, "pageSize": 50}):
            out.append(_newsapi_article(a, f"newsapi/{cat}"))
    since = (datetime.now(UTC) - timedelta(hours=48)).strftime("%Y-%m-%dT%H:%M:%S")
    for q in NEWSAPI_EVERYTHING_QUERIES:
        for a in _call("/everything", {"q": q, "language": "en", "sortBy": "publishedAt",
                                       "pageSize": 15, "from": since}):
            out.append(_newsapi_article(a, "newsapi/everything"))
    return out


def fetch_hn() -> list:
    """Hacker News (Algolia) - free, no key. v5.6: global front page, big
    stories only (the day's biggest tech/launch moments - the fun side)."""
    try:
        r = _http_get("https://hn.algolia.com/api/v1/search",
                      params={"tags": "front_page", "hitsPerPage": 30},
                      timeout=15, retries=1)
        if r is None:
            return []
        hits = r.json().get("hits", [])
        out = []
        for h in hits:
            t = (h.get("title") or "").strip()
            if not t or (h.get("points") or 0) < 50:   # only the big ones
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
                  params={"q": "(viral OR entertainment OR box office OR celebrity OR trending)",
                          "lang": "en", "max": 15, "apikey": key},
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
        blocked = 0
        for a in items:
            if not a or not a.title or not a.url:
                continue
            if a.title.lower() in ("[removed]", "removed"):
                continue
            # v5.6: explicit-content firewall - family-friendly page, always
            if _is_explicit(f"{a.title} {a.description or ''}"):
                blocked += 1
                continue
            if a.url in seen:
                continue
            seen.add(a.url)
            arts.append(a)
            n += 1
        if blocked:
            log.info("%s: %d article(s) dropped by the explicit-content filter", label, blocked)
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
# v5.4: cheap-clickbait hooks that real newsroom headlines never carry
_CLICKBAIT_RE = re.compile(
    r"\b(?:you won'?t believe|here'?s why|here'?s what happened|what happens next|"
    r"this is why|shocking(?:ly)?|jaw[- ]dropping|gone (?:wrong|viral)|"
    r"breaking:|must (?:read|watch)|mind[- ]blowing|wait for it)\b", re.I)


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
    # v5.4: cheap-clickbait tells - shout-words, punctuation spam, bait hooks.
    # Real newsroom headlines (ET/Inc42/Reuters style) never use these.
    shout_words = [w for w in a.title.split() if len(w) >= 4 and w.isupper()
                   and not w.isdigit()]
    if len(shout_words) >= 3:
        pen -= 2.5                      # THIS ONE WORD JUST DID SHOCKING...
    bangs = a.title.count("!") + a.title.count("?")
    if bangs >= 2:
        pen -= 1.5                      # !!! clickbait punctuation
    if _CLICKBAIT_RE.search(a.title):
        pen -= 2.0                      # "you won't believe" family

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


# v5.3: fuzzy-dup clustering helper kept for corroboration counting (the
# India gate itself was removed in v5.6 - the page is worldwide now).


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
        for a in c:
            a.india_ok = True          # v5.6: gate retired - worldwide page
            a.score += boost
    return clusters


# ==========================================================================
# SELECTION (threshold + diversity + quiet-page safety valve)
# ==========================================================================
def _today_category_counts(state: dict) -> dict:
    """v5.4: per-day category counters ("today" in IST) for the hard cap."""
    days = state.get("category_days") or {}
    return dict(days.get(_today_ist_str()) or {})


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
        # v5.6: worldwide page - every clean story is eligible (the old India
        # gate retired; the explicit-content firewall runs at fetch time)
        return getattr(a, "india_ok", True)

    def diverse(a: Article) -> bool:
        # v5.4: HARD daily diversity - max 2 posts per category per day and
        # never back-to-back with the same category (soft penalties stay on
        # top so the page covers 4 different beats across its 4 daily posts)
        if today_counts.get(a.category, 0) >= MAX_PER_CATEGORY_PER_DAY:
            return False
        return a.category != last_cat

    today_counts = _today_category_counts(state)
    rc_list = state.get("recent_categories") or []
    last_cat = rc_list[-1] if rc_list else None

    quals = [a for a in flat if effective(a) >= threshold and not _is_duplicate(a, state)
             and eligible(a) and diverse(a)]
    if not quals:   # fall back to soft-penalties-only before touching the bar
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
    {"id": "arjun", "desc": "Arjun, 28, movie nerd in Bengaluru with first-day-first-show energy. Dry humour, allergic to hype, quotes box-office numbers to make points."},
    {"id": "meera", "desc": "Meera, 33, ex-music journalist in Mumbai who now runs a pop-culture meme page. Lives for award-show chaos, explains industry tea simply."},
    {"id": "dev", "desc": "Dev, 42, sports fanatic in Pune. Yells at the TV during finals, quotes stats like gospel, respects hustle but not flops."},
    {"id": "riya", "desc": "Riya, 24, K-pop stan and internet-native in Kolkata. Playful, sharp, allergic to corporate speak, first to spot a viral moment."},
    {"id": "kabir", "desc": "Kabir, 36, gaming dad in Delhi with 2,000 hours across consoles. Skeptical of hype launches, soft spot for a great speedrun."},
    {"id": "ananya", "desc": "Ananya, 31, space-and-science geek in Chennai. Notices the number everyone else skipped, explains a launch in one line."},
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
    {"id": "receipts", "name": "RECEIPTS",
     "scaffold": "No opinion, no question, just receipts.\n"
                 "Line 1: a two-word framing like 'Just the numbers:' or 'The facts:'\n"
                 "Then EXACTLY 3 dash lines ('- ...'), each ONE hard fact or number from the story, under 14 words.\n"
                 "No closing line. Nothing else. Facts only - let them do the talking."},
    {"id": "cold_open", "name": "COLD OPEN",
     "scaffold": "Start mid-conversation, as if the reader walked in on you talking: 'So...' / 'Okay so' / 'Right, so' followed immediately by the event.\n"
                 "Then 2-3 sentences of what just happened, with the key number.\n"
                 "End with one sentence of consequence for the reader - no question mark."},
    # v5.7: engagement shapes - the post IS the interaction
    {"id": "react_split", "name": "REACTION SPLIT",
     "scaffold": "Line 1: a reaction-ask built on the story, e.g. 'Drop \U0001F525 if you're team YES, \u2764\ufe0f if you're team NO.' - adapt the two sides to THIS story in your own words (keep one emoji per side).\n"
                 "Then 2-3 sentences: what happened, one key fact or number.\n"
                 "Last line: tell readers which side YOU picked and why in under 10 words."},
    {"id": "guess_what", "name": "GUESS HOOK",
     "scaffold": "Line 1: a tease that hides the subject: name the CATEGORY, not the name (e.g. 'A certain pop star just did something no one saw coming...').\n"
                 "Then 2-3 sentences of clues - specific facts that make guessing fun, without revealing who/what.\n"
                 "Last line: 'Guess who/what in the comments - answer pinned below.'"},
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
    # v5.5: the new generation of AI tells
    "let's unpack", "let us unpack", "here's the kicker", "the kicker", "plot twist",
    "big if true", "read that again", "let that sink in", "chef's kiss", "nail in the coffin",
    "writing on the wall", "isn't just", "is not just", "it's not just", "more than just",
    "the math is mathing", "we love to see it", "and that's the tea", "no notes",
    "under the radar", "quietly shipped", "quietly launched", "picture this", "imagine this",
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
    return f"""You write Facebook posts for a global entertainment & trending-news page (movies, music, sports, celebs, gaming, viral moments, space, tech launches) read by casual, fun-loving followers.

You are writing as: {persona['desc']}

The #1 rule: sound like a real human posting to friends - never like AI, never like a corporate page, never like a press release.

Style rules:
- Contractions everywhere (it's, doesn't, they're).
- Vary rhythm: mix longer sentences with very short ones (under 5 words).
- Plain conversational English is welcome ("honestly", "no joke", "big if true", "worth noting").
- Facts over adjectives. If a number exists, use it.
- Never invent facts. Only use what's in the story you're given.
- FUN BEATS FORMAL: this is entertainment - energy, reactions, hot takes. Not a news bulletin.
- FAMILY-FRIENDLY, ALWAYS: no explicit content, no sleaze, no graphic gore. Keep it clean enough for a teenager's feed. If the story is explicit, refuse by writing about a different angle or keep it PG.
- No markdown, no bold, no links.
- Emoji: use 0-3, only as visual anchors (fire on a big number, popcorn on a movie line). Never decorate every line. Never use emoji in the first line.
- Max 2 hashtags, only if they feel natural. Do NOT end the post with hashtags.
- Don't start with the headline - the image card already shows it.
- At most one em dash in the whole post.
- Sentence fragments are fine. Starting a line with 'And' or 'But' is fine.

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
    # v5.6: explicit-content firewall applies to LLM output too
    if _is_explicit(t):
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
    if _is_explicit(t):
        return "explicit content"
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


# ------------------------------------------------------------------ hashtags
# v5.4: deterministic hashtag engine - clean, topical, never spammy.
# Fires ONLY when the LLM post ships zero hashtags (safety net, not overlay).
CATEGORY_TAGS = {
    "viral": "#Viral", "movies": "#Movies", "music": "#Music", "tv": "#Streaming",
    "celebs": "#PopCulture", "sports": "#Sports", "gaming": "#Gaming",
    "space": "#Space", "tech": "#Tech", "odd": "#WorldRecord",
    "general": "#Trending",
}
_TAG_STOPWORDS = {
    "The", "This", "That", "Here", "There", "Now", "New", "Why", "How", "What",
    "India", "Indian", "After", "Before", "Amid", "Over", "From", "With", "Will",
    "Just", "Says", "Said", "Gets", "Set", "May", "Can", "And", "For", "All",
    "Big", "Top", "First", "Best", "Record", "Report", "Reports", "Updates",
}


def _smart_tags(art, text=""):
    """Up to 2 clean tags: one category tag + one company handle-ish tag."""
    have = {t.lower() for t in re.findall(r"#[\w]+", text or "")}
    tags = []
    cat = CATEGORY_TAGS.get(art.category)
    if cat and cat.lower() not in have:
        tags.append(cat)
        have.add(cat.lower())
    # company/entity: longest capitalised token in the headline that isn't a stopword
    words = re.findall(r"\b([A-Z][A-Za-z0-9&]{2,15})\b", art.title or "")
    for w in sorted(words, key=len, reverse=True):
        if w in _TAG_STOPWORDS:
            continue
        t = "#" + w
        if t.lower() not in have:
            tags.append(t)
            break
    return tags[:2]


# ------------------------------------------------- v5.5 interaction hooks
# Commercial pages end most posts with ONE cheap action the reader can take in
# two seconds. This is the single biggest "real page vs AI page" tell. The hook
# is appended AFTER the post body (never inside it), rotates with memory so the
# same CTA never repeats back-to-back, and is skipped entirely ~25% of the time
# (pages that ask on every single post feel like bots).
ENGAGEMENT_HOOKS = [
    {"id": "vote_emoji",    "text": "Your one-word take: {rx} or {dw}?"},
    {"id": "vote_fire",     "text": "Agree? Drop a {fire} | Disagree? Tell me why {down}"},
    {"id": "scale",         "text": "On a scale of 1-10, how crazy is this?"},
    {"id": "tag_friend",    "text": "Tag someone who needs to see this."},
    {"id": "predict",       "text": "Big or flop? Call it below."},
    {"id": "fill_blank",    "text": "Fill in the blank: this is the most {___} thing I've seen today."},
    {"id": "poll_choice",   "text": "Quick poll: hyped, obsessed, or over it already?"},
    {"id": "hot_or_not",    "text": "Iconic or overrated? Pick a side."},
    {"id": "first_comment", "text": "First thought in comments - unfiltered."},
    {"id": "verdict",       "text": "Your verdict: {up} hit, {dw} miss."},
    {"id": "followup",      "text": "Want the follow-up when the numbers land? Say 'yes' below."},
    {"id": "disagree_ok",   "text": "Think I'm wrong? Good - comment and argue."},
    # v5.7: specials-flavoured hooks
    {"id": "share_score",   "text": "Drop your answer below - no googling."},
    {"id": "which_team",    "text": "Which side are you on? Fight it out in the comments."},
]
_HOOK_EMOJI = {"rx": "\U0001F44D", "dw": "\U0001F44E", "fire": "\U0001F525",
               "down": "\U0001F447", "___": "_____", "up": "\U0001F60A"}
_HOOK_CATEGORIES = {
    "movies": ["vote_emoji", "scale", "poll_choice", "first_comment", "tag_friend", "hot_or_not"],
    "music": ["vote_fire", "scale", "poll_choice", "tag_friend", "first_comment", "verdict"],
    "sports": ["vote_emoji", "hot_or_not", "first_comment", "tag_friend", "disagree_ok", "predict"],
    "tv": ["poll_choice", "scale", "first_comment", "followup", "tag_friend"],
    "celebs": ["poll_choice", "first_comment", "tag_friend", "hot_or_not"],
    "gaming": ["vote_emoji", "hot_or_not", "scale", "first_comment", "poll_choice", "predict"],
    "viral": ["first_comment", "tag_friend", "vote_fire", "fill_blank", "scale"],
}


def _pick_hook(art, state, shape_id=""):
    """Choose one interaction hook, or None. Skips hooks used in the last 4
    posts, honours ENGAGE_HOOK_PROBABILITY, and never doubles up on shapes
    that already end with a question (question_hook ends with an ask)."""
    if shape_id in ("question_hook",):
        return None
    if random.random() > ENGAGE_HOOK_PROBABILITY:
        return None
    recent = set((state.get("recent_hooks") or [])[-4:])
    preferred = _HOOK_CATEGORIES.get(art.category) or [
        h["id"] for h in ENGAGEMENT_HOOKS if h["id"] not in recent]
    pool = [h for h in ENGAGEMENT_HOOKS
            if h["id"] in preferred and h["id"] not in recent] or \
           [h for h in ENGAGEMENT_HOOKS if h["id"] not in recent] or ENGAGEMENT_HOOKS
    hook = random.choice(pool)
    state.setdefault("recent_hooks", []).append(hook["id"])
    state["recent_hooks"] = state["recent_hooks"][-8:]
    text = hook["text"]
    for k, v in _HOOK_EMOJI.items():
        text = text.replace("{" + k + "}", v)
    return text


def _apply_interaction_layer(text, art, state, shape_id=""):
    """v5.5: append the engagement hook + reorder hashtags so posts stop
    ending identically (body -> hook line -> tags). Returns final post text."""
    if not text:
        return text
    body = text.rstrip()
    # pull existing trailing hashtags off, re-attach AFTER the hook
    m = re.search(r"(\n+)((?:#[\w]+\s*)+)$", body)
    tags = ""
    if m:
        tags = m.group(2).strip()
        body = body[: m.start()].rstrip()
    hook = _pick_hook(art, state, shape_id)
    parts = [body]
    if hook:
        parts.append(hook)
    if tags:
        parts.append(tags)
    out = "\n\n".join(parts)
    return out.rstrip() + "\n"


# ==========================================================================
# v5.7: ENGAGEMENT SPECIALS - trivia / this-or-that / guess-the-title
# ==========================================================================
# Commercial entertainment pages don't just broadcast news: roughly 1 in 3
# posts is an interactive game post (quiz, battle, riddle). Those posts
# collect 5-10x the comments of straight news because the comment IS the
# game. Each special has its own custom card design, its own caption voice,
# and its own guard rails: never two specials in a row, a hard daily cap,
# and the same family-friendly NSFW gate the news pipeline uses.

def _flag_run_alert(reason: str):
    """v5.7: write .run_alert so the workflow can open a GitHub issue WITHOUT
    failing the job (green CI + email alert). Never raises."""
    try:
        path = BASE_DIR / ".run_alert"
        path.write_text(f"v{VERSION} {_now_ist().isoformat()}\n{reason}\n", encoding="utf-8")
    except Exception:
        pass


# ---- TRIVIA (OpenTDB - free, no key, clean entertainment categories) -----
_TRIVIA_CATEGORIES = [11, 12, 15, 32]   # film / music / video games / cartoons
_TRIVIA_CAT_LABEL = {11: "movies", 12: "music", 15: "gaming", 32: "tv"}


def fetch_trivia():
    """One clean multiple-choice entertainment question from OpenTDB.
    Returns dict(question, options[4], answer_idx, answer, category,
    difficulty) or None. Base64 encoding is requested so apostrophes,
    quotes and ampersands survive every layer untouched."""
    import base64
    try:
        cat = random.choice(_TRIVIA_CATEGORIES)
        r = _http_get("https://opentdb.com/api.php",
                      params={"amount": 1, "category": cat,
                              "difficulty": random.choice(("easy", "medium")),
                              "type": "multiple", "encode": "base64"},
                      timeout=15, retries=1)
        if r is None:
            return None
        blob = (r.json() or {}).get("results") or []
        if not blob:
            return None
        q = blob[0]

        def b64(s):
            return base64.b64decode(s or "").decode("utf-8", "replace")

        question = _html.unescape(b64(q.get("question", ""))).strip()
        correct = _html.unescape(b64(q.get("correct_answer", ""))).strip()
        wrong = [_html.unescape(b64(w)).strip() for w in (q.get("incorrect_answers") or [])]
        if not question or not correct or len(wrong) != 3:
            return None
        # family-friendly gate + layout gate (long options wreck the card)
        if _is_explicit(" ".join([question, correct] + wrong)):
            log.warning("trivia question tripped the NSFW gate - skipping it")
            return None
        if any(len(o) > 58 for o in [correct] + wrong):
            return None
        options = wrong + [correct]
        random.shuffle(options)
        return {"question": question, "options": options,
                "answer_idx": options.index(correct), "answer": correct,
                "category": _TRIVIA_CAT_LABEL.get(cat, "general"),
                "difficulty": (b64(q.get("difficulty", "")) or "medium").strip()}
    except Exception as e:
        log.debug("OpenTDB fetch failed: %s", e)
        return None


# ---- GUESS THE TITLE (hand-written riddle bank; no LLM dependency) -------
_RIDDLE_BANK = [
    {"answer": "Titanic", "kind": "movie", "emoji": "\U0001F6A4 \u2744\ufe0f \U0001F9CA \U0001F48B",
     "clues": ["The ship everyone swore was unsinkable", "A door that famously fit only one", "An iceberg, a necklace, and a band that kept playing"]},
    {"answer": "Inception", "kind": "movie", "emoji": "\U0001F4A4 \U0001F5ED \U0001F503 \U0001F5B1\ufe0f",
     "clues": ["Dreams inside dreams inside dreams", "A spinning top that decides everything", "A city folds onto itself like paper"]},
    {"answer": "The Lion King", "kind": "movie", "emoji": "\U0001F981 \U0001F41A \u2600\ufe0f \U0001F3B6",
     "clues": ["An uncle with serious trust issues", "A heir who'd rather sing about no worries", "Hakuna Matata for two full hours"]},
    {"answer": "The Matrix", "kind": "movie", "emoji": "\U0001F457 \u26a1 \U0001F455 \u2699\ufe0f",
     "clues": ["A red pill and a blue pill", "Reality turns out to be code", "Dodge this - literally in slow motion"]},
    {"answer": "Interstellar", "kind": "movie", "emoji": "\U0001F30C \U0001F680 \u23F3 \U0001F5A1\ufe0f",
     "clues": ["A father races time itself", "Every hour costs seven years", "A wormhole with a bookshelf twist"]},
    {"answer": "Jurassic Park", "kind": "movie", "emoji": "\U0001F996 \U0001F7E9 \u26c4 \U0001F35C",
     "clues": ["Science revived something it shouldn't have", "A theme park with a VERY bad opening day", "Life finds a way"]},
    {"answer": "Minecraft", "kind": "game", "emoji": "\u26cf\ufe0f \U0001F9F9 \U0001F7DC \U0001F40A",
     "clues": ["Punch a tree, get wood - that's lesson one", "Creepers blow up everything you love", "The whole world is literally blocks"]},
    {"answer": "Spider-Man", "kind": "movie", "emoji": "\U0001F577\ufe0f \U0001F997 \u2764\ufe0f \U0001F3A9",
     "clues": ["Bitten by the world's most career-changing bug", "Great power, great you-know-what", "Swings to work instead of taking the subway"]},
    {"answer": "Frozen", "kind": "movie", "emoji": "\u2744\ufe0f \U0001F9AC \U0001F3B6 \u26a1",
     "clues": ["A queen who accidentally ices her kingdom", "A snowman who dreams of summer", "A song every kid belted for a full year"]},
    {"answer": "Avengers", "kind": "movie", "emoji": "\U0001F535 \U0001F7E9 \u2694\ufe0f \U0001F3B2",
     "clues": ["The world's strangest job interview", "A snap that ended half of everything", "Earth's mightiest group therapy session"]},
]

# ---- THIS OR THAT (A/B battles with per-side photo queries) --------------
_VS_PAIRS = [
    {"a": "Movie night in bed", "a_q": "cozy movie night home popcorn blanket",
     "b": "First-day-first-show", "b_q": "cinema hall audience crowd premiere",
     "q": "A movie you've waited months for: bed + laptop, or the big screen on day one?"},
    {"a": "Stadium concert", "a_q": "stadium concert crowd lights night",
     "b": "Living-room playlist", "b_q": "home music speaker relaxing evening",
     "q": "Your favourite artist on tour: front-row chaos, or home speakers and zero queues?"},
    {"a": "Late-night gaming", "a_q": "gamer room neon night controller",
     "b": "Binge the whole season", "b_q": "tv series marathon night couch",
     "q": "One free night: rank up with the squad, or finish the season everyone's spoiling?"},
    {"a": "Football final", "a_q": "football stadium night match floodlights",
     "b": "Cricket final", "b_q": "cricket stadium crowd floodlights",
     "q": "Both finals, same weekend, ONE ticket: football or cricket?"},
    {"a": "Rewatch a favourite", "a_q": "cinema classic film scene",
     "b": "Gamble on something new", "b_q": "new movie release neon poster night",
     "q": "Two hours to yourself: the comfort rewatch you know line-by-line, or a gamble on something new?"},
    {"a": "Paper books", "a_q": "stack of books reading cozy warm",
     "b": "Audiobooks", "b_q": "headphones music walking street",
     "q": "Same story, two formats: pages you can smell, or narration on the move?"},
]


def _pick_special_kind(state):
    """'trivia' | 'vs' | 'riddle' | None. Guards: hard daily cap, never two
    specials in a row, light same-kind damping (a page full of quizzes is
    as robotic as a page full of links)."""
    today = _today_ist_str()
    if state.get("specials_date") != today:
        state["specials_date"] = today
        state["specials_today"] = 0
    if int(state.get("specials_today", 0) or 0) >= MAX_SPECIALS_PER_DAY:
        return None
    if state.get("last_post_was_special"):
        return None
    r = random.random()
    kind = None
    if r < TRIVIA_PROBABILITY:
        kind = "trivia"
    elif r < TRIVIA_PROBABILITY + VS_PROBABILITY:
        kind = "vs"
    elif r < TRIVIA_PROBABILITY + VS_PROBABILITY + RIDDLE_PROBABILITY:
        kind = "riddle"
    if kind is None:
        return None
    if kind == state.get("last_special_kind") and random.random() < 0.7:
        return None
    return kind


def _download_first(query, orientation="portrait"):
    """v5.7 helper: best-effort download of ONE photo for a query (VS sides)."""
    photos = _pexels_search(query, orientation) or _pexels_search(query, None)
    for p in (photos or [])[:3]:
        img = _download_photo(p)
        if img is not None:
            return img
    return None


# ---- special card renderers (own design language: neon quiz stages) -----
def _save_special_card(img, prefix):
    try:
        IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = IMAGE_DIR / f"{prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}.jpg"
        img.save(path, "JPEG", quality=90, optimize=True, progressive=True)
        return path
    except Exception as e:
        log.error("special card save failed: %s", e)
        return None


def _render_quiz_card(header, question=None, clues=None, options=None,
                      footer="", accent=(170, 120, 255), watermark="?"):
    """v5.7: the quiz stage - dark neon gradient, dot grid, giant translucent
    watermark, auto-fit question or numbered clues, lettered option pills.
    Used by both TRIVIA DROP and GUESS THE TITLE (options=None)."""
    if not PIL_OK:
        return None
    _ensure_fonts()
    _set_card_format(False)          # specials are always portrait drama
    S = max(1, SUPERSAMPLE)
    W, H = CARD_W * S, CARD_H * S
    M = int(72 * S)

    # ---- stage: dark vertical blend + dot grid + rings
    dark = (16, 10, 34)
    base = Image.new("RGBA", (W, H), dark + (255,))
    ov = _vertical_gradient((W, H), (34, 12, 70), 0, 235, curve=1.25)
    base = Image.alpha_composite(base, ov)
    d = ImageDraw.Draw(base, "RGBA")
    step = int(44 * S)
    for gy in range(int(60 * S), H, step):
        for gx in range(int(60 * S), W, step):
            d.ellipse([gx - 2 * S, gy - 2 * S, gx + 2 * S, gy + 2 * S],
                      fill=(255, 255, 255, 22))
    for (cx, cy, rad, alpha) in ((W - int(150 * S), int(190 * S), int(120 * S), 26),
                                 (int(140 * S), H - int(260 * S), int(170 * S), 20)):
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], outline=accent + (alpha,), width=3 * S)
    # giant watermark glyph (top-right, barely-there)
    try:
        wm_font = _font(int(560 * S), "bold")
        if wm_font is not None:
            d.text((W - int(430 * S), int(-60 * S)), watermark, font=wm_font,
                   fill=accent + (30,))
    except Exception:
        pass

    y = int(84 * S)
    # ---- header chip
    _, chip_h = _draw_chip(d, M, y, header, accent, int(25 * S))
    y += chip_h + int(46 * S)
    if PAGE_HANDLE:
        hf = _font(int(26 * S), "medium")
        if hf is not None:
            hw = d.textlength(PAGE_HANDLE, font=hf)
            d.text((W - M - hw, int(90 * S)), PAGE_HANDLE, font=hf, fill=(255, 255, 255, 150))

    # ---- question / clue block
    if question:
        lines, font, lh, th, _sz = _fit_headline(d, question, "semibold",
                                                 W - 2 * M, 5, int(54 * S), int(32 * S))
        for ln in lines:
            _txt(d, (M, y), ln, font, (255, 255, 255, 250))
            y += lh
        y += int(40 * S)
    if clues:
        nf = _font(int(24 * S), "bold")
        tf = _font(int(33 * S), "medium")
        for i, c in enumerate(clues, 1):
            if nf is not None:
                d.text((M, y), f"0{i}", font=nf, fill=accent + (255,))
            if tf is not None:
                cl = _wrap_text(d, c, tf, W - 2 * M - int(90 * S))
                cy = y
                for ln in cl[:3]:
                    _txt(d, (M + int(90 * S), cy), ln, tf, (255, 255, 255, 235))
                    cy += int(44 * S)
            y += max(int(46 * S), int(44 * S) * min(2, len(cl)))
            y += int(26 * S)
        y += int(20 * S)

    # ---- options (lettered pills)
    if options:
        letters = "ABCD"
        row_h = int(118 * S)
        for i, opt in enumerate(options[:4]):
            box = [M, y, W - M, y + row_h - int(22 * S)]
            d.rounded_rectangle(box, radius=int(26 * S), fill=(8, 10, 18, 185),
                                outline=accent + (120,), width=2 * S)
            # letter badge
            br = int(38 * S)
            bx, by = M + int(26 * S), y + int(18 * S)
            d.ellipse([bx, by, bx + 2 * br, by + 2 * br], fill=accent + (255,))
            lf = _font(int(30 * S), "bold")
            if lf is not None:
                lw = d.textlength(letters[i], font=lf)
                d.text((bx + br - lw / 2, by + br - int(21 * S)), letters[i],
                       font=lf, fill=(12, 10, 24, 255))
            of = _font(int(32 * S), "medium")
            if of is not None:
                ols = _wrap_text(d, opt, of, W - 2 * M - int(130 * S))
                oy = y + int(30 * S) if len(ols) > 1 else y + int(34 * S)
                for ln in ols[:2]:
                    d.text((M + int(120 * S), oy), ln, font=of, fill=(255, 255, 255, 240))
                    oy += int(40 * S)
            y += row_h
        y += int(28 * S)

    # ---- footer
    ff = _font(int(25 * S), "medium")
    if ff is not None and footer:
        fw = d.textlength(footer, font=ff)
        d.text(((W - fw) / 2, H - M - int(10 * S)), footer, font=ff, fill=(255, 255, 255, 190))

    out = base.convert("RGB")
    out = _apply_grain(out)
    return out.resize((CARD_W, CARD_H), _RESAMPLE.LANCZOS)


def _render_vs_card(pair, photo_a=None, photo_b=None):
    """v5.7: THIS OR THAT battle card - diagonal split, two photos (or two
    tinted stages), giant VS badge, side labels, question strip at the bottom."""
    if not PIL_OK:
        return None
    _ensure_fonts()
    _set_card_format(False)
    S = max(1, SUPERSAMPLE)
    W, H = CARD_W * S, CARD_H * S
    M = int(72 * S)
    acc_a = (255, 64, 96)      # team pink
    acc_b = (80, 200, 255)     # team blue

    base = Image.new("RGBA", (W, H), (12, 12, 26, 255))
    # diagonal cut: left A / right B
    cut_top, cut_bot = int(W * 0.54), int(W * 0.46)

    def half_photo(img, polygon):
        if img is None:
            return None
        try:
            box = (0, 0, W, int(H * 0.78))
            ph = _smart_crop(img, W, int(H * 0.78)).resize((W, int(H * 0.78)), _RESAMPLE.LANCZOS)
            ph = _enhance_photo(ph)
            mask = Image.new("L", (W, int(H * 0.78)), 0)
            ImageDraw.Draw(mask).polygon(polygon, fill=255)
            return ph, mask
        except Exception:
            return None

    left = half_photo(photo_a, [(0, 0), (cut_top, 0), (cut_bot, int(H * 0.78)), (0, int(H * 0.78))])
    right = half_photo(photo_b, [(cut_top, 0), (W, 0), (W, int(H * 0.78)), (cut_bot, int(H * 0.78))])
    if left:
        base.paste(left[0].convert("RGB"), (0, 0), left[1])
    else:
        d0 = ImageDraw.Draw(base, "RGBA")
        d0.polygon([(0, 0), (cut_top, 0), (cut_bot, int(H * 0.78)), (0, int(H * 0.78))],
                   fill=tuple(int(c * 0.35) for c in acc_a) + (255,))
    if right:
        base.paste(right[0].convert("RGB"), (0, 0), right[1])
    else:
        d0 = ImageDraw.Draw(base, "RGBA")
        d0.polygon([(cut_top, 0), (W, 0), (W, int(H * 0.78)), (cut_bot, int(H * 0.78))],
                   fill=tuple(int(c * 0.35) for c in acc_b) + (255,))

    d = ImageDraw.Draw(base, "RGBA")
    # scrim the top/bottom of the photo zone so labels read
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov.alpha_composite(_vertical_gradient((W, int(H * 0.14)), (8, 10, 18), 150, 0), (0, 0))
    ov.alpha_composite(_vertical_gradient((W, int(H * 0.22)), (8, 10, 18), 0, 190, curve=1.4),
                       (0, int(H * 0.78) - int(H * 0.22)))
    base = Image.alpha_composite(base, ov)
    d = ImageDraw.Draw(base, "RGBA")

    # neon divider along the cut
    d.line([(cut_top, 0), (cut_bot, int(H * 0.78))], fill=(255, 255, 255, 230), width=4 * S)

    # ---- VS badge (circle, ring, bold text)
    cx, cy, rr = W // 2, int(H * 0.36), int(105 * S)
    d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(10, 10, 22, 255),
              outline=(255, 255, 255, 255), width=5 * S)
    d.ellipse([cx - rr + int(14 * S), cy - rr + int(14 * S),
               cx + rr - int(14 * S), cy + rr - int(14 * S)],
              outline=acc_a + (255,), width=3 * S)
    vf = _font(int(96 * S), "bold")
    if vf is not None:
        vw = d.textlength("VS", font=vf)
        d.text((cx - vw / 2, cy - int(62 * S)), "VS", font=vf, fill=(255, 255, 255, 255))

    # ---- team labels + chips
    ytop = int(90 * S)
    _draw_chip(d, M, ytop, "THIS OR THAT", (255, 200, 60), int(25 * S))
    la = _font(int(40 * S), "bold")
    ma = _font(int(26 * S), "medium")
    if la is not None:
        _txt(d, (M, int(H * 0.10)), "A", la, acc_a + (255,))
    if ma is not None:
        for i, ln in enumerate(_wrap_text(d, pair["a"], ma, int(W * 0.38))[:2]):
            _txt(d, (M, int(H * 0.10) + int(56 * S) + i * int(32 * S)), ln, ma, (255, 255, 255, 220))
    if la is not None:
        aw = d.textlength("B", font=la)
        _txt(d, (W - M - aw, int(H * 0.10)), "B", la, acc_b + (255,))
    for i, ln in enumerate(_wrap_text(d, pair["b"], ma, int(W * 0.38))[:2]):
        lw = d.textlength(ln, font=ma) if ma is not None else 0
        _txt(d, (W - M - lw, int(H * 0.10) + int(56 * S) + i * int(32 * S)), ln, ma, (255, 255, 255, 220))

    # ---- question strip (bottom solid panel)
    panel_y = int(H * 0.78)
    d.rectangle([0, panel_y, W, H], fill=(8, 10, 18, 255))
    d.rectangle([0, panel_y, W, panel_y + int(6 * S)], fill=(255, 200, 60, 255))
    qf_lines, qf, qlh, _, _ = _fit_headline(d, pair["q"], "semibold",
                                            W - 2 * M, 3, int(40 * S), int(28 * S))
    qy = panel_y + int(60 * S)
    for ln in qf_lines:
        _txt(d, (M, qy), ln, qf, (255, 255, 255, 250))
        qy += qlh
    ff = _font(int(25 * S), "medium")
    if ff is not None:
        footer = "Pick a side in the comments \u00b7 " + _now_ist().strftime("%d %b")
        fw = d.textlength(footer, font=ff)
        d.text(((W - fw) / 2, H - M - int(10 * S)), footer, font=ff, fill=(255, 255, 255, 190))

    out = base.convert("RGB")
    out = _apply_grain(out)
    return out.resize((CARD_W, CARD_H), _RESAMPLE.LANCZOS)


# ---- special post builder -------------------------------------------------
def build_special_post(state):
    """v5.7 entry point: maybe build an interactive special post.
    Returns dict(kind, message, card_path, comment, art) or None (regular
    news flow then continues). Every failure inside degrades to None - the
    page always has the news pipeline to fall back on."""
    kind = _pick_special_kind(state)
    if kind is None:
        return None

    if kind == "trivia":
        t = fetch_trivia()
        if t is None:
            log.info("Trivia source unavailable - regular news flow")
            return None
        letters = "ABCD"
        opts = "\n".join(f"{letters[i]}) {o}" for i, o in enumerate(t["options"]))
        theme = CATEGORY_THEME.get(t["category"], CATEGORY_THEME["general"])
        message = (
            f"TRIVIA DROP \u00b7 {theme['label']}\n\n"
            f"{t['question']}\n\n{opts}\n\n"
            "No googling. First correct answer in the comments gets the bragging rights - "
            "pinned comment has the answer.\n\n"
            "#Trivia #PopCultureQuiz")
        card = None
        if PIL_OK:
            try:
                card = _save_special_card(_render_quiz_card(
                    header="TRIVIA DROP",
                    question=t["question"],
                    options=t["options"],
                    footer="Answer in the comments \u00b7 " + _now_ist().strftime("%d %b"),
                    accent=(170, 120, 255)), "quiz")
            except Exception as e:
                log.warning("quiz card render failed (%s) - posting without card", e)
        correct_letter = letters[t["answer_idx"]]
        comment = (f"ANSWER: {correct_letter}) {t['answer']}\n"
                   "How'd you do? Honest scores only.")
        art = Article(title=f"special:trivia:{t['question'][:70]}",
                      url="https://opentdb.com", source="OpenTDB",
                      domain="opentdb.com", category=t["category"])
        return {"kind": "trivia", "message": message, "card_path": card,
                "comment": comment, "art": art}

    if kind == "vs":
        recent = set((state.get("recent_vs") or [])[-3:])
        pool = [p for p in _VS_PAIRS if p["q"] not in recent] or _VS_PAIRS
        pair = random.choice(pool)
        state.setdefault("recent_vs", []).append(pair["q"])
        state["recent_vs"] = (state.get("recent_vs") or [])[-6:]
        pa = pb = None
        if PIL_OK and _get_secret("PEXELS_API_KEY"):
            try:
                with ThreadPoolExecutor(max_workers=2) as ex:
                    fa = ex.submit(_download_first, pair["a_q"])
                    fb = ex.submit(_download_first, pair["b_q"])
                    pa, pb = fa.result(), fb.result()
            except Exception as e:
                log.debug("vs photos failed: %s", e)
        card = None
        if PIL_OK:
            try:
                card = _save_special_card(_render_vs_card(pair, pa, pb), "vs")
            except Exception as e:
                log.warning("vs card render failed (%s) - posting without card", e)
        message = (
            f"THIS OR THAT \u00b7 pick your side\n\n{pair['q']}\n\n"
            f"A) {pair['a']}  or  B) {pair['b']}?\n\n"
            "Team A or Team B - drop your pick below and defend it like it's a final.\n\n"
            "#ThisOrThat #PickASide")
        art = Article(title=f"special:vs:{pair['a']} vs {pair['b']}",
                      url="https://www.pexels.com", source="This or That",
                      domain="pexels.com", category="viral")
        return {"kind": "vs", "message": message, "card_path": card,
                "comment": None, "art": art}

    # riddle
    recent = set((state.get("recent_riddles") or [])[-4:])
    pool = [r for r in _RIDDLE_BANK if r["answer"] not in recent] or _RIDDLE_BANK
    r = random.choice(pool)
    state.setdefault("recent_riddles", []).append(r["answer"])
    state["recent_riddles"] = (state.get("recent_riddles") or [])[-8:]
    card = None
    if PIL_OK:
        try:
            card = _save_special_card(_render_quiz_card(
                header=f"GUESS THE {r['kind'].upper()}",
                clues=r["clues"],
                footer="First correct comment wins \u00b7 " + _now_ist().strftime("%d %b"),
                accent=(255, 200, 60)), "riddle")
        except Exception as e:
            log.warning("riddle card render failed (%s) - posting without card", e)
    message = (
        f"GUESS THE {r['kind'].upper()} \u2014 decode the emoji\n\n"
        f"{r['emoji']}\n\n"
        + "\n".join(f"- {c}" for c in r["clues"])
        + "\n\nName the title in the comments. First correct guess gets pinned. "
          "Answer drops in the comments in an hour.\n\n"
          "#GuessTheTitle #EmojiRiddle")
    comment = f"ANSWER: {r['answer']}. How fast did you get it?"
    art = Article(title=f"special:riddle:{r['answer']}",
                  url="https://www.facebook.com", source="Riddle Bank",
                  domain="local", category="movies")
    return {"kind": "riddle", "message": message, "card_path": card,
            "comment": comment, "art": art}


def _publish_special(sp, state, args) -> int:
    """Publish one special post with the same crash-safety as news posts:
    mark in-flight BEFORE upload, answer-comment after, lenient exits."""
    kind, art = sp["kind"], sp["art"]
    if getattr(args, "dry_run", False):
        files = {"message.txt": sp["message"], "kind.txt": kind + "\n"}
        imgs = {"card.jpg": sp["card_path"]} if sp.get("card_path") else {}
        _save_preview(files, imgs)
        log.info("DRY RUN special (%s) - nothing published. Message:\n%s", kind, sp["message"])
        return 0

    _mark_in_flight(state, art)
    post_id = None
    try:
        post_id = (publish_photo_post(sp["card_path"], sp["message"])
                   if sp.get("card_path") else publish_text_post(sp["message"]))
        log.info("Published SPECIAL post (%s) id=%s", kind, post_id)
    except Exception as e:
        log.error("Special publish failed: %s - job stays green, alert flagged", e)
        _flag_run_alert(f"special ({kind}) publish failed: {e}")
        _save_state(state)
        return 0

    if sp.get("comment") and post_id:
        try:
            publish_comment(post_id, sp["comment"])
            log.info("Answer comment posted (the reveal)")
        except Exception as e:
            log.warning("answer comment failed (non-fatal): %s", e)

    _update_state_after_post(state, art, f"special_{kind}", kind)
    state["last_post_was_special"] = True
    state["specials_today"] = int(state.get("specials_today", 0) or 0) + 1
    state["last_special_kind"] = kind
    _save_state(state)
    log.info("Run complete (special=%s). post=%s", kind, post_id)
    return 0


# ---- v5.7: hand-written caption templates (LLM-down fallback) ------------
# If both Groq and Gemini are down (keys rotated, free tier gone), the page
# still posts: a varied template beats a silent page, and the interaction
# layer still adds hooks + tags on top. Written like texts from a friend.
_TEMPLATE_CAPTIONS = [
    "{title}.\n\nHonestly, this is the story I keep re-reading today. {src} has the "
    "details - the part that got me is how quietly it happened.\n\nWhat's your take on it?",
    "Okay, {title} - did anyone else see this coming?\n\n{src} broke it earlier today. "
    "Read it once and you'll see why everyone's suddenly talking about this.\n\nRight or wrong - where do you stand?",
    "So this just happened: {title}.\n\nNo take needed, the headline does the work. "
    "{src} has the full story - worth two minutes of your scroll.\n\nThoughts?",
    "{title} - and the internet is already divided.\n\n{src} reports it with receipts. "
    "I'm curious which side of this you land on.\n\nTell me below.",
    "Stop scrolling for 30 seconds: {title}.\n\nIt sounds made up until you check {src}. "
    "Checked. Real.\n\nDrop a reaction - stunned or shrug?",
    "{title}.\n\nFiled under: things I did NOT have on my bingo card. {src} confirms it, "
    "details and all. The comments on this one should be fun.\n\nYour call: big deal or nothingburger?",
]


def _template_caption(art) -> str:
    """Varied hand-written fallback caption - never lets the page go quiet."""
    t = _strip_html(art.title or "").strip().rstrip(".!?")
    src = (art.source or art.domain or "the report").strip().split()[0].title()
    return random.choice(_TEMPLATE_CAPTIONS).format(title=t, src=src)


# ------------------------------------------------------------------ Bengali
def _bengali_system_prompt(persona):
    return f"""তুমি একটা বিশ্ব-বিনোদন ও ট্রেন্ডিং নিউজ ফেসবুক পেজের হয়ে বাংলায় পোস্ট লেখো। পাঠক: বাংলাভাষী তরুণ-তরুণী, সিনেমা-মিউজিক-খেলা-ভাইরাল সব খবরে আগ্রহী।

তুমি এই চরিত্রে লিখছ: {persona['desc']}

সবচেয়ে বড় নিয়ম: লেখাটা যেন মনে হয় একজন সাধারণ মানুষ নিজের ভাষায় লিখেছে - কখনও নিউজ বুলেটিন নয়, অনুবাদ নয়, এআই-লেখা নয়।

ভাষার নিয়ম:
- কলকাতার কথ্য বাংলার টোন: 'হচ্ছে', 'দিচ্ছে', 'করেই', 'একদম', 'সোজা কথায়' - এই ধরনের প্রাকৃতিক রূপ।
- বিনোদনের ইংরেজি শব্দ বাংলা অক্ষরে লেখো: সিনেমা, ট্রেলার, কনসার্ট, ভাইরাল, বক্স অফিস - এগুলোই স্বাভাবিক।
- ছোট ছোট বাক্য। দু-একটা খুব ছোট বাক্য রাখো (৩-৪ শব্দের)।
- সংস্কৃতঘেঁষা ভারী শব্দ একেবারে নিষিদ্ধ।
- মজার, এনার্জি-ভরা টোন - এটা বিনোদন পেজ, সংবাদ বুলেটিন নয়।
- ফ্যামিলি-ফ্রেন্ডলি রাখো: কোনো অশ্লীল বা গোর কনটেন্ট একেবারেই না।
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
    # v5.5: weighted random over shapes NOT used in the last 3 posts -
    # deterministic modulo rotation made the feed pattern-readable
    recent = set((state.get("recent_shapes") or [""])[-3:])
    pool = [s for s in POST_SHAPES if s["id"] not in recent] or POST_SHAPES
    # receipts + cold_open are the freshest shapes - slight extra weight
    weights = [1.15 if s["id"] in ("receipts", "cold_open") else 1.0 for s in pool]
    return random.choices(pool, weights=weights, k=1)[0]


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
    # v5.5: editorial serif for the quote layout (bundled in the repo; if a
    # clone somehow lacks it, system DejaVu Serif is the fallback)
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
                 "medium": "Poppins-Medium.ttf", "regular": "Poppins-Regular.ttf",
                 "serif": "DejaVuSerif.ttf", "serif_bold": "DejaVuSerif-Bold.ttf"}
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
            "serif": "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
            "serif_bold": "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
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


def _auto_headline_size(text, start, min_size):
    """v5.4: dynamic typography - short punchy headlines earn bigger type
    (scroll-stopping in the feed), long ones start smaller so they wrap
    gracefully instead of smashing into max-lines at full size."""
    n = len(text or "")
    if n <= 34:
        return start + 14          # poster mode: "Jio posts record profit"
    if n <= 46:
        return start + 8
    if n <= 62:
        return start
    if n <= 84:
        return max(min_size, start - 6)
    return max(min_size, start - 12)


def _fit_headline(draw, text, weight, max_width, max_lines, start, min_size,
                  bengali=False, budget=None):
    """Shrink-to-fit headline (v5.4: auto-sized start + optional height budget).
    Returns (lines, font, line_height, total_h, size)."""
    size = _auto_headline_size(text, start, min_size)
    while size >= min_size:
        font = _font(size, weight, bengali)
        if font is None:
            break
        lines = _wrap_text(draw, text, font, max_width)
        lh = int(size * 1.22)
        if len(lines) <= max_lines and (budget is None or lh * len(lines) <= budget):
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


def _entropy_profile(small):
    """Edge-energy per column and per row of a small grayscale image.
    Detail-rich regions (faces, logos, skylines, products) score high so
    they survive the crop; flat walls/sky score ~0."""
    w, h = small.size
    px = list(small.getdata())
    col = [0.0] * w
    row = [0.0] * h
    for y in range(h):
        base = y * w
        above = px[base] if y == 0 else px[base - w]
        for x in range(w):
            p = px[base + x]
            if x + 1 < w:
                col[x] += abs(px[base + x + 1] - p)
            row[y] += abs(p - above)
    return col, row


def _best_window(energy, win, center_bias=0.15, center=0.5):
    """Sliding-window argmax over energy with a soft pull toward `center`
    (0..1 of the movable range) so busy edges never slam the crop to a side.
    Returns the window start index in energy-array coordinates."""
    n = len(energy)
    if win >= n:
        return 0
    pre = [0.0]
    for e in energy:
        pre.append(pre[-1] + e)
    cpos = center * (n - win)
    best, best_score = 0, -1.0
    for s in range(0, n - win + 1):
        wsum = pre[s + win] - pre[s]
        score = (wsum / win) * (1.0 - center_bias * abs(s - cpos) / max(1.0, cpos))
        if score > best_score:
            best, best_score = s, score
    return best


def _smart_crop(img, tw, th):
    """v5.4: entropy-aware crop. Instead of blind center + top-bias, we measure
    edge energy on a downscaled grayscale and keep the busiest region, with a
    soft center pull for stability. Falls back to the v5.3 crop on any error."""
    w, h = img.size
    target = tw / th
    try:
        work_w = 160
        work_h = max(8, int(round(h * work_w / w)))
        small = img.convert("L").resize((work_w, work_h))
        col, row = _entropy_profile(small)
        if w / h > target:          # too wide -> best x window
            nw = int(round(h * target))
            if nw >= w:
                return img
            win = max(1, int(round(work_h * target)))
            s = _best_window(col, win, center_bias=0.15, center=0.5)
            x0 = int(round(s * w / work_w))
            x0 = max(0, min(w - nw, x0))
            return img.crop((x0, 0, x0 + nw, h))
        if w / h < target:          # too tall -> best y window, upper-middle lean
            nh = int(round(w / target))
            if nh >= h:
                return img
            win = max(1, int(round(work_w / target)))
            s = _best_window(row, win, center_bias=0.25, center=0.38)
            y0 = int(round(s * h / work_h))
            y0 = max(0, min(h - nh, y0))
            return img.crop((0, y0, w, y0 + nh))
        return img
    except Exception:
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


def _zone_luma(img, y0f=0.55, y1f=0.95) -> float:
    """v5.4: mean luminance (0-255) of a horizontal band of the photo.
    Used to strengthen the scrim when the text zone is bright and soften it
    when the photo is already dark (so cards never look like night shots)."""
    try:
        w, h = img.size
        band = img.convert("L").crop((0, int(h * y0f), w, int(h * y1f)))
        band = band.resize((min(64, band.width) or 1, min(64, band.height) or 1))
        px = list(band.getdata())
        return sum(px) / max(1, len(px))
    except Exception:
        return 128.0


def _scrim_adj(luma: float) -> int:
    """v5.4: scrim alpha adjustment from headline-zone brightness."""
    if luma > 158:
        return 45      # bright/busy background - much stronger scrim
    if luma > 126:
        return 26      # moderately bright - moderate boost
    if luma < 58:
        return -16     # already dark - let the photo breathe a little
    return 0


# ------------------------------------------------------------------ Pexels
def _pexels_search(query, orientation="portrait", per_page=18):
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


# v5.6: cinematic modifiers rotate per run so the same category never pulls
# identical photos day after day (the "every card looks the same" killer).
_QUERY_MODIFIERS = ["", " dramatic", " cinematic", " night", " aerial", " neon",
                    " closeup", " bokeh", " golden hour"]


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
    # v5.6: randomised cinematic variant of the top category query
    out.append(cat_q[0] + random.choice(_QUERY_MODIFIERS))
    out.append(cat_q[-1] + random.choice(_QUERY_MODIFIERS))
    out.extend(FALLBACK_IMAGE_QUERIES)
    seen = set()
    dedup = []
    for q in out:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            dedup.append(q)
    return dedup[:7]


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


def _pick_photo_pool(art, avoid_ids=None, want=4):
    """Query ladder -> rank candidates -> download top 4 -> keep most vivid.
    v5.6 'impressive photos': bigger pool (18/page), landscape-friendly floor
    (dramatic wide shots crop beautifully), softened aspect penalty so a huge
    cinematic landscape can beat a mediocre portrait, and 4 downloads.
    v5.7: returns the whole ranked pool (up to `want`) - the first entry feeds
    the card, the extras feed carousel slides.
    avoid_ids: Pexels photo ids used recently, so the feed never repeats a photo.
    Returns [(PIL.Image, photographer, photo_id), ...] or []."""
    if not PIL_OK:
        return []
    avoid = avoid_ids or set()
    target = CARD_W / CARD_H
    for query in _image_queries_for(art):
        photos = _pexels_search(query, "portrait")
        if not photos:
            photos = _pexels_search(query, None)
        if not photos:
            photos = _pexels_search(query, "landscape")   # v5.6: dramatic wide shots
        if not photos:
            continue

        def _rank(p):
            w, h = p.get("width", 0) or 0, p.get("height", 0) or 0
            if w < 900 or h < 700:          # v5.6: landscape-friendly floor
                return None
            aspect = w / max(1, h)
            size = min(w, h)
            # v5.6: 1200 (was 2200) - big beautiful landscapes may win now
            return size - abs(aspect - target) * 1200

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
        with ThreadPoolExecutor(max_workers=4) as ex:      # v5.6: top 4 (was 3)
            futs = {ex.submit(_download_photo, p): p for _, p in ranked[:4]}
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
        log.info("Photo pool for query '%s' (%d candidates)", query, len(cand))
        return cand[:max(1, int(want))]
    return []


def _pick_photo(art, avoid_ids=None):
    pool = _pick_photo_pool(art, avoid_ids)
    return pool[0] if pool else None


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
        side = _enhance_photo(side)
        if variant_idx % 4 == 3:                       # v5.5: periodic duotone
            side = _duotone(side, (16, 18, 28), accent)
        base.paste(side.convert("RGB"), (split_x, 0))
    elif variant == "quote":
        # v5.5: editorial pull-quote - duotone photo on top, cream paper below
        paper = (247, 244, 238)
        photo_h = int(H * 0.52)
        base = Image.new("RGBA", (W, H), paper + (255,))
        ph = _smart_crop(photo, W, photo_h).resize((W, photo_h), _RESAMPLE.LANCZOS)
        ph = _enhance_photo(ph)
        ph = _duotone(ph, (18, 20, 32), accent)
        base.paste(ph.convert("RGB"), (0, 0))
    else:
        base = _smart_crop(photo, W, H).resize((W, H), _RESAMPLE.LANCZOS)
        base = _enhance_photo(base).convert("RGBA")
        if variant == "full_bleed" and variant_idx % 4 == 3:   # v5.5: duotone
            base = _duotone(base, (14, 16, 26), accent).convert("RGBA")

    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    # ---- scrims (text legibility on ANY photo)
    # v5.4: contrast-aware - measure the actual luminance under the headline
    # zone and scale the scrim strength to it (bright photo -> stronger scrim,
    # dark photo -> gentler so the card doesn't look like a night shot)
    _TEXT_ZONE = {"bottom_sheet": (0.55, 0.95), "top_banner": (0.08, 0.55),
                  "full_bleed": (0.55, 0.95), "stat_hero": (0.30, 0.72),
                  "ticker": (0.20, 0.80)}
    if variant in _TEXT_ZONE:
        adj = _scrim_adj(_zone_luma(base, *_TEXT_ZONE[variant]))
    else:                       # split/magazine/quote put text on a solid panel
        adj = 0
    log.debug("scrim adj=%d (variant=%s)", adj, variant)

    if variant == "bottom_sheet":
        y0 = int(H * 0.40)
        a_start = max(0, adj) // 2            # v5.4: bright photos ramp from a lifted floor
        a_peak = min(255, 247 + adj)
        a_curve = 1.9 - (0.6 if adj >= 45 else 0.35 if adj >= 26 else 0.0)
        overlay.alpha_composite(_vertical_gradient((W, H - y0), (8, 10, 18), a_start, a_peak, curve=a_curve), (0, y0))
        overlay.alpha_composite(_vertical_gradient((W, int(H * 0.15)), (8, 10, 18), 150, 0), (0, 0))
    elif variant == "top_banner":
        overlay.alpha_composite(_vertical_gradient((W, int(H * 0.66)), (8, 10, 18), min(255, 248 + adj), max(0, adj) // 4, curve=1.15), (0, 0))
        bh = int(H * 0.14)
        overlay.alpha_composite(_vertical_gradient((W, bh), (8, 10, 18), 0, 140), (0, H - bh))
    elif variant == "split_card":
        overlay.alpha_composite(_vertical_gradient((W, int(H * 0.15)), (8, 10, 18), 140, 0), (0, 0))
    elif variant == "full_bleed":
        overlay.alpha_composite(_vertical_gradient((W, H), (8, 10, 18), 150, 215, curve=1.0), (0, 0))
        overlay.alpha_composite(
            _vertical_gradient((W, int(H * 0.18)), (8, 10, 18), max(0, adj) // 2, min(255, 175 + adj), curve=1.6),
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
    elif variant == "ticker":
        # v5.5: broadcast-style center scrim - the bands carry the branding
        overlay.alpha_composite(_vertical_gradient((W, H), (8, 10, 18),
                                                   max(0, adj) // 2, min(255, 150 + adj), curve=1.0), (0, 0))
    elif variant == "quote":
        pass                    # ink sits on cream paper - no scrim at all
    else:  # stat_hero - heavy cinematic darken so the giant number pops
        # never soften stat_hero's stage; only strengthen on bright photos
        sadj = max(0, adj)
        overlay.alpha_composite(_vertical_gradient((W, H), (8, 10, 18), 200 + sadj // 2, min(255, 240 + sadj), curve=1.1), (0, 0))
        vig = _vignette((W, H), 72)             # v5.3: poster-style corners
        if vig is not None:
            overlay.alpha_composite(vig)

    # ---- chip (top-left) + handle
    chip_size = int(25 * S)
    label = (BN_CATEGORY_LABELS if bengali else {}).get(art.category) or theme["label"]
    if variant == "ticker":
        chip_h = int(50 * S)      # the accent band IS the slug - no chip pill
    else:
        _, chip_h = _draw_chip(d, M, M, label, accent, chip_size, bengali)
    if PAGE_HANDLE and variant not in ("top_banner", "stat_hero", "magazine", "ticker", "quote"):
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
            d, headline, "bold", W - 2 * M, 5, int(74 * S), int(42 * S), bengali,
            budget=avail_bottom - avail_top)   # v5.4: never creep above mid-card
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

    elif variant == "ticker":
        # v5.5: broadcast banner - accent band top with pulsing-dot label,
        # heavy centered headline, dark strip bottom. Loudest layout we have.
        band_h = int(H * 0.16)
        d.rectangle([0, 0, W, band_h], fill=accent + (255,))
        dot_r = int(13 * S)
        dcy = band_h // 2
        d.ellipse([M - int(8 * S), dcy - dot_r, M - int(8 * S) + 2 * dot_r, dcy + dot_r],
                  fill=(255, 255, 255, 255))
        lab = label.upper() if not bengali else label
        lf = _font(int(42 * S), "bold", bengali)
        if lf is not None:
            d.text((M + int(30 * S), dcy - int(24 * S)), lab, font=lf, fill=(10, 12, 20, 255))
        df = _font(int(24 * S), "semibold", bengali)
        if df is not None:
            dstr = _now_ist().strftime("%d %b %Y")
            if bengali:
                dstr = _bn_digits(dstr)
            dw = d.textlength(dstr, font=df)
            d.text((W - M - int(dw), dcy - int(14 * S)), dstr, font=df,
                   fill=(10, 12, 20, 210))
        strip_h = int(104 * S)
        d.rectangle([0, H - strip_h, W, H], fill=(10, 12, 20, 250))
        sf = _font(int(27 * S), "medium", bengali)
        if sf is not None:
            d.text((M, H - strip_h + int(34 * S)), source_line, font=sf,
                   fill=(255, 255, 255, 225))
        if PAGE_HANDLE:
            hf = _font(int(25 * S), "semibold", bengali)
            if hf is not None:
                hw = d.textlength(PAGE_HANDLE, font=hf)
                d.text((W - M - int(hw), H - strip_h + int(34 * S)), PAGE_HANDLE,
                       font=hf, fill=accent + (255,))
        mid_top = band_h + int(64 * S)
        mid_bot = H - strip_h - int(56 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 5, int(78 * S), int(44 * S), bengali,
            budget=mid_bot - mid_top)
        y = mid_top + max(0, (mid_bot - mid_top - th) // 2)
        for ln in lines:
            _txt(d, (M, y), ln, font)
            y += lh

    elif variant == "quote":
        # v5.5: editorial pull-quote - giant serif quote mark on cream paper,
        # serif headline, hairline rule. The quietest, most 'magazine' layout.
        ink = (30, 32, 40, 255)
        photo_h = int(H * 0.52)
        qf = _font(int(170 * S), "serif_bold", bengali)
        if qf is not None:
            d.text((M - int(14 * S), photo_h - int(150 * S)), "\u201C",
                   font=qf, fill=accent + (235,))
        top = photo_h + int(34 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "serif_bold", W - 2 * M, 6, int(64 * S), int(38 * S), bengali)
        y = top
        for ln in lines:
            if font is not None:
                d.text((M, y), ln, font=font, fill=ink)
            y += lh
        y += int(34 * S)
        d.line([M, y, M + int(190 * S), y], fill=ink[:3] + (110,), width=max(2, int(2 * S)))
        y += int(26 * S)
        sf = _font(int(28 * S), "serif", bengali)
        if sf is not None:
            d.text((M, y), source_line, font=sf, fill=ink[:3] + (215,))
        if PAGE_HANDLE:
            hf = _font(int(26 * S), "serif", bengali)
            if hf is not None:
                hw = d.textlength(PAGE_HANDLE, font=hf)
                d.text((W - M - int(hw), y), PAGE_HANDLE, font=hf, fill=ink[:3] + (215,))

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
    out = _apply_grain(out)                    # v5.5: kill the too-clean AI look
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
    variant = CARD_VARIANTS[variant_idx % len(CARD_VARIANTS)]
    if variant == "quote":
        # v5.5: full editorial paper card - the no-photo twin of the quote layout
        base = Image.new("RGBA", (W, H), (247, 244, 238, 255))
    else:
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
    if variant not in ("ticker",):
        _draw_chip(d, M, M, label, accent, chip_size, bengali)

    if stat and variant not in ("quote", "ticker"):
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
    elif variant == "ticker":
        # v5.5: broadcast banner on the gradient + motif background
        band_h = int(H * 0.16)
        d.rectangle([0, 0, W, band_h], fill=accent + (255,))
        dot_r = int(13 * S)
        dcy = band_h // 2
        d.ellipse([M - int(8 * S), dcy - dot_r, M - int(8 * S) + 2 * dot_r, dcy + dot_r],
                  fill=(255, 255, 255, 255))
        lab = label.upper() if not bengali else label
        lf = _font(int(42 * S), "bold", bengali)
        if lf is not None:
            d.text((M + int(30 * S), dcy - int(24 * S)), lab, font=lf, fill=(10, 12, 20, 255))
        strip_h = int(104 * S)
        d.rectangle([0, H - strip_h, W, H], fill=(10, 12, 20, 250))
        sf = _font(int(27 * S), "medium", bengali)
        if sf is not None:
            d.text((M, H - strip_h + int(34 * S)), source_line, font=sf,
                   fill=(255, 255, 255, 225))
        if PAGE_HANDLE:
            hf = _font(int(25 * S), "semibold", bengali)
            if hf is not None:
                hw = d.textlength(PAGE_HANDLE, font=hf)
                d.text((W - M - int(hw), H - strip_h + int(34 * S)), PAGE_HANDLE,
                       font=hf, fill=accent + (255,))
        mid_top = band_h + int(64 * S)
        mid_bot = H - strip_h - int(56 * S)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "bold", W - 2 * M, 5, int(78 * S), int(44 * S), bengali,
            budget=mid_bot - mid_top)
        y = mid_top + max(0, (mid_bot - mid_top - th) // 2)
        for ln in lines:
            if font is not None:
                lw = d.textlength(ln, font=font)
                _txt(d, ((W - lw) / 2, y), ln, font)
            y += lh

    elif variant == "quote":
        # v5.5: serif pull-quote on cream paper - quiet, editorial, zero-photo
        ink = (30, 32, 40, 255)
        qf = _font(int(170 * S), "serif_bold", bengali)
        if qf is not None:
            d.text((M - int(14 * S), int(70 * S)), "\u201C", font=qf, fill=accent + (235,))
        top = int(H * 0.24)
        lines, font, lh, th, _ = _fit_headline(
            d, headline, "serif_bold", W - 2 * M, 6, int(64 * S), int(38 * S), bengali)
        y = top
        for ln in lines:
            if font is not None:
                d.text((M, y), ln, font=font, fill=ink)
            y += lh
        y += int(34 * S)
        d.line([M, y, M + int(190 * S), y], fill=ink[:3] + (110,), width=max(2, int(2 * S)))
        y += int(26 * S)
        sf = _font(int(28 * S), "serif", bengali)
        if sf is not None:
            d.text((M, y), source_line, font=sf, fill=ink[:3] + (215,))
        if PAGE_HANDLE:
            hf = _font(int(26 * S), "serif", bengali)
            if hf is not None:
                hw = d.textlength(PAGE_HANDLE, font=hf)
                d.text((W - M - int(hw), y), PAGE_HANDLE, font=hf, fill=ink[:3] + (215,))

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

    # source + handle (quote/ticker variants draw their own source strip)
    if variant not in ("quote", "ticker"):
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
    out = _apply_grain(out)                    # v5.5: kill the too-clean AI look
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

    # v5.5: square/portrait mixing - a feed of only 4:5 cards reads as a
    # template; ~35% squares (never two in a row) reads like a real page
    fmts = list((state or {}).get("recent_formats") or [])
    square = (fmts[-1:] != ["square"]) and (random.random() < SQUARE_CARD_PROBABILITY)
    _set_card_format(square)
    if state is not None:
        state["recent_formats"] = (fmts + ["square" if square else "portrait"])[-6:]
    log.info("Card format: %s", "1080x1080 square" if square else "1080x1350 portrait")

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
    pool = []
    avoid = set((state or {}).get("recent_photo_ids") or [])
    try:
        pool = _pick_photo_pool(art, avoid)
    except Exception as e:
        log.debug("photo pick failed: %s", e)
    if pool:
        photo = pool[0]
    # v5.7: stash the extra downloaded photos on the article - the carousel
    # builder turns them into swipeable slides (plain images, no typography)
    try:
        art.photo_pool = pool
    except Exception:
        pass

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
# v5.7: CAROUSEL SLIDES - extra photos become swipeable Facebook carousels
# ==========================================================================
def _render_plain_photo(img, art=None):
    """Carousel slide: enhanced smart-crop + soft bottom scrim + category
    accent bar + grain. No typography - the designed card carries the
    headline; the slides carry pure wow. Slides share the feed's format
    language so the carousel reads as one designed object."""
    _set_card_format(False)
    S = max(1, SUPERSAMPLE)
    W, H = CARD_W * S, CARD_H * S
    base = _enhance_photo(_smart_crop(img, W, H).resize((W, H), _RESAMPLE.LANCZOS)).convert("RGBA")
    theme = CATEGORY_THEME.get(art.category if art is not None else None,
                               CATEGORY_THEME["general"])
    ov = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ov.alpha_composite(_vertical_gradient((W, int(H * 0.28)), (8, 10, 18), 0, 145, curve=1.6),
                       (0, H - int(H * 0.28)))
    d = ImageDraw.Draw(ov, "RGBA")
    bar_h = int(12 * S)
    d.rectangle([0, H - bar_h, W, H], fill=theme["accent"] + (255,))
    if PAGE_HANDLE:
        hf = _font(int(26 * S), "medium")
        if hf is not None:
            hw = d.textlength(PAGE_HANDLE, font=hf)
            d.text((W - int(46 * S) - hw, H - int(46 * S)), PAGE_HANDLE,
                   font=hf, fill=(255, 255, 255, 185))
    out = Image.alpha_composite(base, ov).convert("RGB")
    out = _apply_grain(out)
    return out.resize((CARD_W, CARD_H), _RESAMPLE.LANCZOS)


def _build_carousel_paths(art, state, card_path):
    """[card, slide, slide] for a swipeable multi-photo post. Uses the extra
    photos render_card already downloaded (art.photo_pool) - no new API calls.
    Returns [card_path] alone if not enough photos survived."""
    pool = list(getattr(art, "photo_pool", None) or [])[1:]
    random.shuffle(pool)                      # variety between runs
    take = pool[: max(1, CAROUSEL_MAX_PHOTOS - 1)]
    paths = [card_path] if card_path else []
    for img, _credit, pid in take:
        try:
            slide = _render_plain_photo(img, art)
            p = _save_special_card(slide, "slide")
            if p:
                paths.append(p)
        except Exception as e:
            log.debug("carousel slide failed: %s", e)
    if state is not None:
        ids = [pid for _i, _c, pid in take if pid]
        if ids:
            state["recent_photo_ids"] = (list(state.get("recent_photo_ids") or []) + ids)[-10:]
    if len(paths) >= 2:
        log.info("Carousel ready: %d slides (headline card + %d photos)", len(paths), len(paths) - 1)
    return paths


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


def fb_fetch_post_stats(limit=25):
    """v5.4: engagement snapshot for recent page posts via the Graph API
    (reactions + comments summary + shares; needs a valid page token)."""
    tok, pid = _fb_creds()
    if not tok or not pid:
        return []
    fields = ("id,created_time,message,permalink_url"
              ",reactions.summary(true).limit(0)"
              ",comments.summary(true).limit(0)"
              ",shares")
    r = _http_get(f"{_fb_base()}/{pid}/posts",
                  params={"fields": fields, "limit": limit, "access_token": tok},
                  timeout=25, retries=1)
    if r is None:
        return []
    try:
        return r.json().get("data", [])
    except Exception:
        return []


def fb_fetch_comments(post_id, limit=20):
    """v5.5: top-level comments on a page post (filter=stream + parent check
    client-side; Graph returns replies nested flat in stream mode)."""
    tok, _ = _fb_creds()
    if not tok:
        return []
    r = _http_get(f"{_fb_base()}/{post_id}/comments",
                  params={"fields": "id,from,message,created_time,like_count,parent",
                          "filter": "stream", "limit": limit,
                          "access_token": tok},
                  timeout=20, retries=1)
    if r is None:
        return []
    try:
        return r.json().get("data", [])
    except Exception:
        return []


def fb_like_comment(comment_id):
    """v5.5: page likes a fan comment - the cheapest 'this page is alive'
    signal; returns True on success (already-liked counts as success too)."""
    tok, _ = _fb_creds()
    if not tok:
        return False
    try:
        j = _fb_post_once(f"{_fb_base()}/{comment_id}/likes",
                          data={"access_token": tok}, timeout=20)
        return bool(j is not None and (j == {} or j.get("success", True)))
    except Exception as e:
        log.debug("comment like failed (%s): %s", comment_id, e)
        return False


def _parse_post_stats(posts, state):
    """v5.4: merge engagement numbers into state['post_stats'] (capped at 150
    freshest) and accumulate a best-hour-of-day histogram (IST)."""
    stats = dict(state.get("post_stats") or {})
    hours = dict(state.get("hour_eng") or {})
    for p in posts:
        pid = (p.get("id") or "").strip()
        if not pid:
            continue
        try:
            ts = datetime.strptime(p.get("created_time", ""),
                                   "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except Exception:
            continue
        rx = ((p.get("reactions") or {}).get("summary") or {}).get("total_count", 0)
        cm = ((p.get("comments") or {}).get("summary") or {}).get("total_count", 0)
        sh = ((p.get("shares") or {}).get("count", 0)) if isinstance(p.get("shares"), dict) else 0
        eng = int(rx) + int(cm) + 2 * int(sh)
        prev = stats.get(pid) or {}
        stats[pid] = {"ts": int(ts), "rx": int(rx), "cm": int(cm), "sh": int(sh),
                      "eng": max(eng, prev.get("eng", 0))}
        h = str(datetime.fromtimestamp(ts, IST).hour)
        hours[h] = hours.get(h, 0) + max(0, eng - int(prev.get("eng", 0)))
    state["post_stats"] = dict(sorted(stats.items(),
                                      key=lambda kv: -kv[1]["ts"])[:150])
    state["hour_eng"] = dict(sorted(hours.items(), key=lambda kv: int(kv[0])))
    return stats


def run_insights(limit=25) -> int:
    """v5.4: standalone engagement tracker (cron job): pulls reactions /
    comments / shares for the latest posts into state.json and logs a
    leaderboard so the posting schedule can be tuned on data, not vibes."""
    log.info("=== Insights tracker v%s | %s ===", VERSION,
             _now_ist().strftime("%d %b %Y, %H:%M IST"))
    state = _load_state()
    posts = fb_fetch_post_stats(limit)
    if not posts:
        # v5.5: a quiet page / rotated token / FB hiccup is "nothing to do",
        # NOT a failure - exit 0 so the scheduled workflow stays green
        log.info("No posts/engagement data yet (token missing or FB API unavailable) - nothing to do")
        return 0
    stats = _parse_post_stats(posts, state)
    _save_state(state)
    week = time.time() - 7 * 86400
    top = sorted((s for s in stats.values() if s["ts"] >= week),
                 key=lambda s: -s["eng"])[:5]
    log.info("Engagement leaderboard (last 7 days):")
    for i, s in enumerate(top, 1):
        log.info("   %d. %4d eng  (%d reactions / %d comments / %d shares, %.1f days old)",
                 i, s["eng"], s["rx"], s["cm"], s["sh"], (time.time() - s["ts"]) / 86400)
    hours = state.get("hour_eng") or {}
    if hours:
        best_h, best_e = max(hours.items(), key=lambda kv: kv[1])
        log.info("Best IST posting hour so far: %s:00 (cumulative engagement %d) - "
                 "consider shifting the cron toward it", best_h, best_e)
    log.info("Insights complete - %d posts tracked", len(state.get("post_stats") or {}))
    return 0


# ---------------------------------------------------------------- engage mode
def _engage_reply_system():
    return """You reply to comments on a global entertainment & trending-news
Facebook page. You are the voice of the page: warm, sharp, a little playful -
the admin who actually reads the comments.

Hard rules:
- 40 words MAX. One short paragraph, no line breaks.
- Reply to WHAT THEY SAID specifically - never a generic "thanks for commenting".
- Agree, add one small insight or push back gently if they're off the mark.
- If they state something wrong that the post itself contradicts, correct it kindly.
- Contractions everywhere. 0-2 emoji max, only if natural.
- Keep every reply family-friendly: no explicit content, no sleaze.
- NEVER invent numbers or facts that aren't in the post. Stay vague if unsure.
- No links, no hashtags, no questions that sound like customer support.
- Never mention being an AI or a bot. Never discuss religion or party politics.
- If the comment is pure abuse or spam, output exactly: SKIP

Output only the reply text."""


def _validate_reply(text):
    if not text:
        return None
    t = text.strip().strip('"')
    if t.upper() == "SKIP":
        return None
    t = t.replace("**", "")
    words = t.split()
    if not (3 <= len(words) <= 55):
        return None
    if "http" in t or "#" in t or "@" in t:
        return None
    low = t.lower()
    if re.search(r"\b(as an ai|language model|i am a bot)\b", low):
        return None
    if _is_explicit(t):            # v5.6: replies pass the same NSFW gate
        return None
    if t.count("\n") > 1:
        t = " ".join(t.split())
    return t


def run_engage(max_posts=10) -> int:
    """v5.5: the page talks back. Every few hours: read fresh comments on the
    latest posts, reply to the best ones in the page's voice (LLM), and like
    a handful more. This is the single biggest 'real page vs bot page' signal
    - Facebook's ranking also rewards comment replies with extra reach."""
    log.info("=== Engagement mode v%s | %s ===", VERSION,
             _now_ist().strftime("%d %b %Y, %H:%M IST"))
    state = _load_state()
    tok, pid = _fb_creds()
    if not tok or not pid:
        log.info("FB token/page missing - engagement pass skipped (nothing to do)")
        return 0

    posts = fb_get_recent_posts(max_posts)
    if not posts:
        log.info("No recent posts to engage with - nothing to do")
        return 0

    replied = list(state.get("replied_comment_ids") or [])
    liked = list(state.get("liked_comment_ids") or [])
    replied_set, liked_set = set(replied), set(liked)
    cutoff = time.time() - 48 * 3600
    candidates = []
    now = time.time()
    for p in posts:
        pmsg = (p.get("message") or "")[:700]
        try:
            p_ts = datetime.strptime(p.get("created_time", ""),
                                     "%Y-%m-%dT%H:%M:%S%z").timestamp()
        except Exception:
            continue
        if now - p_ts > 72 * 3600:            # only work recent posts
            continue
        for c in fb_fetch_comments(p.get("id"), ENGAGE_COMMENTS_PER_POST):
            cid = (c.get("id") or "").strip()
            msg = (c.get("message") or "").strip()
            author = ((c.get("from") or {}).get("id") or "")
            if not cid or not msg:
                continue
            if c.get("parent"):               # threaded reply, not top-level
                continue
            if author == pid:                 # never reply to ourselves
                continue
            if cid in replied_set:
                continue
            if len(msg) < 3 or len(msg) > 400:
                continue
            try:
                c_ts = datetime.strptime(c.get("created_time", ""),
                                         "%Y-%m-%dT%H:%M:%S%z").timestamp()
            except Exception:
                c_ts = now
            if c_ts < cutoff:                 # stale comment, skip
                continue
            # rank: liked comments first, then freshest
            candidates.append((int(c.get("like_count") or 0) + (now - c_ts) / -86400.0,
                               p.get("id"), pmsg, cid, msg))
    candidates.sort(key=lambda t: -t[0])
    log.info("Engagement scan: %d fresh top-level comments on %d posts",
             len(candidates), len(posts))

    # ---- 1. reply to the best N comments
    n_replies = 0
    for rank, post_id, pmsg, cid, cmsg in candidates[:ENGAGE_MAX_REPLIES]:
        user = (f"THE POST (excerpt):\n{pmsg or '(image-only post)'}\n\n"
                f"THE COMMENT:\n{cmsg}\n\nWrite the page's reply.")
        reply, provider = _llm_complete(_engage_reply_system(), user,
                                        temperature=0.9, max_tokens=450)
        reply = _validate_reply(reply)
        if not reply:
            log.info("no valid reply for comment %s (LLM skipped or rejected) - moving on", cid[:18])
            replied.append(cid)               # don't retry the same comment
            replied_set.add(cid)
            continue
        try:
            publish_comment(post_id, reply)
            replied.append(cid)
            replied_set.add(cid)
            n_replies += 1
            log.info("Replied to comment %s (%s): %s", cid[:18], provider, reply)
        except Exception as e:
            log.warning("reply publish failed (non-fatal): %s", e)

    # ---- 2. like a handful of comments (cheap aliveness signal; real admins
    # like the comment they just replied to, so replied-this-run ones count)
    n_likes = 0
    for rank, post_id, pmsg, cid, cmsg in candidates:
        if n_likes >= ENGAGE_LIKE_COMMENTS:
            break
        if cid in liked_set:
            continue
        if fb_like_comment(cid):
            liked.append(cid)
            liked_set.add(cid)
            n_likes += 1

    state["replied_comment_ids"] = replied[-400:]
    state["liked_comment_ids"] = liked[-600:]
    state["engage_last_ts"] = int(time.time())
    _save_state(state)
    log.info("Engagement pass done: %d replies, %d comment likes", n_replies, n_likes)
    return 0


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


def publish_carousel_post(image_paths, message):
    """v5.7: Facebook multi-photo (swipeable carousel). Each photo is uploaded
    UNPUBLISHED first, then one feed post references them via attached_media.
    Safety: if the final feed call fails, the unpublished photos stay invisible
    on the page, so falling back to a single-photo post can never duplicate
    content - the photo is just re-used."""
    tok, pid = _fb_creds()
    if not tok or not pid:
        raise RuntimeError("FB_PAGE_ACCESS_TOKEN / FB_PAGE_ID missing")
    fbids = []
    for p in image_paths:
        with open(p, "rb") as f:
            img_bytes = f.read()
        files = {"source": (Path(p).name, img_bytes, "image/jpeg")}
        j = _fb_post_once(f"{_fb_base()}/{pid}/photos",
                          data={"access_token": tok, "published": "false"},
                          files=files, timeout=120)
        fid = j.get("id")
        if not fid:
            raise RuntimeError("carousel photo upload returned no media id")
        fbids.append(str(fid))
    j = _fb_post_once(f"{_fb_base()}/{pid}/feed",
                      data={"access_token": tok, "message": message,
                            "attached_media": json.dumps([{"media_fbid": f} for f in fbids])},
                      timeout=60)
    return j.get("id")


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
    key = _dedup_key(art)
    if key not in (state.get("posted_keys") or []):        # v5.4: idempotent
        state["posted_keys"] = (state.get("posted_keys") or []) + [key]
    nt = _normalize_title(art.title)
    if nt not in (state.get("posted_titles") or []):       # v5.4: idempotent
        state["posted_titles"] = (state.get("posted_titles") or []) + [nt]
    state["last_post_ts"] = time.time()
    for key, val, cap in (("recent_categories", art.category, 5),
                          ("recent_domains", art.domain, 3),
                          ("recent_shapes", shape_id, 3),
                          ("recent_personas", persona_id, 2)):
        lst = list(state.get(key) or [])
        lst.append(val)
        state[key] = lst[-cap:]
    state["post_counter"] = (state.get("post_counter", 0) or 0) + 1
    # v5.4: hard daily topic diversity bookkeeping (keep last 7 days only)
    today = _today_ist_str()
    days = dict(state.get("category_days") or {})
    todays = dict(days.get(today) or {})
    todays[art.category] = todays.get(art.category, 0) + 1
    days[today] = todays
    state["category_days"] = dict(sorted(days.items())[-7:])


def _mark_in_flight(state, art):
    """v5.4: consume the story's dedup key BEFORE the upload starts. If the
    workflow dies mid-upload (timeout kill, crash, network hiccup), the
    retry pass can never double-post the same story. Worst case we lose one
    candidate; best case we avoid an embarrassing duplicate on the page."""
    keys = state.setdefault("posted_keys", [])
    key = _dedup_key(art)
    if key not in keys:
        keys.append(key)
    titles = state.setdefault("posted_titles", [])
    nt = _normalize_title(art.title)
    if nt not in titles:
        titles.append(nt)
    try:
        _save_state(state)
    except Exception as e:
        log.warning("could not persist in-flight mark: %s", e)


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
    log.info("=== Trending & Entertainment Page Bot v%s | %s ===", VERSION,
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
        # v5.7: a total source outage is "nothing to do this run", not a
        # failure - the next cron retries in a few hours and the job stays green
        log.warning("No articles fetched from any source - nothing to do this run "
                    "(next scheduled run retries automatically)")
        _save_state(state)
        return 0

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
            return 0
        card = render_card(art, state=state)
        _save_preview({"headline.txt": art.title + "\n" + art.url}, {"card.jpg": card} if card else {})
        return 0

    if getattr(args, "bengali_preview", False):
        art = select_article(clusters, state, max(4, threshold - 2)) or (flat[0] if flat else None)
        if art is None:
            log.warning("nothing to preview")
            return 0
        grounding = _article_grounding(art)
        post, card_h, provider = generate_bengali_post(art, grounding)
        if not post:
            log.error("Bengali generation failed")
            return 0
        card = render_card(art, state=state, bengali=True, card_headline=card_h)
        _save_preview({"bengali.txt": post}, {"bengali_card.jpg": card} if card else {})
        log.info("BENGALI PREVIEW (provider=%s):\n%s", provider, post)
        return 0

    # ---- 3.5 v5.7 ENGAGEMENT SPECIALS: trivia / this-or-that / riddle.
    # ~1 in 3 posts is a game post; the comment section becomes the show.
    # Any failure degrades to the regular news flow below.
    if SPECIAL_POSTS_ENABLED:
        special = None
        try:
            special = build_special_post(state)
        except Exception as e:
            log.warning("special-post build failed (%s) - continuing with news flow", e)
        if special:
            return _publish_special(special, state, args)
        _save_state(state)      # special roll may have updated counters

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
        # v5.7: LLM down is NOT a reason to go quiet. A hand-written template
        # caption keeps the page alive (and the CI green); the alert file tells
        # the owner their LLM keys need a look.
        if TEMPLATE_CAPTION_FALLBACK:
            commentary = _template_caption(art)
            meta = {"provider": "template-fallback", "persona": "template"}
            log.warning("LLM commentary failed after retries - posting a hand-written "
                        "template caption instead (page stays alive)")
            _flag_run_alert("LLM commentary failed after retries - template caption used. "
                            "Check GROQ_API_KEY/GEMINI_API_KEY and the WARNING lines in the log.")
        else:
            log.error("LLM commentary failed after retries and template fallback disabled - "
                      "skipping this run (nothing posted)")
            _save_state(state)
            return 0
    log.info("Commentary ready (provider=%s, shape=%s, persona=%s):\n%s",
             meta.get("provider"), shape["id"], meta.get("persona"), commentary)
    # v5.4: hashtag safety net - LLM shipped zero tags -> add up to 2 clean ones
    if SMART_TAGS and not re.search(r"#[\w]", commentary):
        tags = _smart_tags(art, commentary)
        if tags:
            commentary = commentary.rstrip() + "\n\n" + " ".join(tags)
            log.info("Smart tags appended: %s", " ".join(tags))
    # v5.5: interaction layer - hook CTA + hashtag reordering (user interaction)
    before_len = len(commentary)
    commentary = _apply_interaction_layer(commentary, art, state, shape["id"])
    if len(commentary) != before_len:
        log.info("Interaction layer applied (hook + tag reorder)")

    # ---- 6. image
    image_path = None
    carousel_paths = []
    conversational = shape["id"] in ("hot_take", "question_hook", "cold_open", "contrarian")
    text_only = (conversational and random.random() < TEXT_POST_PROBABILITY) \
        or getattr(args, "no_image", False)
    if text_only and not getattr(args, "no_image", False):
        log.info("Text-only hot take (v5.5 feed variety) - no image card this post")
    if not text_only and PIL_OK:
        try:
            image_path = render_card(art, state=state)
        except Exception as e:
            log.warning("image render failed (%s) - continuing without image", e)
        # v5.7: multi-photo carousel - the extra photos from the pool become
        # swipeable slides; carousels consistently out-reach single-photo posts
        if (CAROUSEL_ENABLED and image_path
                and random.random() < CAROUSEL_PROBABILITY):
            try:
                carousel_paths = _build_carousel_paths(art, state, image_path)
            except Exception as e:
                log.warning("carousel build failed (%s) - single-photo post", e)
                carousel_paths = []
            if len(carousel_paths) < 2:
                carousel_paths = []

    # ---- 7. publish (or preview)
    if getattr(args, "dry_run", False):
        files = {"message.txt": commentary}
        imgs = {"card.jpg": image_path} if image_path else {}
        if carousel_paths:
            for i, p in enumerate(carousel_paths[1:], 2):
                imgs[f"slide_{i}.jpg"] = p
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

    # v5.4: mark the story consumed BEFORE uploading - a timeout/crash/retry
    # can never re-select it, so double-posting is structurally impossible
    _mark_in_flight(state, art)

    post_id = None
    try:
        if len(carousel_paths) >= 2:
            try:
                post_id = publish_carousel_post(carousel_paths, commentary)
                log.info("Published CAROUSEL post id=%s (%d photos)", post_id, len(carousel_paths))
            except Exception as e:
                # unpublished uploads stay invisible - safe to retry as single photo
                log.warning("carousel publish failed (%s) - falling back to single photo", e)
                post_id = None
        if post_id is None:
            post_id = publish_photo_post(image_path, commentary) if image_path \
                else publish_text_post(commentary)
            log.info("Published post id=%s", post_id)
    except Exception as e:
        # v5.7: a broken token / FB hiccup flags an alert issue but does NOT
        # fail the workflow - the story is already marked, so the next run
        # simply picks a fresh story (no double-post, no red X)
        log.error("Publish failed: %s - story stays marked (no double-post risk on retry)", e)
        _flag_run_alert(f"publish failed: {e}")
        _save_state(state)
        return 0

    # optional reach trick: link as first comment instead of post body
    if POST_LINK_AS_FIRST_COMMENT and post_id and art.url:
        try:
            publish_comment(post_id, f"Full story: {art.url}")
            log.info("Article link posted as first comment")
        except Exception as e:
            log.warning("Link comment failed (non-fatal): %s", e)

    _update_state_after_post(state, art, shape["id"], meta.get("persona", ""))
    state["last_post_was_special"] = False      # v5.7: specials rotation bookkeeping

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
    a1 = Article("Taylor Swift announces 15-stadium world tour after album tops charts",
                 "https://x.com/a", "T", "variety.com", now)
    a2 = Article("Best smartphone deals and coupons this week",
                 "https://x.com/b", "T", "t.com", now)
    a3 = Article("Local man wins chess tournament",
                 "https://x.com/c", "T", "t.com", None)
    score_articles([a1, a2, a3])
    if not (a1.score >= 8 and a1.category in ("music", "celebs", "general")):
        failures.append(f"scoring strong story: {a1.score} {a1.category}")
    if a2.score >= 8:
        failures.append(f"junk leaked through: {a2.score}")
    if a3.score >= 4:
        failures.append(f"irrelevant scored too high: {a3.score}")

    # v5.3: content-quality firewall - SEO listicles die, tier-1 wins
    a4 = Article("Top 10 best 5G smartphones under \u20b920,000 to buy this week",
                 "https://x.com/d", "T", "gadgetsnow.com", now)
    a5 = Article("Top 10 best 5G smartphones under \u20b920,000 to buy this week",
                 "https://x.com/e", "T", "variety.com", now)
    a6 = Article("Avatar sequel breaks opening weekend box office record with $340 million worldwide",
                 "https://x.com/f", "T", "variety.com", now)
    big = Article("Messi signs record $1.2 billion contract extension",
                  "https://x.com/g", "T", "espn.com", now)
    small = Article("Messi extends deal worth $5 million",
                    "https://x.com/h", "T", "espn.com", now)
    score_articles([a4, a5, a6, big, small])
    if a5.score >= 9:
        failures.append(f"SEO listicle leaked through a tier-1 outlet: {a5.score}")
    if a4.score >= 9:
        failures.append(f"SEO listicle leaked through: {a4.score}")
    if a6.score < 12:
        failures.append(f"mega entertainment story scored too low: {a6.score}")
    if big.score <= small.score:
        failures.append(f"money magnitude not ranked: big={big.score} small={small.score}")
    if _domain_authority("variety.com") <= 0:
        failures.append("domain authority table broken")
    # v5.6: explicit-content firewall
    nsfw = Article("Celebrity nude photo scandal leaks online",
                   "https://x.com/z", "T", "tmz.com", now)
    clean_tour = Article("Beyonce announces world tour dates",
                         "https://x.com/y", "T", "variety.com", now)
    score_articles([nsfw, clean_tour])
    if not _is_explicit(nsfw.title):
        failures.append("explicit filter missed an NSFW headline")
    if _is_explicit(clean_tour.title):
        failures.append("explicit filter false-positive on a clean headline")

    # v5.4: clickbait firewall - bait hooks die even on a tier-1 outlet
    bait = Article("You won't believe what this SHOCKING celebrity just did!!!",
                   "https://x.com/i", "T", "variety.com", now)
    clean = Article("NASA unveils new spacecraft for first crewed Mars mission",
                    "https://x.com/j", "T", "nasa.gov", now)
    score_articles([bait, clean])
    if bait.score >= clean.score:
        failures.append(f"clickbait outranked clean news: {bait.score} vs {clean.score}")

    # v5.4: smart hashtag engine
    tag_art = Article("Marvel announces casting for new Avengers sequel film",
                      "https://x.com/k", "T", "t.com", now, category="movies")
    tags = _smart_tags(tag_art, "plain post text without any tags")
    if tags and tags[0] != "#Movies":
        failures.append(f"category tag wrong: {tags}")
    if "Avengers" not in (tags[1] if len(tags) > 1 else ""):
        failures.append(f"entity tag missing/poor: {tags}")
    dupes = [t.lower() for t in _smart_tags(tag_art, "already tagged #Movies here")]
    if "#movies" in dupes:
        failures.append("smart tags duplicated an existing tag")

    # v5.5: interaction hooks - deterministic tests (probability forced to 1)
    global ENGAGE_HOOK_PROBABILITY
    _saved_hook_p = ENGAGE_HOOK_PROBABILITY
    ENGAGE_HOOK_PROBABILITY = 1.0
    hook_art = Article("Taylor Swift world tour sells out stadiums as viral demand crashes sites",
                       "https://x.com/h1", "T", "t.com", now, category="music")
    hs = _default_state()
    prev = None
    for _ in range(25):
        h = _pick_hook(hook_art, hs, "stat_first")
        if h is None:
            failures.append("hook picker returned None with probability 1")
            break
        if "{" in h:
            failures.append(f"hook placeholder unfilled: {h}")
        if h == prev:
            failures.append("hook repeated back-to-back (rotation memory broken)")
        prev = h
    for _ in range(20):
        if _pick_hook(hook_art, _default_state(), "question_hook") is not None:
            failures.append("question_hook shape must never get an extra hook")
            break
    post = _apply_interaction_layer("Body line one.\nBody line two.", hook_art,
                                    _default_state(), "hot_take")
    blocks = [b for b in post.strip().split("\n\n") if b.strip()]
    if not (2 <= len(blocks) <= 2) or "Body line one" not in blocks[0]:
        failures.append(f"interaction layer mangled the body: {post!r}")
    post_t = _apply_interaction_layer("Body text here.\n\n#TagOne #TagTwo", hook_art,
                                     _default_state(), "contrarian")
    blocks_t = [b for b in post_t.strip().split("\n\n") if b.strip()]
    if len(blocks_t) < 2 or not blocks_t[-1].startswith("#TagOne"):
        failures.append(f"tags must move AFTER the hook line: {post_t!r}")
    ENGAGE_HOOK_PROBABILITY = _saved_hook_p

    # v5.5: engage reply validation
    if _validate_reply("SKIP") is not None:
        failures.append("SKIP reply must be rejected")
    if _validate_reply("Honestly fair point, the burn math does look scary.") is None:
        failures.append("valid reply rejected")
    if _validate_reply("As an AI language model I cannot predict that.") is not None:
        failures.append("AI self-outing reply must be rejected")
    if _validate_reply("see https://spam.example") is not None:
        failures.append("link-carrying reply must be rejected")
    if _validate_reply("word " * 60) is not None:
        failures.append("over-long reply must be rejected")

    # v5.4: insights parser - Graph-API-shaped payload in, leaderboard out
    st2 = _default_state()
    ct = datetime.now(IST).strftime("%Y-%m-%dT%H:%M:%S%z")
    fake_posts = [
        {"id": "p1", "created_time": ct,
         "reactions": {"summary": {"total_count": 40}},
         "comments": {"summary": {"total_count": 5}},
         "shares": {"count": 2}},
        {"id": "p2", "created_time": ct,
         "reactions": {"summary": {"total_count": 10}},
         "comments": {"summary": {"total_count": 1}}, "shares": None},
    ]
    _parse_post_stats(fake_posts, st2)
    if st2["post_stats"]["p1"]["eng"] != 49 or st2["post_stats"]["p2"]["eng"] != 11:
        failures.append(f"insights engagement math wrong: {st2['post_stats']}")
    _parse_post_stats(fake_posts, st2)     # re-run must be idempotent (no double count)
    if st2["post_stats"]["p1"]["eng"] != 49:
        failures.append("insights parser double-counts on re-runs")

    # dedup + selection safety valve
    st = _default_state()
    st["posted_titles"] = [_normalize_title(a1.title)]
    if select_article([[a1]], st, 1) is not None:
        failures.append("duplicate should be rejected")

    # image pipeline (offline: synthetic photo, no Pexels)
    if PIL_OK:
        _ensure_fonts()
        # v5.4: entropy crop must KEEP detail and DROP the flat side.
        # Build a photo with a noisy left half and a flat right half.
        busy = Image.effect_noise((1600, 2000), 92).convert("RGB")
        flat = Image.new("RGB", (1600, 2000), (20, 20, 20))
        wide = Image.new("RGB", (3200, 2000))
        wide.paste(busy, (0, 0))
        wide.paste(flat, (1600, 0))
        cropped = _smart_crop(wide, 1080, 1350)     # portrait target from landscape
        mid = cropped.convert("L").resize((16, 20))
        if sum(mid.getdata()) < 128 * 16 * 20 * 0.7:
            failures.append("entropy crop kept the flat half of the photo")
        # v5.4: auto headline sizing - short punch gets bigger type than long
        if _auto_headline_size("Jio posts record profit", 74, 42) <= \
           _auto_headline_size("Reliance Industries completes acquisition of Disney India assets today", 74, 42):
            failures.append("auto headline size not length-aware")
        out_dir = PREVIEW_DIR / "selftest"
        out_dir.mkdir(parents=True, exist_ok=True)
        art = Article("NASA moon rocket launch watched by 500 million people worldwide",
                      "https://example.com/moon", "Test Feed", "nasa.gov", now, category="space")
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
        # v5.5: square format mixing
        _set_card_format(True)
        img = _render_designer_card(art, "Square format sanity check", "S \u00b7 TODAY", False, 4)
        img.save(out_dir / "designer_card_square.jpg", "JPEG", quality=90)
        if img.size != (1080, 1080):
            failures.append(f"square card size {img.size}")
        img = _render_photo_card(photo, art, "ticker", "Square ticker sanity check", "S \u00b7 TODAY")
        img.save(out_dir / "photo_card_square_ticker.jpg", "JPEG", quality=90)
        if img.size != (1080, 1080):
            failures.append(f"square photo card size {img.size}")
        _set_card_format(False)
        # v5.5: film grain must actually perturb pixels (de-AI the clean renders)
        plain = Image.new("RGB", (300, 300), (40, 60, 90))
        if plain.tobytes() == _apply_grain(plain).tobytes():
            failures.append("grain is a no-op")
        # ---- v5.7: special card renders (quiz stage / riddle / VS battle)
        img = _render_quiz_card(header="TRIVIA DROP",
                                question="Which movie famously won 11 Oscars in a single night?",
                                options=["Titanic", "Ben-Hur", "LOTR: Return of the King",
                                         "West Side Story"],
                                footer="Answer in the comments \u00b7 TEST")
        img.save(out_dir / "special_card_quiz.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"quiz card size {img.size}")
        img = _render_quiz_card(header="GUESS THE MOVIE",
                                clues=["The ship everyone swore was unsinkable",
                                       "A door that famously fit only one",
                                       "An iceberg, a necklace, and a band that kept playing"],
                                footer="First correct comment wins \u00b7 TEST",
                                accent=(255, 200, 60))
        img.save(out_dir / "special_card_riddle.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"riddle card size {img.size}")
        img = _render_vs_card(_VS_PAIRS[0], photo, photo)
        img.save(out_dir / "special_card_vs.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"vs card size {img.size}")
        img = _render_vs_card(_VS_PAIRS[1], None, None)   # designer-half fallback
        img.save(out_dir / "special_card_vs_fallback.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"vs fallback card size {img.size}")
        # v5.7: carousel slide (plain photo, no typography)
        img = _render_plain_photo(photo, art)
        img.save(out_dir / "carousel_slide.jpg", "JPEG", quality=90)
        if img.size != (CARD_W, CARD_H):
            failures.append(f"carousel slide size {img.size}")
        log.info("Sample cards written to %s", out_dir)
    else:
        log.warning("Pillow missing - image tests skipped")

    # ---- v5.7: engagement specials (pure logic - runs with or without PIL)
    capped = {"specials_date": _today_ist_str(), "specials_today": MAX_SPECIALS_PER_DAY}
    if _pick_special_kind(capped) is not None:
        failures.append("special daily cap not enforced")
    b2b = {"specials_date": _today_ist_str(), "specials_today": 0,
           "last_post_was_special": True}
    if _pick_special_kind(b2b) is not None:
        failures.append("back-to-back specials allowed")
    for rb in _RIDDLE_BANK:
        if not rb.get("answer") or len(rb.get("clues") or []) != 3 or not rb.get("emoji"):
            failures.append(f"malformed riddle bank entry: {rb.get('answer')}")
            continue
        if _is_explicit(" ".join(rb["clues"]) + " " + rb["answer"]):
            failures.append(f"riddle tripped NSFW gate: {rb['answer']}")
    for vp in _VS_PAIRS:
        if not (vp.get("a") and vp.get("b") and vp.get("q") and vp.get("a_q") and vp.get("b_q")):
            failures.append("malformed VS pair")
    # riddle special builds fully offline (bank, no network)
    _orig_rand = random.random
    try:
        random.random = lambda: 0.30          # lands inside the riddle band
        sp = build_special_post({})
    finally:
        random.random = _orig_rand
    if not sp or sp.get("kind") != "riddle":
        failures.append(f"riddle special not built: {type(sp)}")
    else:
        if "GUESS THE" not in sp["message"].upper() or "ANSWER:" not in (sp["comment"] or ""):
            failures.append("riddle special message/comment malformed")
        if not sp["message"].strip() or _is_explicit(sp["message"]):
            failures.append("riddle special message failed safety/format check")
        if not str(sp["art"].title).startswith("special:riddle:"):
            failures.append("riddle special art key malformed")
    # template-caption fallback (LLM-down path)
    tcap = _template_caption(a1)
    if not tcap or "{" in tcap or "}" in tcap:
        failures.append("template caption malformed (unfilled placeholder)")
    if a1.title.split()[0].lower() not in tcap.lower():
        failures.append("template caption lost the headline")

    if failures:
        for f in failures:
            log.error("FAIL: %s", f)
        log.error("Self-test FAILED (%d failures)", len(failures))
        return 1
    log.info("Self-test PASSED")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Trending & Entertainment News Facebook Page Bot")
    ap.add_argument("--dry-run", action="store_true", help="no posting; save previews to ./preview/")
    ap.add_argument("--bengali-preview", action="store_true", help="force one Bengali companion preview")
    ap.add_argument("--preview-image", action="store_true", help="render the best card only (no LLM, no post)")
    ap.add_argument("--no-image", action="store_true", help="publish text-only post")
    ap.add_argument("--self-test", action="store_true", help="offline checks + sample cards")
    ap.add_argument("--insights", action="store_true",
                    help="pull engagement stats for recent posts into state.json + log leaderboard (no posting)")
    ap.add_argument("--engage", action="store_true",
                    help="v5.5: reply to fresh comments + like fan comments (the page talks back)")
    ap.add_argument("--threshold", type=int, default=None, help="override MIN_ENGAGEMENT_SCORE_TO_POST")
    ap.add_argument("--verbose", action="store_true", help="debug logging")
    args = ap.parse_args()
    _setup_logging(args.verbose)
    # v5.7: clear any stale alert flag from a previous run before starting
    # (the workflow turns a FRESH .run_alert into a GitHub issue)
    try:
        (BASE_DIR / ".run_alert").unlink(missing_ok=True)
    except Exception:
        pass
    if args.self_test:
        return _self_test()
    if args.insights:
        return run_insights()
    if args.engage:
        return run_engage()
    rc = run(args)
    # v5.4: completion sentinel - the GitHub Actions retry step fires ONLY
    # when this file is missing (process crashed / was killed mid-run).
    # Clean skips and clean failures complete without crashing -> no retry.
    try:
        Path(".run_ok").write_text(f"v{VERSION} {int(time.time())}\n", encoding="utf-8")
    except Exception:
        pass
    return rc


if __name__ == "__main__":
    sys.exit(main())
