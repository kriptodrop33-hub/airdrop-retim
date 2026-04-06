import os
import re
import json
import time as _time
import random as _random
import hashlib as _hashlib
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
# LOGGING & CONFIG
# ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

BOT_TOKEN           = os.environ.get("BOT_TOKEN", "")
GROQ_API_KEY        = os.environ.get("GROQ_API_KEY", "")
TAVILY_API_KEY      = os.environ.get("TAVILY_API_KEY", "")
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY", "")
ADMIN_CHAT_ID       = int(os.environ.get("ADMIN_CHAT_ID", "0"))
GROUP_CHAT_ID       = int(os.environ.get("GROUP_CHAT_ID", "0"))

_DATA_DIR = "data"
os.makedirs(_DATA_DIR, exist_ok=True)
_DATA_FILE = os.path.join(_DATA_DIR, "bot_data.json")

# ──────────────────────────────────────────────────────────────────
# LİNK YÖNETİMİ
# ──────────────────────────────────────────────────────────────────
_LINK_STORE: dict = {}

def _gen_link_id() -> str:
    lid = _hashlib.md5(str(_random.random()).encode()).hexdigest()[:6].upper()
    return lid if lid not in _LINK_STORE else _gen_link_id()

def register_link(original_url: str, platform: str, category: str = "genel") -> dict:
    lid = _gen_link_id()
    _LINK_STORE[lid] = {
        "id": lid, "url": original_url, "platform": platform,
        "category": category, "created": _time.strftime("%d.%m.%Y %H:%M"),
        "clicks": 0, "posts": 0,
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
        lines.append(f"🔗 {lnk['platform']} `[{lnk['id']}]` \n   📤 {lnk['posts']} posta eklendi | 📅 {lnk['created']}\n   🌐 `{short_url}`")
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
# VERİ SAKLAMA (ASYNC SAFE)
# ──────────────────────────────────────────────────────────────────
def _load_data_sync() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tracked": {}, "posts": [], "blacklist": []}

async def _load_data() -> dict:
    return await asyncio.to_thread(_load_data_sync)

def _save_data_sync(data: dict):
    try:
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Veri kaydetme hatası: {e}")

async def _save_data(data: dict):
    await asyncio.to_thread(_save_data_sync, data)

async def track_opportunity(name: str, deadline: str, analysis: str, post: str):
    data = await _load_data()
    tid = str(int(_time.time()))
    data["tracked"][tid] = {
        "id": tid, "name": name, "deadline": deadline,
        "analysis": analysis[:500], "post": post,
        "added": _time.strftime("%d.%m.%Y %H:%M"), "warned": False,
    }
    await _save_data(data)
    return tid

async def get_tracked() -> list:
    data = await _load_data()
    return list(data["tracked"].values())

async def remove_tracked(tid: str):
    data = await _load_data()
    data["tracked"].pop(tid, None)
    await _save_data(data)

async def save_post_archive(project: str, post: str, fmt: str):
    data = await _load_data()
    entry = {
        "id": str(int(_time.time())), "project": project,
        "post": post, "fmt": fmt, "date": _time.strftime("%d.%m.%Y %H:%M"),
    }
    data["posts"].insert(0, entry)
    data["posts"] = data["posts"][:30]
    await _save_data(data)
    return entry["id"]

async def get_post_archive() -> list:
    data = await _load_data()
    return data["posts"]

async def get_blacklist() -> list:
    data = await _load_data()
    return data["blacklist"]

async def add_to_blacklist(name: str):
    data = await _load_data()
    if name.lower() not in [b.lower() for b in data["blacklist"]]:
        data["blacklist"].append(name)
        await _save_data(data)

async def is_blacklisted(name: str) -> bool:
    data = await _load_data()
    return any(name.lower() in b.lower() or b.lower() in name.lower() for b in data["blacklist"])

async def check_deadlines() -> list:
    data = await _load_data()
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
            except Exception: pass
    await _save_data(data)
    return alerts

# ──────────────────────────────────────────────────────────────────
# AI & ARAMA MOTORLARI (ASYNC)
# ──────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)
_groq_exhausted: set = set()

async def ai(system: str, user: str, tokens: int = 1800, temp: float = 0.75) -> str:
    for model in ["llama-3.3-70b-versatile", "llama3-70b-8192", "llama-3.1-8b-instant", "gemma2-9b-it"]:
        if model in _groq_exhausted: continue
        try:
            # Async wrapper for blocking Groq call
            r = await asyncio.to_thread(
                groq_client.chat.completions.create,
                model=model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=tokens, temperature=temp,
            )
            result = r.choices[0].message.content.strip()
            if model != "llama-3.3-70b-versatile": logger.info(f"Groq fallback model: {model}")
            return result
        except Exception as e:
            err_str = str(e)
            logger.error(f"Groq hata [{model}]: {err_str[:200]}")
            if "429" in err_str and "tokens per day" in err_str.lower():
                _groq_exhausted.add(model)
                continue
            elif "429" in err_str:
                await asyncio.sleep(2) # Non-blocking sleep
                try:
                    r2 = await asyncio.to_thread(
                        groq_client.chat.completions.create,
                        model=model,
                        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        max_tokens=tokens, temperature=temp,
                    )
                    return r2.choices[0].message.content.strip()
                except Exception:
                    _groq_exhausted.add(model)
                    continue
            else: continue
    return "❌ AI yanıt üretemedi."

_tavily_quota_ok = True

def _ddg_search_sync(query: str, max_results: int = 5) -> list[dict]:
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({"title": r.get("title", ""), "url": r.get("href", ""), "content": r.get("body", "")})
        return results
    except Exception: return []

async def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    return await asyncio.to_thread(_ddg_search_sync, query, max_results)

def _httpx_scrape_sync(url: str) -> str:
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            text = re.sub(r'<[^>]+>', ' ', r.text)
            return re.sub(r'\s+', ' ', text)[:3000]
    except Exception: return ""

async def _httpx_scrape(url: str) -> str:
    return await asyncio.to_thread(_httpx_scrape_sync, url)

async def deep_search(query: str, max_results: int = 5) -> list[dict]:
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            r = await asyncio.to_thread(tavily_client.search, query=query, search_depth="basic", max_results=max_results, include_answer=False)
            return r.get("results", [])
        except Exception as e:
            if "432" in str(e) or "quota" in str(e).lower():
                logger.warning("Tavily kotası doldu → DDG'ye geçildi")
                _tavily_quota_ok = False
    return await _ddg_search(query, max_results)

async def fetch_url_content(url: str) -> str:
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            r = await asyncio.to_thread(tavily_client.extract, urls=[url])
            results = r.get("results", [])
            if results: return results[0].get("raw_content", "")[:3000]
        except Exception as e:
            if "432" in str(e) or "quota" in str(e).lower():
                _tavily_quota_ok = False
    return await _httpx_scrape(url)

async def get_image(query: str = "cryptocurrency airdrop") -> str | None:
    def _unsplash_sync():
        try:
            r = requests.get("https://api.unsplash.com/search/photos", params={"query": query, "per_page": 6, "orientation": "landscape", "client_id": UNSPLASH_ACCESS_KEY}, timeout=10)
            results = r.json().get("results", [])
            if results: return results[0]["urls"]["regular"]
        except Exception: return None
    return await asyncio.to_thread(_unsplash_sync)

def is_url(text: str) -> bool:
    return bool(re.match(r"https?://\S+", text.strip()))

# ──────────────────────────────────────────────────────────────────
# ARAŞTIRMA & DOĞRULAMA
# ──────────────────────────────────────────────────────────────────
async def research_airdrop_by_name(name: str) -> dict:
    queries = [f"{name} new user bonus reward how to claim 2026", f"{name} airdrop tasks eligibility reward amount 2026", f"{name} kripto kampanya kayıt bonusu nasıl alınır"]
    all_results = []
    for q in queries:
        hits = await deep_search(q, max_results=4)
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
            full = await fetch_url_content(best_url)
            if full: raw_text = f"=== TAM SAYFA ({best_url}) ===\n{full[:2500]}\n\n=== DİĞER KAYNAKLAR ===\n{raw_text}"
        except: pass
    return {"name": name, "raw": raw_text, "sources": unique[:8]}

async def research_airdrop_by_url(url: str) -> dict:
    content = await fetch_url_content(url)
    name_hint = await ai("Extract the project or airdrop name from the text. Reply with ONLY the name, nothing else.", content[:500] if content else url, tokens=50, temp=0.1)
    extra = await deep_search(f"{name_hint} airdrop claim guide tasks 2025", max_results=6)
    extra_text = "\n\n".join([f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:400]}" for i, r in enumerate(extra[:6])])
    raw = f"=== SAYFA İÇERİĞİ ===\n{content}\n\n=== EK KAYNAKLAR ===\n{extra_text}"
    return {"name": name_hint.strip(), "raw": raw, "sources": extra[:6], "url": url}

async def verify_and_score(name: str, initial_data: dict) -> dict:
    extra_queries = [f"{name} legit scam review reddit 2026", f"{name} official website social media verified"]
    extra_results = []
    for q in extra_queries:
        extra_results.extend(await deep_search(q, max_results=3))
    extra_text = "\n\n".join([f"[DOĞRULAMA {i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:600]}" for i, r in enumerate(extra_results[:6])])
    combined_raw = initial_data.get("raw","") + "\n\n=== ÇAPRAZ DOĞRULAMA SONUÇLARI ===\n" + extra_text

    score_system = """Sen bir kripto fırsat doğrulama uzmanısın. Verilen ham veriyi analiz ederek GÜVENİLİRLİK SKORU hesapla.
SKOR KRİTERLERİ (0-100): +20: Resmi web sitesi, +20: Bilinen proje, +15: Birden fazla kaynak, +15: Net ödül, +10: Pozitif yorumlar. -20: Tek kaynak, -25: Scam geçiyor, -30: Kaynak yok.
ÇIKTI FORMAT (SADECE JSON): {"score": 75, "verdict": "GÜVENİLİR/ŞÜPHELİ/RİSKLİ", "reasons": ["neden1"], "warning": ""}"""
    result_str = await ai(score_system, f"Proje: {name}\n\n{combined_raw[:5000]}", tokens=400, temp=0.1)
    try:
        json_match = re.search(r"\{.*\}", result_str, re.DOTALL)
        score_data = json.loads(json_match.group()) if json_match else {"score": 50, "verdict": "BELİRSİZ", "reasons": [], "warning": ""}
    except: score_data = {"score": 50, "verdict": "BELİRSİZ", "reasons": [], "warning": ""}
    score_data["extra_raw"] = extra_text
    return score_data

def format_score_badge(score: int, verdict: str) -> str:
    if score >= 75: return f"🟢 {verdict} ({score}/100)"
    elif score >= 50: return f"🟡 {verdict} ({score}/100)"
    return f"🔴 {verdict} ({score}/100)"

async def analyze_research(data: dict) -> str:
    system = """Sen deneyimli bir kripto fırsat araştırmacısısın. HAM VERİDEN SADECE gerçek bilgileri çıkar. Rakamlar kaynaktan kopyalanacak.
FORMAT:
📌 PLATFORM/PROJE: [adı]
🏷 TÜRÜ: [tip]
💰 ÖDÜL: [EXACT rakam]
👥 KİMLER: [hedef]
📋 ADIMLAR: ...
💎 TOPLAM: ...
⏰ SON TARİH: ...
🔗 LİNK: ...
⭐ GÜVENİLİRLİK: [1-5]
⚠️ UYARI: ...
Türkçe yaz."""
    return await ai(system, f"Proje: {data['name']}\n\n{data['raw']}", tokens=2000, temp=0.1)

# ──────────────────────────────────────────────────────────────────
# TARAMA & POST (GÖRSEL DÜZEN)
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

async def run_opportunity_search(cats: list[str] | None = None) -> list[dict]:
    seen_urls, results, seen_cats = set(), [], set()
    for category, query in OPPORTUNITY_QUERIES:
        if cats and category not in cats: continue
        if category in seen_cats: continue
        seen_cats.add(category)
        hits = await deep_search(query, max_results=4)
        for r in hits:
            url = r.get("url", "")
            if url not in seen_urls:
                seen_urls.add(url)
                results.append({"category": category, "title": r.get("title", ""), "url": url, "content": r.get("content", "")[:1200]})
        if len(results) >= 20: break
    return results

async def scan_active_airdrops(cats: list[str] | None = None) -> str:
    raw_results = await run_opportunity_search(cats=cats)
    if not raw_results: return "❌ Veri çekilemedi. Lütfen tekrar deneyin."
    by_cat = {}
    for r in raw_results: by_cat.setdefault(r["category"], []).append(r)
    cat_labels = {"bonus": "🎁 BORSA KAYIT BONUSU", "referral": "👥 REFERRAL", "kampanya": "🏆 İŞLEM KAMPANYASI", "sosyal": "📱 SOSYAL GÖREV", "airdrop": "🪂 AIRDROP"}
    combined_raw = ""
    for cat, items in by_cat.items():
        combined_raw += f"\n\n{'='*40}\n{cat_labels.get(cat, cat.upper())}\n{'='*40}\n"
        for item in items[:3]: combined_raw += f"Başlık: {item['title']}\nURL: {item['url']}\nİçerik: {item['content']}\n---\n"
    system = """Sen kripto fırsatları araştıran uzman bir analistsin. Sadece rakamlı, aktif fırsatları listele.
FORMAT:
━━━━━━━━━━━━━━━━━━━━━━
🎁 [PLATFORM]
┣ 💰 Ödül: [KAYNAKTAN EXACT]
┣ 🏦 Tür: [tip]
┣ 👥 Kimler: [hedef]
┣ 📋 Adımlar: 1️⃣ ... 2️⃣ ... 3️⃣ ...
┣ ⏰ Son Tarih: [...]
┣ ⭐ Güvenilirlik: [...]
┗ 🔗 [URL]
4-6 fırsat yaz. Türkçe."""
    return await ai(system, combined_raw[:8000], tokens=3500, temp=0.1)

# ──────────────────────────────────────────────────────────────────
# POST OLUŞTURMA (2. RESİMDEKİ GİBİ)
# ──────────────────────────────────────────────────────────────────
POST_SYSTEM = """Sen KriptoDropTR Telegram kanalı için airdrop/fırsat postları hazırlıyorsun.

⛔ KESİN YASAKLAR:
Analizde OLMAYAN rakam, tarih, URL yazma.
Referral/promo kodu ASLA yazma.
Hashtag (#) yasak.
Şablon ifadelerini ("yoksa sil" gibi) bırakma.
Link için SADECE: `[🔗 TIKLA 🖊]` kullan.
Türkçe yaz.

🎨 YAPI VE DÜZEN (Aynen bu sıralamayı ve emojileri kullan):

🎁 [PLATFORM ADI] [BAŞLIK]! 🎁
[Tek cümle açıklama — örn: "Yeni kullanıcılara özel 880 TL bonus kazanma fırsatı"] 🤑

────────────────

✅ YAPMAN GEREKENLER:

1️⃣ [adım 1]
2️⃣ [adım 2]
3️⃣ [adım 3]

────────────────

🔗 Hemen Katıl → `[🔗 TIKLA 🖊]`

💡 Görev zorluğu: [Kolay/Orta/Zor]
💸 Ödül miktarı: [KAYNAKTAN ALINAN TAM RAKAM]
⭐ Airdrop puanı: [1-5 arası yıldız sayısı]

📅 Kampanya Dönemi: [tarih — yoksa bu satırı komple sil]
⏰ Son Tarih: [tarih — yoksa "Devam ediyor" yaz]

────────────────

🔔 Daha fazla airdrop için duyuru kanalını pinle 📌
📢 @kriptodropduyuru
🎁 @kriptodroptr

——
Skor: [🟢 GÜVENİLİR / 🟡 ŞÜPHELİ / 🔴 RİSKLİ] ([Skor]/100)
"""

POST_SYSTEM_SHORT = """KriptoDropTR için kısa airdrop postu yaz.
⛔ Uydurma rakam, referral kodu, hashtag yasak.
✅ HTML: kalın | Link: `[🔗 TIKLA 🖊]` | Maks 300 karakter | Türkçe
YAPI:
🎁 [PLATFORM] — [BAŞLIK]! ✨
1️⃣ [adım 1]
2️⃣ [adım 2]
3️⃣ [adım 3]
💸 Ödül: [rakam] · 💡 [Kolay/Orta/Zor]
🔗 [ TIKLA 🖊]
[🟢 GÜVENİLİR/🟡 ŞÜPHELİ/🔴 RİSKLİ]
📢 @kriptodropduyuru | 🎁 @kriptodroptr"""

POST_SYSTEM_SUMMARY = """KriptoDropTR için 2-3 satır airdrop özeti yaz.
⛔ Uydurma rakam, referral kodu, hashtag yasak.
HTML: kalın | Link: `[🔗 TIKLA 🖊]` | Türkçe
FORMAT:
🎁 [PLATFORM] — [ödül] kazan! ✨ [1 cümle nasıl]. 🔗 [🔗 TIKLA 🖊]
[🟢/🟡/🔴] · 📢 @kriptodropduyuru 🎁 @kriptodroptr"""

def _build_prompt(analysis: str, project_name: str) -> str:
    # Analiz içindeki skor/uyarı kısımlarını temizle
    clean_analysis = re.split(r'(?:📊|Skor|Güvenilirlik Raporu|⚠️ UYARI:).*', analysis, flags=re.IGNORECASE)[0].strip()
    return (
        f"Platform/Proje: {project_name}\n\n"
        f"=== ARAŞTIRMA ANALİZİ ===\n{clean_analysis}\n\n"
        f"=== KESİN KURALLAR ===\n"
        f"1. SADECE yukarıdaki analizde AÇIKÇA geçen rakamları kullan\n"
        f"2. Referral kodu, promo kodu, davet kodu YAZMA\n"
        f"3. Bir satırı dolduracak bilgi yoksa o satırı komple SİL\n"
        f"4. Adımları analizden al, kendin adım uydurma\n"
        f"5. [🔗 TIKLA 🖊] placeholder'ını koru"
    )

async def build_post(analysis: str, project_name: str, score_data: dict = None, fmt: str = "long") -> str:
    prompt = _build_prompt(analysis, project_name)
    if fmt == "short":
        post = await ai(POST_SYSTEM_SHORT, prompt, tokens=500, temp=0.3)
    elif fmt == "summary":
        post = await ai(POST_SYSTEM_SUMMARY, prompt, tokens=200, temp=0.3)
    else:
        post = await ai(POST_SYSTEM, prompt, tokens=1200, temp=0.3)
    
    # Skor footer'ını ekle
    if score_data:
        score = score_data.get("score", 50)
        verdict = score_data.get("verdict", "BELİRSİZ")
        badge = "🟢 GÜVENİLİR" if score >= 75 else "🟡 ŞÜPHELİ" if score >= 50 else "🔴 RİSKLİ"
        post += f"\n\n——\nSkor: {badge} ({score}/100)"
        
    return md_to_html(post)

# ──────────────────────────────────────────────────────────────────
# TELEGRAM HELPERS
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
    link_label  = "✅ Link Eklendi" if has_link else "🔗 Link Ekle"
    fmt_long    = "📄 Uzun ●" if fmt == "long" else "📄 Uzun"
    fmt_short   = "📝 Kısa ●" if fmt == "short" else "📝 Kısa"
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
    text = re.sub(r'(?m)^#+\s.*$', '', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'<b>\1</b>', text)
    text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    return re.sub(r'\n{3,}', '\n\n', text).strip()

# ──────────────────────────────────────────────────────────────────
# HANDLERLAR
# ──────────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.type in ("group", "supergroup", "channel"): return
    if update.effective_user.id != ADMIN_CHAT_ID: return
    context.user_data.clear()
    await update.message.reply_text("🤖 AIRDROP BOT — Admin Paneli\n\n━━━━━━━━━━━━━━━━━━━━\n🔍 Airdrop Tara → İnterneti tara\n✍️ Post Oluştur → Derin araştır\n📢 Gruba Gönder → Hazır postu gönder\n━━━━━━━━━━━━━━━━━━━━\n💡 Direkt mesajla çalışır.", parse_mode=ParseMode.HTML, reply_markup=main_menu())

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    await update.message.reply_text("📖 KOMUTLAR\n\n/start — Ana menü\n/scan — Tara\n/post `[isim]` — Araştır & post\n/sendgroup — Son postu gruba gönder\n\n💡 Direkt URL veya proje adı yazabilirsin.", parse_mode=ParseMode.HTML)

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    await update.message.reply_text("🔍 Hangi kategoriyi tarayalım?\n\nHepsi → tüm kategoriler taranır.", parse_mode=ParseMode.HTML, reply_markup=category_filter_menu())

async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
    if not context.args:
        await update.message.reply_text("⚠️ Kullanım: `/post [airdrop adı]`", parse_mode=ParseMode.HTML); return
    await _do_research(update, context, " ".join(context.args))

async def cmd_sendgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_CHAT_ID: return
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
            await update.message.reply_text("⚠️ Format hatalı: `PLATFORM | https://link.com`", parse_mode=ParseMode.HTML); return
        lnk = register_link(parts[1], parts[0])
        await update.message.reply_text(f"✅ *Link kaydedildi!*\n🔑 ID: `{lnk['id']}`\n🏦 Platform: *{lnk['platform']}*\n🌐 URL: `{lnk['url'][:60]}`", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    if waiting == "add_link":
        context.user_data["waiting_for"] = None
        post = context.user_data.get("last_post", "")
        link = text.strip()
        updated = post.replace("[🔗 TIKLA 🖊]", link)
        context.user_data["final_post"] = updated; context.user_data["has_link"] = True
        platform = context.user_data.get("last_post_platform", "crypto")
        await update.message.reply_text("✅ *Link eklendi!* Görsel aranıyor...", parse_mode=ParseMode.HTML)
        img_url = await get_image(f"{platform} crypto")
        caption = md_to_html(updated[:1024] if len(updated) > 1024 else updated)
        try:
            if img_url: await update.message.reply_photo(photo=img_url, caption=caption, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True))
            else: await update.message.reply_text(f"📣 *GÜNCEL POST:*\n\n{md_to_html(updated)}\n\nHazır! Gruba gönderebilirsin.", parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True))
        except: pass; return

    if waiting in ("edit_post", "edit_post_inline"):
        context.user_data["waiting_for"] = None; context.user_data["final_post"] = text; context.user_data["last_post"] = text
        fmt = context.user_data.get("post_fmt", "long")
        preview = f"✅ <b>Post güncellendi!</b>\n\n━━━━━━━━━━━━━━━━━━━━\n{md_to_html(text)}\n━━━━━━━━━━━━━━━━━━━━"
        if len(preview) > 4096: preview = preview[:4086] + "..."
        await update.message.reply_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=context.user_data.get("has_link",False), fmt=fmt)); return

    if waiting == "track_deadline":
        context.user_data["waiting_for"] = None
        deadline = text.strip(); project_name = context.user_data.get("last_project", "?")
        analysis = context.user_data.get("last_analysis", ""); post = context.user_data.get("final_post", "")
        await track_opportunity(project_name, deadline, analysis, post)
        await update.message.reply_text(f"📌 <b>{project_name}</b> takibe alındı!\n⏰ Son Tarih: <code>{deadline}</code>\n🔔 3 gün kala hatırlatma gelecek.", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    await _do_research(update, context, text)

async def _do_research(update: Update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    if await is_blacklisted(input_text):
        await update.effective_message.reply_text(f"🚫 {input_text} kara listede!", parse_mode=ParseMode.HTML, reply_markup=main_menu()); return

    msg = await update.effective_message.reply_text(f"🔬 <b>Araştırma başladı:</b> <code>{input_text[:60]}</code>\n⏳ 30-60 sn sürebilir...", parse_mode=ParseMode.HTML)
    await update.effective_chat.send_action(ChatAction.TYPING)

    if is_url(input_text):
        await msg.edit_text("🔗 <b>URL içeriği çekiliyor...</b>", parse_mode=ParseMode.HTML)
        data = await research_airdrop_by_url(input_text)
    else:
        await msg.edit_text(f"🔍 <b>'{input_text}' araştırılıyor...</b>\n<i>Çoklu sorgu çalışıyor...</i>", parse_mode=ParseMode.HTML)
        data = await research_airdrop_by_name(input_text)

    project_name = data.get("name", input_text)
    await msg.edit_text("🔁 <b>Çoklu kaynak doğrulanıyor...</b>\n<i>Güvenilirlik skoru hesaplanıyor...</i>", parse_mode=ParseMode.HTML)
    score_data = await verify_and_score(project_name, data)
    score, verdict, reasons, warning = score_data.get("score", 50), score_data.get("verdict", "BELİRSİZ"), score_data.get("reasons", []), score_data.get("warning", "")
    badge = format_score_badge(score, verdict)
    context.user_data["last_score"] = score_data; context.user_data["last_project"] = project_name

    await msg.edit_text("🤖 <b>AI analizi yapılıyor...</b>", parse_mode=ParseMode.HTML)
    enriched_data = data.copy()
    enriched_data["raw"] = data.get("raw", "") + "\n\n=== DOĞRULAMA ===\n" + score_data.get("extra_raw", "")
    analysis = await analyze_research(enriched_data)
    context.user_data["last_analysis"] = analysis

    await msg.edit_text("✍️ <b>Post yazılıyor...</b>", parse_mode=ParseMode.HTML)
    post = await build_post(analysis, project_name, score_data=score_data)
    context.user_data["last_post"] = post; context.user_data["final_post"] = post
    context.user_data["last_post_platform"] = project_name; context.user_data["has_link"] = False; context.user_data["post_fmt"] = "long"
    await save_post_archive(project_name, post, "long")

    reasons_text = "\n".join([f"  • {r}" for r in reasons]) if reasons else "  • Bilgi yetersiz"
    score_msg = f"📊 <b>GÜVENİLİRLİK RAPORU — {project_name.upper()}</b>\n━━━━━━━━━━━━━━━━━━━━\nSkor: <b>{badge}</b>\n\n📋 <b>Değerlendirme:</b>\n{reasons_text}\n"
    if warning: score_msg += f"\n⚠️ <b>Uyarı:</b> {warning}\n"
    score_msg += f"\n{md_to_html(analysis)}"
    if len(score_msg) > 4000: score_msg = score_msg[:3990] + "\n<i>...kırpıldı</i>"
    await msg.edit_text(score_msg, parse_mode=ParseMode.HTML)

    post_preview = f"━━━━━━━━━━━━━━━━━━━━\n📣 <b>HAZIRLANAN POST:</b>\n\n{md_to_html(post)}\n\n━━━━━━━━━━━━━━━━━━━━"
    if len(post_preview) > 4096: post_preview = post_preview[:4086] + "..."
    await update.effective_message.reply_text(post_preview, parse_mode=ParseMode.HTML, reply_markup=post_actions_extended(has_link=False, fmt="long", score=score))

async def _send_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, with_photo: bool):
    post = context.user_data.get("final_post") or context.user_data.get("last_post")
    if not post:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("⚠️ Önce bir post oluştur!"); return
    platform = context.user_data.get("last_post_platform", "cryptocurrency airdrop")
    try:
        if with_photo:
            img_url = await get_image(f"{platform} crypto blockchain token")
            caption = post[:1024] if len(post) > 1024 else post
            if img_url: await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=img_url, caption=md_to_html(caption), parse_mode=ParseMode.HTML)
            else: await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=md_to_html(post), parse_mode=ParseMode.HTML)
        else:
            await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=md_to_html(post), parse_mode=ParseMode.HTML)
        confirm = "✅ *Post gruba gönderildi!* " + (" 🖼️ (görsel ile)" if with_photo else "")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(confirm, parse_mode=ParseMode.HTML, reply_markup=main_menu())
    except Exception as e:
        logger.error(f"Gönderme hatası: {e}")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(f"❌ Gönderim hatası: `{e}`", parse_mode=ParseMode.HTML)

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type in ("group", "supergroup", "channel"): return
        if update.effective_user.id != ADMIN_CHAT_ID: return
        return await func(update, context)
    wrapper.__name__ = func.__name__; return wrapper

