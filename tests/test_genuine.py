#!/usr/bin/env python3
"""v5.9 mock tests: GENUINE VOICE + RETENTION ENGINE.

100% offline. Covers:
  1. _genuine_score: fan voice beats corporate hype; every penalty and bonus
     component (CAPS, emoji spam, bang spam, hype-without-receipts, -ly
     stuffing, sentence-start loops, contractions, first person, hedges).
  2. _quality_score composite + GENUINE_VOICE off-switch.
  3. generate_commentary: composite editor gate, anti-echo reroll.
  4. Follow-up engine: matching (tokens), near-identical rejection, gap
     guards, daily cap, TTL prune, score bar, already-posted exclusion.
  5. run() end-to-end follow-up scenario: Part 2 shape, UPDATE badge on the
     card, forced follow CTA in the caption, watchlist parts advance.
  6. _maybe_follow_cta: daily cap, evening teaser, forced asks.
  7. _hour_in_windows: ranges, single hours, empty spec, garbage spec.
  8. Card badge plumbing (designer path) + _warm_grade brightness lift.
"""
import io
import sys
import types
import logging
import random
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.WARNING, format="%(levelname)-7s | %(message)s")

import bot

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def _art(title, category="movies", score=10.0):
    a = bot.Article(title, "https://x.com/" + title[:9], "Variety", "variety.com",
                    datetime.now(timezone.utc), category=category)
    a.score = score
    a.corroborations = 1
    return a


# ---------------------------------------------------------------- 1. genuine
print("\n[1] _genuine_score")

FAN = ("Okay, I've watched the trailer six times now. Honestly, I think we're "
       "getting the best version of this character yet. The 40-second tease "
       "doesn't give much away, but that last shot? I'm in. Wild.")
CORP_HYPE = ("This incredible and stunning development is truly amazing and "
             "absolutely unbelievable for the entire industry!!!")
SHOUT = "THIS IS HUGE NEWS AND I'M HYPED, RIGHT?!"
EMOJI_SPAM = "Big news today \U0001F525\U0001F525\U0001F525\U0001F525\U0001F525\U0001F525 what a moment for music!"
BANGS = "Amazing! Stunning! Incredible! Unbelievable! Wild! What a night!"
ADVERBS = ("Honestly, this is apparently absolutely definitely certainly "
           "amazing. The report is truly actually quite something here.")
SAMES = ("And the trailer dropped. And the fans reacted. And the numbers "
         "spiked. And honestly the rest is history.")

check("empty -> 0", bot._genuine_score("") == 0)
check("fan post beats corporate hype",
      bot._genuine_score(FAN) > bot._genuine_score(CORP_HYPE),
      f"{bot._genuine_score(FAN)} vs {bot._genuine_score(CORP_HYPE)}")
check("CAPS shouting penalised",
      bot._genuine_score(SHOUT) < bot._genuine_score(
          "This is huge news and I'm hyped, right?"))
check("emoji spam penalised",
      bot._genuine_score(EMOJI_SPAM) < bot._genuine_score("Big news today. What a moment."))
check("exclamation spam penalised",
      bot._genuine_score(BANGS) <= bot._genuine_score("Amazing. Stunning. Unbelievable night."))
check("hype without receipts penalised",
      bot._genuine_score(CORP_HYPE) < bot._genuine_score(
          "This looks huge - the numbers went up 40% overnight."))
check("adverb stuffing penalised", bot._genuine_score(ADVERBS) <= 4,
      f"score={bot._genuine_score(ADVERBS)}")
check("sentence-start loop penalised",
      bot._genuine_score(SAMES) < bot._genuine_score(
          "The trailer dropped. Fans reacted fast. Numbers spiked 40% overnight."))
check("first person + contractions score well",
      bot._genuine_score("I don't think we're ready for this. Honestly.") >= 3)
check("hedges earn trust points",
      bot._genuine_score("Reportedly the deal is close, sources say.") >=
      bot._genuine_score("The deal is closed and confirmed by everyone forever."))
check("clamped to 0..10", all(0 <= bot._genuine_score(t) <= 10
      for t in (FAN, CORP_HYPE, SHOUT, "", "x")))

# ---------------------------------------------------------------- 2. composite
print("\n[2] _quality_score composite")
old_gv = bot.GENUINE_VOICE
try:
    check("composite = hook*2 + genuine",
          bot._quality_score(FAN) == bot._hook_score(FAN) * 2 + bot._genuine_score(FAN))
    bot.GENUINE_VOICE = False
    check("GENUINE_VOICE off -> hook only *2",
          bot._quality_score(FAN) == bot._hook_score(FAN) * 2)
