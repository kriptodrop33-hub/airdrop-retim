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


# ══════════════════════════════════════════════════════════
#  FIRSAT TAKİP & ARŞİV SİSTEMİ
# ══════════════════════════════════════════════════════════
import json, os, time as _t

_DATA_FILE  = "bot_data.json"

def _load_data() -> dict:
    """JSON dosyasından veriyi yükle."""
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"tracked": {}, "posts": [], "blacklist": []}

def _save_data(data: dict):
    """Veriyi JSON dosyasına kaydet."""
    try:
        with open(_DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Veri kaydetme hatası: {e}")

def track_opportunity(name: str, deadline: str, analysis: str, post: str):
    """Fırsatı takibe al."""
    data = _load_data()
    tid  = str(int(_t.time()))
    data["tracked"][tid] = {
        "id":        tid,
        "name":      name,
        "deadline":  deadline,
        "analysis":  analysis[:500],
        "post":      post,
        "added":     _t.strftime("%d.%m.%Y %H:%M"),
        "warned":    False,
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
    """Postu arşive kaydet."""
    data = _load_data()
    entry = {
        "id":      str(int(_t.time())),
        "project": project,
        "post":    post,
        "fmt":     fmt,
        "date":    _t.strftime("%d.%m.%Y %H:%M"),
    }
    data["posts"].insert(0, entry)
    data["posts"] = data["posts"][:30]  # Son 30 post
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
    return any(name.lower() in b.lower() or b.lower() in name.lower()
               for b in _load_data()["blacklist"])

def check_deadlines() -> list:
    """Son tarihi yaklaşan fırsatları döndür (3 gün veya daha az)."""
    data   = _load_data()
    alerts = []
    today  = datetime.now()
    for tid, opp in data["tracked"].items():
        if opp.get("warned"):
            continue
        dl = opp.get("deadline","")
        if not dl or dl in ("Belirtilmemiş","Bulunamadı",""):
            continue
        for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d/%m/%Y"):
            try:
                dl_dt = datetime.strptime(dl.strip(), fmt)
                days_left = (dl_dt - today).days
                if 0 <= days_left <= 3:
                    alerts.append({**opp, "days_left": days_left})
                    data["tracked"][tid]["warned"] = True
                break
            except Exception:
                pass
    _save_data(data)
    return alerts


def verify_and_score(name: str, initial_data: dict) -> dict:
    """
    Aynı projeyi 2 farklı kaynaktan daha ara, çapraz doğrula.
    Güvenilirlik skoru hesapla — tarih & ödül doğrulama dahil.
    """
    now_tr    = _now_tr()
    now_label = _now_label()

    # İkinci tur arama — güncel ve farklı sorgular
    extra_queries = [
        f"{name} legit scam review reddit {now_label}",
        f"{name} official website social media verified {now_label}",
        f"{name} airdrop aktif mi bitti mi {now_label}",
    ]
    extra_results = []
    for q in extra_queries:
        extra_results.extend(deep_search(q, max_results=3, advanced=True))

    extra_text = "\n\n".join([
        f"[DOĞRULAMA {i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:600]}"
        for i, r in enumerate(extra_results[:8])
    ])

    combined_raw = initial_data.get("raw","") + "\n\n=== ÇAPRAZ DOĞRULAMA SONUÇLARI ===\n" + extra_text

    score_system = f"""Sen bir kripto fırsat doğrulama uzmanısın.
Verilen ham veriyi analiz ederek GÜVENİLİRLİK SKORU hesapla.

🛑 BUGÜNÜN TARİHİ: {now_tr}

SKOR KRİTERLERİ (0-100):
+20: Resmi web sitesi veya sosyal medya bulundu
+20: Bilinen borsa/proje (Binance, OKX, Bybit, Arbitrum vb.)
+15: Birden fazla bağımsız kaynakta aynı bilgi
+15: Net ödül miktarı kaynakta AÇIKÇA yazıyor ve doğrulanabilir
+15: Kampanya son tarihi {now_tr} SONRA → AKTİF
+10: Reddit/Twitter'da pozitif yorumlar var
-20: Yalnızca 1 kaynak bulunan bilinmez proje
-25: "Scam", "fraud", "fake" kelimesi geçiyor
-30: Kaynak bulunamadı veya çok az bilgi var
-40: Son tarihi {now_tr} ÖNCE → SONA ERMİŞ (bu kampanya listelenemez!)
-20: Ödül rakamı kaynakta yok, sadece tahmin/belirsiz

ÇIKTI FORMAT (kesinlikle bu JSON yapısında):
{{
  "score": 75,
  "verdict": "GÜVENİLİR / ŞÜPHELİ / RİSKLİ / SONA ERMİŞ",
  "expired": false,
  "reasons": ["neden 1", "neden 2", "neden 3"],
  "warning": "varsa uyarı metni, yoksa boş string"
}}

SADECE JSON döndür, başka hiçbir şey yazma."""

    result_str = ai(score_system, f"Proje: {name}\n\n{combined_raw[:5000]}", tokens=500, temp=0.1)

    # JSON parse
    try:
        import re as _re
        json_match = _re.search(r"\{.*\}", result_str, _re.DOTALL)
        if json_match:
            score_data = json.loads(json_match.group())
        else:
            score_data = {"score": 50, "verdict": "BELİRSİZ", "expired": False, "reasons": [], "warning": ""}
    except Exception:
        score_data = {"score": 50, "verdict": "BELİRSİZ", "expired": False, "reasons": [], "warning": ""}

    score_data["extra_raw"] = extra_text
    return score_data

def format_score_badge(score: int, verdict: str) -> str:
    """Skora göre rozet metni döndür."""
    if score >= 75:
        return f"🟢 {verdict} ({score}/100)"
    elif score >= 50:
        return f"🟡 {verdict} ({score}/100)"
    else:
        return f"🔴 {verdict} ({score}/100)"

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
    """Decorator: callback butonlar için admin guard. Grup/kanal ve yabancıları sessizce yoksay."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id   = update.effective_user.id
        chat_type = update.effective_chat.type
        # Grup/kanal ise hiç cevap verme
        if chat_type in ("group", "supergroup", "channel"):
            await update.callback_query.answer()  # Telegram spinner'ını kapat, sessiz kal
            return
        # Admin değilse sessiz kal
        if user_id != ADMIN_CHAT_ID:
            await update.callback_query.answer()  # Spinner kapat, uyarı gösterme
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

# ── Dinamik tarih yardımcısı ──────────────────────────────────────────────
def _now_label() -> str:
    """Arama sorgularına eklenecek dinamik AY YILI etiketi: 'April 2026'"""
    return datetime.now().strftime("%B %Y")

def _now_tr() -> str:
    """Güncel tarih Türkçe tam formatta: '12 Nisan 2026'"""
    months_tr = {
        1: "Ocak", 2: "Şubat", 3: "Mart", 4: "Nisan", 5: "Mayıs", 6: "Haziran",
        7: "Temmuz", 8: "Ağustos", 9: "Eylül", 10: "Ekim", 11: "Kasım", 12: "Aralık"
    }
    now = datetime.now()
    return f"{now.day} {months_tr[now.month]} {now.year}"

def _ddg_search(query: str, max_results: int = 5) -> list[dict]:
    """
    Gerçek DuckDuckGo arama — duckduckgo_search kütüphanesi.
    API key yok, limit yok, gerçek sonuçlar döner.
    """
    try:
        from duckduckgo_search import DDGS
        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title":   r.get("title", ""),
                    "url":     r.get("href", ""),
                    "content": r.get("body", ""),
                })
        logger.info(f"DDG arama: '{query[:50]}' → {len(results)} sonuç")
        return results
    except Exception as e:
        logger.error(f"DDG hata: {e}")
        return []

def _httpx_scrape(url: str) -> str:
    """Basit HTTP scrape — URL içeriğini çek ve temizle."""
    try:
        r = requests.get(url, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            text = re.sub(r'<[^>]+>', ' ', r.text)
            text = re.sub(r'\s+', ' ', text)
            return text[:3000]
    except Exception as e:
        logger.debug(f"Scrape hata: {e}")
    return ""

# Tavily kota durumunu takip et
_tavily_quota_ok = True

def deep_search(query: str, max_results: int = 5, advanced: bool = False) -> list[dict]:
    """
    Önce Tavily dene (advanced mode destekli), kota dolmuşsa DuckDuckGo'ya geç.
    advanced=True → Tavily search_depth="advanced" (daha derin, daha güncel sonuçlar)
    """
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            depth = "advanced" if advanced else "basic"
            r = tavily_client.search(
                query=query,
                search_depth=depth,
                max_results=max_results,
                include_answer=False,
                # Güncel sonuçları ön plana çıkar
                include_raw_content=False,
            )
            results = r.get("results", [])
            # Eski sonuçları filtrele: URL'de veya başlıkta geçen yıldan eski yıl varsa ağırlık düşür
            current_year = str(datetime.now().year)
            prev_year    = str(datetime.now().year - 1)
            two_yrs_ago  = str(datetime.now().year - 2)
            filtered = []
            for item in results:
                title   = (item.get("title") or "").lower()
                content = (item.get("content") or "").lower()
                # 2 yıl öncesine ait içeriği at
                if two_yrs_ago in title or two_yrs_ago in content:
                    logger.debug(f"Eski sonuç atlandı: {item.get('url','')}")
                    continue
                filtered.append(item)
            return filtered if filtered else results  # Hepsi eskiyse orijinali döndür
        except Exception as e:
            err_str = str(e)
            if "432" in err_str or "quota" in err_str.lower() or "rate" in err_str.lower():
                logger.warning("Tavily kotası doldu → DuckDuckGo'ya geçildi (ücretsiz, limitsiz)")
                _tavily_quota_ok = False
            else:
                logger.error(f"Tavily hata: {e}")
                return _ddg_search(query, max_results)
    return _ddg_search(query, max_results)

def fetch_url_content(url: str) -> str:
    """URL içeriğini çek — Tavily extract, yoksa httpx scrape."""
    global _tavily_quota_ok
    if _tavily_quota_ok:
        try:
            r = tavily_client.extract(urls=[url])
            results = r.get("results", [])
            if results:
                return results[0].get("raw_content", "")[:3000]
        except Exception as e:
            if "432" in str(e) or "quota" in str(e).lower():
                _tavily_quota_ok = False
                logger.warning("Tavily extract kotası doldu → httpx scrape'e geçildi")
            else:
                logger.error(f"URL çekme hata: {e}")
    return _httpx_scrape(url)

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
    Platform/proje adına göre araştırma — gelişmiş tarih-duyarlı sorgular.
    Tavily: advanced mode, güncel ay+yıl etiketli sorgular.
    """
    now_label = _now_label()   # örn: "April 2026"
    now_tr    = _now_tr()      # örn: "12 Nisan 2026"

    # Güncel ay+yıl eklenmiş, tarih-duyarlı sorgular
    queries = [
        f"{name} new user bonus reward how to claim {now_label} active",
        f"{name} airdrop campaign tasks eligibility reward amount {now_label}",
        f"{name} kripto kampanya kayıt bonusu nasıl alınır {now_label}",
        f"{name} official announcement airdrop {now_label} site:twitter.com OR site:x.com OR site:medium.com",
        f"{name} airdrop deadline end date {now_label}",
    ]
    all_results = []
    for q in queries:
        hits = deep_search(q, max_results=4, advanced=True)  # advanced=True → daha derin
        all_results.extend(hits)
        if len(all_results) >= 12:
            break  # Yeterli sonuç varsa devam etme

    # Tekrar edenleri filtrele
    seen_urls = set()
    unique = []
    for item in all_results:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    raw_text = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:1200]}"
        for i, r in enumerate(unique[:8])
    ])

    # En alakalı sayfanın içeriğini çek (1 kredi)
    if unique:
        best_url = unique[0].get("url", "")
        try:
            full = fetch_url_content(best_url)
            if full:
                raw_text = f"=== TAM SAYFA ({best_url}) ===\n{full[:2500]}\n\n=== DİĞER KAYNAKLAR ===\n{raw_text}"
        except Exception:
            pass

    return {"name": name, "raw": raw_text, "sources": unique[:8]}


