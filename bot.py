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

# ── Kayıt Linki Takip Sistemi ─────────────────────────────────────────
import hashlib as _hashlib, time as _time, random as _random

_LINK_STORE: dict = {}   # {"LINK_ID": {...meta...}}

def _gen_link_id() -> str:
    lid = _hashlib.md5(str(_random.random()).encode()).hexdigest()[:6].upper()
    return lid if lid not in _LINK_STORE else _gen_link_id()

def register_link(original_url: str, platform: str, category: str = "genel") -> dict:
    """Yeni link kaydı oluştur."""
    lid = _gen_link_id()
    _LINK_STORE[lid] = {
        "id":       lid,
        "url":      original_url,
        "platform": platform,
        "category": category,
        "created":  _time.strftime("%d.%m.%Y %H:%M"),
        "clicks":   0,
        "posts":    0,   # kaç posta eklendi
    }
    return _LINK_STORE[lid]

def record_post_use(link_id: str):
    """Link bir posta eklenince sayacı artır."""
    if link_id in _LINK_STORE:
        _LINK_STORE[link_id]["posts"] += 1

def get_link_stats() -> str:
    """Admin için istatistik özeti."""
    if not _LINK_STORE:
        return "📊 Henüz kayıtlı link yok.\n/link_ekle komutuyla link ekleyebilirsin."
    lines = ["📊 *KAYIT LİNKİ İSTATİSTİKLERİ*\n━━━━━━━━━━━━━━━━━━━━"]
    sorted_links = sorted(_LINK_STORE.values(), key=lambda x: x["posts"], reverse=True)
    for lnk in sorted_links[:10]:
        short_url = lnk["url"][:45] + ("..." if len(lnk["url"]) > 45 else "")
        lines.append(
            f"🔗 *{lnk['platform']}* `[{lnk['id']}]`\n"
            f"   📤 {lnk['posts']} posta eklendi | 📅 {lnk['created']}\n"
            f"   🌐 `{short_url}`"
        )
    return "\n\n".join(lines)

def get_link_list_menu() -> InlineKeyboardMarkup:
    """Kayıtlı linkleri buton olarak göster."""
    if not _LINK_STORE:
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("➕ Yeni Link Ekle", callback_data="link_add_new"),
            InlineKeyboardButton("🏠 Ana Menü", callback_data="home"),
        ]])
    rows = []
    for lnk in list(_LINK_STORE.values())[-8:]:  # son 8 link
        label = f"[{lnk['id']}] {lnk['platform']} ({lnk['posts']} post)"
        rows.append([InlineKeyboardButton(label, callback_data=f"link_use_{lnk['id']}")])
    rows.append([
        InlineKeyboardButton("➕ Yeni Link Ekle", callback_data="link_add_new"),
        InlineKeyboardButton("🗑️ Temizle", callback_data="link_clear"),
    ])
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
    return InlineKeyboardMarkup(rows)

groq_client   = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

# ══════════════════════════════════════════════════════════
#  ADMIN GUARD
# ══════════════════════════════════════════════════════════
def admin_only(func):
    """Decorator: yalnızca admin DM'den erişebilir. Grupları ve yabancıları sessizce yoksay."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id   = update.effective_user.id
        chat_type = update.effective_chat.type
        # Grup/kanal ise hiç cevap verme
        if chat_type in ("group", "supergroup", "channel"):
            return
        # Admin değilse sessiz kal (uyarı mesajı yazma)
        if user_id != ADMIN_CHAT_ID:
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def admin_only_callback(func):
    """Decorator: callback butonlar için admin guard."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID:
            await update.callback_query.answer("⛔ Yetkisiz erişim.", show_alert=True)
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

# ══════════════════════════════════════════════════════════
#  GROQ — AI ÜRETME
# ══════════════════════════════════════════════════════════
def ai(system: str, user: str, tokens: int = 1800, temp: float = 0.75) -> str:
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
            max_tokens=tokens,
            temperature=temp,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq hata: {e}")
        return "❌ AI yanıt üretemedi."

# ══════════════════════════════════════════════════════════
#  TAVILY — DERIN ARAMA
# ══════════════════════════════════════════════════════════
AIRDROP_DOMAINS = [
    "airdrops.io", "earnifi.com", "cryptorank.io", "dappradar.com",
    "coinmarketcap.com", "coingecko.com", "coindesk.com", "decrypt.co",
    "cointelegraph.com", "theblock.co", "beincrypto.com", "cryptoslate.com",
    "blockworks.co", "twitter.com", "x.com", "medium.com", "mirror.xyz"
]

def deep_search(query: str, max_results: int = 10) -> list[dict]:
    """Tavily advanced search – tüm kaynakları tara."""
    try:
        r = tavily_client.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            include_answer=True,
        )
        return r.get("results", [])
    except Exception as e:
        logger.error(f"Tavily hata: {e}")
        return []

def fetch_url_content(url: str) -> str:
    """URL içeriğini Tavily extract ile çek."""
    try:
        r = tavily_client.extract(urls=[url])
        results = r.get("results", [])
        if results:
            return results[0].get("raw_content", "")[:3000]
    except Exception as e:
        logger.error(f"URL çekme hata: {e}")
    return ""

def is_url(text: str) -> bool:
    return bool(re.match(r"https?://\S+", text.strip()))

