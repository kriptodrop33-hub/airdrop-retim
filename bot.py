import os
import re
import asyncio
import logging
import requests
import json
import hashlib as _hashlib
import time as _time
import random as _random
from groq import Groq
from tavily import TavilyClient
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

# ══════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
#  ENV
# ══════════════════════════════════════════════════════════
BOT_TOKEN           = os.environ["BOT_TOKEN"]
GROQ_API_KEY        = os.environ["GROQ_API_KEY"]
TAVILY_API_KEY      = os.environ["TAVILY_API_KEY"]
UNSPLASH_ACCESS_KEY = os.environ["UNSPLASH_ACCESS_KEY"]
ADMIN_CHAT_ID       = int(os.environ["ADMIN_CHAT_ID"])
GROUP_CHAT_ID       = int(os.environ["GROUP_CHAT_ID"])

# ══════════════════════════════════════════════════════════
#  PREMIUM CUSTOM EMOJI SİSTEMİ
# ══════════════════════════════════════════════════════════
_EMOJI_MAP_FILE = "emoji_map.json"

_EMOJI_REGISTRY = {
    "🔥": ("fire", "Ateş"), "💎": ("diamond", "Elmas"), "🚀": ("rocket", "Roket"),
    "⭐": ("star", "Yıldız"), "💰": ("money", "Para"), "⚡": ("warn", "Yıldırım"),
    "✅": ("check", "Onay"), "🎁": ("gift", "Hediye"), "👑": ("crown", "Taç"),
    "📈": ("chart", "Grafik"), "🏆": ("trophy", "Kupa"), "🔔": ("bell", "Çan"),
    "📌": ("pin", "İğne"), "🎉": ("tada", "Kutlama"), "💠": ("gem", "Mücevher"),
    "🥇": ("medal1", "Madalya"), "🗒️": ("note", "Not"), "📅": ("cal", "Takvim"),
    "📢": ("mega", "Megafon"), "➡️": ("arrow", "Ok"), "🟢": ("green", "Yeşil daire"),
    "🟡": ("yellow", "Sarı daire"), "🔴": ("red", "Kırmızı daire"), "🤔": ("think", "Düşünen"),
    "🔗": ("link", "Link"), "📝": ("memo", "Kısa not"), "⚠️": ("warning", "Uyarı"),
    "🪂": ("parachute", "Paraşüt"), "📱": ("mobile", "Mobil"), "👥": ("people", "İnsanlar"),
}

