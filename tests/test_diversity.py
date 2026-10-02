#!/usr/bin/env python3
"""v5.4 test: 4 simulated posts in one day - categories must stay diverse."""
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
import bot

arts = bot.fetch_rss_all(bot.RSS_FEEDS[:6])
gn = bot.fetch_google_news(bot.GOOGLE_NEWS_QUERIES[:3])
hn = bot.fetch_hn()
clusters = bot.score_articles(arts + gn + hn)

state = bot._default_state()
cats, titles = [], []
for i in range(4):
    sel = bot.select_article(clusters, state, 9)
    if sel is None:
        print(f"post {i+1}: nothing selected (threshold too high) - OK for a slow day")
        break
    bot._update_state_after_post(state, sel, "hot_take", "arjun")
    cats.append(sel.category)
    titles.append(sel.title[:66])
    print(f"post {i+1}: [{sel.category:12s}] eff={bot.select_article.__defaults__ or ''}{sel.score:5.1f}  {sel.title[:66]}")

print(f"\ncategories posted today: {cats}")
counts = {c: cats.count(c) for c in set(cats)}
print(f"counts: {counts}")
# hard-rule verification: no category > 2, no back-to-back repeats
assert max(counts.values()) <= 2, "daily cap violated"
for a, b in zip(cats, cats[1:]):
    assert a != b, "back-to-back repeat slipped through"
print("PASS: daily diversity holds (max 2/category, no back-to-back repeats)")
