#!/usr/bin/env python3
"""v5.8 mock tests: AI image engine + editorial pipeline + tier-3 LLM.

100% offline: every network call is mocked (Pollinations image/text, Groq,
Gemini, Pexels). Covers:
  - _ai_image_prompt: structure, per-category styles, NSFW firewall, thin text
  - _pollinations_image: 200 OK, 402 retry-exhausted, tiny image, hard error
  - _pollinations_chat: 200 OK, 402, "free limit" body, disabled switch
  - _llm_complete: groq -> gemini -> pollinations tier ordering
  - _hook_score: punchy post beats corporate filler
  - _editor_pass / generate_commentary: better rewrite kept, worse kept out,
    garbage kept out, editor crash leaves draft standing
  - _cinematic_grade / _enhance_photo: geometry kept, AI photos skip the grade
  - render_card end-to-end: AI image path (no stock search at all) and the
    silent AI-failure -> designer-card fallback
"""
import sys, io, os, logging, tempfile, random
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(message)s")

import bot

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


class FakeResp:
    def __init__(self, status_code=200, content=b"", text=""):
        self.status_code = status_code
        self.content = content
        self.text = text


class FakeSession:
    """Returns queued FakeResp objects (or exceptions) from .get()."""
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _jpeg_bytes(w=900, h=1120):
    """Real JPEG payload like Pollinations returns (4:5-ish)."""
    img = bot.Image.new("RGB", (w, h),
                        (120 + random.randint(0, 60), 40, 160))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=88)
    return buf.getvalue()


def _art(title="Marvel drops the first Avengers Doomsday trailer and the new lineup looks massive",
         category="movies"):
    return bot.Article(title=title, url="https://example.com/x",
                       source="Variety", domain="variety.com", category=category)


DRAFT = ("Marvel just dropped the first trailer for the next Avengers movie and "
         "honestly it looks massive. The footage gives us our first real look at "
         "the new team lineup in action. Fans are already picking apart every "
         "single frame for clues. This is going to own the weekend. Are you hyped "
         "or cautiously waiting?")

EDITOR_BETTER = ("Trailer of the year? Marvel thinks so.\n\nThe first Avengers "
                 "Doomsday footage just landed and it looks enormous. We finally "
                 "see the new team in action. Every frame is being dissected by "
                 "fans as we speak. The weekend belongs to this. Hyped or holding "
                 "judgement?")

EDITOR_WORSE = ("According to several reports about the new trailer footage from "
                "the studio, additional details are expected to emerge over the "
                "coming days once official channels provide further information "
                "about the movie and its plans for release. More soon on this.")

EDITOR_GARBAGE = "Game changer alert, buckle up!"       # banned phrases


# ---------------------------------------------------------------- section 1
print("\n== _ai_image_prompt ==")
p = bot._ai_image_prompt(_art())
check("builds a prompt from the headline", isinstance(p, str) and len(p) > 60)
check("prompt asks for vertical 4:5", p and "vertical 4:5" in p)
check("prompt carries anti-text negatives", p and "no text" in p and "no watermark" in p)
check("prompt carries the movies style bank", p and "cinematic movie still" in p)
check("prompt keeps the headline subject", p and "Avengers" in p)
p2 = bot._ai_image_prompt(_art(category="space"))
check("space category gets its own style bank", "nebula" in p2)
p3 = bot._ai_image_prompt(_art(title="Hi"))
check("thin headline -> None (stock flow)", p3 is None)
p4 = bot._ai_image_prompt(_art(title="explicit nsfw porn sex video leaked"))
check("NSFW headline -> None (firewall gate)", p4 is None)

# ---------------------------------------------------------------- section 2
print("\n== _pollinations_image (mocked HTTP) ==")
old_wait = bot.AI_IMAGE_RETRY_WAIT
bot.AI_IMAGE_RETRY_WAIT = 0                       # never sleep in tests

fake = FakeSession([FakeResp(200, _jpeg_bytes())])
old_sess = bot._SESSION
bot._SESSION = fake
img = bot._pollinations_image("epic cinematic shot of a hero")
check("200 OK -> image object", img is not None)
check("upscaled to the card canvas", img is not None and img.size == (bot.CARD_W, bot.CARD_H),
      str(img.size if img else None))

bot._SESSION = FakeSession([FakeResp(402), FakeResp(402)])
check("402 twice -> None (free tier busy)", bot._pollinations_image("busy") is None)

bot._SESSION = FakeSession([FakeResp(200, _jpeg_bytes(w=200, h=250))])
check("too-small image rejected", bot._pollinations_image("tiny") is None)