def research_airdrop_by_url(url: str) -> dict:
    """
    URL'ye göre derin araştırma:
    - URL içeriğini çek
    - Ek arama sorgularıyla zenginleştir (tarih-duyarlı)
    """
    content = fetch_url_content(url)
    now_label = _now_label()

    # İçerikten proje adı çıkar (AI ile)
    name_hint = ai(
        "Extract the project or airdrop name from the text. Reply with ONLY the name, nothing else.",
        content[:500] if content else url,
        tokens=50, temp=0.1
    )

    extra = deep_search(
        f"{name_hint} airdrop claim guide tasks {now_label} active",
        max_results=6, advanced=True
    )
    # Platform duyuru kaynaklarını da tara
    extra2 = deep_search(
        f"{name_hint} official announcement {now_label} site:twitter.com OR site:medium.com OR site:discord.com",
        max_results=4, advanced=True
    )
    all_extra = extra + extra2
    extra_text = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:500]}"
        for i, r in enumerate(all_extra[:8])
    ])

    raw = f"=== SAYFA İÇERİĞİ ===\n{content}\n\n=== EK KAYNAKLAR ===\n{extra_text}"
    return {"name": name_hint.strip(), "raw": raw, "sources": all_extra[:8], "url": url}


def analyze_research(data: dict) -> str:
    """AI ile araştırma verisini analiz et — sadece belgeli bilgileri yaz. Tarih doğrulamalı."""
    now_tr   = _now_tr()     # örn: "12 Nisan 2026"
    now_year = str(datetime.now().year)
    now_mon  = datetime.now().month
    now_yr_i = datetime.now().year

    system = f"""Sen deneyimli bir kripto kazanım fırsatı araştırmacısısın.
Görevin: HAM VERİDEN SADECE gerçek, belgeli bilgileri çıkarmak.

🛑 BUGÜNÜN TARİHİ: {now_tr}
Bu tarihten önceki kampanyalar SONA ERMİŞ sayılır.

KRİTİK KURALLAR:
1. SADECE ham veride açıkça geçen bilgileri yaz — asla tahmin/uydurma yapma
2. ÖDÜL RAKAMI: yalnızca kaynak metinden kopyala. Kaynak metinde yoksa "Belirtilmemiş" yaz.
   ❌ Asla tahmin etme, "genellikle", "yaklaşık" gibi ifade kullanma
   ❌ Abartılı rakamlara dikkat: kaynakta görünmüyorsa yaz
3. Tarihler KAYNAK metinden kopyalanacak
4. Ham veride yoksa: "Bulunamadı" yaz
5. Son tarihi {now_tr} öncesinde olan kampanyanın başına BÜYÜK harfle «❌ SONA ERMİŞ» yaz
6. Son tarihi belirtilmemiş kampanyaya dikkat: "Son tarih belirsiz — güncelliği doğrulanamadı" ekle
7. Kaynak URL-lerini mutlaka yaz

FORMAT:
📌 PLATFORM/PROJE: [adı ve ne olduğu]
🏷 FIRSATIN TÜRÜ: [borsa bonusu / airdrop / kampanya / referral]
💰 ÖDÜL MİKTARI: [kaynaktaki EXACT rakam — yoksa: Belirtilmemiş]
   KAYNAK: [Bilginin alındığı URL]
👥 KİMLER KATILABİLİR: [yeni kullanıcı / mevcut / herkes]
📋 ADIMLAR:
  1. [kaynak metindeki adım]
  2. [kaynak metindeki adım]
  3. devam...
💎 TOPLAM: [varsa — yoksa belirtme]
⏰ SON TARİH: [varsa — yoksa Belirtilmemiş] →  ❌ SONA ERMİŞ veya ✅ AKTİF bilgisi ekle
🔗 KATILIM LİNKİ: [kaynaktaki URL]
⭐ GÜVENİLİRLİK: [1-5 yıldız + neden]
⚠️ UYARI: [KYC / min yatırım / ülke kısıtı / SONA ERMİŞ OLABİLİR]

Türkçe yaz. Uydurma YAPMA."""

    return ai(system, f"Proje: {data['name']}\n\n{data['raw']}", tokens=800)


