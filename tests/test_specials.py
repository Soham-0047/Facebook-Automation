#!/usr/bin/env python3
"""v5.7 mock tests: engagement specials + carousels + never-fail automation.

Covers (all offline, no keys, no network):
  1. OpenTDB trivia: base64 decode, shuffle, answer index, NSFW gate,
     long-option gate, API-down degradation.
  2. Special-kind rotation: daily cap, no back-to-back, same-kind damping.
  3. Riddle + VS special building offline (bank rotation, photo fallbacks).
  4. Template caption fallback (LLM-down path).
  5. Carousel: slide building + publish_carousel_post Graph API payload
     (unpublished uploads + attached_media feed post).
  6. CI leniency fault-injection: fetch empty / LLM down / publish raise
     -> run() must ALWAYS return 0; publish failures write .run_alert.
"""
import base64
import json
import sys
import types
import logging
import random
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s | %(message)s")

import bot

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def b64e(s: str) -> str:
    return base64.b64encode(s.encode()).decode()


class FakeResp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


# ---------------------------------------------------------------- fixtures
NOW = datetime.now(timezone.utc)
ART = bot.Article("Taylor Swift announces 15-stadium world tour after album tops charts",
                  "https://x.com/a", "Variety", "variety.com", NOW)
ART.category = "music"


def fresh_state():
    return {"posted_keys": [], "posted_titles": [], "post_counter": 0}


# ---------------------------------------------------------------- 1. trivia
print("\n[1] OpenTDB trivia decoding + gates")
_TRIVIA_PAYLOAD = {"response_code": 0, "results": [{
    "category": "Film", "type": "multiple", "difficulty": "easy",
    "question": b64e("Which movie won the Oscar for Best Picture in 2020?"),
    "correct_answer": b64e("Parasite"),
    "incorrect_answers": [b64e("1917"), b64e("Joker"), b64e("Once Upon a Time in Hollywood")]}]}

_orig_http_get = bot._http_get
try:
    bot._http_get = lambda *a, **k: FakeResp(json.loads(json.dumps(_TRIVIA_PAYLOAD)))
    t = bot.fetch_trivia()
    check("trivia decodes question", t and "Oscar" in t["question"])
    check("trivia has 4 options", t and len(t["options"]) == 4)
    check("trivia answer present", t and t["answer"] == "Parasite")
    check("trivia answer_idx valid", t and t["options"][t["answer_idx"]] == "Parasite")
    check("trivia category mapped", t and t["category"] in ("movies", "music", "gaming", "tv"))

    nsfw = json.loads(json.dumps(_TRIVIA_PAYLOAD))
    nsfw["results"][0]["question"] = b64e("Which celebrity sex tape scandal broke the internet?")
    bot._http_get = lambda *a, **k: FakeResp(nsfw)
    check("trivia NSFW gate trips", bot.fetch_trivia() is None)

    long = json.loads(json.dumps(_TRIVIA_PAYLOAD))
    long["results"][0]["correct_answer"] = b64e("A Very Extremely Long Correct Answer That Cannot Fit Any Card Anywhere")
    bot._http_get = lambda *a, **k: FakeResp(long)
    check("trivia long-option gate trips", bot.fetch_trivia() is None)

    bot._http_get = lambda *a, **k: None
    check("trivia API-down returns None", bot.fetch_trivia() is None)
finally:
    bot._http_get = _orig_http_get

# ---------------------------------------------------------------- 2. rotation
print("\n[2] special-kind rotation rules")
capped = {"specials_date": bot._today_ist_str(), "specials_today": bot.MAX_SPECIALS_PER_DAY}
check("daily cap enforced", bot._pick_special_kind(capped) is None)
b2b = {"specials_date": bot._today_ist_str(), "specials_today": 0, "last_post_was_special": True}
check("no back-to-back specials", bot._pick_special_kind(b2b) is None)
forced = {"specials_date": bot._today_ist_str(), "specials_today": 0}
_orig_rand = random.random
try:
    random.random = lambda: 0.01          # deep inside trivia band
    check("trivia band selects trivia", bot._pick_special_kind(forced) == "trivia")
    random.random = lambda: 0.20          # inside vs band (0.16..0.28)
    check("vs band selects vs", bot._pick_special_kind(forced) == "vs")
    random.random = lambda: 0.33          # inside riddle band (0.28..0.36)
    check("riddle band selects riddle", bot._pick_special_kind(forced) == "riddle")
    random.random = lambda: 0.90          # beyond all bands
    check("no special outside bands", bot._pick_special_kind(forced) is None)
    # same-kind damping: 70% chance of skip when last was same kind
    damp = {"specials_date": bot._today_ist_str(), "specials_today": 0,
            "last_special_kind": "trivia"}
    random.random = lambda: 0.01
    random.random = lambda: 0.01          # roll=trivia, damping roll also < 0.7
    check("same-kind damped", bot._pick_special_kind(damp) is None)
finally:
    random.random = _orig_rand