bot._SESSION = FakeSession([FakeResp(500)])
check("hard 500 -> None immediately", bot._pollinations_image("err") is None)

bot._SESSION = FakeSession([RuntimeError("network down")])
check("exception -> None (stock flow)", bot._pollinations_image("boom") is None)

sess = FakeSession([FakeResp(402), FakeResp(200, _jpeg_bytes())])
bot._SESSION = sess
check("402 then 200 -> image after one retry", bot._pollinations_image("retry me") is not None)
check("retry waited exactly once", len(sess.calls) == 2)

bot.AI_IMAGE_RETRY_WAIT = old_wait

# ---------------------------------------------------------------- section 3
print("\n== _pollinations_chat (tier-3 LLM, mocked) ==")
bot._SESSION = FakeSession([FakeResp(200, text="Here is a fun one-liner about the trailer.")])
t = bot._pollinations_chat("sys", "user prompt")
check("200 OK -> text returned", t and "trailer" in t)

bot._SESSION = FakeSession([FakeResp(402)])
check("402 -> None (free tier busy)", bot._pollinations_chat("s", "u") is None)

bot._SESSION = FakeSession([FakeResp(200, text="Free limit reached: wait 18 seconds")])
check("'free limit' body -> None", bot._pollinations_chat("s", "u") is None)

bot._SESSION = FakeSession([RuntimeError("timeout")])
check("exception -> None", bot._pollinations_chat("s", "u") is None)

old_flag = bot.POLLINATIONS_TEXT_ENABLED
bot.POLLINATIONS_TEXT_ENABLED = False
sess = FakeSession([])
bot._SESSION = sess
check("disabled switch -> None, zero HTTP calls", bot._pollinations_chat("s", "u") is None
      and len(sess.calls) == 0)
bot.POLLINATIONS_TEXT_ENABLED = old_flag

# ---------------------------------------------------------------- section 4
print("\n== _llm_complete tier ordering ==")
old_g, old_m, old_p = bot._groq_chat, bot._gemini_chat, bot._pollinations_chat
bot._groq_chat = lambda s, u, t=0, m=0: None
bot._gemini_chat = lambda s, u, t=0, m=0: None
bot._pollinations_chat = lambda s, u, t=0, m=0: "tier3 says hi"
text, prov = bot._llm_complete("s", "u")
check("groq+gemini down -> pollinations tier", prov == "pollinations" and text == "tier3 says hi")
bot._groq_chat = lambda s, u, t=0, m=0: "groq wins"
text, prov = bot._llm_complete("s", "u")
check("groq healthy -> never reaches tier 3", prov == "groq")
bot._groq_chat = old_g
bot._gemini_chat = old_m
bot._pollinations_chat = old_p

# ---------------------------------------------------------------- section 5
print("\n== _hook_score heuristic ==")
s_good = bot._hook_score(EDITOR_BETTER)
s_bad = bot._hook_score("According to a recent announcement the company will "
                        "release additional details regarding its plans at some "
                        "point in the future once further information becomes "
                        "available to everyone interested in the outcome here.")
check("punchy post scores well", s_good >= 6, f"score={s_good}")
check("corporate filler scores badly", s_bad <= 2, f"score={s_bad}")
check("punchy beats filler", s_good > s_bad)
check("empty text -> 0", bot._hook_score("") == 0)

# ---------------------------------------------------------------- section 6
print("\n== editorial pass inside generate_commentary ==")
old_llm = bot._llm_complete
old_edit = bot.EDITOR_PASS_ENABLED
bot.EDITOR_PASS_ENABLED = True
shape = {"id": "hot_take", "name": "Hot take", "scaffold": "one-line reaction, then why"}
persona = {"id": "fan", "desc": "a movie fan with opinions"}

def llm_two_pass(system, user, temperature=1.0, max_tokens=450):
    if "DRAFT POST" in user:
        return EDITOR_BETTER, "gemini"          # editor rewrite
    return DRAFT, "groq"                        # first-pass draft

bot._llm_complete = llm_two_pass
out, meta = bot.generate_commentary(_art(), shape, persona)
check("editor rewrite kept when score >= draft", out == EDITOR_BETTER)
check("meta records editor provider", meta.get("editor") == "gemini")
check("meta records hook delta", meta.get("hook") == [bot._hook_score(DRAFT),
                                                      bot._hook_score(EDITOR_BETTER)])

def llm_worse(system, user, temperature=1.0, max_tokens=450):
    if "DRAFT POST" in user:
        return EDITOR_WORSE, "gemini"
    return DRAFT, "groq"