# ── Fırsat kategorileri ve arama sorguları ──────────────────────────────
# ── Fırsat arama sorguları (dinamik tarihli) ──────────────────────────────
def _build_opportunity_queries() -> list[tuple[str, str]]:
    """Her çağrıda güncel ay+yıl ile sorgu listesini oluştur."""
    m = _now_label()   # örn: "April 2026"
    y = str(datetime.now().year)
    return [
        # Borsa yeni kullanıcı bonusu — güncel + Türkçe borsalar dahil
        ("bonus", f"kripto borsa yeni üye kampanyası kayıt ödülü {m} USDT TL aktif"),
        ("bonus", f"crypto exchange new user bonus welcome reward USDT {m} site:binance.com OR site:bybit.com OR site:okx.com OR site:cointr.com"),
        ("bonus", f"crypto exchange sign up reward deposit bonus free USDT {m} active"),
        ("bonus", f"borsa kayıt kampanyası hediye {m} site:cointr.com OR site:paribu.com OR site:btcturk.com"),
        # Referral / davet kampanyası
        ("referral", f"crypto referral program earn USDT invite friends commission {m} active"),
        ("referral", f"kripto borsa arkadaş davet et kazan referral ödülü {m}"),
        # İşlem / trading kampanyası
        ("kampanya", f"crypto exchange trading competition reward prize USDT {m}"),
        ("kampanya", f"kripto borsa işlem kampanyası ödül havuzu {m}"),
        # Telegram / sosyal görev ödülü
        ("sosyal", f"telegram crypto bot task reward earn token USDT {m}"),
        ("sosyal", f"crypto project telegram galxe zealy task reward points {m}"),
        # Klasik kolay airdrop — güncel ay
        ("airdrop", f"crypto airdrop claim {m} active free no investment required"),
        ("airdrop", f"galxe zealy intract quest airdrop reward {m} active"),
    ]

# Geriye dönük uyumluluk için sabit liste (dinamik fonksiyon çağrılmazsa)
OPPORTUNITY_QUERIES = _build_opportunity_queries()


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
    Her kategoriden en iyi 1 sorgu kullan (kredi tasarrufu).
    Dinamik tarih ile her çalışmada güncel sorgular oluşturulur.
    """
    live_queries = _build_opportunity_queries()  # Anlık tarihli sorgular
    seen_urls  = set()
    results    = []
    seen_cats  = set()
    for category, query in live_queries:
        if cats and category not in cats:
            continue
        if category in seen_cats:
            continue  # Her kategoriden tek sorgu
        seen_cats.add(category)
        hits = deep_search(query, max_results=5, advanced=True)  # advanced=True → daha güncel
        for r in hits:
            url = r.get("url", "")
            if url in seen_urls:
                continue
            seen_urls.add(url)
            results.append({
                "category": category,
                "title":    r.get("title", ""),
                "url":      url,
                "content":  r.get("content", "")[:1500],
            })
        if len(results) >= 25:
            break  # Yeterli sonuç var, devam etme
    return results


def scan_active_airdrops(cats: list[str] | None = None) -> str:
    """
    Kripto kazanım fırsatı tarayıcısı — tarih-duyarlı, güncel.
    cats=None → tüm kategoriler
    """
    now_tr    = _now_tr()     # örn: "12 Nisan 2026"
    now_label = _now_label()  # örn: "April 2026"
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

    combined_raw = f"BUGÜNÜN TARİHİ: {now_tr}\nARAMA DÖNEMİ: {now_label}\n"
    for cat, items in by_cat.items():
        label = cat_labels.get(cat, cat.upper())
        sep = "=" * 40
        combined_raw += f"\n\n{sep}\n{label}\n{sep}\n"
        for item in items[:3]:
            t = item["title"]
            u = item["url"]
            c = item["content"]
            combined_raw += f"Başlık: {t}\nURL: {u}\nİçerik: {c}\n---\n"

    system = f"""Sen kripto para kazanım fırsatları araştıran uzman bir analistsin.
Amacın: Sıradan bir kullanıcının GERÇEKTEN para kazanabileceği, somut rakamlı, BUGÜN AKTİF fırsatları bulmak.

🛑 BUGÜNÜN TARİHİ: {now_tr}
Bu tarihten önce biten kampanyaları KESİNLİKLE listeye alma!