# ══════════════════════════════════════════════════════════
#  UNSPLASH — GÖRSEL
# ══════════════════════════════════════════════════════════
def get_image(query: str = "cryptocurrency airdrop") -> str | None:
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 6, "orientation": "landscape",
                    "client_id": UNSPLASH_ACCESS_KEY},
            timeout=10,
        )
        results = r.json().get("results", [])
        if results:
            import random
            return random.choice(results[:4])["urls"]["regular"]
    except Exception as e:
        logger.error(f"Unsplash hata: {e}")
    return None

# ══════════════════════════════════════════════════════════
#  ARAŞTIRMA FONKSİYONLARI
# ══════════════════════════════════════════════════════════

def research_airdrop_by_name(name: str) -> dict:
    """
    Platform/proje adına göre derin araştırma.
    Borsa bonusu, airdrop, kampanya — her türü kapsar.
    """
    queries = [
        f"{name} yeni kullanıcı bonusu kayıt ödülü nasıl alınır 2025 2026",
        f"{name} new user bonus sign up reward how to claim 2025 2026",
        f"{name} airdrop campaign tasks eligibility reward amount",
        f"{name} referral program bonus USDT earn invite",
        f"{name} crypto promotion trading reward deposit bonus",
    ]
    all_results = []
    for q in queries:
        all_results.extend(deep_search(q, max_results=5))

    # Tekrar edenleri filtrele
    seen_urls = set()
    unique = []
    for item in all_results:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    # Uzun içerik — 1200 karakter/kaynak
    raw_text = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:1200]}"
        for i, r in enumerate(unique[:12])
    ])

    # En alakalı sayfanın tam içeriğini de çek
    if unique:
        best_url = unique[0].get("url", "")
        try:
            full = fetch_url_content(best_url)
            if full:
                raw_text = f"=== TAM SAYFA İÇERİĞİ ({best_url}) ===\n{full[:3000]}\n\n=== DİĞER KAYNAKLAR ===\n{raw_text}"
        except Exception:
            pass

    return {"name": name, "raw": raw_text, "sources": unique[:12]}


def research_airdrop_by_url(url: str) -> dict:
    """
    URL'ye göre derin araştırma:
    - URL içeriğini çek
    - Ek arama sorgularıyla zenginleştir
    """
    content = fetch_url_content(url)

    # İçerikten proje adı çıkar (AI ile)
    name_hint = ai(
        "Extract the project or airdrop name from the text. Reply with ONLY the name, nothing else.",
        content[:500] if content else url,
        tokens=50, temp=0.1
    )

    extra = deep_search(f"{name_hint} airdrop claim guide tasks 2025", max_results=6)
    extra_text = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:400]}"
        for i, r in enumerate(extra[:6])
    ])

    raw = f"=== SAYFA İÇERİĞİ ===\n{content}\n\n=== EK KAYNAKLAR ===\n{extra_text}"
    return {"name": name_hint.strip(), "raw": raw, "sources": extra[:6], "url": url}


def analyze_research(data: dict) -> str:
    """AI ile araştırma verisini analiz et — sadece belgeli bilgileri yaz."""
    system = """Sen deneyimli bir kripto kazanım fırsatı araştırmacısısın.
Görevin: Verilen HAM VERİDEN gerçek bilgileri çıkarmak.

KRİTİK KURAL: Sadece ham veride AÇIKÇA geçen bilgileri yaz.
Rakamlar, tarihler, adımlar — hepsini KAYNAK VERİDEN al.
Eğer ham veride geçmiyorsa o alanı "Bulunamadı" yaz — ASLA uydurma.

ÇIKART:
PLATFORM/PROJE: (adı ve ne olduğu)
FIRSATIN TÜRÜ: (borsa bonusu / airdrop / kampanya / referral — hangisi?)
ÖDÜL MİKTARI: (varsa EXACT rakam — "60.000 USDT" değil, kaynakta ne yazıyorsa)
KİMLER KATILABİLİR: (yeni kullanıcı / mevcut / herkes)
ADIMLAR: (kaynakta yazan GERÇEK adımlar, numaralı)
  → Her adımın ödülü varsa onu da yaz
TOPLAM KAZANILABİLİR: (varsa)
SON TARİH: (varsa)
KATILABİLECEK LİNK: (varsa ham veride geçen URL)
GÜVENİLİRLİK: ⭐⭐⭐⭐⭐ (tanınan borsa/proje ise yüksek)
UYARI: (varsa dikkat edilmesi gereken şey — min yatırım, KYC zorunluluğu vb.)

Türkçe yaz. Uydurma yapma — yoksa "Bulunamadı" yaz."""

    return ai(system, f"Proje: {data['name']}\n\n{data['raw']}", tokens=2000)