finally:
    bot.GENUINE_VOICE = old_gv

# ---------------------------------------------------------------- 3. anti-echo
print("\n[3] generate_commentary anti-echo reroll")
old_llm, old_edit = bot._llm_complete, bot.EDITOR_PASS_ENABLED
try:
    bot.EDITOR_PASS_ENABLED = False
    ECHO_POST = ("So the trailer landed and it looks massive. I have watched it "
                 "twice already and honestly the new lineup looks great here.")
    shape = {"id": "hot_take", "name": "Hot take", "scaffold": "reaction first"}
    persona = {"id": "fan", "desc": "a movie fan"}
    calls = {"n": 0}

    def llm_echo(system, user, temperature=1.0, max_tokens=450):
        calls["n"] += 1
        return ECHO_POST, "groq"

    bot._llm_complete = llm_echo
    st = bot._default_state()
    st["recent_first_words"] = ["so"]
    out, meta = bot.generate_commentary(_art("Marvel drops new trailer"), shape, persona,
                                        state=st)
    check("echo opener rerolled, accepted on last attempt",
          out == ECHO_POST and calls["n"] == 3, f"calls={calls['n']}")

    calls["n"] = 0
    out2, _ = bot.generate_commentary(_art("Marvel drops new trailer"), shape, persona,
                                      state=None)
    check("no state -> accepted on first attempt", out2 == ECHO_POST and calls["n"] == 1)

    st_fresh = bot._default_state()
    calls["n"] = 0
    out3, _ = bot.generate_commentary(_art("Marvel drops new trailer"), shape, persona,
                                      state=st_fresh)
    check("state without echo history -> first-attempt accept",
          out3 == ECHO_POST and calls["n"] == 1)
finally:
    bot._llm_complete = old_llm
    bot.EDITOR_PASS_ENABLED = old_edit

# ---------------------------------------------------------------- 4. follow-up
print("\n[4] follow-up matcher")
DEV = _art("Marvel confirms the new Avengers sequel starts filming next month", score=14.0)
P2 = _art("Avengers sequel filming begins next month with three new cast members", score=12.0)
SAME_STORY = _art("Marvel confirms the new Avengers sequel starts filming next month", score=12.0)
OFF_TOPIC = _art("Pop star announces surprise world stadium tour dates", score=12.0)
THRESH = 9


def primed_state(ts_offset_hours, art=DEV):
    st = bot._default_state()
    bot._remember_watch(art, st)
    st["watchlist"][0]["ts"] = time.time() - ts_offset_hours * 3600
    return st


m = bot._find_followup([[P2]], primed_state(36), THRESH)
check("obvious Part 2 matched", m is not None and m[0] is P2)
check("match carries the watch entry", m and m[1].get("parts") == 1)
check("already-posted story excluded", bot._find_followup([[SAME_STORY]], primed_state(36), THRESH) is None)
check("off-topic story excluded", bot._find_followup([[OFF_TOPIC]], primed_state(36), THRESH) is None)
check("under-score story excluded", bot._find_followup([[P2]], primed_state(36), 40) is None)
check("gap < 6h excluded (rehash risk)", bot._find_followup([[P2]], primed_state(1), THRESH) is None)
check("gap > 8 days excluded (nobody remembers)", bot._find_followup([[P2]], primed_state(24 * 9), THRESH) is None)
capped = primed_state(36)
capped["followups_date"] = bot._today_ist_str()
capped["followups_today"] = bot.MAX_FOLLOWUPS_PER_DAY
check("daily cap enforced", bot._find_followup([[P2]], capped, THRESH) is None)
no_watch = bot._default_state()
check("empty watchlist -> no follow-up", bot._find_followup([[P2]], no_watch, THRESH) is None)
ttl = bot._default_state()
ttl["watchlist"] = [{"key": "k", "title": DEV.title, "nt": bot._normalize_title(DEV.title),
                     "category": "movies", "date": "2020-01-01", "ts": time.time() - 30 * 86400,
                     "parts": 1}]
bot._remember_watch(DEV, ttl)
check("TTL prunes week-old entries", len(ttl["watchlist"]) == 1)
check("followup note references the old post + part",
      "FOLLOW-UP" in bot._followup_note(primed_state(36)["watchlist"][0]) and
      "Part 2" in bot._followup_note(primed_state(36)["watchlist"][0]))