# ---------------------------------------------------------------- 3. specials
print("\n[3] special post building (offline)")
try:
    random.random = lambda: 0.33
    sp = bot.build_special_post({})
    check("riddle special builds", sp and sp["kind"] == "riddle")
    check("riddle has card path", sp and sp["card_path"] and Path(sp["card_path"]).exists())
    check("riddle message mentions game", sp and "GUESS THE" in sp["message"].upper())
    check("riddle comment carries answer", sp and "ANSWER:" in sp["comment"])
    check("riddle art key namespaced", str(sp["art"].title).startswith("special:riddle:"))
    st = {}
    seen = set()
    for _ in range(8):
        s = bot.build_special_post(st)
        if s:
            seen.add(s["art"].title.split(":")[2])
    check("riddle rotation avoids repeats", len(seen) >= 4, f"distinct={len(seen)}")
finally:
    random.random = _orig_rand

# trivia special end-to-end with mocked API
_orig_http_get = bot._http_get
try:
    bot._http_get = lambda *a, **k: FakeResp(json.loads(json.dumps(_TRIVIA_PAYLOAD)))
    random.random = lambda: 0.01
    sp = bot.build_special_post({})
    check("trivia special builds", sp and sp["kind"] == "trivia")
    check("trivia message has options", sp and "A)" in sp["message"] and "D)" in sp["message"])
    check("trivia comment reveals answer", sp and "Parasite" in sp["comment"])
    check("trivia card exists", sp and sp["card_path"] and Path(sp["card_path"]).exists())
finally:
    random.random = _orig_rand
    bot._http_get = _orig_http_get

# vs special with mocked photo pipeline
_orig_ps, _orig_dp, _orig_secret = bot._pexels_search, bot._download_photo, bot._get_secret
try:
    bot._get_secret = lambda name, default="": "FAKE_KEY" if name == "PEXELS_API_KEY" else ""
    bot._pexels_search = lambda q, o, per_page=18: [
        {"id": 1, "width": 2000, "height": 3000, "photographer": "T",
         "src": {"large2x": "http://x/1.jpg"}}]
    bot._download_photo = lambda p: bot._synthetic_photo(1200, 1600)
    random.random = lambda: 0.20
    st = {}
    sp = bot.build_special_post(st)
    check("vs special builds", sp and sp["kind"] == "vs")
    check("vs message has both teams", sp and "A)" in sp["message"] and "B)" in sp["message"])
    check("vs card exists", sp and sp["card_path"] and Path(sp["card_path"]).exists())
    check("vs state tracks pair", "recent_vs" in st and len(st["recent_vs"]) == 1)
finally:
    random.random = _orig_rand
    bot._pexels_search, bot._download_photo, bot._get_secret = _orig_ps, _orig_dp, _orig_secret

# ---------------------------------------------------------------- 4. templates
print("\n[4] template caption fallback")
for _ in range(6):
    tc = bot._template_caption(ART)
    ok = bool(tc) and "{" not in tc and "}" not in tc and "Taylor" in tc
    if not ok:
        check("template caption well-formed", False, tc[:60])
        break
else:
    check("template caption well-formed (6 rolls)", True)
caps = {bot._template_caption(ART).split("\n")[0][:20] for _ in range(12)}
check("templates vary across rolls", len(caps) >= 3, f"distinct openers={len(caps)}")

# ---------------------------------------------------------------- 5. carousel
print("\n[5] carousel slides + Graph API payload")
from PIL import Image
tmp = Path(bot.IMAGE_DIR)
tmp.mkdir(parents=True, exist_ok=True)
paths = []
for i in range(3):
    p = tmp / f"_test_slide_{i}.jpg"
    bot._synthetic_photo(1400, 1800).save(p, "JPEG", quality=88)
    paths.append(p)

art = bot.Article("Test", "https://x.com/z", "T", "t.com", NOW)
synth = bot._synthetic_photo(1400, 1800)
art.photo_pool = [(synth, "T", 101), (synth, "T", 102), (synth, "T", 103)]
cpaths = bot._build_carousel_paths(art, {}, paths[0])
check("carousel builder returns card + 2 slides", len(cpaths) == 3, f"paths={len(cpaths)}")
check("carousel keeps the designed card as slide 1", cpaths[0] == paths[0])
for p in cpaths[1:]:
    check(f"slide exists {Path(p).name}", Path(p).exists())
    check(f"slide size {Path(p).name}", Image.open(p).size == (1080, 1350))

calls = []
_orig_creds, _orig_post_once = bot._fb_creds, bot._fb_post_once


def fake_post_once(url, data=None, files=None, timeout=90):
    calls.append((url, dict(data or {}), bool(files)))
    if "/feed" in url:
        return {"id": "feed_post_123"}
    return {"id": f"media_{len(calls)}"}