# ── Fırsat kategorileri ve arama sorguları ──────────────────────────────
# ── Fırsat arama sorguları ─────────────────────────────────────────────
# Borsa kayıt bonusu + kampanya ağırlıklı, airdrop destekli
OPPORTUNITY_QUERIES = [
    # Borsa yeni kullanıcı bonusu — Türkçe borsalar dahil
    ("bonus", "kripto borsa yeni kullanıcı ödülü kayıt bonusu USDT TL 2025"),
    ("bonus", "crypto exchange new user bonus welcome reward USDT 2025 site:binance.com OR site:bybit.com OR site:okx.com OR site:cointr.com OR site:bitlo.com"),
    ("bonus", "crypto exchange sign up reward deposit bonus free USDT 2026"),
    ("bonus", "borsa kayıt kampanyası hediye 2025 site:cointr.com OR site:paribu.com OR site:btcturk.com"),
    # Referral / davet kampanyası
    ("referral", "crypto referral program earn USDT invite friends commission 2025 2026"),
    ("referral", "kripto borsa arkadaş davet et kazan referral ödülü 2025"),
    # İşlem / trading kampanyası
    ("kampanya", "crypto exchange trading competition reward prize USDT 2025 2026"),
    ("kampanya", "kripto borsa işlem kampanyası ödül havuzu 2025"),
    # Telegram / sosyal görev ödülü
    ("sosyal", "telegram crypto bot task reward earn token USDT 2025"),
    ("sosyal", "crypto project telegram task reward points 2025 airdrop"),
    # Klasik kolay airdrop
    ("airdrop", "easy crypto airdrop 2026 free token social task discord twitter"),
    ("airdrop", "new airdrop claim 2025 2026 galxe zealy no stake required free"),
]


# Kategori tanımları — kullanıcıya gösterilen label ve filtre key'i
CATEGORY_DEFS = {
    "hepsi":    ("🌐 Hepsi",          None),           # filtre yok
    "bonus":    ("🎁 Borsa Bonusu",   ["bonus"]),
    "referral": ("👥 Referral",       ["referral"]),
    "kampanya": ("🏆 Kampanya",       ["kampanya"]),
    "sosyal":   ("📱 Sosyal Görev",   ["sosyal"]),
    "airdrop":  ("🪂 Airdrop",        ["airdrop"]),
}


def category_filter_menu() -> InlineKeyboardMarkup:
    """Tarama öncesi kategori seçim menüsü."""
    rows = []
    keys = list(CATEGORY_DEFS.keys())
    for i in range(0, len(keys), 3):
        row = []
        for k in keys[i:i+3]:
            label, _ = CATEGORY_DEFS[k]
            row.append(InlineKeyboardButton(label, callback_data=f"cat_{k}"))
        rows.append(row)
    rows.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
    return InlineKeyboardMarkup(rows)


def run_opportunity_search(cats: list[str] | None = None) -> list[dict]:
    """
    Tavily araması — cats verilirse sadece o kategorileri tara.
    cats=None → hepsi
    """
    seen_urls = set()
    results = []
    for category, query in OPPORTUNITY_QUERIES:
        if cats and category not in cats:
            continue
        hits = deep_search(query, max_results=4)
        for r in hits:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({
                "category": category,
                "title":    r.get("title", ""),
                "url":      url,
                "content":  r.get("content", "")[:1200],
            })
    return results


def scan_active_airdrops(cats: list[str] | None = None) -> str:
    """
    Kripto kazanım fırsatı tarayıcısı.
    cats=None → tüm kategoriler
    """
    raw_results = run_opportunity_search(cats=cats)

    if not raw_results:
        return "❌ Veri çekilemedi. Lütfen tekrar deneyin."

    # Kategoriye göre grupla
    by_cat: dict = {}
    for r in raw_results:
        cat = r["category"]
        by_cat.setdefault(cat, []).append(r)

    cat_labels = {
        "bonus":    "🎁 BORSA KAYIT / YENİ KULLANICI BONUSU",
        "referral": "👥 REFERRAL / DAVET KAMPANYASI",
        "kampanya": "🏆 İŞLEM / TRADİNG KAMPANYASI",
        "sosyal":   "📱 TELEGRAM / SOSYAL GÖREV ÖDÜLÜ",
        "airdrop":  "🪂 AIRDROP",
    }

    combined_raw = ""
    for cat, items in by_cat.items():
        label = cat_labels.get(cat, cat.upper())
        sep = "=" * 40
        combined_raw += f"\n\n{sep}\n{label}\n{sep}\n"
        for item in items[:3]:
            t = item["title"]
            u = item["url"]
            c = item["content"]
            combined_raw += f"Başlık: {t}\nURL: {u}\nİçerik: {c}\n---\n"
    system = """Sen kripto para kazanım fırsatları araştıran uzman bir analistsin.
Amacın: Sıradan bir kullanıcının GERÇEKTEN para kazanabileceği, somut rakamlı, aktif fırsatları bulmak.

ÖNCELİKLİ FIRSATLAR (bunları ön plana çıkar):
🎁 Borsa kayıt bonusu — Yeni üye ol, KYC yap, işlem yap → USDT/TL kazan
   Örnek: "Kayıt ol + 1000TL yatır → 200TL bonus"
👥 Referral kampanyası — Arkadaşını davet et → komisyon/bonus kazan
🏆 Trading kampanyası — Belirli hacimde işlem yap → ödül havuzundan pay al
📱 Telegram/Discord görevi — Bot kullan, görev tamamla → token/USDT kazan
🪂 Kolay airdrop — Sosyal takip, form doldur → token kazan

REDDET:
❌ Validator/node gerektiren
❌ 10.000$+ yatırım gerektiren
❌ Sona ermiş kampanyalar
❌ Bilgisi belirsiz/eksik fırsatlar

FORMAT — CoinTR örneğindeki gibi somut ve detaylı yaz:

🎁 *[BORSA/PLATFORM ADI]*
├ 💰 Toplam Ödül: [somut rakam — örn: 2600 TL veya 50 USDT]
├ 🏦 Platform: [borsa veya platform adı]
├ 👥 Kimler: [Yeni kullanıcı / Mevcut kullanıcı / Herkes]
├ 📋 Adımlar:
│  1️⃣ [ilk adım → kaç TL/USDT]
│  2️⃣ [ikinci adım → kaç TL/USDT]
│  3️⃣ [üçüncü adım → kaç TL/USDT]
├ ⏰ Son Tarih: [tarih veya kampanya süresi boyunca]
├ ⭐ Güvenilirlik: [⭐⭐⭐⭐⭐]
└ 🔗 Link: [direkt kayıt/katılım linki]

KURALLAR:
- Rakamlar somut olsun: "2600 TL", "50 USDT", "25$ bonus" gibi
- Adımlar numaralı ve net olsun
- Eksik bilgi varsa o fırsatı ATLA — asla "?" yazma
- 5-8 fırsat listele, kaliteli ve gerçek olanları seç
- Türkçe yaz"""

    return ai(system, combined_raw[:8000], tokens=3500)

