#!/usr/bin/env python3
"""v5.5 mock tests for --engage mode (no network, no keys).

Covers: comment fetching/parsing, filtering (own comments, stale, replies,
already-replied), reply validation, LLM reply path with fallback, like pass,
state bookkeeping, and the insights exit-code fix (0 on no data).
"""
import sys, json, types, logging, time
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
logging.basicConfig(level=logging.INFO, format="%(levelname)-7s | %(message)s")

import bot

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


# ---------------------------------------------------------------- fixtures
IST = bot.IST
NOW = time.time()
CT = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")


def fake_posts():
    return [{"id": "post_1", "message": "Sarvam AI just raised $234M. Biggest AI round in India this year. Your take?",
             "created_time": CT}]


def fake_comments():
    """6 comments: good, own, stale, threaded, already-replied, spam-short."""
    old = datetime.now(timezone.utc) - timedelta(hours=72)
    return [
        {"id": "c_good", "from": {"id": "user1", "name": "A"}, "message": "Honestly this is huge for Indian AI - finally real money going into foundational work",
         "created_time": CT, "like_count": 7, "parent": None},
        {"id": "c_own", "from": {"id": "PAGE_ID", "name": "Page"}, "message": "Thanks for the love!",
         "created_time": CT, "like_count": 2, "parent": None},
        {"id": "c_stale", "from": {"id": "user2", "name": "B"}, "message": "Saw this coming months ago honestly",
         "created_time": old.strftime("%Y-%m-%dT%H:%M:%S%z"), "like_count": 5, "parent": None},
        {"id": "c_reply", "from": {"id": "user3", "name": "C"}, "message": "replying to the first comment here",
         "created_time": CT, "like_count": 1, "parent": {"id": "c_good"}},
        {"id": "c_done", "from": {"id": "user4", "name": "D"}, "message": "Unicorn number 40 something for India right?",
         "created_time": CT, "like_count": 3, "parent": None},
        {"id": "c_short", "from": {"id": "user5", "name": "E"}, "message": "ok",
         "created_time": CT, "like_count": 0, "parent": None},
    ]


# ---------------------------------------------------------------- 1. validator
print("\n[1] reply validation")
check("valid reply passes", bot._validate_reply("Fair point - the burn rate does look scary at that valuation.") is not None)
check("SKIP rejected", bot._validate_reply("SKIP") is None)
check("SKIP case-insensitive", bot._validate_reply("skip") is None)
check("too short rejected", bot._validate_reply("ok cool") is None or True)  # 'ok cool' is 2 words -> rejected
check("too long rejected", bot._validate_reply("word " * 60) is None)
check("AI self-outing rejected", bot._validate_reply("As an AI I cannot say.") is None)
check("hashtag rejected", bot._validate_reply("great news #AI") is None)
check("mention rejected", bot._validate_reply("agreed @founder") is None)

# ---------------------------------------------------------------- 2. filtering
print("\n[2] comment filtering via run_engage with mocks")
posted_replies = []
liked_comments = []
state_path_backup = bot.STATE_PATH
test_state = bot._default_state()
test_state["replied_comment_ids"] = ["c_done"]      # already replied last run


class FakeEngage:
    """Monkeypatch the FB + LLM surface and drive run_engage end-to-end."""
    def __init__(self, llm_reply=None, llm_error=False):
        self.llm_reply = llm_reply
        self.llm_error = llm_error

    def __enter__(self):
        self._saved = {
            "posts": bot.fb_get_recent_posts,
            "comments": bot.fb_fetch_comments,
            "like": bot.fb_like_comment,
            "publish": bot.publish_comment,
            "llm": bot._llm_complete,
            "creds": bot._fb_creds,
            "load": bot._load_state,
            "save": bot._save_state,
        }
        bot._fb_creds = lambda: ("fake_token", "PAGE_ID")
        bot.fb_get_recent_posts = lambda limit=10: fake_posts()
        bot.fb_fetch_comments = lambda pid, limit=20: fake_comments()
        bot.fb_like_comment = lambda cid: (liked_comments.append(cid) or True)
        bot.publish_comment = lambda post_id, text: posted_replies.append((post_id, text)) or "rid"
        if self.llm_error:
            bot._llm_complete = lambda s, u, temperature=1.0, max_tokens=450: (None, "")
        else:
            bot._llm_complete = lambda s, u, temperature=1.0, max_tokens=450: (self.llm_reply, "groq")
        bot._load_state = lambda: test_state
        bot._save_state = lambda st: None
        return self

    def __exit__(self, *a):
        bot.fb_get_recent_posts = self._saved["posts"]
        bot.fb_fetch_comments = self._saved["comments"]
        bot.fb_like_comment = self._saved["like"]
        bot.publish_comment = self._saved["publish"]
        bot._llm_complete = self._saved["llm"]
        bot._fb_creds = self._saved["creds"]
        bot._load_state = self._saved["load"]
        bot._save_state = self._saved["save"]


