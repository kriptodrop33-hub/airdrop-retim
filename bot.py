import os
import re
import asyncio
import logging
import requests
from groq import Groq
from tavily import TavilyClient
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

# ──────────────────────────────────────────────────────────────────
# LOGGING
# ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────
# ENV
# ──────────────────────────────────────────────────────────────────
BOT_TOKEN           = os.environ["BOT_TOKEN"]
GROQ_API_KEY        = os.environ["GROQ_API_KEY"]
TAVILY_API_KEY      = os.environ["TAVILY_API_KEY"]
UNSPLASH_ACCESS_KEY = os.environ["UNSPLASH_ACCESS_KEY"]
ADMIN_CHAT_ID       = int(os.environ["ADMIN_CHAT_ID"])
GROUP_CHAT_ID       = int(os.environ["GROUP_CHAT_ID"])

# ── Kayıt Linki Takip Sistemi ─────────────────────────────────────────
import hashlib as _hashlib, time as _time, random as _random
_LINK_STORE: dict = {}

def _gen_link_id() -> str:
    lid = _hashlib.md5(str(_random.random()).encode()).hexdigest()[:6].upper()
    return lid if lid not in _LINK_STORE else _gen_link_id()

def register_link(original_url: str, platform: str, category: str = "genel") -> dict:
    lid = _gen_link_id()
    _LINK_STORE[lid] = {
        "id":       lid,
        "url":      original_url,
        "platform": platform,
        "category": category,
        "created":  _time.strftime("%d.%m.%Y %H:%M"),
        "clicks":   0,
        "posts":    0,
    }
    return _LINK_STORE[lid]

def record_post_use(link_id: str):
    if link_id in _LINK_STORE:
        _LINK_STORE[link_id]["posts"] += 1

def get_link_stats() -> str:
    if not _LINK_STORE:
        return "📊 Henüz kayıtlı link yok."
    lines = ["📊 KAYIT LİNKİ İSTATİSTİKLERİ \n━━━━━━━━━━━━━━━━━━━━"]
    sorted_links = sorted(_LINK_STORE.values(), key=lambda x: x["posts"], reverse=True)
    for lnk in sorted_links[:10]:
        short_url = lnk["url"][:45] + ("..." if len(lnk["url"]) > 45 else "")
        lines.append(
            f"🔗 {lnk['platform']} `[{lnk['id']}]` \n"
            f"   📤 {lnk['posts']} posta eklendi | 📅 {lnk['created']}\n"
            f"   🌐 `{short_url}`"
        )
    return "\n\n".join(lines)

def get_link_list_menu() -> InlineKeyboardMarkup:
    if not _LINK_STORE:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ Yeni Link Ekle", callback_data="link_add_new"),
            InlineKeyboardButton("🏠 Ana Menü", callback_data="home"),
        ]])
    rows = []
    for lnk in list(_LINK_STORE.values())[-8:]:
        label = f"[{lnk['id']}] {lnk['platform']} ({lnk['posts']} post)"
        rows.append([InlineKeyboardButton(label, callback_data=f"link_use_{lnk['id']}")])
    rows.append([
        InlineKeyboardButton("➕ Yeni Link Ekle", callback_data="link_add_new"),
        InlineKeyboardButton("🗑️ Temizle", callback_data="link_clear"),
    ])
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
    return InlineKeyboardMarkup(rows)

# ──────────────────────────────────────────────────────────────────
# FIRSAT TAKİP & ARŞİV SİSTEMİ
# ──────────────────────────────────────────────────────────────────
import json, os, time as _t
_DATA_FILE = "bot_data.json"

def _load_data() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tracked": {}, "posts": [], "blacklist": []}

def _save_data(data: dict):
    try:
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Veri kaydetme hatası: {e}")

def track_opportunity(name: str, deadline: str, analysis: str, post: str):
    data = _load_data()
    tid = str(int(_t.time()))
    data["tracked"][tid] = {
        "id": tid, "name": name, "deadline": deadline,
        "analysis": analysis[:500], "post": post,
        "added": _t.strftime("%d.%m.%Y %H:%M"), "warned": False,
    }
    _save_data(data)
    return tid

def get_tracked() -> list:
    return list(_load_data()["tracked"].values())

def remove_tracked(tid: str):
    data = _load_data()
    data["tracked"].pop(tid, None)
    _save_data(data)

def save_post_archive(project: str, post: str, fmt: str):
    data = _load_data()
    entry = {"id": str(int(_t.time())), "project": project, "post": post, "fmt": fmt, "date": _t.strftime("%d.%m.%Y %H:%M")}
    data["posts"].insert(0, entry)
    data["posts"] = data["posts"][:30]
    _save_data(data)
    return entry["id"]