# ══════════════════════════════════════════════════════════
#  POST OLUŞTURMA
# ══════════════════════════════════════════════════════════

POST_SYSTEM = """Sen Telegram kripto topluluklarına yönelik çarpıcı, dikkat çekici kazanım fırsatı postları yazan uzmansın.

KURAL:
- Türkçe yaz
- Telegram normal Markdown (*bold*, _italic_) — MarkdownV2 KULLANMA
- KESİNLİKLE hashtag (#) kullanma — hiçbir satırda etiket yok
- Link için tam olarak şu metni bırak: [🔗 LİNK]
- SADECE analizde geçen gerçek rakamları kullan — asla uydurma
- Adımlar numaralı, her adımın yanında ödülü varsa yaz
- Post dolup taşsın — boş alan bırakma, her bilgiyi doldur
- Maksimum 1000 karakter

ŞABLON:

🚨 *[PLATFORM] — [KISA BAŞLIK]* 🚨

┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
🎁 *Yeni Kullanıcı Ödülü:* [rakam + birim]
🔄 *Mevcut Kullanıcı:* [rakam varsa — yoksa satırı sil]
👤 *Kimler:* [yeni üye / herkes / ülke kısıtı]
┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄

✅ *Nasıl Kazanılır?*
1️⃣ [adım] → [ödül miktarı]
2️⃣ [adım] → [ödül miktarı]
3️⃣ [adım] → [ödül miktarı]
4️⃣ [adım] → [ödül miktarı]

┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄
💎 *Toplam Kazanılabilir:* [toplam rakam]
⏳ *Son Tarih:* [tarih veya kampanya süresi boyunca]
⚠️ *Dikkat:* [min yatırım / KYC / kısıt — yoksa satırı sil]
┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄

🔥 *Hemen Katıl — Kontenjan Dolmadan!*
👉 [🔗 LİNK]

_⚡ Bu fırsatı kaçırma — arkadaşlarınla da paylaş!_"""

# ── Post sistem promptları ─────────────────────────────────────────────

POST_SYSTEM_SHORT = """Sen Telegram için KISA ve öz kripto fırsat postları yazıyorsun.
KURAL:
- Türkçe, normal Markdown (*bold*, _italic_)
- Hashtag (#) KULLANMA
- Maksimum 400 karakter — kısa tut
- Link için: [🔗 LİNK]
- Sadece analizde geçen gerçek rakamları kullan

ŞABLON:
💥 *[PLATFORM] — [BAŞLIK]*

💰 *Ödül:* [rakam]
✅ [en önemli 1-2 adım]

👉 [🔗 LİNK]
_⚡ Hızlı ol!_"""

POST_SYSTEM_SUMMARY = """Sen Telegram için tek satır kripto fırsat özeti yazıyorsun.
KURAL:
- Türkçe
- Tek mesaj, 1-3 satır MAX
- Hashtag (#) KULLANMA
- Link için: [🔗 LİNK]
- Sadece analizde geçen gerçek rakamları kullan

FORMAT:
🎁 *[PLATFORM]* — [ödül miktarı] kazan! [1 cümle nasıl]. 👉 [🔗 LİNK]"""


def _build_prompt(analysis: str, project_name: str) -> str:
    return (
        f"Platform/Proje: {project_name}\n\n"
        f"=== ARAŞTIRMA ANALİZİ ===\n{analysis}\n\n"
        f"=== TALİMAT ===\n"
        f"Yukarıdaki ANALİZ VERİSİNİ kullanarak post oluştur.\n"
        f"SADECE analizde geçen rakam ve bilgileri kullan — uydurma yapma.\n"
        f"Ödül miktarı bulunamadıysa o satırı kaldır."
    )


def build_post(analysis: str, project_name: str, fmt: str = "long") -> str:
    """
    fmt: "long" | "short" | "summary"
    """
    prompt = _build_prompt(analysis, project_name)
    if fmt == "short":
        return ai(POST_SYSTEM_SHORT, prompt, tokens=450, temp=0.5)
    elif fmt == "summary":
        return ai(POST_SYSTEM_SUMMARY, prompt, tokens=200, temp=0.5)
    else:
        return ai(POST_SYSTEM, prompt, tokens=1000, temp=0.5)