ÖNCELİK SIRASI:
1. 🎁 Borsa kayıt bonusu — yeni üye ol, az emekle somut TL/USDT kazan
2. 👥 Referral kampanyası — davet et, komisyon kazan
3. 🏆 Trading kampanyası — işlem yap, ödül al
4. 📱 Telegram/sosyal görev — kolay görevler, token kazan
5. 🪂 Airdrop — form doldur, sosyal takip, token kazan

KESİN REDDET (listeye ekleme):
❌ Validator/node çalıştırma gerektiren
❌ 1000$+ yatırım zorunlu olanlar
❌ {now_tr} tarihinden önce biten kampanyalar
❌ Rakamı belirsiz/eksik fırsatlar
❌ Sadece "yakında" diyip tarih vermeyen projeler
❌ Ödül rakamı kaynakta geçmiyorsa yazma — belirsiz bırak

FORMAT (HER fırsat için AYNEN bu yapıyı kullan):

━━━━━━━━━━━━━━━━━━━━━━
🎁 [BORSA/PLATFORM ADI]
┣ 💰 Ödül: [EXACT rakam — kaynakta yoksa: Belirtilmemiş]
┣ 🏦 Tür: [borsa bonusu / airdrop / referral / görev]
┣ 👥 Kimler: [yeni kullanıcı / mevcut / herkes]
┣ 📋 Adımlar:
┃  1️⃣ [adım]
┃  2️⃣ [adım]
┃  3️⃣ [adım]
┣ ⏰ Son Tarih: [tarih / devam ediyor / belirsiz]
┣ ⭐ Güvenilirlik: [⭐⭐⭐⭐⭐]
┗ 🔗 [kayıt/katılım URL]

KURALLAR:
- Somut rakam varsa yaz: "50 USDT", "2600 TL", "500 TOKEN"
- Kaynakta YOK ise: "Ödül: Belirtilmemiş" yaz, uydurma
- 4-6 kaliteli ve AKTİF fırsat listele
- Türkçe yaz, net ve anlaşılır ol"""

    return ai(system, combined_raw[:8000], tokens=3500)

# ══════════════════════════════════════════════════════════
#  POST OLUŞTURMA
# ══════════════════════════════════════════════════════════

# ── POST TASARIMI ────────────────────────────────────────────────────────────
POST_FOOTER = """
────────────────
🔥 Daha fazla airdrop için duyuru kanalını pinle
📢 @kriptodropduyuru
🎁 @kriptodroptr

────────────────
"""

POST_SYSTEM = """
Sen KriptoDropTR Telegram kanalı için Türkçe airdrop/fırsat postları hazırlıyorsun.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KESİN UYULACAK TASARIM KURALLARI:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