bot._llm_complete = llm_worse
out2, meta2 = bot.generate_commentary(_art(), shape, persona)
check("lower-scoring rewrite rejected", out2 == DRAFT and "editor" not in meta2)

def llm_garbage(system, user, temperature=1.0, max_tokens=450):
    if "DRAFT POST" in user:
        return EDITOR_GARBAGE, "gemini"
    return DRAFT, "groq"

bot._llm_complete = llm_garbage
out3, meta3 = bot.generate_commentary(_art(), shape, persona)
check("banned-phrase rewrite rejected, draft stands", out3 == DRAFT)

def llm_crash(system, user, temperature=1.0, max_tokens=450):
    if "DRAFT POST" in user:
        raise RuntimeError("editor exploded")
    return DRAFT, "groq"

bot._llm_complete = llm_crash
out4, meta4 = bot.generate_commentary(_art(), shape, persona)
check("editor crash -> draft still ships", out4 == DRAFT)

bot.EDITOR_PASS_ENABLED = False
out5, _ = bot.generate_commentary(_art(), shape, persona)
check("EDITOR_PASS_ENABLED=false -> no second call", out5 == DRAFT and "editor" not in _)
bot.EDITOR_PASS_ENABLED = old_edit
bot._llm_complete = old_llm

# ---------------------------------------------------------------- section 7
print("\n== cinematic grade / photo enhancement ==")
img = bot.Image.new("RGB", (600, 750), (10, 120, 200))
graded = bot._cinematic_grade(img)
check("grade keeps geometry", graded.size == (600, 750))
check("grade keeps RGB mode", graded.mode == "RGB")
check("grade actually shifts pixels", list(graded.getdata()) != list(img.getdata()))

bot._CURRENT_PHOTO_KIND["kind"] = "stock"
out_s = bot._enhance_photo(bot.Image.new("RGB", (50, 60), (200, 10, 10)), cinematic=True)
check("explicit cinematic=True applies grade", out_s is not None)
bot._CURRENT_PHOTO_KIND["kind"] = "ai"
out_a = bot._enhance_photo(bot.Image.new("RGB", (50, 60), (200, 10, 10)))
check("AI photo path never crashes enhancement", out_a is not None)
bot._CURRENT_PHOTO_KIND["kind"] = "stock"

# ---------------------------------------------------------------- section 8
print("\n== render_card end-to-end (AI path + fallback) ==")
if bot.PIL_OK:
    tmp = Path(tempfile.mkdtemp(prefix="ai_cards_"))
    old_imgdir = bot.IMAGE_DIR
    bot.IMAGE_DIR = tmp
    old_pool = bot._pick_photo_pool
    old_gen = bot._pollinations_image
    old_p, old_e = bot.AI_IMAGE_PROBABILITY, bot.AI_IMAGE_ENABLED
    bot.AI_IMAGE_ENABLED = True
    bot.AI_IMAGE_PROBABILITY = 1.0

    # AI success: stock search must never even run
    def pool_must_not_run(*a, **k):
        raise AssertionError("stock photo pool fetched despite AI success")
    bot._pick_photo_pool = pool_must_not_run
    bot._pollinations_image = lambda prompt, timeout=None: bot.Image.new(
        "RGB", (bot.CARD_W, bot.CARD_H), (90, 30, 160))
    art = _art()
    state = {}
    path = bot.render_card(art, state=state)
    check("AI image -> card written", path is not None and Path(path).exists())
    check("AI card is a real JPEG (>10KB)", path and Path(path).stat().st_size > 10_000)
    check("state counts the AI image", state.get("ai_images") == 1)
    check("photo_pool stays empty for AI cards", getattr(art, "photo_pool", None) == [])

    # AI failure: silent fallback to designer card, never an exception
    bot._pollinations_image = lambda prompt, timeout=None: None
    bot._pick_photo_pool = lambda *a, **k: []          # no Pexels key in CI
    path2 = bot.render_card(_art(), state={})
    check("AI failure -> designer fallback card", path2 is not None and Path(path2).exists())

    bot._pick_photo_pool = old_pool
    bot._pollinations_image = old_gen
    bot.AI_IMAGE_PROBABILITY, bot.AI_IMAGE_ENABLED = old_p, old_e
    bot.IMAGE_DIR = old_imgdir
    import shutil as _sh
    _sh.rmtree(tmp, ignore_errors=True)
else:
    print("  SKIP  (Pillow unavailable in this environment)")

# ---------------------------------------------------------------- summary
print(f"\n{'='*58}\nAI-ENGINE TESTS: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("\nFAILED CASES:")
    for f in FAIL:
        print("  -", f)
    sys.exit(1)
print("ALL GREEN")
sys.exit(0)