# ══════════════════════════════════════════════════════════
#  TELEGRAM HELPERS
# ══════════════════════════════════════════════════════════

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Fırsat Tara", callback_data="scan_menu"),
         InlineKeyboardButton("✍️ Post Oluştur", callback_data="manual_post")],
        [InlineKeyboardButton("📊 Link İstatistik", callback_data="link_stats"),
         InlineKeyboardButton("🔗 Linklerimi Yönet", callback_data="link_manage")],
        [InlineKeyboardButton("🔄 Yeni Araştırma", callback_data="new_research"),
         InlineKeyboardButton("❓ Yardım", callback_data="help")],
    ])

def post_actions(has_link: bool = False, fmt: str = "long") -> InlineKeyboardMarkup:
    link_btn_label = "✅ Link Eklendi" if has_link else "🔗 Link Ekle"
    # Format butonları — aktif olan kalın gösterilemez ama emoji ile vurgulanır
    fmt_long    = "📄 Uzun ●" if fmt == "long"    else "📄 Uzun"
    fmt_short   = "📝 Kısa ●" if fmt == "short"   else "📝 Kısa"
    fmt_summary = "⚡ Özet ●" if fmt == "summary" else "⚡ Özet"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(fmt_long,    callback_data="fmt_long"),
         InlineKeyboardButton(fmt_short,   callback_data="fmt_short"),
         InlineKeyboardButton(fmt_summary, callback_data="fmt_summary")],
        [InlineKeyboardButton(link_btn_label, callback_data="add_link")],
        [InlineKeyboardButton("📢 Gruba Gönder (Metin)", callback_data="send_text"),
         InlineKeyboardButton("🖼️ Görsel ile Gönder", callback_data="send_photo")],
        [InlineKeyboardButton("♻️ Yenile", callback_data="regen_post"),
         InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
    ])

async def typing(update: Update):
    await update.effective_chat.send_action(ChatAction.TYPING)

def safe_md(text: str) -> str:
    """Markdown parse hatalarına karşı temizle, hashtag kaldır."""
    import re
    text = text.replace("**", "*")
    # Satır başındaki hashtag satırlarını temizle
    text = re.sub(r'(?m)^#\w[\w\s#]*$', "", text)
    # Satır içi hashtag'leri kaldır
    text = re.sub(r'#\w+', "", text)
    # Birden fazla boş satırı teke indir
    text = re.sub(r'\n{3,}', "\n\n", text)
    return text.strip()

# ══════════════════════════════════════════════════════════
#  KOMUTLAR
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id   = update.effective_user.id
    chat_type = update.effective_chat.type
    if user_id != ADMIN_CHAT_ID or chat_type != "private":
        await update.message.reply_text(
            f"⛔ Bu bot yalnızca admin DM'inden kullanılabilir.\n\n"
            f"🆔 Senin ID'n: `{user_id}`\n"
            f"Eğer admin sensin Railway Variables'da `ADMIN_CHAT_ID = {user_id}` yap.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 *AIRDROP BOT* — Admin Paneli\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 *Airdrop Tara* → İnterneti tara, aktif airdropları listele\n"
        "✍️ *Post Oluştur* → Airdrop adı veya link at, derin araştır\n"
        "📢 *Gruba Gönder* → Hazır postu gruba gönder\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 _Airdrop adı veya linki direkt yazabilirsin._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )

@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *KOMUTLAR*\n\n"
        "/start — Ana menü\n"
        "/scan — İnterneti tara, aktif airdropları listele\n"
        "/post `[isim]` — İsme göre araştır & post oluştur\n"
        "/sendgroup — Son postu gruba gönder\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Direkt mesaj:*\n"
        "• Bir URL at → sayfa derin araştırılır\n"
        "• Airdrop adı yaz → derin araştırma başlar\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ Post hazır olunca *🔗 Link Ekle* butonuna bas,\n"
        "linki yapıştır — post'a otomatik eklenir.",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 *Hangi kategoriyi tarayalım?*\n\n"
        "_Hepsi → tüm kategoriler taranır (daha uzun sürer)_",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=category_filter_menu(),
    )

@admin_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Kullanım: `/post [airdrop adı]`\nÖrnek: `/post Arbitrum`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    name = " ".join(context.args)
    await _do_research(update, context, name)

@admin_only
async def cmd_sendgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_to_group(update, context, with_photo=False)

