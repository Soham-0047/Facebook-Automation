"""Mock tests for bot.py v5.1 LLM call paths (no network, no keys).

Simulates: dead-model 404, empty-content 200, 429-then-200 retry,
think-block stripping, missing-key warnings, validator rejection reasons.
"""
import sys, json, types, logging
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.DEBUG, format="%(levelname)-7s | %(message)s")

import bot

PASS = []
FAIL = []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(f"{name} {detail}")
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


class FakeResp:
    def __init__(self, status, payload=None, text=""):
        self.status_code = status
        self._payload = payload
        self.text = text or (json.dumps(payload) if payload is not None else "")
    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def groq_content(text):
    return {"choices": [{"message": {"role": "assistant", "content": text}}]}


# ---------------------------------------------------------------- test 1
print("\n[1] dead model 404 -> falls through to next model -> success")
calls = []
def fake_post_1(url, **kw):
    calls.append((url, kw))
    model = kw["json"]["model"]
    if model == "openai/gpt-oss-120b":
        return FakeResp(404, {"error": {"message": "The model ... does not exist"}})
    if model == "qwen/qwen3.6-27b":
        return FakeResp(200, groq_content("<think>let me think</think>\nPlain post body here."))
    return FakeResp(200, groq_content("fallback"))
bot._SESSION.post = fake_post_1
bot._LLM_KEY_WARNED.clear()
import os
os.environ["GROQ_API_KEY"] = "gsk_test"
os.environ.pop("GEMINI_API_KEY", None)

text, provider = bot._llm_complete("sys", "usr")
check("falls through 404 to qwen", provider == "groq", provider)
check("think-block stripped", text == "Plain post body here.", repr(text))

# verify request body uses new params
body = calls[-1][1]["json"]
check("max_completion_tokens used", "max_completion_tokens" in body and "max_tokens" not in body)
check("token floor >= 1100", body["max_completion_tokens"] >= 1100, str(body["max_completion_tokens"]))
check("reasoning_effort=low only for gpt-oss",
      (calls[0][1]["json"].get("reasoning_effort") == "low") and ("reasoning_effort" not in body))

# ---------------------------------------------------------------- test 2
print("\n[2] 429 rate-limit -> retries same model -> succeeds")
seq = {"n": 0}
def fake_post_2(url, **kw):
    seq["n"] += 1
    if seq["n"] <= 2:
        return FakeResp(429, {"error": {"message": "Rate limit reached"}})
    return FakeResp(200, groq_content("Recovered after backoff."))
bot._SESSION.post = fake_post_2
bot.time.sleep = lambda s: None          # skip backoff delays
text, provider = bot._llm_complete("sys", "usr")
check("429 retried then success", text == "Recovered after backoff.", repr(text))
check("exactly 3 attempts", seq["n"] == 3, str(seq["n"]))

# ---------------------------------------------------------------- test 3
print("\n[3] empty content (reasoning ate budget) -> warns + next model")
counter = {"n": 0}
def fake_post_3(url, **kw):
    counter["n"] += 1
    if kw["json"]["model"] == "openai/gpt-oss-120b":
        return FakeResp(200, {"choices": [{"message": {"role": "assistant", "content": ""}}]})
    return FakeResp(200, groq_content("qwen to the rescue"))
bot._SESSION.post = fake_post_3
text, provider = bot._llm_complete("sys", "usr")
check("empty content -> next model", text == "qwen to the rescue", repr(text))

# ---------------------------------------------------------------- test 4
print("\n[4] all groq dead + no gemini key -> clean None with visible warnings")
def fake_post_4(url, **kw):
    return FakeResp(404, {"error": {"message": "model does not exist"}})
bot._SESSION.post = fake_post_4
bot._LLM_KEY_WARNED.clear()
text, provider = bot._llm_complete("sys", "usr")
check("returns None when chain dead", text is None and provider == "")

# ---------------------------------------------------------------- test 5
print("\n[5] gemini path: thinkingConfig + retry + empty parts")
gem_calls = []
def fake_post_5(url, **kw):
    if "generativelanguage" in url:
        gem_calls.append((url, kw["json"]))
        model = url.split("/models/")[1].split(":")[0]
        if model == "gemini-2.5-flash":
            return FakeResp(200, {"candidates": [{"content": {"parts": [{"text": "hello from gemini"}]}}]})
        return FakeResp(404, {}, text="not found")
    return FakeResp(404, {"error": {"message": "model does not exist"}})
bot._SESSION.post = fake_post_5
os.environ["GEMINI_API_KEY"] = "gem_test"
text, provider = bot._llm_complete("sys", "usr")
check("gemini fallback works", text == "hello from gemini" and provider == "gemini", f"{provider}/{text!r}")
check("gemini thinkingBudget=0 on 2.5-flash",
      gem_calls[0][1]["generationConfig"].get("thinkingConfig", {}).get("thinkingBudget") == 0)
check("gemini maxOutputTokens floor >= 1200",
      gem_calls[0][1]["generationConfig"]["maxOutputTokens"] >= 1200)

# ---------------------------------------------------------------- test 6
print("\n[6] validator rejection reasons are diagnosable")
r1 = bot._reject_reason("This is a game changer post about tech.", bengali=False)
check("detects banned phrase", "banned phrase" in r1, r1)
r2 = bot._reject_reason("Read more at https://example.com now thanks", bengali=False)
check("detects link", r2 == "contains a link", r2)
r3 = bot._reject_reason("too short", bengali=False)
check("detects short", "word count" in r3, r3)
r4 = bot._reject_reason("x " * 200, bengali=False)
check("detects long", "word count" in r4, r4)

# ---------------------------------------------------------------- test 7
print("\n[7] _strip_reasoning edge cases")
check("removes closed block", bot._strip_reasoning("<think>a\nb</think>POST") == "POST")
check("handles unclosed block", bot._strip_reasoning("POST<think>partial") == "POST")
check("no think tag", bot._strip_reasoning("plain") == "plain")

# ---------------------------------------------------------------- test 8
print("\n[8] missing-key warning fires once, not per attempt")
bot._LLM_KEY_WARNED.clear()
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("GEMINI_API_KEY", None)
seen = []
class WarnRecorder(logging.Handler):
    def emit(self, record): seen.append(record.getMessage())
bot.log.addHandler(WarnRecorder())
bot.log.setLevel(logging.WARNING)
for _ in range(3):
    bot._groq_chat("s", "u")
keywarns = [m for m in seen if "GROQ_API_KEY not set" in m]
check("warned exactly once for 3 calls", len(keywarns) == 1, str(len(keywarns)))
bot.log.removeHandler(WarnRecorder())

print("\n" + "=" * 60)
print(f"RESULT: {len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILURES:")
    for f in FAIL:
        print(" -", f)
    sys.exit(1)
print("ALL MOCK TESTS PASSED")