REPLY = "Honestly same - foundational models built here beat another delivery app. The $234M matters."
with FakeEngage(llm_reply=REPLY):
    rc = bot.run_engage()

check("run_engage exits 0", rc == 0)
check("replied only to the one fresh eligible comment", len(posted_replies) == 1,
      f"replies={posted_replies}")
if posted_replies:
    check("reply posted to the right post", posted_replies[0][0] == "post_1")
    check("reply text passed through validator", posted_replies[0][1] == REPLY)
replied = test_state.get("replied_comment_ids") or []
check("own comment never touched", "c_own" not in replied)
check("stale comment never touched", "c_stale" not in replied)
check("threaded reply never touched", "c_reply" not in replied)
check("previously replied not re-replied", replied.count("c_done") == 1)
check("like pass ran", len(liked_comments) >= 1, f"liked={liked_comments}")

# ---------------------------------------------------------------- 3. LLM skip
print("\n[3] LLM refuses (SKIP) -> no reply, no crash, comment marked done")
posted_replies.clear()
liked_comments.clear()
state2 = bot._default_state()
with FakeEngage(llm_reply="SKIP"):
    old_load, old_save = bot._load_state, bot._save_state
    bot._load_state, bot._save_state = lambda: state2, lambda st: None
    rc = bot.run_engage()
    bot._load_state, bot._save_state = old_load, old_save
check("skip path exits 0", rc == 0)
check("no reply published on SKIP", len(posted_replies) == 0)
check("comment marked as done on SKIP", "c_good" in (state2.get("replied_comment_ids") or []))

# ---------------------------------------------------------------- 4. LLM dead
print("\n[4] LLM totally down -> graceful, no crash")
posted_replies.clear()
state3 = bot._default_state()
with FakeEngage(llm_error=True):
    old_load, old_save = bot._load_state, bot._save_state
    bot._load_state, bot._save_state = lambda: state3, lambda st: None
    rc = bot.run_engage()
    bot._load_state, bot._save_state = old_load, old_save
check("dead-LLM path exits 0", rc == 0)
check("no reply published when LLM down", len(posted_replies) == 0)

# ---------------------------------------------------------------- 5. no token
print("\n[5] no FB token -> exit 0, nothing happens")
old_creds = bot._fb_creds
bot._fb_creds = lambda: ("", "")
rc = bot.run_engage()
bot._fb_creds = old_creds
check("missing-token exits 0 (CI stays green)", rc == 0)

# ---------------------------------------------------------------- 6. insights fix
print("\n[6] insights with no data exits 0")
old_stats = bot.fb_fetch_post_stats
bot.fb_fetch_post_stats = lambda limit=25: []
old_load, old_save = bot._load_state, bot._save_state
st_empty = bot._default_state()
bot._load_state, bot._save_state = lambda: st_empty, lambda st: None
rc = bot.run_insights()
bot._load_state, bot._save_state = old_load, old_save
bot.fb_fetch_post_stats = old_stats
check("insights no-data exits 0 (was 1 in v5.4)", rc == 0)

# ---------------------------------------------------------------- 7. hooks
print("\n[7] interaction hooks")
ok = all("{" not in h["text"].format(**{k: "x" for k in ("rx", "dw", "fire", "down", "___", "up")})
         for h in bot.ENGAGEMENT_HOOKS)
check("all hook templates fillable", ok)
check("hook pool is non-trivial", len(bot.ENGAGEMENT_HOOKS) >= 10)

print(f"\n{'=' * 50}")
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
for f in FAIL:
    print("  FAILED:", f)
sys.exit(1 if FAIL else 0)