def admin_only_callback(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat.type in ("group", "supergroup", "channel"):
            await update.callback_query.answer(); return
        if update.effective_user.id != ADMIN_CHAT_ID:
            await update.callback_query.answer(); return
        return await func(update, context)
    wrapper.__name__ = func.__name__; return wrapper

@admin_only_callback
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; await q.answer(); data = q.data
    if data == "home": await q.message.reply_text("🏠 *Ana Menü*", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "help": await q.message.reply_text("📖 *KOMUTLAR*\n/scan — Aktif airdropları tara\n/post `[isim]` — Araştır & post\n/sendgroup — Son postu gruba gönder\n💡 Direkt yazabilirsin.", parse_mode=ParseMode.HTML)
    elif data == "scan":
        msg = await q.message.reply_text("🌐 *Taranıyor...*\n_Tüm kripto fırsatları aranıyor (30-50 sn)_", parse_mode=ParseMode.HTML)
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = await scan_active_airdrops(); context.user_data["last_scan"] = result
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")], [InlineKeyboardButton("🔄 Yeniden Tara", callback_data="scan"), InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]])
        text = f"✅ *FIRSATLAR TARANDI*\n\n{md_to_html(result)}"
        if len(text) > 4096: text = text[:4086] + "..."
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif data == "manual_post":
        context.user_data["waiting_for"] = None
        await q.message.reply_text("✍️ *Manuel Araştırma*\n\n• Airdrop / proje adı\n• Airdrop URL'si\n\n_Örnek: `Arbitrum` veya `https://...`_", parse_mode=ParseMode.HTML)
    elif data == "add_link":
        context.user_data["waiting_for"] = "add_link"
        saved_btns = [[InlineKeyboardButton(f"[{lnk['id']}] {lnk['platform']}", callback_data=f"link_use_{lnk['id']}")] for lnk in list(_LINK_STORE.values())[-4:]]
        saved_btns.append([InlineKeyboardButton("🏠 İptal", callback_data="home")])
        kb = InlineKeyboardMarkup(saved_btns) if _LINK_STORE else None
        text_msg = "🔗 *Link Ekle*\n\n" + ("Kayıtlı linklerinden birini seç *veya* aşağıya yapıştır:\n\n" if _LINK_STORE else "") + "_Linki buraya yazabilirsin:_"
        await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=kb)
    elif data == "send_text": await _send_to_group(update, context, with_photo=False)
    elif data == "send_photo":
        await q.message.reply_text("🖼️ Görsel aranıyor...", parse_mode=ParseMode.HTML)
        await _send_to_group(update, context, with_photo=True)
    elif data == "regen_post":
        analysis, project, fmt = context.user_data.get("last_analysis"), context.user_data.get("last_project", ""), context.user_data.get("post_fmt", "long")
        if not analysis: await q.message.reply_text("⚠️ Yenilemek için önce araştırma yap."); return
        fmt_label = {"long": "📄 Uzun", "short": "📝 Kısa", "summary": "⚡ Özet"}
        msg = await q.message.reply_text(f"♻️ *{fmt_label.get(fmt,'Post')} yeniden yazılıyor...*", parse_mode=ParseMode.HTML)
        post = await build_post(analysis, project, fmt=fmt)
        context.user_data["last_post"] = post; context.user_data["final_post"] = post; context.user_data["has_link"] = False
        preview = f"♻️ *YENİLENEN POST ({fmt_label.get(fmt,'').upper()}):*\n\n{md_to_html(post)}\n\n👇 * Link Ekle* butonuna bas, sonra gruba gönder."
        if len(preview) > 4096: preview = preview[:4086] + "..."
        await msg.edit_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False, fmt=fmt))
    elif data in ("fmt_long", "fmt_short", "fmt_summary"):
        analysis, project = context.user_data.get("last_analysis"), context.user_data.get("last_project", "")
        if not analysis: await q.answer("⚠️ Önce araştırma yap.", show_alert=True); return
        fmt_map = {"fmt_long": "long", "fmt_short": "short", "fmt_summary": "summary"}
        fmt_label = {"long": "📄 Uzun", "short": "📝 Kısa", "summary": "⚡ Özet"}
        fmt = fmt_map[data]
        await q.answer(f"{fmt_label[fmt]} format seçildi...")
        msg = await q.message.reply_text(f"{fmt_label[fmt]} *format hazırlanıyor...*", parse_mode=ParseMode.HTML)
        post = await build_post(analysis, project, fmt=fmt)
        context.user_data["last_post"] = post; context.user_data["final_post"] = post; context.user_data["has_link"] = False; context.user_data["post_fmt"] = fmt
        preview = f"{'📄' if fmt=='long' else '📝' if fmt=='short' else '⚡'} *{fmt_label[fmt].upper()} FORMAT:*\n\n{md_to_html(post)}\n\n👇 * Link Ekle* butonuna bas, sonra gruba gönder."
        if len(preview) > 4096: preview = preview[:4086] + "..."
        await msg.edit_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False, fmt=fmt))
    elif data == "scan_menu":
        await q.message.reply_text("🔍 *Hangi kategoriyi tarayalım?*\n\nSadece belirli bir türü taramak için seç.\n_Hepsi → tüm kategoriler_", parse_mode=ParseMode.HTML, reply_markup=category_filter_menu())
    elif data.startswith("cat_"):
        cat_key = data[4:]
        _, cats = CATEGORY_DEFS.get(cat_key, ("Hepsi", None))
        cat_label, _ = CATEGORY_DEFS.get(cat_key, ("🌐 Hepsi", None))
        msg = await q.message.reply_text(f"🌐 *{cat_label} taranıyor...*\n_30-50 sn sürebilir_", parse_mode=ParseMode.HTML)
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = await scan_active_airdrops(cats=cats); context.user_data["last_scan"] = result
        keyboard = InlineKeyboardMarkup([[InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")], [InlineKeyboardButton("🔄 Yeniden Tara", callback_data=data), InlineKeyboardButton("🔍 Kategori Değiştir", callback_data="scan_menu")], [InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]])
        text = f"✅ *{cat_label.upper()} TARAMASI TAMAMLANDI*\n\n{md_to_html(result)}"
        if len(text) > 4096: text = text[:4086] + "..."
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    elif data == "link_stats":
        stats = get_link_stats()
        await q.message.reply_text(stats, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔗 Linklerimi Yönet", callback_data="link_manage"), InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))
    elif data == "link_manage":
        await q.message.reply_text("🔗 *KAYIT LİNKLERİM*\n\nBir linki seçerek posta ekleyebilirsin.\nYeni link eklemek için *➕ Yeni Link Ekle*'ye bas.", parse_mode=ParseMode.HTML, reply_markup=get_link_list_menu())
    elif data == "link_add_new":
        context.user_data["waiting_for"] = "link_add"
        await q.message.reply_text("🔗 *Yeni Kayıt Linki Ekle*\n\nŞu formatta yaz:\n`PLATFORM_ADI | https://link.com/referral`\n\n_Örnek: `CoinTR | https://partner...`_", parse_mode=ParseMode.HTML)
    elif data.startswith("link_use_"):
        lid = data[9:]; lnk = _LINK_STORE.get(lid)
        if not lnk: await q.answer("Link bulunamadı.", show_alert=True); return
        post = context.user_data.get("last_post", "")
        if not post: await q.answer("⚠️ Önce bir post oluştur.", show_alert=True); return
        updated = post.replace("[🔗 TIKLA 🖊]", lnk["url"]); context.user_data["final_post"] = updated; context.user_data["has_link"] = True
        record_post_use(lid); await q.answer(f"✅ {lnk['platform']} linki eklendi!", show_alert=False)
        fmt = context.user_data.get("post_fmt", "long")
        preview = f"✅ *{lnk['platform']} linki eklendi!*\n\n{md_to_html(updated)}"
        if len(preview) > 4096: preview = preview[:4086] + "..."
        await q.message.reply_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=True, fmt=fmt))
    elif data == "link_clear": _LINK_STORE.clear(); await q.answer("🗑️ Tüm linkler silindi.", show_alert=True); await q.message.reply_text("🗑️ Kayıtlı tüm linkler silindi.", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "edit_post_inline":
        context.user_data["waiting_for"] = "edit_post_inline"
        current = context.user_data.get("final_post", "")
        await q.message.reply_text(f"✏️ <b>Postu Düzenle</b>\n\nAşağıdaki metni değiştirerek gönder:\n<i>(Tüm metni yeniden yaz)</i>\n\n<code>{current[:800]}</code>", parse_mode=ParseMode.HTML)
    elif data == "track_opp":
        context.user_data["waiting_for"] = "track_deadline"
        project = context.user_data.get("last_project", "?")
        await q.message.reply_text(f"📌 <b>{project}</b> takibe alınıyor...\n\nSon tarihi gir (ör: <code>31.05.2026</code>)\nBilmiyorsan <code>belirsiz</code> yaz:", parse_mode=ParseMode.HTML)
    elif data == "blacklist_opp":
        project = context.user_data.get("last_project", "?")
        await add_to_blacklist(project); await q.answer(f"🚫 {project} kara listeye eklendi!", show_alert=True)
        await q.message.reply_text(f"🚫 <b>{project}</b> kara listeye eklendi.\nBu proje artık gösterilmeyecek.", parse_mode=ParseMode.HTML, reply_markup=main_menu())
    elif data == "tracked_list":
        items = await get_tracked()
        if not items: await q.message.reply_text("📌 <b>Takip Listesi</b>\n\nHenüz takip edilen fırsat yok.\nAraştırma sonrası <b>Fırsatı Takibe Al</b> butonuna bas.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))
        else:
            text_msg = "📌 <b>TAKİP LİSTESİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"; kb = []
            for opp in items[-8:]:
                text_msg += f"• <b>{opp['name']}</b> | ⏰ {opp.get('deadline','?')} | 📅 {opp['added']}\n"
                kb.append([InlineKeyboardButton(f"🗑 {opp['name'][:20]}", callback_data=f"untrack_{opp['id']}"), InlineKeyboardButton("✍️ Post", callback_data=f"repost_{opp['id']}")])
            kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("untrack_"):
        tid = data[8:]; await remove_tracked(tid); await q.answer("✅ Takipten çıkarıldı.", show_alert=False)
        items = await get_tracked()
        if not items: await q.message.reply_text("📌 Takip listesi boş.", parse_mode=ParseMode.HTML, reply_markup=main_menu())
        else:
            text_msg = "📌 <b>TAKİP LİSTESİ (güncellendi)</b>\n\n"
            for opp in items[-8:]: text_msg += f"• <b>{opp['name']}</b> | ⏰ {opp.get('deadline','?')}\n"
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))
    elif data.startswith("repost_"):
        tid = data[7:]; items = {o["id"]: o for o in await get_tracked()}; opp = items.get(tid)
        if opp:
            context.user_data["last_post"] = opp.get("post", ""); context.user_data["final_post"] = opp.get("post", "")
            context.user_data["last_project"] = opp.get("name", "")
            preview = f"📣 <b>POST:</b>\n\n{md_to_html(opp.get('post',''))}"
            if len(preview) > 4096: preview = preview[:4086] + "..."
            await q.message.reply_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False))
        else: await q.answer("Fırsat bulunamadı.", show_alert=True)
    elif data == "post_archive":
        posts = await get_post_archive()
        if not posts: await q.message.reply_text("📁 <b>Post Arşivi</b>\n\nHenüz arşivlenmiş post yok.", parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))
        else:
            text_msg = "📁 <b>POST ARŞİVİ</b> (son 10)\n━━━━━━━━━━━━━━━━━━━━\n\n"; kb = []
            for p in posts[:10]:
                text_msg += f"• <b>{p['project']}</b> | {p['fmt']} | {p['date']}\n"
                kb.append([InlineKeyboardButton(f"📄 {p['project'][:25]} ({p['date'][:5]})", callback_data=f"archive_load_{p['id']}")])
            kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))
    elif data.startswith("archive_load_"):
        pid = data[13:]; posts = {p["id"]: p for p in await get_post_archive()}; p = posts.get(pid)
        if p:
            context.user_data["last_post"] = p["post"]; context.user_data["final_post"] = p["post"]
            context.user_data["last_project"] = p["project"]; context.user_data["post_fmt"] = p["fmt"]
            preview = f"📄 <b>{p['project']}</b> | {p['date']}\n\n{md_to_html(p['post'])}"
            if len(preview) > 4096: preview = preview[:4086] + "..."
            await q.message.reply_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False, fmt=p["fmt"]))
        else: await q.answer("Post bulunamadı.", show_alert=True)
    elif data == "blacklist_view":
        bl = await get_blacklist(); text_msg = "🚫 <b>KARA LİSTE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if bl: 
            for item in bl: text_msg += f"• {item}\n"
        else: text_msg += "Kara liste boş."
        await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))
    elif data == "new_research":
        context.user_data["waiting_for"] = None
        await q.message.reply_text("🔬 *Yeni araştırma için airdrop adı veya linkini yaz:*", parse_mode=ParseMode.HTML)

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
    logger.info("🚀 Airdrop Bot başlatıldı. Async mod aktif.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
