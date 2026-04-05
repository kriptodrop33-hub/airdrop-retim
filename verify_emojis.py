import re

# Mocking the CE and functions from bot.py
CE = {
    "🚀": "5368324170671202286",
    "🔥": "5368324170671202286",
    "🎁": "5386367538735104399",
    "⭐": "5368324170671202286",
    "🥇": "5386367538735104399",
}

def apply_custom_emojis(text: str) -> str:
    for emoji, eid in CE.items():
        if emoji in text:
            text = text.replace(emoji, f'<tg-emoji emoji-id="{eid}">{emoji}</tg-emoji>')
    return text

def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def md_to_html(text: str) -> str:
    text = html_escape(text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = apply_custom_emojis(text)
    return text.strip()

test_text = "🚀 **Binance TR** yeni üye Bonusu! 🎁\n🥇 Kayıt ol\n⭐⭐⭐⭐⭐"
result = md_to_html(test_text)
print(f"Original: {test_text}")
print(f"Result: {result}")

expected_rocket = '<tg-emoji emoji-id="5368324170671202286">🚀</tg-emoji>'
if expected_rocket in result:
    print("SUCCESS: Animated rocket found.")
else:
    print("FAILURE: Animated rocket NOT found.")

if '<b>' in result:
    print("SUCCESS: Bold tag found.")
else:
    print("FAILURE: Bold tag NOT found.")