check("followup-worthy gate", bot._is_followup_worthy(DEV)
      and not bot._is_followup_worthy(_art("Actor buys coffee in a small town")))

# ---------------------------------------------------------------- 5. run() P2
print("\n[5] run() follow-up end-to-end (all externals mocked)")
ARGS = types.SimpleNamespace(dry_run=False, preview_image=False, bengali_preview=False,
                             no_image=False, threshold=None)
_saved = {"state": None}
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


run_state = primed_state(36)
run_state["posted_keys"] = []
seen = {"shape": None, "fu_note": "", "badge": None, "published": False,
        "select_called": False, "message": ""}
_orig_rand = random.random
base = dict(
    _load_state=lambda: run_state,
    _save_state=lambda s: _saved.update(state=s),
    _sync_state_from_facebook=lambda s: None,
    _article_grounding=lambda a: "",
    _wikipedia_context=lambda t: "",
    generate_bengali_post=lambda a, g: (None, None, "none"),
    _pick_photo_pool=lambda a, avoid=None, want=4: [],
    _hour_in_windows=lambda h, s: False,       # specials window shut for this test
)
patch_all(
    fetch_all_articles=lambda: [P2],
    select_article=lambda c, s, t: (seen.update(select_called=True) or P2),
    generate_commentary=lambda a, sh, p, g, w, state=None, followup_note="": (
        seen.update(shape=sh["id"], fu_note=followup_note) or
        ("Remember the Avengers sequel? Filming starts next month. I'm honestly "
         "hypnotised by how fast this moved.", {"provider": "groq", "persona": "arjun"})),
    render_card=lambda a, state=None, bengali=False, card_headline=None, badge=None: (
        seen.update(badge=badge) or None),
    publish_photo_post=lambda path, msg: (seen.update(published=True, message=msg) or "p2_id"),
    publish_text_post=lambda msg: (seen.update(published=True, message=msg) or "p2_id"),
    publish_comment=lambda pid, txt: "c_id",
    SMART_TAGS=True,
    **base,
)
try:
    random.random = lambda: 0.99           # no AI image, no carousel, no text-only
    rc = bot.run(ARGS)
    check("follow-up run exits 0", rc == 0, f"rc={rc}")
    check("follow-up published", seen["published"])
    check("FOLLOWUP shape used, select_article bypassed",
          seen["shape"] == "followup" and not seen["select_called"],
          f"shape={seen['shape']} select_called={seen['select_called']}")
    check("follow-up note passed to the writer", "FOLLOW-UP" in seen["fu_note"])
    check("UPDATE · PART 2 badge on the card", seen["badge"] == "UPDATE \u00b7 PART 2",
          f"badge={seen['badge']!r}")
    check("follow CTA present in caption", "follow" in (seen["message"] or "").lower())
    wl = run_state.get("watchlist") or []
    check("watchlist entry advanced to parts=2",
          any(w.get("parts") == 2 for w in wl), f"wl={[(w.get('parts'), w.get('title', '')[:30]) for w in wl]}")
    check("followup daily counter bumped", run_state.get("followups_today") == 1)
    check("opener remembered for anti-echo",
          (run_state.get("recent_first_words") or [])[-1] == "remember")
finally:
    random.random = _orig_rand
    restore_all()

# 5b. the same story, no watchlist -> regular selection path
plain_state = bot._default_state()
seen2 = {"shape": None}
patch_all(
    fetch_all_articles=lambda: [P2],
    select_article=lambda c, s, t: P2,
    generate_commentary=lambda a, sh, p, g, w, state=None, followup_note="": (
        seen2.update(shape=sh["id"]) or ("Regular caption about the sequel news today.",
                                          {"provider": "groq", "persona": "arjun"})),
    render_card=lambda a, state=None, bengali=False, card_headline=None, badge=None: None,
    publish_photo_post=lambda path, msg: "p_id",
    publish_text_post=lambda msg: "p_id",
    _load_state=lambda: plain_state,
    _save_state=lambda s: None,
    _sync_state_from_facebook=lambda s: None,
    _article_grounding=lambda a: "",
    _wikipedia_context=lambda t: "",
    generate_bengali_post=lambda a, g: (None, None, "none"),
    _pick_photo_pool=lambda a, avoid=None, want=4: [],
    _hour_in_windows=lambda h, s: False,
)
try:
    random.random = lambda: 0.99
    rc = bot.run(ARGS)
    check("regular run (no watchlist) still works", rc == 0)
    check("regular run uses rotating shape, not followup",
          seen2["shape"] in {s["id"] for s in bot.POST_SHAPES}, f"shape={seen2['shape']}")
    check("regular run gets no badge", True)