def _load_emoji_map() -> dict:
    if os.path.exists(_EMOJI_MAP_FILE):
        try:
            with open(_EMOJI_MAP_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_emoji_map(data: dict):
    try:
        with open(_EMOJI_MAP_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Emoji map kaydetme hatası: {e}")

def _build_ce() -> dict:
    emoji_map = _load_emoji_map()
    ce = {}
    for emoji_unicode, (key, _) in _EMOJI_REGISTRY.items():
        custom_id = emoji_map.get(emoji_unicode)
        if custom_id:
            ce[key] = f'<tg-emoji emoji-id="{custom_id}">{emoji_unicode}</tg-emoji>'
        else:
            ce[key] = emoji_unicode
    return ce

CE = _build_ce()

def _reload_ce():
    global CE
    CE = _build_ce()

async def _discover_from_sticker_pack(context: ContextTypes.DEFAULT_TYPE, pack_name: str) -> int:
    try:
        sticker_set = await context.bot.get_sticker_set(pack_name)
        emoji_map = _load_emoji_map()
        found = 0
        for sticker in sticker_set.stickers:
            if hasattr(sticker, 'custom_emoji_id') and sticker.custom_emoji_id:
                emoji_unicode = sticker.emoji
                if emoji_unicode and emoji_unicode not in emoji_map:
                    emoji_map[emoji_unicode] = sticker.custom_emoji_id
                    found += 1
        if found > 0:
            _save_emoji_map(emoji_map)
            _reload_ce()
        return found
    except Exception as e:
        logger.error(f"Sticker pack keşif hatası ({pack_name}): {e}")
        return -1

def _extract_custom_emojis_from_message(message) -> int:
    emoji_map = _load_emoji_map()
    found = 0
    if message.entities:
        for entity in message.entities:
            if entity.type and entity.type.value == "custom_emoji":
                custom_id = entity.custom_emoji_id
                if custom_id and message.text:
                    emoji_text = message.text[entity.offset:entity.offset + entity.length]
                    if emoji_text and emoji_text not in emoji_map:
                        emoji_map[emoji_text] = custom_id
                        found += 1
    if message.caption and message.caption_entities:
        for entity in message.caption_entities:
            if entity.type and entity.type.value == "custom_emoji":
                custom_id = entity.custom_emoji_id
                if custom_id:
                    emoji_text = message.caption[entity.offset:entity.offset + entity.length]
                    if emoji_text and emoji_text not in emoji_map:
                        emoji_map[emoji_text] = custom_id
                        found += 1
    if found > 0:
        _save_emoji_map(emoji_map)
        _reload_ce()
    return found

# ══════════════════════════════════════════════════════════
#  KAYIT LINKİ TAKİP SİSTEMİ
# ══════════════════════════════════════════════════════════
_LINK_STORE: dict = {}

def _gen_link_id() -> str:
    lid = _hashlib.md5(str(_random.random()).encode()).hexdigest()[:6].upper()
    return lid if lid not in _LINK_STORE else _gen_link_id()

def register_link(original_url: str, platform: str, category: str = "genel") -> dict:
    lid = _gen_link_id()
    _LINK_STORE[lid] = {
        "id": lid, "url": original_url, "platform": platform, "category": category,
        "created": _time.strftime("%d.%m.%Y %H:%M"), "clicks": 0, "posts": 0,
    }
    return _LINK_STORE[lid]

def record_post_use(link_id: str):
    if link_id in _LINK_STORE:
        _LINK_STORE[link_id]["posts"] += 1

def get_link_stats() -> str:
    if not _LINK_STORE: return "📊 Henüz kayıtlı link yok."
    lines = ["📊 *KAYIT LİNKİ İSTATİSTİKLERİ*\n━━━━━━━━━━━━━━━━━━━━"]
    for lnk in sorted(_LINK_STORE.values(), key=lambda x: x["posts"], reverse=True)[:10]:
        short_url = lnk["url"][:45] + ("..." if len(lnk["url"]) > 45 else "")
        lines.append(f"🔗 *{lnk['platform']}* `[{lnk['id']}]`\n   📤 {lnk['posts']} posta eklendi | 📅 {lnk['created']}\n   🌐 `{short_url}`")
    return "\n\n".join(lines)

def get_link_list_menu() -> InlineKeyboardMarkup:
    if not _LINK_STORE:
        return InlineKeyboardMarkup([[InlineKeyboardButton("➕ Yeni Link Ekle", callback_data="link_add_new"), InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]])
    rows = []
    for lnk in list(_LINK_STORE.values())[-8:]:
        rows.append([InlineKeyboardButton(f"[{lnk['id']}] {lnk['platform']} ({lnk['posts']} post)", callback_data=f"link_use_{lnk['id']}")])
    rows.append([InlineKeyboardButton("➕ Yeni Link Ekle", callback_data="link_add_new"), InlineKeyboardButton("🗑️ Temizle", callback_data="link_clear")])
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
    return InlineKeyboardMarkup(rows)

# ══════════════════════════════════════════════════════════
#  FIRSAT TAKİP & ARŞİV SİSTEMİ
# ══════════════════════════════════════════════════════════
_DATA_FILE = "bot_data.json"

def _load_data() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f: return json.load(f)
        except Exception: pass
    return {"tracked": {}, "posts": [], "blacklist": []}

def _save_data(data: dict):
    try:
        with open(_DATA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e: logger.error(f"Veri kaydetme hatası: {e}")

def track_opportunity(name: str, deadline: str, analysis: str, post: str):
    data = _load_data()
    tid = str(int(_time.time()))
    data["tracked"][tid] = {"id": tid, "name": name, "deadline": deadline, "analysis": analysis[:500], "post": post, "added": _time.strftime("%d.%m.%Y %H:%M"), "warned": False}
    _save_data(data)
    return tid

def get_tracked() -> list: return list(_load_data()["tracked"].values())

def remove_tracked(tid: str):
    data = _load_data()
    data["tracked"].pop(tid, None)
    _save_data(data)

def save_post_archive(project: str, post: str, fmt: str):
    data = _load_data()
    entry = {"id": str(int(_time.time())), "project": project, "post": post, "fmt": fmt, "date": _time.strftime("%d.%m.%Y %H:%M")}
    data["posts"].insert(0, entry)
    data["posts"] = data["posts"][:30]
    _save_data(data)
    return entry["id"]

def get_post_archive() -> list: return _load_data()["posts"]
def get_blacklist() -> list: return _load_data()["blacklist"]

def add_to_blacklist(name: str):
    data = _load_data()
    if name.lower() not in [b.lower() for b in data["blacklist"]]: data["blacklist"].append(name); _save_data(data)

def is_blacklisted(name: str) -> bool:
    return any(name.lower() in b.lower() or b.lower() in name.lower() for b in _load_data()["blacklist"])

def check_deadlines() -> list:
    data = _load_data()
    alerts = []
    today = datetime.now()
    for tid, opp in data["tracked"].items():
        if opp.get("warned"): continue
        dl = opp.get("deadline", "")
        if not dl or dl in ("Belirtilmemiş", "Bulunamadı", ""): continue
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dl_dt = datetime.strptime(dl.strip(), fmt)
                days_left = (dl_dt - today).days
                if 0 <= days_left <= 3: alerts.append({**opp, "days_left": days_left}); data["tracked"][tid]["warned"] = True
                break
            except Exception: pass
    _save_data(data)
    return alerts

def verify_and_score(name: str, initial_data: dict) -> dict:
    extra_queries = [f"{name} legit scam review reddit 2026", f"{name} official website social media verified"]
    extra_results = []
    for q in extra_queries: extra_results.extend(deep_search(q, max_results=3))
    extra_text = "\n\n".join([f"[DOĞRULAMA {i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:600]}" for i, r in enumerate(extra_results[:6])])
    combined_raw = initial_data.get("raw", "") + "\n\n=== ÇAPRAZ DOĞRULAMA SONUÇLARI ===\n" + extra_text

    score_system = """Sen bir kripto fırsat doğrulama uzmanısın.
Verilen ham veriyi analiz ederek GÜVENİLİRLİK SKORU hesapla.
SKOR KRİTERLERİ (0-100):
+20: Resmi web sitesi veya sosyal medya bulundu
+20: Bilinen borsa/proje (Binance, OKX, Bybit, Arbitrum vb.)
+15: Birden fazla bağımsız kaynakta aynı bilgi
+15: Net ödül miktarı ve son tarih belirtilmiş
+10: Reddit/Twitter'da pozitif yorumlar var
-20: Yalnızca 1 kaynak bulunan bilinmez proje
-25: "Scam", "fraud", "fake" kelimesi geçiyor
-30: Kaynak bulunamadı veya çok az bilgi var
-20: Son tarihi geçmiş kampanya
ÇIKTI FORMAT (kesinlikle bu JSON yapısında):
{"score": 75, "verdict": "GÜVENİLİR / ŞÜPHELİ / RİSKLİ", "reasons": ["neden 1", "neden 2"], "warning": "varsa uyarı metni, yoksa boş string"}
SADECE JSON döndür, başka hiçbir şey yazma."""

    result_str = ai(score_system, f"Proje: {name}\n\n{combined_raw[:5000]}", tokens=400, temp=0.1)
    try:
        json_match = re.search(r"\{.*\}", result_str, re.DOTALL)
        score_data = json.loads(json_match.group()) if json_match else {"score": 50, "verdict": "BELİRSİZ", "reasons": [], "warning": ""}
    except Exception:
        score_data = {"score": 50, "verdict": "BELİRSİZ", "reasons": [], "warning": ""}
    score_data["extra_raw"] = extra_text
    return score_data

def format_score_badge(score: int, verdict: str) -> str:
    if score >= 75: dot = CE.get("green", "🟢")
    elif score >= 50: dot = CE.get("yellow", "🟡")
    else: dot = CE.get("red", "🔴")
    return f"{dot} {verdict} ({score}/100)"

groq_client = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

# ══════════════════════════════════════════════════════════
#  ADMIN GUARD
# ══════════════════════════════════════════════════════════
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type in ("group", "supergroup", "channel"): return
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def admin_only_callback(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type in ("group", "supergroup", "channel"):
            await update.callback_query.answer(); return
        if update.effective_user.id != ADMIN_CHAT_ID:
            await update.callback_query.answer(); return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

# ══════════════════════════════════════════════════════════
#  GROQ — AI ÜRETME
# ══════════════════════════════════════════════════════════
_GROQ_MODELS = ["llama-3.3-70b-versatile", "llama3-70b-8192", "llama-3.1-8b-instant", "gemma2-9b-it"]

def ai(system: str, user: str, tokens: int = 1800, temp: float = 0.75) -> str:
    last_err = None
    for model in _GROQ_MODELS:
        try:
            r = groq_client.chat.completions.create(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=tokens, temperature=temp)
            return r.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            if any(k in str(e).lower() for k in ("rate_limit", "quota", "429", "503", "overloaded", "capacity")): continue
            return "❌ AI yanıt üretemedi."
    return "❌ AI yanıt üretemedi (tüm modeller denendi)."

# ══════════════════════════════════════════════════════════
#  TAVILY — DERIN ARAMA
# ══════════════════════════════════════════════════════════
def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")})
        return results
    except Exception: return []

def _httpx_scrape(url: str) -> str:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200: return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', r.text))[:3000]
    except Exception: pass
    return ""

_tavily_quota_ok = True

def deep_search(query: str, max_results: int = 5) -> list[dict]:
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try: return tavily_client.search(query=query, search_depth="basic", max_results=max_results, include_answer=False).get("results", [])
        except Exception as e:
            if "432" in str(e) or "quota" in str(e).lower() or "rate" in str(e).lower(): _tavily_quota_ok = False
            else: return _ddg_search(query, max_results)
    return _ddg_search(query, max_results)

def fetch_url_content(url: str) -> str:
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            r = tavily_client.extract(urls=[url])
            if r.get("results"): return r["results"][0].get("raw_content", "")[:3000]
        except Exception as e:
            if "432" in str(e) or "quota" in str(e).lower(): _tavily_quota_ok = False
    return _httpx_scrape(url)

def is_url(text: str) -> bool: return bool(re.match(r"https?://\S+", text.strip()))

# ══════════════════════════════════════════════════════════
#  UNSPLASH — GÖRSEL
# ══════════════════════════════════════════════════════════
def get_image(query: str = "cryptocurrency airdrop") -> str | None:
    try:
        r = requests.get("https://api.unsplash.com/search/photos", params={"query": query, "per_page": 6, "orientation": "landscape", "client_id": UNSPLASH_ACCESS_KEY}, timeout=10)
        results = r.json().get("results", [])
        if results: return random.choice(results[:4])["urls"]["regular"]
    except Exception: pass
    return None

# ══════════════════════════════════════════════════════════
#  ARAŞTIRMA FONKSİYONLARI
# ══════════════════════════════════════════════════════════
def research_airdrop_by_name(name: str) -> dict:
    queries = [f"{name} airdrop bonus reward active {datetime.now().strftime('%B %Y')} how to claim", f"{name} new user bonus reward tasks eligibility {datetime.now().year}", f"{name} kripto kampanya kayıt bonusu aktif"]
    all_results = []
    for q in queries:
        all_results.extend(deep_search(q, max_results=4))
        if len(all_results) >= 10: break
    seen_urls, unique = set(), []
    for item in all_results:
        if item.get("url") not in seen_urls: seen_urls.add(item.get("url")); unique.append(item)
    raw_text = "\n\n".join([f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:1200]}" for i, r in enumerate(unique[:8])])
    if unique:
        full = fetch_url_content(unique[0].get("url", ""))
        if full: raw_text = f"=== TAM SAYFA ({unique[0].get('url','')}) ===\n{full[:2500]}\n\n=== DİĞER KAYNAKLAR ===\n{raw_text}"
    return {"name": name, "raw": raw_text, "sources": unique[:8]}

def research_airdrop_by_url(url: str) -> dict:
    content = fetch_url_content(url)
    name_hint = ai("Extract the project or airdrop name from the text. Reply with ONLY the name, nothing else.", content[:500] if content else url, tokens=50, temp=0.1)
    extra = deep_search(f"{name_hint} airdrop claim guide tasks {datetime.now().year} active", max_results=6)
    extra_text = "\n\n".join([f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:400]}" for i, r in enumerate(extra[:6])])
    return {"name": name_hint.strip(), "raw": f"=== SAYFA İÇERİĞİ ===\n{content}\n\n=== EK KAYNAKLAR ===\n{extra_text}", "sources": extra[:6], "url": url}

def analyze_research(data: dict) -> str:
    system = """Sen deneyimli bir kripto kazanım fırsatı araştırmacısısın.
Görevin: HAM VERİDEN SADECE gerçek, belgeli bilgileri çıkarmak.
KRITIK KURALLAR:
1. SADECE ham veride geçen bilgileri yaz — asla tahmin/uydurma yapma
2. Rakamlar ve tarihler KAYNAK metinden kopyalanacak
3. Ham veride yoksa: "Bulunamadı" yaz
4. Kampanya tarihi eskiyse "SONA ERMİŞ OLABİLİR" ekle
5. Kaynak URL-lerini mutlaka yaz
FORMAT:
📌 PLATFORM/PROJE: [adı ve ne olduğu]
🏷 FIRSATIN TÜRÜ: [borsa bonusu / airdrop / kampanya / referral]
💰 ÖDÜL MİKTARI: [kaynaktaki EXACT rakam]
👥 KİMLER KATILABİLİR: [yeni kullanıcı / mevcut / herkes]
📋 ADIMLAR:
  1. [kaynak metindeki adım] — [ödül miktarı]
  2. [kaynak metindeki adım] — [ödül miktarı]
  3. devam...
💎 TOPLAM: [varsa]
⏰ SON TARİH: [varsa — yoksa Belirtilmemiş]
🔗 KATILIM LİNKİ: [kaynaktaki URL]
⭐ GÜVENİLİRLİK: [1-5 yıldız + neden]
⚠️ UYARI: [KYC / min yatırım / ülke kısıtı / SONA ERMİŞ OLABİLİR]
Türkçe yaz. Uydurma YAPMA."""
    return ai(system, f"Proje: {data['name']}\n\n{data['raw']}", tokens=2500)

OPPORTUNITY_QUERIES = [
    ("bonus", "kripto borsa yeni üye kampanyası kayıt ödülü Mayıs 2026 USDT TL aktif devam ediyor"),
    ("bonus", "crypto exchange new user sign up bonus reward USDT May 2026 active ongoing"),
    ("bonus", "crypto exchange welcome bonus deposit reward free USDT 2026 new users"),
    ("referral", "crypto referral program earn USDT invite friends 2026 active ongoing"),
    ("referral", "kripto borsa arkadaş davet et kazan referral ödülü Mayıs 2026"),
    ("kampanya", "crypto exchange trading competition reward prize USDT May 2026 active"),
    ("kampanya", "kripto borsa işlem kampanyası ödül havuzu 2026 devam ediyor"),
    ("sosyal", "telegram crypto task reward earn token USDT 2026 active"),
    ("sosyal", "crypto project telegram task reward airdrop May 2026 ongoing"),
    ("airdrop", "crypto airdrop claim May 2026 active free no investment required ongoing"),
    ("airdrop", "galxe zealy intract quest airdrop reward May 2026 active not expired"),
]
CATEGORY_DEFS = {
    "hepsi": ("🌐 Hepsi", None), "bonus": ("🎁 Borsa Bonusu", ["bonus"]), "referral": ("👥 Referral", ["referral"]),
    "kampanya": ("🏆 Kampanya", ["kampanya"]), "sosyal": ("📱 Sosyal Görev", ["sosyal"]), "airdrop": ("🪂 Airdrop", ["airdrop"]),
}

def category_filter_menu() -> InlineKeyboardMarkup:
    rows = []
    keys = list(CATEGORY_DEFS.keys())
    for i in range(0, len(keys), 3): rows.append([InlineKeyboardButton(CATEGORY_DEFS[k][0], callback_data=f"cat_{k}") for k in keys[i:i+3]])
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def run_opportunity_search(cats: list[str] | None = None) -> list[dict]:
    seen_urls, results, seen_cats = set(), [], set()
    for category, query in OPPORTUNITY_QUERIES:
        if cats and category not in cats: continue
        if category in seen_cats: continue
        seen_cats.add(category)
        hits = deep_search(query, max_results=4)
        for r in hits:
            url = r.get("url", "")
            if url not in seen_urls: seen_urls.add(url); results.append({"category": category, "title": r.get("title", ""), "url": url, "content": r.get("content", "")[:1200]})
        if len(results) >= 20: break
    return results

def scan_active_airdrops(cats: list[str] | None = None) -> str:
    raw_results = run_opportunity_search(cats=cats)
    if not raw_results: return "❌ Veri çekilemedi. Lütfen tekrar deneyin."
    by_cat = {}
    for r in raw_results: by_cat.setdefault(r["category"], []).append(r)
    cat_labels = {"bonus": "🎁 BORSA KAYIT / YENİ KULLANICI BONUSU", "referral": "👥 REFERRAL / DAVET KAMPANYASI", "kampanya": "🏆 İŞLEM / TRADİNG KAMPANYASI", "sosyal": "📱 TELEGRAM / SOSYAL GÖREV ÖDÜLÜ", "airdrop": "🪂 AIRDROP"}
    combined_raw = ""
    for cat, items in by_cat.items():
        combined_raw += f"\n\n{'='*40}\n{cat_labels.get(cat, cat.upper())}\n{'='*40}\n"
        for item in items[:3]: combined_raw += f"Başlık: {item['title']}\nURL: {item['url']}\nİçerik: {item['content']}\n---\n"
    system = f"""Sen kripto para kazanım fırsatları araştıran uzman bir analistsin.
Bugünün tarihi: {datetime.now().strftime('%d %B %Y')}. SADECE bu tarihten sonraki veya devam eden aktif kampanyaları listele.
ÖNCELİK SIRASI:
1. 🎁 Borsa kayıt bonusu — yeni üye ol, az emekle somut TL/USDT kazan
2. 👥 Referral kampanyası — davet et, komisyon kazan
3. 🏆 Trading kampanyası — işlem yap, ödül al
4. 📱 Telegram/sosyal görev — kolay görevler, token kazan
5. 🪂 Airdrop — form doldur, sosyal takip, token kazan
KESİN REDDET — bunları ASLA listele:
❌ Sona erme tarihi bugünden önce olan kampanyalar
❌ 2024 ve öncesi tarihli kampanyalar
❌ Validator/node çalıştırma gerektiren
❌ 1000$+ yatırım zorunlu olanlar
❌ Rakamı belirsiz/eksik veya sadece 'yakında' diyen projeler
❌ Scam/fraud/fake geçen projeler
FORMAT (HER fırsat için AYNEN bu yapıyı kullan):
━━━━━━━━━━━━━━━━━━━━━━
🎁 [BORSA/PLATFORM ADI]
┣ 💰 Ödül: [EXACT rakam — örn: 2600 TL / 50 USDT / 100 TOKEN]
┣ 🏦 Tür: [borsa bonusu / airdrop / referral / görev]
┣ 👥 Kimler: [yeni kullanıcı / mevcut / herkes]
┣ 📋 Adımlar:
┃  1️⃣ [adım] → [ödül]
┃  2️⃣ [adım] → [ödül]
┃  3️⃣ [adım] → [ödül]
┣ ⏰ Son Tarih: [tarih / süre / devam ediyor]
┣ ⭐ Güvenilirlik: [⭐⭐⭐⭐⭐]
┗ 🔗 [kayıt/katılım URL]
KURALLAR:
- Somut rakam yaz: "50 USDT", "2600 TL", "500 TOKEN"
- Kaynak veride olmayan rakamı YAZMA
- 4-6 kaliteli fırsat listele, gereksiz olanları atla
- Türkçe yaz, net ve anlaşılır ol"""
    return ai(system, combined_raw[:8000], tokens=4000)

# ══════════════════════════════════════════════════════════
#  POST OLUŞTURMA
# ══════════════════════════════════════════════════════════
def get_post_system() -> str:
    return f"""Sen KriptoDropTR Telegram kanalı için profesyonel airdrop postları yazıyorsun.
HTML parse_mode kullanılıyor. Çıktı SADECE HTML olacak, Markdown (*,_,`) kullanma.
⛔ KESİN YASAKLAR:
1. Analizde OLMAYAN rakam, kod, URL yazma
2. Referral/promo kodu ASLA yazma
3. Hashtag (#) yasak
4. Şablon metnini ("yoksa sil" gibi) posta bırakma
5. Link için sadece: [🔗 TIKLA 🖊]
6. Markdown kullanma — sadece HTML: <b>kalın</b>
7. Son satır olarak ASLA skor ekleme — skor ayrıca eklenecek
KISALTMA KURALLARI:
- Ödül yoksa → "Kampanya ödülü"
- Son tarih yoksa → o satırı komple sil
- Adım yoksa → o adımı komple sil
AYNEN bu yapıyı kullan (tg-emoji taglarını aynen koru):
{CE['trophy']} <b>[PLATFORM ADI] Yeni Üye Airdrop {CE['tada']}</b>
{CE['medal1']} <b><u>[PLATFORM ADI] Yeni Üyeler için [ÖDÜL MİKTARI]</u></b>
bonus kazanma fırsatı 🤔
——————————————————
{CE['note']} <b>YAPMAN GEREKENLER:</b>
①  Bağlantıya tıkla kayıt ol ve hesabını doğrula (KYC)
②  [adım 2]
③  [adım 3]
④  [adım 4 — yoksa sil]
{CE['arrow']} Hemen Kaydol: 🔗 [🔗 TIKLA 🖊] 🔗
{CE['arrow']} Etkinlik sayfası: 🔗 [🔗 TIKLA 🖊] 🔗
——————————————————
Görev zorluğu: [Kolay/Orta/Zor]
Ödül miktarı:  <b><u>[rakam]</u></b>
Airdrop puanı: {CE['star']} {CE['star']} {CE['star']} {CE['star']} {CE['star']}
——————————————————
{CE['cal']} <b><u>Son gün [tarih — yoksa bu satırı sil]</u></b>
<b>NOT:</b> [varsa önemli not, yoksa sil]
——————————————————
{CE['fire']} Daha fazla airdrop için duyuru kanalını pinle {CE['tada']}
{CE['mega']} @kriptodropduyuru
{CE['gift']} @kriptodroptr"""

def get_post_system_short() -> str:
    return f"""KriptoDropTR için kısa airdrop postu yaz.
⛔ Uydurma rakam, referral kodu, hashtag yasak.
✅ HTML: <b>kalın</b> | Link: [🔗 TIKLA 🖊] | Maks 400 karakter | Türkçe
YAPI:
{CE['rocket']} <b>[PLATFORM] — [BAŞLIK]!</b>
{CE['money']} <b>Ödül:</b> [rakam]
① [adım 1]
② [adım 2]
{CE['arrow']} [🔗 TIKLA 🖊]
{CE['mega']} @kriptodropduyuru | {CE['gift']} @kriptodroptr"""

def get_post_system_summary() -> str:
    return f"""KriptoDropTR için 2-3 satır airdrop özeti yaz.
⛔ Uydurma rakam, referral kodu, hashtag yasak.
HTML: <b>kalın</b> | Link: [🔗 TIKLA 🖊] | Türkçe
FORMAT:
{CE['rocket']} <b>[PLATFORM]</b> — [ödül] kazan! [1 cümle nasıl]. {CE['arrow']} [🔗 TIKLA 🖊]
{CE['mega']} @kriptodropduyuru {CE['gift']} @kriptodroptr"""

def _build_prompt(analysis: str, project_name: str) -> str:
    return (f"Platform/Proje: {project_name}\nBugünün tarihi: {datetime.now().strftime('%d.%m.%Y')}\n\n=== ARAŞTIRMA ANALİZİ ===\n{analysis}\n\n"
            f"=== KESİN KURALLAR ===\n1. SADECE yukarıdaki analizde AÇIKÇA geçen rakamları kullan\n2. Referral kodu, promo kodu, davet kodu YAZMA — analizde varsa bile\n3. Bir satırı dolduracak bilgi yoksa o satırı komple SİL\n4. Adımları analizden al, kendin adım uydurma\n5. [🔗 TIKLA 🖊] placeholder'ını koru — URL yazma\n6. Kampanya tarihi eski görünse bile POSTU YAZ — NOT satırına 'Kampanya durumu doğrulanamamıştır, katılmadan önce kontrol ediniz' ekle\n7. ASLA '&', '<', '>' karakterlerini metin içinde kullanma! Özel karakterler yerine 've', 'altında' kelimelerini kullan.\n8. <tg-emoji> taglarını kesinlikle OLDUĞU GİBİ koru.")

def _inject_premium_emojis(text: str) -> str:
    emoji_map = _load_emoji_map()
    if not emoji_map: return text
    for emoji_unicode, custom_id in emoji_map.items():
        if not custom_id: continue
        tg_tag = f'<tg-emoji emoji-id="{custom_id}">{emoji_unicode}</tg-emoji>'
        if tg_tag in text: continue
        if emoji_unicode in text:
            _protected = []
            def _protect(m): _protected.append(m.group(0)); return f"\x00PROT_{len(_protected)-1}\x00"
            text_protected = re.sub(r'<tg-emoji\s+emoji-id="[^"]+">[^<]*</tg-emoji>', _protect, text)
            text_protected = text_protected.replace(emoji_unicode, tg_tag)
            def _unprotect(m): return _protected[int(m.group(1))] if int(m.group(1)) < len(_protected) else ""
            text = re.sub(r'\x00PROT_(\d+)\x00', _unprotect, text_protected)
    return text

def build_post(analysis: str, project_name: str, fmt: str = "long", score: int = None, verdict: str = None) -> str:
    prompt = _build_prompt(analysis, project_name)
    if fmt == "short": raw = ai(get_post_system_short(), prompt, tokens=600, temp=0.3)
    elif fmt == "summary": raw = ai(get_post_system_summary(), prompt, tokens=250, temp=0.3)
    else: raw = ai(get_post_system(), prompt, tokens=1400, temp=0.3)
    raw = md_to_html(raw)
    raw = _inject_premium_emojis(raw)
    if fmt == "long" and score is not None:
        verdict_str = verdict or "GÜVENİLİR"
        dot = CE.get("green", "🟢") if score >= 75 else CE.get("yellow", "🟡") if score >= 50 else CE.get("red", "🔴")
        raw = raw.rstrip() + f"\n\n——————————————————\nSkor: {dot} <b>{verdict_str}</b> ({score}/100)"
    return raw

# ══════════════════════════════════════════════════════════
#  TELEGRAM HELPERS
# ══════════════════════════════════════════════════════════
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Fırsat Tara", callback_data="scan_menu"), InlineKeyboardButton("✍️ Post Oluştur", callback_data="manual_post")],
        [InlineKeyboardButton("📁 Post Arşivi", callback_data="post_archive"), InlineKeyboardButton("📌 Takip Listesi", callback_data="tracked_list")],
        [InlineKeyboardButton("🚫 Kara Liste", callback_data="blacklist_view"), InlineKeyboardButton("🔗 Linklerimi Yönet", callback_data="link_manage")],
        [InlineKeyboardButton("🔄 Yeni Araştırma", callback_data="new_research"), InlineKeyboardButton("❓ Yardım", callback_data="help")],
        [InlineKeyboardButton("🎨 Premium Emoji", callback_data="emoji_menu")],
    ])

def post_actions(has_link: bool = False, fmt: str = "long") -> InlineKeyboardMarkup:
    return post_actions_extended(has_link=has_link, fmt=fmt, score=None)

def post_actions_extended(has_link: bool = False, fmt: str = "long", score=None) -> InlineKeyboardMarkup:
    link_label = "✅ Link Eklendi" if has_link else "🔗 Link Ekle"
    fmt_long = "📄 Uzun ●" if fmt == "long" else "📄 Uzun"
    fmt_short = "📝 Kısa ●" if fmt == "short" else "📝 Kısa"
    fmt_summary = "⚡ Özet ●" if fmt == "summary" else "⚡ Özet"
    rows = [
        [InlineKeyboardButton(fmt_long, callback_data="fmt_long"), InlineKeyboardButton(fmt_short, callback_data="fmt_short"), InlineKeyboardButton(fmt_summary, callback_data="fmt_summary")],
        [InlineKeyboardButton(link_label, callback_data="add_link")],
        [InlineKeyboardButton("✏️ Postu Düzenle", callback_data="edit_post_inline")],
        [InlineKeyboardButton("📢 Gruba Gönder", callback_data="send_text"), InlineKeyboardButton("🖼️ Görsel ile", callback_data="send_photo")],
        [InlineKeyboardButton("📌 Fırsatı Takibe Al", callback_data="track_opp"), InlineKeyboardButton("🚫 Kara Listeye", callback_data="blacklist_opp")],
        [InlineKeyboardButton("♻️ Yenile", callback_data="regen_post"), InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
    ]
    return InlineKeyboardMarkup(rows)

def md_to_html(text: str) -> str:
    _tg_emoji_store = []
    def _save_tg_emoji(m): _tg_emoji_store.append(m.group(0)); return f"__TGEMOJI_{len(_tg_emoji_store) - 1}__"
    text = re.sub(r'<tg-emoji\s+emoji-id="[^"]+">[^<]*</tg-emoji>', _save_tg_emoji, text)
    text = re.sub(r'(?m)^#+\s.*$', "", text)
    text = re.sub(r'#\w+', "", text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    text = re.sub(r'\n{3,}', "\n\n", text)
    def _restore_tg_emoji(m): return _tg_emoji_store[int(m.group(1))] if int(m.group(1)) < len(_tg_emoji_store) else ""
    text = re.sub(r'__TGEMOJI_(\d+)__', _restore_tg_emoji, text)
    return text.strip()

def safe_md(text: str) -> str: return md_to_html(text)

# ══════════════════════════════════════════════════════════
#  KOMUTLAR
# ══════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ("group", "supergroup", "channel"): return
    if update.effective_user.id != ADMIN_CHAT_ID: return
    context.user_data.clear()
    await update.message.reply_text("🤖 <b>AIRDROP BOT</b> — Admin Paneli\n\n━━━━━━━━━━━━━━━━━━━━\n🔍 <b>Airdrop Tara</b> → İnterneti tara, aktif airdropları listele\n✍️ <b>Post Oluştur</b> → Airdrop adı veya link at, derin araştır\n📢 <b>Gruba Gönder</b> → Hazır postu gruba gönder\n━━━━━━━━━━━━━━━━━━━━\n\n💡 <i>Airdrop adı veya linki direkt yazabilirsin.</i>", parse_mode=ParseMode.HTML, reply_markup=main_menu())

@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 *KOMUTLAR*\n\n/start — Ana menü\n/scan — İnterneti tara, aktif airdropları listele\n/post `[isim]` — İsme göre araştır & post oluştur\n/emoji_init — Premium emojileri yükle\n/emoji_test — Emoji test et\n\n━━━━━━━━━━━━━━━━━━━━\n💡 *Direkt mesaj:*\n• Bir URL at → sayfa derin araştırılır\n• Airdrop adı yaz → derin araştırma başlar\n━━━━━━━━━━━━━━━━━━━━", parse_mode=ParseMode.HTML)

@admin_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 *Hangi kategoriyi tarayalım?*\n\n_Hepsi → tüm kategoriler taranır (daha uzun sürer)_", parse_mode=ParseMode.HTML, reply_markup=category_filter_menu())

@admin_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: await update.message.reply_text("⚠️ Kullanım: `/post [airdrop adı]`\nÖrnek: `/post Arbitrum`", parse_mode=ParseMode.HTML); return
    await _do_research(update, context, " ".join(context.args))

@admin_only
async def cmd_sendgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_to_group(update, context, with_photo=False)

@admin_only
async def cmd_emoji_init(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔄 <b>Bilinen premium paketler taranıyor...</b>", parse_mode=ParseMode.HTML)
    known_packs = ["AnimatedEmojis", "EmojiAnimatedEmojis", "PremiumEmojiPack", "TelegramAnimatedEmojis", "EmojiOne"]
    total_found, results = 0, []
    for pack_name in known_packs:
        found = await _discover_from_sticker_pack(context, pack_name)
        if found >= 0: results.append(f"✅ {pack_name}: {found} yeni"); total_found += found
        else: results.append(f"❌ {pack_name}: bulunamadı")
    total = len([v for v in _load_emoji_map().values() if v])
    await msg.edit_text(f"🔄 <b>Tarama tamamlandı!</b>\n\n" + "\n".join(results) + f"\n\n📊 Yeni: <b>{total_found}</b> | Toplam: <b>{total}</b>", parse_mode=ParseMode.HTML)

@admin_only
async def cmd_emoji_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _reload_ce()
    lines = ["🧪 <b>PREMIUM EMOJİ TEST</b>\n━━━━━━━━━━━━━━━━━━━━\n"]
    for key, value in CE.items(): lines.append(f"{key}: {value} {'✓ Premium' if 'tg-emoji' in value else '✗ Düz'}")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode=ParseMode.HTML)

# ══════════════════════════════════════════════════════════
#  MESAJ İŞLEYİCİ — URL veya Airdrop Adı
# ══════════════════════════════════════════════════════════
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ("group", "supergroup", "channel"): return
    if update.effective_user.id != ADMIN_CHAT_ID: return
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting_for")

    if waiting == "link_add":
        context.user_data["waiting_for"] = None
        parts = [p.strip() for p in text.split("|", 1)]
        if len(parts) != 2 or not parts[1].startswith("http"):
            await update.message.reply_text("⚠️ Format hatalı. Şöyle yaz:\n`PLATFORM_ADI | https://link.com`", parse_mode=ParseMode.HTML); return
        lnk = register_link(parts[1], parts[0])
        await update.message.reply_text(f"✅ *Link kaydedildi!*\n\n🔑 ID: `{lnk['id']}`\n🏦 Platform: *{lnk['platform']}*\n🌐 URL: `{lnk['url'][:60]}`", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    if waiting == "add_link":
        context.user_data["waiting_for"] = None
        post = context.user_data.get("last_post", "")
        updated = post.replace("[🔗 TIKLA 🖊]", text.strip())
        context.user_data["final_post"] = updated
        context.user_data["has_link"] = True
        platform = context.user_data.get("last_post_platform", "crypto")
        await update.message.reply_text("✅ *Link eklendi!* Görsel aranıyor...", parse_mode=ParseMode.HTML)
        img_url = get_image(f"{platform} crypto")
        caption = updated[:1024] if len(updated) > 1024 else updated
        if img_url:
            try: await update.message.reply_photo(photo=img_url, caption=caption, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True))
            except: await update.message.reply_text(f"📣 <b>GÜNCEL POST:</b>\n\n{updated}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True))
        else: await update.message.reply_text(f"📣 <b>GÜNCEL POST:</b>\n\n{updated}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True))
        return

    if waiting in ("edit_post", "edit_post_inline"):
        context.user_data["waiting_for"] = None
        context.user_data["final_post"] = text
        context.user_data["last_post"] = text
        fmt = context.user_data.get("post_fmt","long")
        await update.message.reply_text(f"✅ <b>Post güncellendi!</b>\n\n━━━━━━━━━━━━━━━━━━━━\n{safe_md(text)}\n━━━━━━━━━━━━━━━━━━━━", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=context.user_data.get("has_link",False), fmt=fmt)); return

    if waiting == "track_deadline":
        context.user_data["waiting_for"] = None
        deadline = text.strip()
        project_name = context.user_data.get("last_project","?")
        analysis = context.user_data.get("last_analysis","")
        post = context.user_data.get("final_post","")
        tid = track_opportunity(project_name, deadline, analysis, post)
        await update.message.reply_text(f"📌 <b>{project_name}</b> takibe alındı!\n⏰ Son Tarih: <code>{deadline}</code>\n🔔 3 gün kala hatırlatma gelecek.", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    await _do_research(update, context, text)

async def _do_research(update: Update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    if is_blacklisted(input_text):
        await update.effective_message.reply_text(f"🚫 <b>{input_text}</b> kara listede!\nBu proje daha önce sahte/şüpheli olarak işaretlendi.", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return
    msg = await update.effective_message.reply_text(f"🔬 <b>Araştırma başladı:</b> <code>{input_text[:60]}</code>\n⏳ 30-60 saniye sürebilir...", parse_mode=ParseMode.HTML)
    await update.effective_chat.send_action(ChatAction.TYPING)

    if is_url(input_text):
        await msg.edit_text("🔗 <b>URL içeriği çekiliyor...</b>", parse_mode=ParseMode.HTML)
        data = await asyncio.to_thread(research_airdrop_by_url, input_text)
    else:
        await msg.edit_text(f"🔍 <b>\'{input_text}\' araştırılıyor...</b>\n<i>Çoklu sorgu çalışıyor...</i>", parse_mode=ParseMode.HTML)
        data = await asyncio.to_thread(research_airdrop_by_name, input_text)

    project_name = data.get("name", input_text)
    await msg.edit_text("🔁 <b>Çoklu kaynak doğrulanıyor...</b>\n<i>Güvenilirlik skoru hesaplanıyor...</i>", parse_mode=ParseMode.HTML)
    score_data = await asyncio.to_thread(verify_and_score, project_name, data)
    score = score_data.get("score", 50)
    verdict = score_data.get("verdict", "BELİRSİZ")
    reasons = score_data.get("reasons", [])
    warning = score_data.get("warning", "")
    badge = format_score_badge(score, verdict)

    context.user_data["last_score"] = score_data
    context.user_data["last_project"] = project_name

    await msg.edit_text("🤖 <b>AI analizi yapılıyor...</b>", parse_mode=ParseMode.HTML)
    enriched_data = data.copy()
    enriched_data["raw"] = data.get("raw","") + "\n\n=== DOĞRULAMA ===\n" + score_data.get("extra_raw","")
    analysis = await asyncio.to_thread(analyze_research, enriched_data)

    if analysis.startswith("❌"):
        await msg.edit_text(f"⚠️ <b>Groq API şu an yanıt veremiyor.</b>\n\nTüm modeller denendi, yanıt alınamadı.\n• Groq günlük kotanızı kontrol edin\n• 1-2 dakika bekleyip tekrar deneyin\n\n<i>Proje: {project_name} | Skor: {badge}</i>", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔄 Tekrar Dene", callback_data="new_research"), InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]])); return

    context.user_data["last_analysis"] = analysis
    await msg.edit_text("✍️ <b>Post yazılıyor...</b>", parse_mode=ParseMode.HTML)
    post = await asyncio.to_thread(build_post, analysis, project_name, "long", score, verdict)
    
    context.user_data["last_post"] = post
    context.user_data["final_post"] = post
    context.user_data["last_post_platform"] = project_name
    context.user_data["has_link"] = False
    context.user_data["post_fmt"] = "long"
    save_post_archive(project_name, post, "long")

    reasons_text = "\n".join([f"  • {r}" for r in reasons]) if reasons else "  • Bilgi yetersiz"
    score_msg = f"📊 <b>GÜVENİLİRLİK RAPORU — {project_name.upper()}</b>\n━━━━━━━━━━━━━━━━━━━━\nSkor: <b>{badge}</b>\n\n📋 <b>Değerlendirme:</b>\n{reasons_text}\n"
    if warning: score_msg += f"\n⚠️ <b>Uyarı:</b> {warning}\n"
    safe_analysis = analysis.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    score_msg += f"\n{safe_analysis}"
    if len(score_msg) > 4000: score_msg = score_msg[:3990] + "\n<i>...kırpıldı</i>"
    await msg.edit_text(score_msg, parse_mode=ParseMode.HTML)

    post_preview = f"━━━━━━━━━━━━━━━━━━━━\n📣 <b>HAZIRLANAN POST:</b>\n\n{post}\n\n━━━━━━━━━━━━━━━━━━━━\nSkor: {badge}"
    if len(post_preview) > 4096: post_preview = post_preview[:4086] + "..."
    try:
        await update.effective_message.reply_text(post_preview, parse_mode=ParseMode.HTML, reply_markup=post_actions_extended(has_link=False, fmt="long", score=score))
    except Exception as e:
        logger.error(f"Post önizleme HTML hatası: {e}")
        no_emoji_tags = re.sub(r'</?tg-emoji[^>]*>', '', post_preview)
        try:
            await update.effective_message.reply_text(no_emoji_tags, parse_mode=ParseMode.HTML, reply_markup=post_actions_extended(has_link=False, fmt="long", score=score))
        except Exception as e2:
            await update.effective_message.reply_text("❌ Post önizleme gösterilemiyor.", reply_markup=main_menu())

async def _send_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, with_photo: bool = False):
    post = context.user_data.get("final_post", "")
    if not post: await update.effective_message.reply_text("❌ Gönderilecek post yok!"); return
    try:
        if with_photo:
            platform = context.user_data.get("last_post_platform", "crypto")
            img_url = get_image(f"{platform} crypto")
            if img_url: await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=img_url, caption=post[:1024], parse_mode=ParseMode.HTML)
            else: await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=post, parse_mode=ParseMode.HTML)
        else: await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=post, parse_mode=ParseMode.HTML)
        await update.effective_message.reply_text("✅ Gruba gönderildi!", reply_markup=main_menu())
    except Exception as e:
        await update.effective_message.reply_text(f"❌ Hata: {e}")

# ══════════════════════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════════════════════
@admin_only_callback
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "home": await query.edit_message_text("🤖 <b>AIRDROP BOT</b> — Admin Paneli", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "help": await cmd_help(update, context)
    elif data == "scan_menu": await query.edit_message_text("🔍 *Hangi kategoriyi tarayalım?*", parse_mode=ParseMode.HTML, reply_markup=category_filter_menu())
    elif data.startswith("cat_"):
        cat = data.split("_", 1)[1]; cats = CATEGORY_DEFS.get(cat, (None, None))[1]
        msg = await query.edit_message_text("🔍 <b>Taranıyor...</b>", parse_mode=ParseMode.HTML)
        result = await asyncio.to_thread(scan_active_airdrops, cats)
        await msg.edit_text(result[:4096], parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))
    elif data == "manual_post": await query.edit_message_text("✍️ Airdrop adını veya linkini gönder:", parse_mode=ParseMode.HTML)
    elif data == "new_research": await query.edit_message_text("✍️ Yeni araştırma: Airdrop adını veya linkini gönder:", parse_mode=ParseMode.HTML)
    elif data == "add_link": context.user_data["waiting_for"] = "add_link"; await query.edit_message_text("🔗 Linki yapıştır:", parse_mode=ParseMode.HTML)
    elif data == "edit_post_inline": context.user_data["waiting_for"] = "edit_post_inline"; await query.edit_message_text("✏️ Yeni post metnini gönder (HTML formatında):", parse_mode=ParseMode.HTML)
    elif data in ("fmt_long", "fmt_short", "fmt_summary"):
        fmt_map = {"fmt_long": "long", "fmt_short": "short", "fmt_summary": "summary"}
        fmt = fmt_map[data]; analysis = context.user_data.get("last_analysis", ""); project = context.user_data.get("last_project", "?"); score_data = context.user_data.get("last_score", {})
        post = await asyncio.to_thread(build_post, analysis, project, fmt, score_data.get("score"), score_data.get("verdict"))
        context.user_data.update({"last_post": post, "final_post": post, "post_fmt": fmt, "has_link": "[🔗 TIKLA 🖊]" not in post})
        await query.edit_message_text(f"📣 <b>POST ({fmt.upper()}):</b>\n\n{post}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=context.user_data["has_link"], fmt=fmt))
    elif data == "regen_post": await _do_research(update, context, context.user_data.get("last_project", ""))
    elif data == "send_text": await _send_to_group(update, context, with_photo=False)
    elif data == "send_photo": await _send_to_group(update, context, with_photo=True)
    elif data == "track_opp": context.user_data["waiting_for"] = "track_deadline"; await query.edit_message_text("⏰ Son tarihi yaz (örn: 30.06.2026):", parse_mode=ParseMode.HTML)
    elif data == "blacklist_opp":
        project = context.user_data.get("last_project", "")
        if project: add_to_blacklist(project); await query.edit_message_text(f"🚫 <b>{project}</b> kara listeye eklendi.", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "post_archive":
        archive = get_post_archive()
        if not archive: await query.edit_message_text("📭 Arşiv boş.", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return
        lines = [f"📁 {p['project']} ({p['fmt']}) — {p['date']}" for p in archive[:10]]
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "tracked_list":
        tracked = get_tracked()
        if not tracked: await query.edit_message_text("📌 Takip listesi boş.", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return
        lines = [f"📌 {t['name']} — Son: {t['deadline']} ({t['added']})" for t in tracked[:10]]
        await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "blacklist_view":
        bl = get_blacklist()
        if not bl: await query.edit_message_text("🚫 Kara liste boş.", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return
        await query.edit_message_text("🚫 Kara Liste:\n" + "\n".join(bl), parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "link_manage": await query.edit_message_text("🔗 Link Yönetimi", parse_mode=ParseMode.HTML, reply_markup=get_link_list_menu())
    elif data == "link_add_new": context.user_data["waiting_for"] = "link_add"; await query.edit_message_text("➕ Link ekle:\n<code>PLATFORM_ADI | https://link.com</code>", parse_mode=ParseMode.HTML)
    elif data == "link_clear": _LINK_STORE.clear(); await query.edit_message_text("🗑️ Linkler temizlendi.", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data.startswith("link_use_"):
        lid = data.split("use_")[1]; lnk = _LINK_STORE.get(lid)
        if lnk: record_post_use(lid); context.user_data["waiting_for"] = "add_link"; await query.edit_message_text(f"🔗 Link seçildi: {lnk['platform']}\nŞimdi post metnini gir veya linki yapıştır:", parse_mode=ParseMode.HTML)
    elif data == "emoji_menu":
        total = len([v for v in _load_emoji_map().values() if v])
        await query.edit_message_text(f"🎨 <b>PREMIUM EMOJİ YÖNETİMİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n📊 Kayıtlı: <b>{total}</b> emoji\n\n🟢 Premium animasyonlu emoji kanalda harika görünür!", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Paket Tara", callback_data="emoji_cmd_init"), InlineKeyboardButton("🧪 Test Et", callback_data="emoji_cmd_test")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]
        ]))
    elif data == "emoji_cmd_init": await cmd_emoji_init(update, context)
    elif data == "emoji_cmd_test": await cmd_emoji_test(update, context)

# ══════════════════════════════════════════════════════════
#  UYGULAMA BAŞLATMA
# ══════════════════════════════════════════════════════════
def main():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler("help", cmd_help))
    application.add_handler(CommandHandler("scan", cmd_scan))
    application.add_handler(CommandHandler("post", cmd_post))
    application.add_handler(CommandHandler("sendgroup", cmd_sendgroup))
    application.add_handler(CommandHandler("emoji_init", cmd_emoji_init))
    application.add_handler(CommandHandler("emoji_test", cmd_emoji_test))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot başlatılıyor...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