def get_post_archive() -> list:
    return _load_data()["posts"]

def get_blacklist() -> list:
    return _load_data()["blacklist"]

def add_to_blacklist(name: str):
    data = _load_data()
    if name.lower() not in [b.lower() for b in data["blacklist"]]:
        data["blacklist"].append(name)
        _save_data(data)

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
                if 0 <= days_left <= 3:
                    alerts.append({**opp, "days_left": days_left})
                    data["tracked"][tid]["warned"] = True
                    break
            except: pass
    _save_data(data)
    return alerts

def verify_and_score(name: str, initial_data: dict) -> dict:
    extra_queries = [f"{name} legit scam review reddit 2026", f"{name} official website social media verified"]
    extra_results = []
    for q in extra_queries:
        extra_results.extend(deep_search(q, max_results=3))
    extra_text = "\n\n".join([f"[DOĞRULAMA {i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:600]}" for i, r in enumerate(extra_results[:6])])
    combined_raw = initial_data.get("raw","") + "\n\n=== ÇAPRAZ DOĞRULAMA ===\n" + extra_text
    score_system = """Sen kripto fırsat doğrulama uzmanısın. GÜVENİLİRLİK SKORU hesapla (0-100).
ÇIKTI FORMAT (SADECE JSON): {"score": 75, "verdict": "GÜVENİLİR/ŞÜPHELİ/RİSKLİ", "reasons": ["neden"], "warning": ""}"""
    result_str = ai(score_system, f"Proje: {name}\n\n{combined_raw[:5000]}", tokens=400, temp=0.1)
    try:
        import re as _re
        json_match = _re.search(r"\{.*\}", result_str, _re.DOTALL)
        score_data = json.loads(json_match.group()) if json_match else {"score": 50, "verdict": "BELİRSİZ", "reasons": [], "warning": ""}
    except:
        score_data = {"score": 50, "verdict": "BELİRSİZ", "reasons": [], "warning": ""}
    score_data["extra_raw"] = extra_text
    return score_data

def format_score_badge(score: int, verdict: str) -> str:
    if score >= 75: return f"🟢 {verdict} ({score}/100)"
    elif score >= 50: return f"🟡 {verdict} ({score}/100)"
    return f"🔴 {verdict} ({score}/100)"

# ──────────────────────────────────────────────────────────────────
# GROQ & TAVILY
# ──────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
_GROQ_MODELS = ["llama-3.3-70b-versatile", "llama3-70b-8192", "llama-3.1-8b-instant", "gemma2-9b-it"]
_groq_exhausted: set = set()

