#!/usr/bin/env python3
"""v5.4 test: contrast-aware scrim on synthetic bright vs dark photos."""
import sys
from pathlib import Path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from PIL import Image
import bot

class A: pass
art = A()
art.title = "Sarvam becomes India's newest AI unicorn with $234 million round"
art.source = "Inc42"
art.domain = "inc42.com"
art.category = "unicorn"
art.url = "https://inc42.com/buzz/sarvam-unicorn"

# luminance helper read-back
for name, color in (("BRIGHT white photo", (245, 245, 250)), ("DARK night photo", (18, 20, 26))):
    photo = Image.new("RGB", (1080, 1350), color)
    # measure zone luma both ways
    luma = bot._zone_luma(photo, 0.55, 0.95)
    adj = bot._scrim_adj(luma)
    print(f"{name:22s} zone_luma={luma:6.1f}  scrim_adj={adj:+d}")

# assert logic
assert bot._scrim_adj(bot._zone_luma(Image.new("RGB", (100, 100), (245, 245, 250)))) > 0
assert bot._scrim_adj(bot._zone_luma(Image.new("RGB", (100, 100), (18, 20, 26)))) < 0
assert bot._scrim_adj(bot._zone_luma(Image.new("RGB", (100, 100), (90, 90, 90)))) == 0
print("scrim adjustment logic OK (+bright / -dark / 0 mid)")

# render two photo cards end-to-end and compare headline-band darkness
bright = Image.new("RGB", (1600, 2000), (250, 248, 245))
dark = Image.new("RGB", (1600, 2000), (16, 18, 24))
head = "Sarvam becomes India's newest AI unicorn with $234 million round"
out = Path("/home/z/my-project/download/preview")
out.mkdir(parents=True, exist_ok=True)
results = {}
for name, ph in (("bright", bright), ("dark", dark)):
    img = bot._render_photo_card(ph, art, "bottom_sheet", bot._card_headline(art.title),
                                 "INC42 - 10 SEP", False, None, 0)
    # measure mean luminance of the HEADLINE band (bottom third, where text sits)
    w, h = img.size
    zone = img.convert("L").crop((0, int(h * 0.75), w, h)).resize((64, 64))
    px = list(zone.getdata())
    results[name] = sum(px) / len(px)
    img.resize((540, 675)).save(out / f"scrim_test_{name}.jpg", quality=88)
    print(f"rendered {name:6s} card -> headline-band mean luma {results[name]:.0f}")

# the headline band must be dark enough for white text on BOTH
assert results["bright"] < 120, f"bright headline band too bright: {results}"
assert results["dark"] < 120, f"dark headline band too bright: {results}"
assert results["dark"] > 4, f"dark card crushed to pure black: {results}"
print(f"PASS: headline bands dark on both ({results['bright']:.0f} bright / {results['dark']:.0f} dark)")