try:
    bot._fb_creds = lambda: ("TOKEN", "PAGE")
    bot._fb_post_once = fake_post_once
    pid = bot.publish_carousel_post(paths, "carousel message")
    check("carousel returns feed post id", pid == "feed_post_123", f"pid={pid}")
    check("carousel made 4 calls (3 uploads + 1 feed)", len(calls) == 4, f"calls={len(calls)}")
    check("photo uploads unpublished", all(c[1].get("published") == "false"
                                           for c in calls[:3]))
    check("feed call carries message", calls[3][1].get("message") == "carousel message")
    am = json.loads(calls[3][1].get("attached_media", "[]"))
    check("attached_media has 3 refs", len(am) == 3 and all("media_fbid" in m for m in am))
    check("media ids in order", [m["media_fbid"] for m in am] == ["media_1", "media_2", "media_3"])
finally:
    bot._fb_creds, bot._fb_post_once = _orig_creds, _orig_post_once

# ---------------------------------------------------------------- 6. leniency
print("\n[6] never-fail automation (fault injection)")
ARGS = types.SimpleNamespace(dry_run=False, preview_image=False, bengali_preview=False,
                             no_image=False, threshold=None)
_saved = {"state": fresh_state()}

_patched = {}


def patch_all(**kv):
    _patched.clear()
    for k, v in kv.items():
        _patched[k] = getattr(bot, k)
        setattr(bot, k, v)


def restore_all():
    for k, v in _patched.items():
        setattr(bot, k, v)
    _patched.clear()


base_stubs = dict(
    _load_state=lambda: _saved["state"],
    _save_state=lambda s: _saved.update(state=s),
    _sync_state_from_facebook=lambda s: None,
    _article_grounding=lambda a: "",
    _wikipedia_context=lambda t: "",
    generate_bengali_post=lambda a, g: (None, None, "none"),
    _pick_photo_pool=lambda a, avoid=None, want=4: [],
    # v5.9: run() now honours the specials time window - force it open so the
    # fault-injection scenarios behave identically at any hour of the day
    _hour_in_windows=lambda h, s: True,
)

# 6a. total source outage -> exit 0
patch_all(fetch_all_articles=lambda: [], **base_stubs)
try:
    rc = bot.run(ARGS)
    check("fetch outage exits 0", rc == 0, f"rc={rc}")
finally:
    restore_all()

# 6b. LLM down -> template caption still posts -> exit 0
published = {}


def fake_publish_photo(image_path, message):
    published["message"] = message
    return "post_llm_down"


def fake_publish_text(message):
    published["message"] = message
    return "post_llm_down_text"


patch_all(fetch_all_articles=lambda: [ART],
          select_article=lambda c, s, t: ART,
          generate_commentary=lambda a, sh, p, g, w, state=None, followup_note="": (None, {}),
          publish_photo_post=fake_publish_photo,
          publish_text_post=fake_publish_text,
          render_card=lambda a, state=None, bengali=False, card_headline=None, badge=None: None,
          _pick_special_kind=lambda s: None,   # deterministic: test the NEWS flow
          **base_stubs)
try:
    rc = bot.run(ARGS)
    check("LLM-down exits 0", rc == 0, f"rc={rc}")
    check("LLM-down still posts", "message" in published)
    check("template caption used", "template" in published.get("message", "") or
          "Taylor" in published.get("message", ""))
finally:
    restore_all()

# 6c. publish raises -> exit 0 + .run_alert flagged
alert_path = Path(bot.BASE_DIR / ".run_alert")
if alert_path.exists():
    alert_path.unlink()


def exploding_publish(*a, **k):
    raise RuntimeError("FB POST failed (HTTP 400): token expired")


patch_all(fetch_all_articles=lambda: [ART],
          select_article=lambda c, s, t: ART,
          generate_commentary=lambda a, sh, p, g, w, state=None, followup_note="": ("LLM caption text here.", {"provider": "groq", "persona": "arjun"}),
          publish_photo_post=exploding_publish,
          publish_text_post=exploding_publish,
          render_card=lambda a, state=None, bengali=False, card_headline=None, badge=None: None,
          _pick_special_kind=lambda s: None,   # deterministic: test the NEWS flow
          **base_stubs)
try:
    rc = bot.run(ARGS)
    check("publish-failure exits 0", rc == 0, f"rc={rc}")
    check("publish-failure flags .run_alert", alert_path.exists())
    if alert_path.exists():
        check("alert names the error", "token expired" in alert_path.read_text())
        alert_path.unlink()
finally:
    restore_all()

# 6d. special publish fails -> exit 0 + alert
patch_all(fetch_all_articles=lambda: [ART],
          publish_photo_post=exploding_publish,
          publish_text_post=exploding_publish,
          **base_stubs)
try:
    random.random = lambda: 0.33
    rc = bot.run(ARGS)
    check("special publish-failure exits 0", rc == 0, f"rc={rc}")
    check("special failure flags .run_alert", alert_path.exists())
    if alert_path.exists():
        alert_path.unlink()
finally:
    random.random = _orig_rand
    restore_all()

# cleanup test slides + rendered artifacts
for pat in ("_test_slide_*.jpg", "slide_*.jpg"):
    for p in tmp.glob(pat):
        try:
            p.unlink()
        except Exception:
            pass

# ---------------------------------------------------------------- result
print("\n" + "=" * 58)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ALL SPECIALS TESTS PASSED")