def ai(system: str, user: str, tokens: int = 1800, temp: float = 0.75) -> str:
    import time as _t
    for model in _GROQ_MODELS:
        if model in _groq_exhausted: continue
        try:
            r = groq_client.chat.completions.create(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=tokens, temperature=temp)
            return r.choices[0].message.content.strip()
        except Exception as e:
            if "429" in str(e) and "tokens per day" in str(e).lower():
                _groq_exhausted.add(model)
                continue
            elif "429" in str(e):
                _t.sleep(2)
                try:
                    r2 = groq_client.chat.completions.create(model=model, messages=[{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=tokens, temperature=temp)
                    return r2.choices[0].message.content.strip()
                except: _groq_exhausted.add(model); continue
            else: continue
    return "❌ AI yanıt üretemedi."

_tavily_quota_ok = True

def _ddg_search(query: str, max_results: int = 5) -> list:
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")})
        return results
    except: return []

def _httpx_scrape(url: str) -> str:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            text = re.sub(r'<[^>]+>', ' ', r.text)
            return re.sub(r'\s+', ' ', text)[:3000]
    except: pass
    return ""

def deep_search(query: str, max_results: int = 5) -> list:
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            r = tavily_client.search(query=query, search_depth="basic", max_results=max_results, include_answer=False)
            return r.get("results", [])
        except Exception as e:
            if "432" in str(e) or "quota" in str(e).lower():
                _tavily_quota_ok = False
    return _ddg_search(query, max_results)

def fetch_url_content(url: str) -> str:
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            r = tavily_client.extract(urls=[url])
            results = r.get("results", [])
            if results: return results[0].get("raw_content", "")[:3000]
        except: _tavily_quota_ok = False
    return _httpx_scrape(url)

def is_url(text: str) -> bool:
    return bool(re.match(r"https?://\S+", text.strip()))

def get_image(query: str = "cryptocurrency airdrop") -> str | None:
    try:
        r = requests.get("https://api.unsplash.com/search/photos", params={"query": query, "per_page": 6, "orientation": "landscape", "client_id": UNSPLASH_ACCESS_KEY}, timeout=10)
        results = r.json().get("results", [])
        if results:
            import random
            return random.choice(results[:4])["urls"]["regular"]
    except: pass
    return None

# ──────────────────────────────────────────────────────────────────
# ARAŞTIRMA
# ──────────────────────────────────────────────────────────────────
def research_airdrop_by_name(name: str) -> dict:
    queries = [f"{name} new user bonus reward how to claim 2026", f"{name} airdrop tasks eligibility reward amount 2026", f"{name} kripto kampanya kayıt bonusu nasıl alınır"]
    all_results = []
    for q in queries:
        hits = deep_search(q, max_results=4)
        all_results.extend(hits)
        if len(all_results) >= 10: break
    seen_urls, unique = set(), []
    for item in all_results:
        url = item.get("url", "")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)
    raw_text = "\n\n".join([f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:1200]}" for i, r in enumerate(unique[:8])])
    if unique:
        best_url = unique[0].get("url", "")
        try:
            full = fetch_url_content(best_url)
            if full: raw_text = f"=== TAM SAYFA ===\n{full[:2500]}\n\n{raw_text}"
        except: pass
    return {"name": name, "raw": raw_text, "sources": unique[:8]}

def research_airdrop_by_url(url: str) -> dict:
    content = fetch_url_content(url)
    name_hint = ai("Extract the project name. Reply with ONLY the name.", content[:500] if content else url, tokens=50, temp=0.1)
    extra = deep_search(f"{name_hint} airdrop claim guide tasks 2025", max_results=6)
    extra_text = "\n\n".join([f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:400]}" for i, r in enumerate(extra[:6])])
    return {"name": name_hint.strip(), "raw": f"=== SAYFA ===\n{content}\n\n=== EK ===\n{extra_text}", "sources": extra[:6], "url": url}

def analyze_research(data: dict) -> str:
    system = """Sen kripto fırsat araştırmacısısın. HAM VERİDEN SADECE gerçek bilgileri çıkar.
FORMAT:
📌 PLATFORM/PROJE: [adı]
🏷 TÜRÜ: [tip]
💰 ÖDÜL: [rakam]
👥 KİMLER: [hedef]
📋 ADIMLAR: ...
⏰ SON TARİH: ...
🔗 LİNK: ...
⭐ GÜVENİLİRLİK: [1-5]
⚠️ UYARI: ...
Türkçe yaz."""
    return ai(system, f"Proje: {data['name']}\n\n{data['raw']}", tokens=2000, temp=0.1)

# ──────────────────────────────────────────────────────────────────
# TARAMA
# ──────────────────────────────────────────────────────────────────
OPPORTUNITY_QUERIES = [
    ("bonus", "kripto borsa yeni üye kampanyası kayıt ödülü 2026 USDT TL Mart aktif"),
    ("bonus", "crypto exchange new user bonus welcome reward USDT 2025 site:binance.com OR site:bybit.com OR site:okx.com OR site:cointr.com OR site:bitlo.com"),
    ("bonus", "crypto exchange sign up reward deposit bonus free USDT 2026"),
    ("bonus", "borsa kayıt kampanyası hediye 2025 site:cointr.com OR site:paribu.com OR site:btcturk.com"),
    ("referral", "crypto referral program earn USDT invite friends commission 2025 2026"),
    ("referral", "kripto borsa arkadaş davet et kazan referral ödülü 2025"),
    ("kampanya", "crypto exchange trading competition reward prize USDT 2025 2026"),
    ("kampanya", "kripto borsa işlem kampanyası ödül havuzu 2025"),
    ("sosyal", "telegram crypto bot task reward earn token USDT 2025"),
    ("sosyal", "crypto project telegram task reward points 2025 airdrop"),
    ("airdrop", "crypto airdrop claim March 2026 active free no investment required"),
    ("airdrop", "galxe zealy intract quest airdrop reward March 2026 active"),
]

CATEGORY_DEFS = {
    "hepsi": ("🌐 Hepsi", None), "bonus": ("🎁 Borsa Bonusu", ["bonus"]),
    "referral": ("👥 Referral", ["referral"]), "kampanya": ("🏆 Kampanya", ["kampanya"]),
    "sosyal": ("📱 Sosyal Görev", ["sosyal"]), "airdrop": ("🪂 Airdrop", ["airdrop"]),
}

def category_filter_menu() -> InlineKeyboardMarkup:
    rows = []
    keys = list(CATEGORY_DEFS.keys())
    for i in range(0, len(keys), 3):
        row = [InlineKeyboardButton(CATEGORY_DEFS[k][0], callback_data=f"cat_{k}") for k in keys[i:i+3]]
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
    return InlineKeyboardMarkup(rows)

def run_opportunity_search(cats: list | None = None) -> list:
    seen_urls, results, seen_cats = set(), [], set()
    for category, query in OPPORTUNITY_QUERIES:
        if cats and category not in cats: continue
        if category in seen_cats: continue
        seen_cats.add(category)
        hits = deep_search(query, max_results=4)
        for r in hits:
            url = r.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append({"category": category, "title": r.get("title", ""), "url": url, "content": r.get("content", "")[:1200]})
        if len(results) >= 20: break
    return results

def scan_active_airdrops(cats: list | None = None) -> str:
    raw_results = run_opportunity_search(cats=cats)
    if not raw_results: return "❌ Veri çekilemedi."
    by_cat = {}
    for r in raw_results: by_cat.setdefault(r["category"], []).append(r)
    cat_labels = {"bonus": "🎁 BORSA BONUSU", "referral": "👥 REFERRAL", "kampanya": "🏆 KAMPANYA", "sosyal": "📱 SOSYAL", "airdrop": "🪂 AIRDROP"}
    combined_raw = ""
    for cat, items in by_cat.items():
        combined_raw += f"\n\n{'='*40}\n{cat_labels.get(cat, cat.upper())}\n{'='*40}\n"
        for item in items[:3]: combined_raw += f"Başlık: {item['title']}\nURL: {item['url']}\nİçerik: {item['content']}\n---\n"
    system = """Sen kripto fırsat analistsin. 4-6 fırsat listele.
FORMAT:
━━━━━━━━━━━━━━━━━━━━━━
🎁 [PLATFORM]
┣ 💰 Ödül: [rakam]
┣ 🏦 Tür: [tip]
┣ 👥 Kimler: [hedef]
┣ 📋 Adımlar: 1️⃣ ... 2️⃣ ... 3️⃣ ...
┣ ⏰ Son Tarih: [...]
┣ ⭐ Güvenilirlik: [...]
┗ 🔗 [URL]
Türkçe yaz."""
    return ai(system, combined_raw[:8000], tokens=3500, temp=0.1)

# ──────────────────────────────────────────────────────────────────
# POST OLUŞTURMA
# ──────────────────────────────────────────────────────────────────
POST_SYSTEM = """Sen KriptoDropTR için airdrop postu yazıyorsun.
⛔ YASAK: Analizde OLMAYAN rakam/kod yazma, referral kodu YAZMA, hashtag yasak.
Link için SADECE: [🔗 TIKLA 🖊]
Türkçe | HTML: kalın

YAPI:
🚀 [PLATFORM] [BAŞLIK]! 🎁
[Tek cümle] ✨
────────────────
✅ YAPMAN GEREKENLER:
1️⃣ [adım 1]
2️⃣ [adım 2]
3️⃣ [adım 3]
────────────────
🔗 Hemen Katıl → [🔗 TIKLA 🖊]
💡 Görev zorluğu: [Kolay/Orta/Zor]
💸 Ödül miktarı: [rakam]
[🟢 GÜVENİLİR/🟡 ŞÜPHELİ/🔴 RİSKLİ] · [1 cümle neden]
📅 Kampanya Dönemi: [tarih — yoksa sil]
⏰ Son Tarih: [tarih — yoksa "Devam ediyor"]
────────────────
🔔 Daha fazla fırsat için kanalı pinle 📌
📢 @kriptodropduyuru
🎁 @kriptodroptr"""

POST_SYSTEM_SHORT = """Kısa airdrop postu yaz.
⛔ Uydurma rakam/referral kodu yasak.
HTML: kalın | Link: [🔗 TIKLA 🖊] | Maks 300 karakter

YAPI:
🚀 [PLATFORM] — [BAŞLIK]! ✨
1️⃣ [adım 1]
2️⃣ [adım 2]
3️⃣ [adım 3]
💸 Ödül: [rakam] · 💡 [Kolay/Orta/Zor]
🔗 [🔗 TIKLA 🖊]
[🟢 GÜVENİLİR/🟡 ŞÜPHELİ/🔴 RİSKLİ]
📢 @kriptodropduyuru | 🎁 @kriptodroptr"""

POST_SYSTEM_SUMMARY = """2-3 satır airdrop özeti yaz.
⛔ Uydurma rakam/referral kodu yasak.
HTML: kalın | Link: [🔗 TIKLA 🖊]

FORMAT:
🚀 [PLATFORM] — [ödül] kazan! ✨ [1 cümle nasıl]. 🔗 [🔗 TIKLA 🖊]
[🟢/🟡/🔴] · 📢 @kriptodropduyuru 🎁 @kriptodroptr"""

def _build_prompt(analysis: str, project_name: str) -> str:
    return f"Platform: {project_name}\n\n=== ANALİZ ===\n{analysis}\n\n=== KURALLAR ===\n1. SADECE analizde geçen rakamları kullan\n2. Referral kodu YAZMA\n3. Bilgi yoksa satırı SİL\n4. [🔗 TIKLA 🖊] placeholder'ını koru"

def build_post(analysis: str, project_name: str, fmt: str = "long") -> str:
    prompt = _build_prompt(analysis, project_name)
    if fmt == "short": post = ai(POST_SYSTEM_SHORT, prompt, tokens=500, temp=0.3)
    elif fmt == "summary": post = ai(POST_SYSTEM_SUMMARY, prompt, tokens=200, temp=0.3)
    else: post = ai(POST_SYSTEM, prompt, tokens=1200, temp=0.3)
    return inject_premium_emojis(post)

# ──────────────────────────────────────────────────────────────────
# PREMIUM EMOJI
# ──────────────────────────────────────────────────────────────────
CE = {
    "fire": "<tg-emoji emoji-id='5424972470023104089'>🔥</tg-emoji>",
    "diamond": "<tg-emoji emoji-id='5427168083074628963'>💎</tg-emoji>",
    "rocket": "<tg-emoji emoji-id='5188481279963715781'>🚀</tg-emoji>",
    "moneybag": "<tg-emoji emoji-id='5233326571099534068'>💸</tg-emoji>",
    "warn": "<tg-emoji emoji-id='5447644880824181073'>⚠️</tg-emoji>",
    "crown": "<tg-emoji emoji-id='5217822164362739968'>👑</tg-emoji>",
    "chart": "<tg-emoji emoji-id='5244837092042750681'>📈</tg-emoji>",
    "trophy": "<tg-emoji emoji-id='5188344996356448758'>🏆</tg-emoji>",
    "bell": "<tg-emoji emoji-id='5458603043203327669'>🔔</tg-emoji>",
    "link": "<tg-emoji emoji-id='5271604874419647061'>🔗</tg-emoji>",
    "speaker": "<tg-emoji emoji-id='6235691325744745133'>📢</tg-emoji>",
    "mega": "<tg-emoji emoji-id='5424818078833715060'>📣</tg-emoji>",
    "bulb": "<tg-emoji emoji-id='5422439311196834318'>💡</tg-emoji>",
    "gold": "<tg-emoji emoji-id='5440539497383087970'>🥇</tg-emoji>",
    "silver": "<tg-emoji emoji-id='5447203607294265305'>🥈</tg-emoji>",
    "bronze": "<tg-emoji emoji-id='5453902265922376865'>🥉</tg-emoji>",
    "medal": "<tg-emoji emoji-id='5424746623462823358'>🏅</tg-emoji>",
    "party": "<tg-emoji emoji-id='5461151367559141950'>🎉</tg-emoji>",
    "target": "<tg-emoji emoji-id='5461009483314517035'>🎯</tg-emoji>",
    "sparkle": "<tg-emoji emoji-id='5325547803936572038'>✨</tg-emoji>",
    "glow": "<tg-emoji emoji-id='5208801655004350721'>🌟</tg-emoji>",
    "gift": "<tg-emoji emoji-id='5203996991054432397'>🎁</tg-emoji>",
    "rich": "<tg-emoji emoji-id='5391292736647209211'>🤑</tg-emoji>",
    "chat": "<tg-emoji emoji-id='5443038326535759644'>💬</tg-emoji>",
    "puzzle": "<tg-emoji emoji-id='5213306719215577669'>🧩</tg-emoji>",
    "green": "<tg-emoji emoji-id='5416081784641168838'>🟢</tg-emoji>",
    "red": "<tg-emoji emoji-id='5411225014148014586'>🔴</tg-emoji>",
    "dizzy": "<tg-emoji emoji-id='5215423854624645141'>💫</tg-emoji>",
    "money": "<tg-emoji emoji-id='6235340371082086934'>💰</tg-emoji>",
    "clock": "<tg-emoji emoji-id='6235362644782484636'>⏰</tg-emoji>",
    "check": "<tg-emoji emoji-id='5217497254381754877'>✅</tg-emoji>",
    "gem": "<tg-emoji emoji-id='5213240855892073022'>💠</tg-emoji>",
    "calendar": "<tg-emoji emoji-id='5213240855892073022'>📅</tg-emoji>",
}

_EMOJI_MAP = {
    "🔥": CE["fire"], "💎": CE["diamond"], "🚀": CE["rocket"], "💸": CE["moneybag"],
    "⚠️": CE["warn"], "👑": CE["crown"], "📈": CE["chart"], "🏆": CE["trophy"],
    "🔔": CE["bell"], "🔗": CE["link"], "📢": CE["speaker"], "📣": CE["mega"],
    "💡": CE["bulb"], "🥇": CE["gold"], "🥈": CE["silver"], "🥉": CE["bronze"],
    "🏅": CE["medal"], "🎉": CE["party"], "🎯": CE["target"], "✨": CE["sparkle"],
    "🌟": CE["glow"], "🎁": CE["gift"], "🤑": CE["rich"], "💬": CE["chat"],
    "🧩": CE["puzzle"], "🟢": CE["green"], "🔴": CE["red"], "💫": CE["dizzy"],
    "💰": CE["money"], "⏰": CE["clock"], "✅": CE["check"], "💠": CE["gem"], "📅": CE["calendar"],
}

def inject_premium_emojis(text: str) -> str:
    for std, prem in _EMOJI_MAP.items():
        text = text.replace(std, prem)
    return text

def md_to_html(text: str) -> str:
    text = re.sub(r'(?m)^#+\s.*$', '', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<b>\1</b>', text)
    text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

def safe_md(text: str) -> str:
    return md_to_html(text)

# ──────────────────────────────────────────────────────────────────
# HANDLERLAR
# ──────────────────────────────────────────────────────────────────
def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Fırsat Tara", callback_data="scan_menu"), InlineKeyboardButton("✍️ Post Oluştur", callback_data="manual_post")],
        [InlineKeyboardButton("📁 Post Arşivi", callback_data="post_archive"), InlineKeyboardButton("📌 Takip Listesi", callback_data="tracked_list")],
        [InlineKeyboardButton("🚫 Kara Liste", callback_data="blacklist_view"), InlineKeyboardButton("🔗 Linklerimi Yönet", callback_data="link_manage")],
        [InlineKeyboardButton("🔄 Yeni Araştırma", callback_data="new_research"), InlineKeyboardButton("❓ Yardım", callback_data="help")],
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

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ("group", "supergroup", "channel"): return
    if update.effective_user.id != ADMIN_CHAT_ID: return
    context.user_data.clear()
    await update.message.reply_text("🤖 AIRDROP BOT — Admin Paneli\n\n━━━━━━━━━━━━━━━━━━━━\n🔍 Airdrop Tara\n✍️ Post Oluştur\n📢 Gruba Gönder\n━━━━━━━━━━━━━━━━━━━━", parse_mode=ParseMode.HTML, reply_markup=main_menu())

@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📖 KOMUTLAR\n\n/start — Ana menü\n/scan — Tara\n/post [isim] — Araştır\n/sendgroup — Gönder", parse_mode=ParseMode.HTML)

@admin_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Hangi kategori?", parse_mode=ParseMode.HTML, reply_markup=category_filter_menu())

@admin_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("⚠️ Kullanım: `/post [ad]`", parse_mode=ParseMode.HTML); return
    await _do_research(update, context, " ".join(context.args))

@admin_only
async def cmd_sendgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_to_group(update, context, with_photo=False)

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ("group", "supergroup", "channel"): return
    if update.effective_user.id != ADMIN_CHAT_ID: return
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting_for")

    if waiting == "link_add":
        context.user_data["waiting_for"] = None
        parts = [p.strip() for p in text.split("|", 1)]
        if len(parts) != 2 or not parts[1].startswith("http"):
            await update.message.reply_text("⚠️ Format: `PLATFORM | https://link.com`", parse_mode=ParseMode.HTML); return
        lnk = register_link(parts[1], parts[0])
        await update.message.reply_text(f"✅ Link kaydedildi!\n🔑 ID: `{lnk['id']}`", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    if waiting == "add_link":
        context.user_data["waiting_for"] = None
        post = context.user_data.get("last_post", "")
        link = text.strip()
        updated = post.replace("[🔗 TIKLA 🖊]", link)
        context.user_data["final_post"] = updated; context.user_data["has_link"] = True
        await update.message.reply_text("✅ Link eklendi!", parse_mode=ParseMode.HTML); return

    if waiting in ("edit_post", "edit_post_inline"):
        context.user_data["waiting_for"] = None; context.user_data["final_post"] = text
        await update.message.reply_text(f"✅ Post güncellendi!\n\n{safe_md(text)}", parse_mode=ParseMode.HTML); return

    if waiting == "track_deadline":
        context.user_data["waiting_for"] = None
        deadline = text.strip(); project_name = context.user_data.get("last_project", "?")
        analysis = context.user_data.get("last_analysis", ""); post = context.user_data.get("final_post", "")
        await track_opportunity(project_name, deadline, analysis, post)
        await update.message.reply_text(f"📌 {project_name} takibe alındı!", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    await _do_research(update, context, text)

async def _do_research(update: Update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    if await asyncio.to_thread(is_blacklisted, input_text):
        await update.effective_message.reply_text(f"🚫 {input_text} kara listede!", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    msg = await update.effective_message.reply_text(f"🔬 Araştırma başladı...", parse_mode=ParseMode.HTML)

    if is_url(input_text):
        data = research_airdrop_by_url(input_text)
    else:
        data = research_airdrop_by_name(input_text)

    project_name = data.get("name", input_text)
    score_data = verify_and_score(project_name, data)
    score = score_data.get("score", 50)
    verdict = score_data.get("verdict", "BELİRSİZ")
    badge = format_score_badge(score, verdict)

    context.user_data["last_score"] = score_data; context.user_data["last_project"] = project_name

    enriched_data = data.copy()
    enriched_data["raw"] = data.get("raw", "") + "\n\n=== DOĞRULAMA ===\n" + score_data.get("extra_raw", "")
    analysis = analyze_research(enriched_data)
    context.user_data["last_analysis"] = analysis

    post = build_post(analysis, project_name)
    context.user_data["last_post"] = post; context.user_data["final_post"] = post
    context.user_data["last_post_platform"] = project_name; context.user_data["has_link"] = False; context.user_data["post_fmt"] = "long"

    await save_post_archive(project_name, post, "long")

    post_preview = f"📣 HAZIRLANAN POST:\n\n{safe_md(post)}\n\n━━━━━━━━━━━━━━━━━━━━\nSkor: {badge}"
    await update.effective_message.reply_text(post_preview, parse_mode=ParseMode.HTML, reply_markup=post_actions_extended(has_link=False, fmt="long", score=score))

async def _send_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, with_photo: bool):
    post = context.user_data.get("final_post") or context.user_data.get("last_post")
    if not post:
        await update.callback_query.message.reply_text("⚠️ Önce post oluştur!"); return
    try:
        if with_photo:
            img_url = get_image("crypto")
            if img_url:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=img_url, caption=safe_md(post[:1024]), parse_mode=ParseMode.HTML)
            else:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=safe_md(post), parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=safe_md(post), parse_mode=ParseMode.HTML)
        await update.callback_query.message.reply_text("✅ Gönderildi!", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    except Exception as e:
        await update.callback_query.message.reply_text(f"❌ Hata: {e}", parse_mode=ParseMode.HTML)

@admin_only_callback
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = q.data

    if data == "home":
        await q.message.reply_text("🏠 Ana Menü", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "help":
        await q.message.reply_text("📖 Yardım", parse_mode=ParseMode.HTML)
    elif data == "scan":
        result = scan_active_airdrops()
        await q.message.reply_text(f"✅ TARAMA:\n\n{safe_md(result)}", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "manual_post":
        context.user_data["waiting_for"] = None
        await q.message.reply_text("✍️ Airdrop adı veya link yaz:", parse_mode=ParseMode.HTML)
    elif data == "add_link":
        context.user_data["waiting_for"] = "add_link"
        await q.message.reply_text("🔗 Link yapıştır:", parse_mode=ParseMode.HTML)
    elif data == "send_text":
        await _send_to_group(update, context, with_photo=False)
    elif data == "send_photo":
        await _send_to_group(update, context, with_photo=True)
    elif data == "regen_post":
        analysis = context.user_data.get("last_analysis")
        project = context.user_data.get("last_project", "")
        if not analysis: await q.message.reply_text("⚠️ Önce araştırma yap."); return
        post = build_post(analysis, project)
        context.user_data["last_post"] = post; context.user_data["final_post"] = post
        await q.message.reply_text(f"♻️ Yenilendi:\n\n{safe_md(post)}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False))
    elif data in ("fmt_long", "fmt_short", "fmt_summary"):
        analysis = context.user_data.get("last_analysis")
        project = context.user_data.get("last_project", "")
        if not analysis: await q.answer("⚠️ Önce araştırma yap.", show_alert=True); return
        fmt_map = {"fmt_long": "long", "fmt_short": "short", "fmt_summary": "summary"}
        fmt = fmt_map[data]
        post = build_post(analysis, project, fmt=fmt)
        context.user_data["last_post"] = post; context.user_data["final_post"] = post; context.user_data["post_fmt"] = fmt
        await q.message.reply_text(f"📝 Format: {fmt}\n\n{safe_md(post)}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False, fmt=fmt))
    elif data == "scan_menu":
        await q.message.reply_text("🔍 Kategori seç:", parse_mode=ParseMode.HTML, reply_markup=category_filter_menu())
    elif data.startswith("cat_"):
        cat_key = data[4:]
        _, cats = CATEGORY_DEFS.get(cat_key, ("Hepsi", None))
        result = scan_active_airdrops(cats=cats)
        await q.message.reply_text(f"✅ {cat_key.upper()}:\n\n{safe_md(result)}", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "link_stats":
        await q.message.reply_text(get_link_stats(), parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "link_manage":
        await q.message.reply_text("🔗 Linkler", parse_mode=ParseMode.HTML, reply_markup=get_link_list_menu())
    elif data == "link_add_new":
        context.user_data["waiting_for"] = "link_add"
        await q.message.reply_text("🔗 Format: `PLATFORM | https://link.com`", parse_mode=ParseMode.HTML)
    elif data.startswith("link_use_"):
        lid = data[9:]; lnk = _LINK_STORE.get(lid)
        if not lnk: await q.answer("Link yok.", show_alert=True); return
        post = context.user_data.get("last_post", "")
        if not post: await q.answer("⚠️ Önce post oluştur.", show_alert=True); return
        updated = post.replace("[🔗 TIKLA 🖊]", lnk["url"])
        context.user_data["final_post"] = updated; context.user_data["has_link"] = True
        record_post_use(lid)
        await q.message.reply_text(f"✅ {lnk['platform']} eklendi!\n\n{safe_md(updated)}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True))
    elif data == "link_clear":
        _LINK_STORE.clear()
        await q.answer("🗑️ Silindi.", show_alert=True)
    elif data == "edit_post_inline":
        context.user_data["waiting_for"] = "edit_post_inline"
        await q.message.reply_text("✏️ Düzenle:", parse_mode=ParseMode.HTML)
    elif data == "track_opp":
        context.user_data["waiting_for"] = "track_deadline"
        project = context.user_data.get("last_project", "?")
        await q.message.reply_text(f"📌 {project} için tarih gir:", parse_mode=ParseMode.HTML)
    elif data == "blacklist_opp":
        project = context.user_data.get("last_project", "?")
        add_to_blacklist(project)
        await q.answer(f"🚫 {project} eklendi.", show_alert=True)
    elif data == "tracked_list":
        items = get_tracked()
        if not items: await q.message.reply_text("📌 Boş", parse_mode=ParseMode.HTML, reply_markup=main_menu())
        else:
            text_msg = "📌 TAKİP LİSTESİ\n\n"
            for opp in items[-8:]: text_msg += f"• {opp['name']} | ⏰ {opp.get('deadline','?')}\n"
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data.startswith("untrack_"):
        tid = data[8:]; await asyncio.to_thread(remove_tracked, tid)
        await q.answer("✅ Silindi.", show_alert=False)
    elif data.startswith("repost_"):
        tid = data[7:]; items = {o["id"]: o for o in get_tracked()}; opp = items.get(tid)
        if opp:
            context.user_data["last_post"] = opp.get("post", ""); context.user_data["final_post"] = opp.get("post", "")
            context.user_data["last_project"] = opp.get("name", "")
            await q.message.reply_text(f"📣 POST:\n\n{safe_md(opp.get('post',''))}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False))
        else: await q.answer("Yok.", show_alert=True)
    elif data == "post_archive":
        posts = get_post_archive()
        if not posts: await q.message.reply_text("📁 Boş", parse_mode=ParseMode.HTML, reply_markup=main_menu())
        else:
            text_msg = "📁 ARŞİV\n\n"
            for p in posts[:10]: text_msg += f"• {p['project']} | {p['date']}\n"
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data.startswith("archive_load_"):
        pid = data[13:]; posts = {p["id"]: p for p in get_post_archive()}; p = posts.get(pid)
        if p:
            context.user_data["last_post"] = p["post"]; context.user_data["final_post"] = p["post"]
            context.user_data["last_project"] = p["project"]; context.user_data["post_fmt"] = p["fmt"]
            await q.message.reply_text(f"📄 {p['project']}:\n\n{safe_md(p['post'])}", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False, fmt=p["fmt"]))
        else: await q.answer("Yok.", show_alert=True)
    elif data == "blacklist_view":
        bl = get_blacklist()
        text_msg = "🚫 KARA LİSTE\n\n" + "\n".join(bl) if bl else "Boş"
        await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "new_research":
        context.user_data["waiting_for"] = None
        await q.message.reply_text("🔬 Araştırma için yaz:", parse_mode=ParseMode.HTML)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    private = filters.ChatType.PRIVATE
    app.add_handler(CommandHandler("start", cmd_start, filters=private))
    app.add_handler(CommandHandler("help", cmd_help, filters=private))
    app.add_handler(CommandHandler("scan", cmd_scan, filters=private))
    app.add_handler(CommandHandler("post", cmd_post, filters=private))
    app.add_handler(CommandHandler("sendgroup", cmd_sendgroup, filters=private))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("🚀 Bot başlatıldı. Hareketli emojiler aktif.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