⛔ YASAK (kesinlikle yapma):
- Referral kodu, promo kodu yazma
- Uydurma/tahmin rakamı yazma (kaynakta AÇIKÇA geçmeyen ödül)
- Hashtag (#) kullanma
- Uzun paragraf blokları oluşturma
- HTML tag kullanma

✅ FORMAT (aşağıdaki şablona %100 sadık kal):

🚀 [Platform Adı] [Kısa Başlık]! 🎁

[1-2 cümle kısa özet — ödülü ve amacı net belirt] 🤑

────────────────
🔥 YAPMAN GEREKENLER:

①  [Adım 1 — kaynak metinden al]
②  [Adım 2 — kaynak metinden al]
③  [Adım 3 — kaynak metinden al, yoksa bu satırı SİL]
④  [Adım 4 — yalnızca varsa ekle, yoksa SİL]

────────────────
»» Hemen Kaydol: ⚡ [🔗 TIKLA ⚡]
»» Etkinlik Sayfası: ⚡ [🔗 TIKLA ⚡]  ← yalnızca ayrı link varsa ekle, yoksa SİL

Görev zorluğu: [Kolay / Orta / Zor]
Ödül miktarı:  [Kaynaktaki EXACT rakam — yoksa: Belirtilmemiş]
Airdrop puanı: [⭐ sayısı skora göre: 1-5]

🗓 Kampanya Dönemi: [Tarih aralığı — yoksa: Belirtilmemiş]
────────────────

KURALLAR:
1. Adım numaraları mutlaka ① ② ③ ④ ⑤ daireli unicode kullan
2. Ayırıcı: ──────────────── (16 tire) — daha uzun/kısa yapma
3. Link satırı: »» [İsim]: ⚡ [🔗 TIKLA ⚡] — her zaman bu formatı kullan
4. Ödül rakamı kaynakta yoksa: "Belirtilmemiş" yaz, uydurma
5. Tüm bölümler arasında 1 boş satır bırak
6. Başlık, YAPMAN GEREKENLER ve footer dışında bold/italic kullanma
7. Türkçe yaz

ÖRNEK ÇIKTI (format şablonu, içerik değişecek):

🚀 Binance TR Yeni Üye Bonusu! 🎁

Yeni kullanıcılara 880 TL bonus kazanma fırsatı 🤑

────────────────
🔥 YAPMAN GEREKENLER:

①  Promosyona katılım için kayıt olun
②  Kayıt olduktan sonra etkinlik sayfasına git otomatik kaydolur
③  İlk para yatırma işlemini tamamla

────────────────
»» Hemen Kaydol: ⚡ [🔗 TIKLA ⚡]
»» Etkinlik Sayfası: ⚡ [🔗 TIKLA ⚡]

Görev zorluğu: Kolay
Ödül miktarı:  880 TL
Airdrop puanı: ⭐⭐⭐⭐⭐

🗓 Kampanya Dönemi: 16.03.2026 Saat 16.00 - 09.04.2026 Saat 23.59
────────────────
"""

# ── Kısa format ───────────────────────────────────────────────────────────────
POST_SYSTEM_SHORT = """KriptoDropTR için kısa airdrop postu yaz.
⛔ Uydurma rakam, referral kodu, hashtag yasak.
✅ Adım numaraları: ① ② ③ | Ayırıcı: ──────────────── | Maks 300 karakter | Türkçe

🚀 [PLATFORM] [BAŞLIK]! 🎁
[1 cümle özet] 🤑

────────────────
①  [adım 1]
②  [adım 2]

────────────────
»» Kaydol: ⚡ [🔗 TIKLA ⚡]

Ödül: [kaynaktaki rakam — yoksa Belirtilmemiş]"""

# ── Özet format ───────────────────────────────────────────────────────────────
POST_SYSTEM_SUMMARY = """KriptoDropTR için 2-3 satır airdrop özeti yaz.
⛔ Uydurma rakam, referral kodu, hashtag yasak.
✅ Türkçe | Adım numaraları: ① ② ③

🚀 [PLATFORM] — [ödül varsa yaz, yoksa kanalımızda aktif kampanya] kazan! 🎁
»» Kaydol: ⚡ [🔗 TIKLA ⚡]"""


def _build_prompt(analysis: str, project_name: str) -> str:
    return (
        f"Platform/Proje: {project_name}\n\n"
        f"=== ARAŞTIRMA ANALİZİ ===\n{analysis}\n\n"
        f"=== KESİN KURALLAR ===\n"
        f"1. SADECE yukarıdaki analizde AÇIKÇA geçen rakamları kullan\n"
        f"2. Referral kodu, promo kodu, davet kodu YAZMA — analizde varsa bile\n"
        f"3. Bir satırı dolduracak bilgi yoksa o satırı komple SİL\n"
        f"4. Adım numaraları: ① ② ③ ④ ⑤ — başka format kullanma\n"
        f"5. Adımları analizden al, kendin adım uydurma\n"
        f"6. Ödül miktarı analizde geçmiyorsa 'Belirtilmemiş' yaz, uydurma\n"
        f"7. Analizde 'SONA ERMİŞ' veya 'geçmiş tarih' yazıyorsa post üretme — sadece uyarı yaz\n"
        f"8. [🔗 TIKLA ⚡] placeholder'ını koru — URL yazma\n"
        f"9. Link satırı formatı: »» [İsim]: ⚡ [🔗 TIKLA ⚡]"
    )


def build_post(analysis: str, project_name: str, fmt: str = "long") -> str:
    """fmt: 'long' | 'short' | 'summary'"""
    # Sona ermiş kampanya kontrolü — post üretme
    expired_indicators = ["SONA ERMİŞ", "sona ermiş", "expired", "EXPIRED"]
    if any(ind in analysis for ind in expired_indicators):
        return (
            f"⚠️ {project_name} kampanyası SONA ERMİŞ görünüyor.\n"
            f"Analiz: sona ermiş tarih tespit edildi — post üretilmedi.\n"
            f"Farklı bir platform araştırmak için yeni araştırma başlat."
        ) + POST_FOOTER

    prompt = _build_prompt(analysis, project_name)
    if fmt == "short":
        content = ai(POST_SYSTEM_SHORT, prompt, tokens=500, temp=0.25)
    elif fmt == "summary":
        content = ai(POST_SYSTEM_SUMMARY, prompt, tokens=200, temp=0.25)
    else:
        content = ai(POST_SYSTEM, prompt, tokens=1400, temp=0.25)

    # SABİT FOOTER EKLE
    return content + POST_FOOTER

# ══════════════════════════════════════════════════════════
#  TELEGRAM HELPERS
# ══════════════════════════════════════════════════════════

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Fırsat Tara",       callback_data="scan_menu"),
         InlineKeyboardButton("✍️ Post Oluştur",       callback_data="manual_post")],
        [InlineKeyboardButton("📁 Post Arşivi",        callback_data="post_archive"),
         InlineKeyboardButton("📌 Takip Listesi",      callback_data="tracked_list")],
        [InlineKeyboardButton("🚫 Kara Liste",         callback_data="blacklist_view"),
         InlineKeyboardButton("🔗 Linklerimi Yönet",   callback_data="link_manage")],
        [InlineKeyboardButton("🔄 Yeni Araştırma",     callback_data="new_research"),
         InlineKeyboardButton("❓ Yardım",              callback_data="help")],
    ])

def post_actions(has_link: bool = False, fmt: str = "long") -> InlineKeyboardMarkup:
    return post_actions_extended(has_link=has_link, fmt=fmt, score=None)

def post_actions_extended(has_link: bool = False, fmt: str = "long", score=None) -> InlineKeyboardMarkup:
    link_label  = "✅ Link Eklendi" if has_link else "🔗 Link Ekle"
    fmt_long    = "📄 Uzun ●" if fmt == "long"    else "📄 Uzun"
    fmt_short   = "📝 Kısa ●" if fmt == "short"   else "📝 Kısa"
    fmt_summary = "⚡ Özet ●" if fmt == "summary" else "⚡ Özet"
    rows = [
        [InlineKeyboardButton(fmt_long,    callback_data="fmt_long"),
         InlineKeyboardButton(fmt_short,   callback_data="fmt_short"),
         InlineKeyboardButton(fmt_summary, callback_data="fmt_summary")],
        [InlineKeyboardButton(link_label,  callback_data="add_link")],
        [InlineKeyboardButton("✏️ Postu Düzenle", callback_data="edit_post_inline")],
        [InlineKeyboardButton("📢 Gruba Gönder",  callback_data="send_text"),
         InlineKeyboardButton("🖼️ Görsel ile",    callback_data="send_photo")],
        [InlineKeyboardButton("📌 Fırsatı Takibe Al", callback_data="track_opp"),
         InlineKeyboardButton("🚫 Kara Listeye", callback_data="blacklist_opp")],
        [InlineKeyboardButton("♻️ Yenile", callback_data="regen_post"),
         InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
    ]
    return InlineKeyboardMarkup(rows)

async def typing(update: Update):
    await update.effective_chat.send_action(ChatAction.TYPING)

# ── Premium Custom Emoji Map (Verified Unique IDs) ───────────────────────────
# HTML mode: <tg-emoji emoji-id="ID">fallback</tg-emoji>
CE = {
    "🚀": "5368324170671202286",  # Roket
    "🔥": "5431321415494215242",  # Ateş
    "🎁": "5431411586529035136",  # Hediye
    "💰": "5431321415490021396",  # Para
    "⚡️": "5431627943585579051", # Şimşek (Varyasyonlu)
    "⚡": "5431627943585579051",  # Şimşek
    "✅": "5431321415515187219",  # Onay
    "📢": "5431627943594002447",  # Duyuru
    "💎": "5431411586512257041",  # Elmas
    "🥇": "5431627943581384725",  # 1. (Altın)
    "🥈": "5431627943585579050",  # 2.
    "🥉": "5431627943572996117",  # 3.
    "📍": "5431627943589773312",  # Konum/Nokta
    "🔹": "5431627943572996118",  # Mavi parlayan
    "⭐": "5431627943585579051",  # Yıldız
    "🤑": "5431411586533229598",  # Para ağızlı
}

def apply_custom_emojis(text: str) -> str:
    """Metindeki standart emojileri Premium animasyonlu versiyonlarıyla değiştir."""
    import re
    # ⛔ GÜVENLİK: Eğer metin zaten <tg-emoji> içeriyorsa veya çok karmaşıksa hata almamak için 
    # placeholder yöntemi kullanıyoruz.
    
    # Değiştirilecek emojileri listele
    sorted_emojis = sorted(CE.keys(), key=len, reverse=True)
    
    # 1. Mevcut HTML etiketlerini ve linkleri korumaya al (placeholder)
    placeholders = []
    def to_placeholder(match):
        placeholders.append(match.group(0))
        return f"__PH{len(placeholders)-1}__"
    
    # Etiketleri koru
    text = re.sub(r'<[^>]+>', to_placeholder, text)
    
    # 2. Sadece düz metin kalan yerlerde emojileri değiştir
    for emoji in sorted_emojis:
        if emoji in text:
            eid = CE[emoji]
            # Emoji tagını placeholder olmadan direkt yerleştir (çünkü içinde başka tag yok)
            text = text.replace(emoji, f'<tg-emoji emoji-id="{eid}">{emoji}</tg-emoji>')
    
    # 3. Korumaya aldığımız etiketleri geri koy
    for i, ph_val in enumerate(placeholders):
        text = text.replace(f"__PH{i}__", ph_val)
        
    return text

def html_escape(text: str) -> str:
    """HTML özel karakterlerini kaçır."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

def md_to_html(text: str) -> str:
    """Metni Telegram HTML formatına çevirir (Sıfır Hata Garantili)."""
    import re
    if not text: return ""
    
    # 1. Kaçış işlemleri
    text = html_escape(text)
    
    # 2. Kalınlıkları çevir (** -> <b>)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    
    return text.strip()

def safe_md(text: str) -> str:
    """Geriye dönük uyumluluk — artık HTML döndürür."""
    return md_to_html(text)

# ══════════════════════════════════════════════════════════
#  KOMUTLAR
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Grup/kanal veya admin değilse sessizce yoksay
    if update.effective_chat.type in ("group", "supergroup", "channel"):
        return
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 <b>AIRDROP BOT</b> — Admin Paneli\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 <b>Airdrop Tara</b> → İnterneti tara, aktif airdropları listele\n"
        "✍️ <b>Post Oluştur</b> → Airdrop adı veya link at, derin araştır\n"
        "📢 <b>Gruba Gönder</b> → Hazır postu gruba gönder\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 <i>Airdrop adı veya linki direkt yazabilirsin.</i>",
        parse_mode=ParseMode.HTML,
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
        parse_mode=ParseMode.HTML,
    )