# ══════════════════════════════════════════════════════════
#  MESAJ İŞLEYİCİ — URL veya Airdrop Adı
# ══════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Grup / kanal mesajlarını tamamen yoksay
    if update.effective_chat.type in ("group", "supergroup", "channel"):
        return
    # Sadece admin DM'i işle, başkasına sessiz kal
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting_for")

    # Yeni kayıt linki ekleme state
    if waiting == "link_add":
        context.user_data["waiting_for"] = None
        parts = [p.strip() for p in text.split("|", 1)]
        if len(parts) != 2 or not parts[1].startswith("http"):
            await update.message.reply_text(
                "⚠️ Format hatalı. Şöyle yaz:\n"
                "`PLATFORM_ADI | https://link.com`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        platform_name, url = parts
        lnk = register_link(url, platform_name)
        await update.message.reply_text(
            f"✅ *Link kaydedildi!*\n\n"
            f"🔑 ID: `{lnk['id']}`\n"
            f"🏦 Platform: *{lnk['platform']}*\n"
            f"🌐 URL: `{lnk['url'][:60]}`\n\n"
            f"Artık *🔗 Linklerimi Yönet* menüsünden postlara ekleyebilirsin.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    # Link ekleme state
    if waiting == "add_link":
        context.user_data["waiting_for"] = None
        post = context.user_data.get("last_post", "")
        link = text.strip()
        updated = post.replace("[🔗 LİNK]", link)
        context.user_data["final_post"] = updated
        context.user_data["has_link"] = True

        # Görsel çek ve DM'de önizle
        platform = context.user_data.get("last_post_platform", "crypto")
        await update.message.reply_text(
            "✅ *Link eklendi!* Görsel aranıyor...",
            parse_mode=ParseMode.MARKDOWN,
        )
        img_url = get_image(f"{platform} crypto")
        caption = safe_md(updated[:1024] if len(updated) > 1024 else updated)

        if img_url:
            try:
                await update.message.reply_photo(
                    photo=img_url,
                    caption=caption,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=post_actions(has_link=True),
                )
            except Exception:
                # Görsel gönderilemezse metin olarak göster
                preview = (
                    f"📣 *GÜNCEL POST:*\n\n{safe_md(updated)}\n\n"
                    f"Hazır! Gruba gönderebilirsin."
                )
                if len(preview) > 4096:
                    preview = preview[:4086] + "_"
                await update.message.reply_text(
                    preview,
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=post_actions(has_link=True),
                )
        else:
            preview = (
                f"📣 *GÜNCEL POST:*\n\n{safe_md(updated)}\n\n"
                f"Hazır! Gruba gönderebilirsin."
            )
            if len(preview) > 4096:
                preview = preview[:4086] + "_"
            await update.message.reply_text(
                preview,
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=post_actions(has_link=True),
            )
        return

    # Post düzenleme (eski compat)
    if waiting == "edit_post":
        context.user_data["waiting_for"] = None
        context.user_data["final_post"] = text
        await update.message.reply_text(
            "✅ *Post güncellendi!*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_actions(),
        )
        return

    # URL veya airdrop adı
    await _do_research(update, context, text)


async def _do_research(update: Update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    """Derin araştırma → AI analiz → Post oluştur."""
    msg = await update.effective_message.reply_text(
        "🔬 *Derin araştırma başlıyor...*\n\n"
        f"📌 Girdi: `{input_text[:80]}`\n\n"
        "_Bu işlem 20-40 saniye sürebilir..._",
        parse_mode=ParseMode.MARKDOWN,
    )
    await update.effective_chat.send_action(ChatAction.TYPING)

    # 1. Araştır
    if is_url(input_text):
        await msg.edit_text("🔗 *URL içeriği çekiliyor...*", parse_mode=ParseMode.MARKDOWN)
        data = research_airdrop_by_url(input_text)
    else:
        await msg.edit_text(
            f"🔍 *'{input_text}' için derin arama yapılıyor...*\n_3 farklı sorgu çalışıyor..._",
            parse_mode=ParseMode.MARKDOWN,
        )
        data = research_airdrop_by_name(input_text)

    project_name = data.get("name", input_text)

    # 2. Analiz et
    await msg.edit_text("🤖 *AI analizi yapılıyor...*", parse_mode=ParseMode.MARKDOWN)
    analysis = analyze_research(data)
    context.user_data["last_analysis"] = analysis
    context.user_data["last_project"] = project_name

    # 3. Post oluştur
    await msg.edit_text("✍️ *Telegram postu yazılıyor...*", parse_mode=ParseMode.MARKDOWN)
    post = build_post(analysis, project_name)
    context.user_data["last_post"] = post
    context.user_data["final_post"] = post
    context.user_data["last_post_platform"] = project_name

    # 4. Analizi göster
    analysis_msg = (
        f"📊 *ARAŞTIRMA RAPORU — {project_name.upper()}*\n\n"
        f"{safe_md(analysis)}"
    )

    # Uzun ise böl
    if len(analysis_msg) > 4000:
        analysis_msg = analysis_msg[:3990] + "\n_...devamı kırpıldı_"

    await msg.edit_text(analysis_msg, parse_mode=ParseMode.MARKDOWN)

    # 5. Postu göster
    post_preview = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📣 *HAZIRLANAN POST:*\n\n"
        f"{safe_md(post)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 *🔗 Link Ekle* butonuna bas, linki yapıştır, sonra gruba gönder."
    )

    if len(post_preview) > 4096:
        post_preview = post_preview[:4086] + "_"

    context.user_data["has_link"]   = False
    context.user_data["post_fmt"]   = "long"
    await update.effective_message.reply_text(
        post_preview,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=post_actions(has_link=False, fmt="long"),
    )

# ══════════════════════════════════════════════════════════
#  GRUBA GÖNDERME
# ══════════════════════════════════════════════════════════

async def _send_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, with_photo: bool):
    post = context.user_data.get("final_post") or context.user_data.get("last_post")
    if not post:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("⚠️ Önce bir post oluştur!")
        return

    platform = context.user_data.get("last_post_platform", "cryptocurrency airdrop")

    try:
        if with_photo:
            img_url = get_image(f"{platform} crypto blockchain token")
            caption = post[:1024] if len(post) > 1024 else post
            if img_url:
                await context.bot.send_photo(
                    chat_id=GROUP_CHAT_ID,
                    photo=img_url,
                    caption=safe_md(caption),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=safe_md(post),
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=safe_md(post),
                parse_mode=ParseMode.MARKDOWN,
            )

        confirm = "✅ *Post gruba gönderildi!*" + (" 🖼️ (görsel ile)" if with_photo else "")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(confirm, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

    except Exception as e:
        logger.error(f"Gönderme hatası: {e}")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(f"❌ Gönderim hatası: `{e}`", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════════════════
#  CALLBACK BUTONLAR
# ══════════════════════════════════════════════════════════

@admin_only_callback
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "home":
        await q.message.reply_text(
            "🏠 *Ana Menü*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )

    elif data == "help":
        await q.message.reply_text(
            "📖 *KOMUTLAR*\n\n"
            "/scan — Aktif airdropları tara\n"
            "/post `[isim]` — Araştır & post oluştur\n"
            "/sendgroup — Son postu gruba gönder\n\n"
            "💡 Direkt airdrop adı veya link yazabilirsin.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "scan":
        msg = await q.message.reply_text(
            "🌐 *Taranıyor...*\n_Tüm kripto fırsatları aranıyor (30-50 sn)_",
            parse_mode=ParseMode.MARKDOWN,
        )
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = scan_active_airdrops()
        context.user_data["last_scan"] = result

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")],
            [InlineKeyboardButton("🔄 Yeniden Tara", callback_data="scan"),
             InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
        ])
        text = f"✅ *FIRSATLAR TARANDII*\n\n{safe_md(result)}"
        if len(text) > 4096:
            text = text[:4086] + "_"
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

    elif data == "manual_post":
        context.user_data["waiting_for"] = None
        await q.message.reply_text(
            "✍️ *Manuel Araştırma*\n\n"
            "Aşağıdakilerden birini yaz:\n"
            "• Airdrop / proje adı\n"
            "• Airdrop URL'si\n\n"
            "_Örnek: `Arbitrum` veya `https://arbitrum.io/airdrop`_",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "add_link":
        context.user_data["waiting_for"] = "add_link"
        # Kayıtlı link varsa seçim sunulsun
        saved_btns = []
        for lnk in list(_LINK_STORE.values())[-4:]:
            label = f"[{lnk['id']}] {lnk['platform']}"
            saved_btns.append([InlineKeyboardButton(label, callback_data=f"link_use_{lnk['id']}")])
        saved_btns.append([InlineKeyboardButton("🏠 İptal", callback_data="home")])
        kb = InlineKeyboardMarkup(saved_btns) if _LINK_STORE else None

        text_msg = (
            "🔗 *Link Ekle*\n\n"
            + ("Kayıtlı linklerinden birini seç *veya* aşağıya yeni link yapıştır:\n\n" if _LINK_STORE else "")
            + "_Linki buraya yazabilirsin:_"
        )
        await q.message.reply_text(
            text_msg,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    elif data == "send_text":
        await _send_to_group(update, context, with_photo=False)

    elif data == "send_photo":
        await q.message.reply_text("🖼️ Görsel aranıyor...", parse_mode=ParseMode.MARKDOWN)
        await _send_to_group(update, context, with_photo=True)

    elif data == "regen_post":
        analysis = context.user_data.get("last_analysis")
        project  = context.user_data.get("last_project", "")
        fmt      = context.user_data.get("post_fmt", "long")
        if not analysis:
            await q.message.reply_text("⚠️ Yenilemek için önce bir araştırma yap.")
            return
        fmt_label = {"long": "📄 Uzun", "short": "📝 Kısa", "summary": "⚡ Özet"}
        msg = await q.message.reply_text(
            f"♻️ *{fmt_label.get(fmt,'Post')} yeniden yazılıyor...*",
            parse_mode=ParseMode.MARKDOWN,
        )
        post = build_post(analysis, project, fmt=fmt)
        context.user_data["last_post"]   = post
        context.user_data["final_post"]  = post
        context.user_data["has_link"]    = False
        preview = (
            f"♻️ *YENİLENEN POST ({fmt_label.get(fmt,'').upper()}):*\n\n{safe_md(post)}\n\n"
            f"👇 *🔗 Link Ekle* butonuna bas, sonra gruba gönder."
        )
        if len(preview) > 4096:
            preview = preview[:4086] + "_"
        await msg.edit_text(
            preview,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_actions(has_link=False, fmt=fmt),
        )

    elif data in ("fmt_long", "fmt_short", "fmt_summary"):
        analysis = context.user_data.get("last_analysis")
        project  = context.user_data.get("last_project", "")
        if not analysis:
            await q.answer("⚠️ Önce bir araştırma yap.", show_alert=True)
            return
        fmt_map  = {"fmt_long": "long", "fmt_short": "short", "fmt_summary": "summary"}
        fmt_label = {"long": "📄 Uzun", "short": "📝 Kısa", "summary": "⚡ Özet"}
        fmt = fmt_map[data]
        await q.answer(f"{fmt_label[fmt]} format seçildi...")
        msg = await q.message.reply_text(
            f"{fmt_label[fmt]} *format hazırlanıyor...*",
            parse_mode=ParseMode.MARKDOWN,
        )
        post = build_post(analysis, project, fmt=fmt)
        context.user_data["last_post"]  = post
        context.user_data["final_post"] = post
        context.user_data["has_link"]   = False
        context.user_data["post_fmt"]   = fmt

        preview = (
            f"{'📄' if fmt=='long' else '📝' if fmt=='short' else '⚡'} "
            f"*{fmt_label[fmt].upper()} FORMAT:*\n\n"
            f"{safe_md(post)}\n\n"
            f"👇 *🔗 Link Ekle* butonuna bas, sonra gruba gönder."
        )
        if len(preview) > 4096:
            preview = preview[:4086] + "_"
        await msg.edit_text(
            preview,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_actions(has_link=False, fmt=fmt),
        )

    elif data == "scan_menu":
        await q.message.reply_text(
            "🔍 *Hangi kategoriyi tarayalım?*\n\n"
            "Sadece belirli bir türü taramak için seç.\n"
            "_Hepsi → tüm kategoriler taranır (daha uzun sürer)_",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=category_filter_menu(),
        )

    elif data.startswith("cat_"):
        cat_key = data[4:]   # "cat_bonus" → "bonus"
        _, cats = CATEGORY_DEFS.get(cat_key, ("Hepsi", None))
        cat_label, _ = CATEGORY_DEFS.get(cat_key, ("🌐 Hepsi", None))
        msg = await q.message.reply_text(
            f"🌐 *{cat_label} taranıyor...*\n_30-50 saniye sürebilir_",
            parse_mode=ParseMode.MARKDOWN,
        )
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = scan_active_airdrops(cats=cats)
        context.user_data["last_scan"] = result

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")],
            [InlineKeyboardButton("🔄 Yeniden Tara", callback_data=data),
             InlineKeyboardButton("🔍 Kategori Değiştir", callback_data="scan_menu")],
            [InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
        ])
        text = f"✅ *{cat_label.upper()} TARAMASI TAMAMLANDI*\n\n{safe_md(result)}"
        if len(text) > 4096:
            text = text[:4086] + "_"
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

    # ── Link Yönetimi ─────────────────────────────────────────────────
    elif data == "link_stats":
        stats = get_link_stats()
        await q.message.reply_text(
            stats,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 Linklerimi Yönet", callback_data="link_manage"),
                InlineKeyboardButton("🏠 Ana Menü", callback_data="home"),
            ]]),
        )

    elif data == "link_manage":
        await q.message.reply_text(
            "🔗 *KAYIT LİNKLERİM*\n\nBir linki seçerek posta ekleyebilirsin.\n"
            "Yeni link eklemek için *➕ Yeni Link Ekle*'ye bas.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=get_link_list_menu(),
        )

    elif data == "link_add_new":
        context.user_data["waiting_for"] = "link_add"
        await q.message.reply_text(
            "🔗 *Yeni Kayıt Linki Ekle*\n\n"
            "Şu formatta yaz:\n"
            "`PLATFORM_ADI | https://link.com/referral`\n\n"
            "_Örnek: `CoinTR | https://partner.cointr.com/short/abc`_",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data.startswith("link_use_"):
        lid = data[9:]   # "link_use_AB1234" → "AB1234"
        lnk = _LINK_STORE.get(lid)
        if not lnk:
            await q.answer("Link bulunamadı.", show_alert=True)
            return
        # Postu güncelle — mevcut posta bu linki ekle
        post = context.user_data.get("last_post", "")
        if not post:
            await q.answer("⚠️ Önce bir post oluştur.", show_alert=True)
            return
        updated = post.replace("[🔗 LİNK]", lnk["url"])
        context.user_data["final_post"] = updated
        context.user_data["has_link"]   = True
        record_post_use(lid)
        await q.answer(f"✅ {lnk['platform']} linki eklendi!", show_alert=False)
        fmt = context.user_data.get("post_fmt", "long")
        preview = (
            f"✅ *{lnk['platform']} linki eklendi!*\n\n"
            f"{safe_md(updated)}"
        )
        if len(preview) > 4096:
            preview = preview[:4086] + "_"
        await q.message.reply_text(
            preview,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_actions(has_link=True, fmt=fmt),
        )

    elif data == "link_clear":
        _LINK_STORE.clear()
        await q.answer("🗑️ Tüm linkler silindi.", show_alert=True)
        await q.message.reply_text(
            "🗑️ Kayıtlı tüm linkler silindi.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )

    elif data == "new_research":
        context.user_data["waiting_for"] = None
        await q.message.reply_text(
            "🔬 *Yeni araştırma için airdrop adı veya linkini yaz:*",
            parse_mode=ParseMode.MARKDOWN,
        )

# ══════════════════════════════════════════════════════════
#  OTOMATİK TARAMA — Her 8 Saatte Bir Admin'e Bildir
# ══════════════════════════════════════════════════════════

async def auto_scan_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Otomatik tarama başladı...")
    result = scan_active_airdrops()
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")],
    ])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🔔 *OTOMATİK TARAMA* — _{ts}_\n\n_Airdrop · Borsa bonusu · Kampanya · Testnet · NFT_\n\n{safe_md(result)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Auto scan bildirim hata: {e}")

# ══════════════════════════════════════════════════════════
#  ANA
# ══════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("post",      cmd_post))
    app.add_handler(CommandHandler("sendgroup", cmd_sendgroup))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Otomatik tarama: 8 saatte bir
    app.job_queue.run_repeating(auto_scan_job, interval=28800, first=120)

    logger.info("🚀 Airdrop Bot başlatıldı.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