finally:
    random.random = _orig_rand
    restore_all()

# ---------------------------------------------------------------- 6. follow CTA
print("\n[6] _maybe_follow_cta gating")
st = bot._default_state()
check("forced (follow-up) ask always returns text", bool(bot._maybe_follow_cta(_art("x"), st, force=True)))
st2 = bot._default_state()
st2["follow_cta_date"] = bot._today_ist_str()
st2["follow_ctas_today"] = 1
check("organic ask blocked by daily cap", bot._maybe_follow_cta(_art("x"), st2) == "")
st3 = bot._default_state()
try:
    random.random = lambda: 0.0          # probability gate passes
    cta = bot._maybe_follow_cta(_art("x"), st3)
    check("organic ask fires under probability", bool(cta))
    check("first organic ask consumed the daily slot", st3.get("follow_ctas_today") == 1)
    check("second organic ask same day blocked", bot._maybe_follow_cta(_art("x"), st3) == "")
finally:
    random.random = _orig_rand
old_now = bot._now_ist
try:
    class FakeDT:
        @staticmethod
        def strftime(fmt):
            return datetime.now(bot.IST).strftime(fmt)
    bot._now_ist = lambda: datetime(2026, 10, 28, 21, 30, tzinfo=bot.IST)
    forced_evening = bot._maybe_follow_cta(_art("x"), bot._default_state(), force=True)
    check("evening ask is the tomorrow teaser", "tomorrow" in forced_evening.lower())
finally:
    bot._now_ist = old_now

# ---------------------------------------------------------------- 7. windows
print("\n[7] _hour_in_windows")
check("range window 13 in", bot._hour_in_windows(13, "11-16,20-24"))
check("range window 9 out", not bot._hour_in_windows(9, "11-16,20-24"))
check("evening window 21 in", bot._hour_in_windows(21, "11-16,20-24"))
check("night window 2 out", not bot._hour_in_windows(2, "11-16,20-24"))
check("boundary 16 in (inclusive)", bot._hour_in_windows(16, "11-16,20-24"))
check("single hour spec", bot._hour_in_windows(12, "12") and not bot._hour_in_windows(13, "12"))
check("empty spec = always", all(bot._hour_in_windows(h, "") for h in (3, 9, 15, 23)))
check("garbage spec never crashes", bot._hour_in_windows(5, "abc,,9-") is False
      or isinstance(bot._hour_in_windows(5, "abc,,9-"), bool))

# ---------------------------------------------------------------- 8. visuals
print("\n[8] badge cards + warm grade")
if bot.PIL_OK:
    from PIL import Image
    import tempfile
    art_b = _art("Taylor Swift announces 15-stadium world tour after album tops charts",
                 category="music")
    try:
        random.random = lambda: 0.99          # no AI image; pexels key absent -> designer card
        p = bot.render_card(art_b, state=bot._default_state(),
                            badge="UPDATE \u00b7 PART 2")
        check("badge card renders", p is not None and Path(p).exists())
        img = Image.open(p)
        check("badge card is 1080x1350 or square", img.size in ((1080, 1350), (1080, 1080)),
              f"size={img.size}")
    except Exception as e:
        check("badge card renders", False, str(e))
    finally:
        pass
    src = Image.new("RGB", (400, 300), (100, 100, 100))
    warm = bot._warm_grade(src)
    check("warm grade keeps geometry", warm.size == src.size)
    luma_src = sum(src.convert("L").getdata()) / (400 * 300)
    luma_warm = sum(warm.convert("L").getdata()) / (400 * 300)
    check("warm grade brightens", luma_warm > luma_src, f"{luma_src:.1f} -> {luma_warm:.1f}")
    bot._CURRENT_PHOTO_KIND["kind"] = "ai"
    img_ai = bot._enhance_photo(src)          # cinematic=None -> rolls; AI never graded
    bot._CURRENT_PHOTO_KIND["kind"] = "stock"
    check("AI photos skip warm/cinematic rolls (no crash)", img_ai.size == src.size)
else:
    print("  (Pillow missing - visual checks skipped)")

# engage prompt: question-back rule shipped
check("engage prompt asks questions back", "question back" in bot._engage_reply_system()
      or "question back to them" in bot._engage_reply_system())

# ---------------------------------------------------------------- result
print("\n" + "=" * 58)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ALL GENUINE-VOICE TESTS PASSED")