@admin_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🔍 *Hangi kategoriyi tarayalım?*\n\n"
        "_Hepsi → tüm kategoriler taranır (daha uzun sürer)_",
        parse_mode=ParseMode.HTML,
        reply_markup=category_filter_menu(),
    )

@admin_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Kullanım: `/post [airdrop adı]`\nÖrnek: `/post Arbitrum`",
            parse_mode=ParseMode.HTML,
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
                parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    # Link ekleme state
    if waiting == "add_link":
        context.user_data["waiting_for"] = None
        post = context.user_data.get("last_post", "")
        link = text.strip()
        updated = post.replace("[🔗 TIKLA 🖊]", link)
        context.user_data["final_post"] = updated
        context.user_data["has_link"] = True

        # Görsel çek ve DM'de önizle
        platform = context.user_data.get("last_post_platform", "crypto")
        await update.message.reply_text(
            "✅ *Link eklendi!* Görsel aranıyor...",
            parse_mode=ParseMode.HTML,
        )
        img_url = get_image(f"{platform} crypto")
        caption = safe_md(updated[:1024] if len(updated) > 1024 else updated)

        if img_url:
            try:
                await update.message.reply_photo(
                    photo=img_url,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
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
                    parse_mode=ParseMode.HTML,
                    reply_markup=post_actions(has_link=True),
                )
        return

    # Post düzenleme
    if waiting in ("edit_post", "edit_post_inline"):
        context.user_data["waiting_for"] = None
        context.user_data["final_post"]  = text
        context.user_data["last_post"]   = text
        fmt  = context.user_data.get("post_fmt","long")
        preview = (
            f"✅ <b>Post güncellendi!</b>\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"{safe_md(text)}\n"
            f"━━━━━━━━━━━━━━━━━━━━"
        )
        if len(preview) > 4096:
            preview = preview[:4086] + "..."
        await update.message.reply_text(
            preview,
            parse_mode=ParseMode.HTML,
            reply_markup=post_actions(has_link=context.user_data.get("has_link",False), fmt=fmt),
        )
        return

    # Takip deadline girişi
    if waiting == "track_deadline":
        context.user_data["waiting_for"] = None
        deadline     = text.strip()
        project_name = context.user_data.get("last_project","?")
        analysis     = context.user_data.get("last_analysis","")
        post         = context.user_data.get("final_post","")
        tid = track_opportunity(project_name, deadline, analysis, post)
        await update.message.reply_text(
            f"📌 <b>{project_name}</b> takibe alındı!\n"
            f"⏰ Son Tarih: <code>{deadline}</code>\n"
            f"🔔 3 gün kala hatırlatma gelecek.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    # URL veya airdrop adı
    await _do_research(update, context, text)


async def _do_research(update: Update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    """Araştırma → Doğrulama → Güvenilirlik skoru → AI analiz → Post oluştur."""
    # Kara liste kontrolü
    if is_blacklisted(input_text):
        await update.effective_message.reply_text(
            f"🚫 <b>{input_text}</b> kara listede!\n"
            "Bu proje daha önce sahte/şüpheli olarak işaretlendi.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )
        return

    msg = await update.effective_message.reply_text(
        f"🔬 <b>Araştırma başladı:</b> <code>{input_text[:60]}</code>\n"
        "⏳ 30-60 saniye sürebilir...",
        parse_mode=ParseMode.HTML,
    )
    await update.effective_chat.send_action(ChatAction.TYPING)

    # 1. Araştır
    if is_url(input_text):
        await msg.edit_text("🔗 <b>URL içeriği çekiliyor...</b>", parse_mode=ParseMode.HTML)
        data = research_airdrop_by_url(input_text)
    else:
        await msg.edit_text(
            f"🔍 <b>\'{input_text}\' araştırılıyor...</b>\n<i>Çoklu sorgu çalışıyor...</i>",
            parse_mode=ParseMode.HTML,
        )
        data = research_airdrop_by_name(input_text)

    project_name = data.get("name", input_text)

    # 2. Çoklu kaynak doğrulama + güvenilirlik skoru
    await msg.edit_text(
        "🔁 <b>Çoklu kaynak doğrulanıyor...</b>\n<i>Güvenilirlik skoru + tarih kontrolü yapılıyor...</i>",
        parse_mode=ParseMode.HTML,
    )
    score_data = verify_and_score(project_name, data)
    score      = score_data.get("score", 50)
    verdict    = score_data.get("verdict", "BELİRSİZ")
    expired    = score_data.get("expired", False)
    reasons    = score_data.get("reasons", [])
    warning    = score_data.get("warning", "")
    badge      = format_score_badge(score, verdict)

    # Sona ermiş kampanya → admin'e ikaz ver, işleme devam et
    if expired or "SONA ERMİŞ" in verdict:
        await update.effective_message.reply_text(
            f"⚠️ <b>DİKKAT:</b> <code>{project_name}</code> kampanyası muhtemelen <b>SONA ERMİŞ</b>!\n"
            "Güvenilirlik skoru düşük tutuldu. Yine de post hazırlanıyor — kontrol et.",
            parse_mode=ParseMode.HTML,
        )

    context.user_data["last_score"]   = score_data
    context.user_data["last_project"] = project_name

    # 3. AI analiz
    await msg.edit_text("🤖 <b>AI analizi yapılıyor...</b>", parse_mode=ParseMode.HTML)
    # Doğrulama verisini de analize ekle
    enriched_data = data.copy()
    enriched_data["raw"] = data.get("raw","") + "\n\n=== DOĞRULAMA ===\n" + score_data.get("extra_raw","")
    analysis = analyze_research(enriched_data)
    context.user_data["last_analysis"] = analysis

    # 4. Post oluştur
    await msg.edit_text("✍️ **Post hazırlanıyor...**", parse_mode=ParseMode.HTML)
    post = build_post(analysis, project_name)
    
    # ⛔ KESİN ÇÖZÜM: Sadece Bold (Kalın) yazı kullanıyoruz
    final_post_html = md_to_html(post)
    context.user_data["last_post"]          = final_post_html
    context.user_data["final_post"]         = final_post_html
    context.user_data["last_post_platform"] = project_name
    context.user_data["has_link"]           = False
    context.user_data["post_fmt"]           = "long"

    # Postu arşive kaydet
    save_post_archive(project_name, post, "long")


    # ⛔ GÜVENLİK: Markdown'dan HTML'e çevirirken hata riskini sıfırlıyoruz
    # 5. Güvenilirlik raporunu hazırlayıp göster
    reasons_text = "\n".join([f"  • {r}" for r in reasons]) if reasons else "  • Bilgi yetersiz"
    score_msg = (
        f"📊 **GÜVENİLİRLİK RAPORU — {project_name.upper()}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Skor: {badge}\n\n"
        f"📋 **Değerlendirme:**\n{reasons_text}\n\n"
        f"{analysis}\n\n"
        f"🤖 **[v2.6 - KESİN ÇÖZÜM]**"
    )
    
    # Önce Markdown olarak kes, sonra HTML yap (Hata riskini sıfırlar)
    if len(score_msg) > 3000:
        score_msg = score_msg[:2900] + "..."
    
    score_html = md_to_html(score_msg)
    
    try:
        await msg.edit_text(score_html, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Rapor HTML Hatasi: {e}")
        # Hala hata alırsak tagları temizleyip düz gönder (Plan B)
        import re
        clean_msg = re.sub(r'<[^>]+>', '', score_html).replace("**", "")
        await msg.edit_text(clean_msg[:4000])

    # 6. Post önizleme (Görsel stile sadık kalınarak)
    post_preview = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🚀 **HAZIRLANAN POST:**\n\n"
        f"{final_post_html}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"Skor: {badge}"
    )
    
    try:
        await update.effective_message.reply_text(
            post_preview,
            parse_mode=ParseMode.HTML,
            reply_markup=post_actions_extended(has_link=False, fmt="long", score=score),
        )
    except Exception as e:
        logger.error(f"Onizleme Hatasi: {e}")
        await update.effective_message.reply_text("⚠️ Post önizlemesi hazırlandı (Filtresiz).")

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
                    parse_mode=ParseMode.HTML,
                )
            else:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=safe_md(post),
                    parse_mode=ParseMode.HTML,
                )
        else:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=safe_md(post),
                parse_mode=ParseMode.HTML,
            )

        confirm = "✅ *Post gruba gönderildi!*" + (" 🖼️ (görsel ile)" if with_photo else "")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(confirm, parse_mode=ParseMode.HTML, reply_markup=main_menu())

    except Exception as e:
        logger.error(f"Gönderme hatası: {e}")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(f"❌ Gönderim hatası: `{e}`", parse_mode=ParseMode.HTML)

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
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    elif data == "help":
        await q.message.reply_text(
            "📖 *KOMUTLAR*\n\n"
            "/scan — Aktif airdropları tara\n"
            "/post `[isim]` — Araştır & post oluştur\n"
            "/sendgroup — Son postu gruba gönder\n\n"
            "💡 Direkt airdrop adı veya link yazabilirsin.",
            parse_mode=ParseMode.HTML,
        )

    elif data == "scan":
        msg = await q.message.reply_text(
            "🌐 *Taranıyor...*\n_Tüm kripto fırsatları aranıyor (30-50 sn)_",
            parse_mode=ParseMode.HTML,
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
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    elif data == "manual_post":
        context.user_data["waiting_for"] = None
        await q.message.reply_text(
            "✍️ *Manuel Araştırma*\n\n"
            "Aşağıdakilerden birini yaz:\n"
            "• Airdrop / proje adı\n"
            "• Airdrop URL'si\n\n"
            "_Örnek: `Arbitrum` veya `https://arbitrum.io/airdrop`_",
            parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
            reply_markup=kb,
        )

    elif data == "send_text":
        await _send_to_group(update, context, with_photo=False)

    elif data == "send_photo":
        await q.message.reply_text("🖼️ Görsel aranıyor...", parse_mode=ParseMode.HTML)
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
            parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
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
            parse_mode=ParseMode.HTML,
            reply_markup=post_actions(has_link=False, fmt=fmt),
        )

    elif data == "scan_menu":
        await q.message.reply_text(
            "🔍 *Hangi kategoriyi tarayalım?*\n\n"
            "Sadece belirli bir türü taramak için seç.\n"
            "_Hepsi → tüm kategoriler taranır (daha uzun sürer)_",
            parse_mode=ParseMode.HTML,
            reply_markup=category_filter_menu(),
        )

    elif data.startswith("cat_"):
        cat_key = data[4:]   # "cat_bonus" → "bonus"
        _, cats = CATEGORY_DEFS.get(cat_key, ("Hepsi", None))
        cat_label, _ = CATEGORY_DEFS.get(cat_key, ("🌐 Hepsi", None))
        msg = await q.message.reply_text(
            f"🌐 *{cat_label} taranıyor...*\n_30-50 saniye sürebilir_",
            parse_mode=ParseMode.HTML,
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
        await msg.edit_text(text, parse_mode=ParseMode.HTML, reply_markup=keyboard)

    # ── Link Yönetimi ─────────────────────────────────────────────────
    elif data == "link_stats":
        stats = get_link_stats()
        await q.message.reply_text(
            stats,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔗 Linklerimi Yönet", callback_data="link_manage"),
                InlineKeyboardButton("🏠 Ana Menü", callback_data="home"),
            ]]),
        )

    elif data == "link_manage":
        await q.message.reply_text(
            "🔗 *KAYIT LİNKLERİM*\n\nBir linki seçerek posta ekleyebilirsin.\n"
            "Yeni link eklemek için *➕ Yeni Link Ekle*'ye bas.",
            parse_mode=ParseMode.HTML,
            reply_markup=get_link_list_menu(),
        )

    elif data == "link_add_new":
        context.user_data["waiting_for"] = "link_add"
        await q.message.reply_text(
            "🔗 *Yeni Kayıt Linki Ekle*\n\n"
            "Şu formatta yaz:\n"
            "`PLATFORM_ADI | https://link.com/referral`\n\n"
            "_Örnek: `CoinTR | https://partner.cointr.com/short/abc`_",
            parse_mode=ParseMode.HTML,
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
        updated = post.replace("[🔗 TIKLA 🖊]", lnk["url"])
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
            parse_mode=ParseMode.HTML,
            reply_markup=post_actions(has_link=True, fmt=fmt),
        )

    elif data == "link_clear":
        _LINK_STORE.clear()
        await q.answer("🗑️ Tüm linkler silindi.", show_alert=True)
        await q.message.reply_text(
            "🗑️ Kayıtlı tüm linkler silindi.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    elif data == "edit_post_inline":
        context.user_data["waiting_for"] = "edit_post_inline"
        current = context.user_data.get("final_post","")
        await q.message.reply_text(
            "✏️ <b>Postu Düzenle</b>\n\n"
            "Aşağıdaki metni değiştirerek gönder:\n"
            "<i>(Tüm metni yeniden yaz)</i>\n\n"
            f"<code>{current[:800]}</code>",
            parse_mode=ParseMode.HTML,
        )

    elif data == "track_opp":
        context.user_data["waiting_for"] = "track_deadline"
        project = context.user_data.get("last_project","?")
        await q.message.reply_text(
            f"📌 <b>{project}</b> takibe alınıyor...\n\n"
            "Son tarihi gir (ör: <code>31.05.2026</code>)\n"
            "Bilmiyorsan <code>belirsiz</code> yaz:",
            parse_mode=ParseMode.HTML,
        )

    elif data == "blacklist_opp":
        project = context.user_data.get("last_project","?")
        add_to_blacklist(project)
        await q.answer(f"🚫 {project} kara listeye eklendi!", show_alert=True)
        await q.message.reply_text(
            f"🚫 <b>{project}</b> kara listeye eklendi.\n"
            "Bu proje artık arama sonuçlarında gösterilmeyecek.",
            parse_mode=ParseMode.HTML,
            reply_markup=main_menu(),
        )

    elif data == "tracked_list":
        items = get_tracked()
        if not items:
            await q.message.reply_text(
                "📌 <b>Takip Listesi</b>\n\nHenüz takip edilen fırsat yok.\n"
                "Araştırma sonrası <b>Fırsatı Takibe Al</b> butonuna bas.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]),
            )
        else:
            text_msg = "📌 <b>TAKİP LİSTESİ</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            kb = []
            for opp in items[-8:]:
                dl = opp.get("deadline","?")
                text_msg += f"• <b>{opp['name']}</b> | ⏰ {dl} | 📅 {opp['added']}\n"
                kb.append([
                    InlineKeyboardButton(f"🗑 {opp['name'][:20]}", callback_data=f"untrack_{opp['id']}"),
                    InlineKeyboardButton("✍️ Post", callback_data=f"repost_{opp['id']}"),
                ])
            kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("untrack_"):
        tid = data[8:]
        remove_tracked(tid)
        await q.answer("✅ Takipten çıkarıldı.", show_alert=False)
        # Listeyi yenile
        items = get_tracked()
        if not items:
            await q.message.reply_text("📌 Takip listesi boş.", parse_mode=ParseMode.HTML, reply_markup=main_menu())
        else:
            text_msg = "📌 <b>TAKİP LİSTESİ (güncellendi)</b>\n\n"
            for opp in items[-8:]:
                text_msg += f"• <b>{opp['name']}</b> | ⏰ {opp.get('deadline','?')}\n"
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]))

    elif data.startswith("repost_"):
        tid  = data[7:]
        items = {o["id"]: o for o in get_tracked()}
        opp  = items.get(tid)
        if opp:
            context.user_data["last_post"]  = opp.get("post","")
            context.user_data["final_post"] = opp.get("post","")
            context.user_data["last_project"] = opp.get("name","")
            preview = f"📣 <b>POST:</b>\n\n{safe_md(opp.get('post',''))}"
            if len(preview) > 4096: preview = preview[:4086] + "..."
            await q.message.reply_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False))
        else:
            await q.answer("Fırsat bulunamadı.", show_alert=True)

    elif data == "post_archive":
        posts = get_post_archive()
        if not posts:
            await q.message.reply_text(
                "📁 <b>Post Arşivi</b>\n\nHenüz arşivlenmiş post yok.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]),
            )
        else:
            text_msg = "📁 <b>POST ARŞİVİ</b> (son 10)\n━━━━━━━━━━━━━━━━━━━━\n\n"
            kb = []
            for p in posts[:10]:
                text_msg += f"• <b>{p['project']}</b> | {p['fmt']} | {p['date']}\n"
                kb.append([InlineKeyboardButton(
                    f"📄 {p['project'][:25]} ({p['date'][:5]})",
                    callback_data=f"archive_load_{p['id']}"
                )])
            kb.append([InlineKeyboardButton("🏠 Ana Menü", callback_data="home")])
            await q.message.reply_text(text_msg, parse_mode=ParseMode.HTML, reply_markup=InlineKeyboardMarkup(kb))

    elif data.startswith("archive_load_"):
        pid   = data[13:]
        posts = {p["id"]: p for p in get_post_archive()}
        p     = posts.get(pid)
        if p:
            context.user_data["last_post"]    = p["post"]
            context.user_data["final_post"]   = p["post"]
            context.user_data["last_project"] = p["project"]
            context.user_data["post_fmt"]     = p["fmt"]
            preview = f"📄 <b>{p['project']}</b> | {p['date']}\n\n{safe_md(p['post'])}"
            if len(preview) > 4096: preview = preview[:4086] + "..."
            await q.message.reply_text(preview, parse_mode=ParseMode.HTML, reply_markup=post_actions(has_link=False, fmt=p["fmt"]))
        else:
            await q.answer("Post bulunamadı.", show_alert=True)

    elif data == "blacklist_view":
        bl = get_blacklist()
        text_msg = "🚫 <b>KARA LİSTE</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        if bl:
            for item in bl:
                text_msg += f"• {item}\n"
        else:
            text_msg += "Kara liste boş."
        await q.message.reply_text(
            text_msg, parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="home")]]),
        )

    elif data == "new_research":
        context.user_data["waiting_for"] = None
        await q.message.reply_text(
            "🔬 *Yeni araştırma için airdrop adı veya linkini yaz:*",
            parse_mode=ParseMode.HTML,
        )

# ══════════════════════════════════════════════════════════
#  OTOMATİK TARAMA — Her 8 Saatte Bir Admin'e Bildir
# ══════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════
#  ANA
# ══════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Tüm handler'lar sadece PRIVATE (DM) mesajlarını işler — grup/kanal tamamen yoksayılır
    private = filters.ChatType.PRIVATE

    app.add_handler(CommandHandler("start",     cmd_start,     filters=private))
    app.add_handler(CommandHandler("help",      cmd_help,      filters=private))
    app.add_handler(CommandHandler("scan",      cmd_scan,      filters=private))
    app.add_handler(CommandHandler("post",      cmd_post,      filters=private))
    app.add_handler(CommandHandler("sendgroup", cmd_sendgroup, filters=private))

    app.add_handler(CallbackQueryHandler(handle_callback))  # callback guard'ı decorator'da
    app.add_handler(MessageHandler(private & filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("🚀 Airdrop Bot başlatıldı.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
