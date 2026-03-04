import os
import re
import asyncio
import logging
import aiohttp
import hashlib
import json as _json
import feedparser
from datetime import datetime, timedelta
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand,
)
from telegram.ext import (
    ApplicationBuilder, ContextTypes, MessageHandler,
    CommandHandler, CallbackQueryHandler, filters,
)

TOKEN      = os.getenv("BOT_TOKEN")
OPENAI_KEY = os.getenv("OPENAI_API_KEY")
ADMIN_IDS  = set(int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip())
GROUP_ID   = int(os.getenv("GROUP_ID", "0"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

airdrops: list       = []
airdrop_counter: int = 0

# ── Otomatik çekilen airdroplar ──
auto_airdrops: list  = []           # scrape ile gelen
_airdrop_cache: dict = {"data": [], "fetched_at": None}
_AIRDROP_CACHE_TTL   = timedelta(hours=3)
seen_airdrop_ids: set = set()       # tekrar bildirim engeli
daily_users:    set = set()
weekly_users:   set = set()
monthly_users:  set = set()
all_time_users: set = set()
join_log: list = []
today       = datetime.now().date()
week_start  = today - timedelta(days=today.weekday())
month_start = today.replace(day=1)
posted_news: set = set()

# ══════════════════════════════════════════
#  OTOMATİK AIRDROP ÇEKME MODÜLİ
# ══════════════════════════════════════════

"""
Strateji:
  1. Birincil öncelik: BORSALAR (MEXC, OKX, Binance, Bybit, Gate.io, KuCoin)
     - Kayıt/KYC bonusu, trading kampanyası, referral ödülü
     - RSS + HTML scraping
  2. İkincil: Cüzdan/DeFi platformları (Rabby, MetaMask, Phantom…)
  3. Üçüncül: Kripto haber sitelerindeki airdrop/kampanya haberleri
  4. Son çare: Genel airdrop takip siteleri (sadece kolay/ücretsiz olanlar)

Kalite Filtresi:
  - "staking required", "node", "validator", "mainnet" → DÜŞÜK PUAN
  - "kayıt ol", "üye ol", "borsada", "trading", "deposit bonus" → YÜKSEK PUAN
  - Bitiş tarihi geçmiş → otomatik çıkar
  - Aynı proje → tekrar gösterme (seen_airdrop_ids)
"""

import hashlib as _hashlib

# Borsa & platform anahtar kelimeleri
_KOLAY_KEYWORDS = [
    "kayıt", "üye ol", "register", "sign up", "signup", "new user",
    "deposit bonus", "trading bonus", "referral", "invite", "davet",
    "welcome bonus", "hoş geldin", "kampanya", "campaign", "promotion",
    "promo", "airdrop", "reward", "ödül", "free", "ücretsiz",
    "claim", "kazan", "earn", "giveaway",
]
_ZOR_KEYWORDS = [
    "staking", "node", "validator", "mainnet", "testnet node",
    "liquidity provider", "lp token", "governance vote", "on-chain",
    "bridge required", "gas fee", "minimum deposit", "lock",
]
_BORSA_KEYWORDS = [
    "mexc", "okx", "binance", "bybit", "gate.io", "gateio", "kucoin",
    "bitget", "bingx", "huobi", "htx", "coinbase", "kraken",
]

def _airdrop_hash(baslik: str, url: str) -> str:
    return _hashlib.md5((baslik.lower().strip() + url).encode()).hexdigest()

def _puan_hesapla(baslik: str, icerik: str, kaynak: str) -> float:
    """0-10 arası otomatik puan hesapla."""
    metin  = (baslik + " " + icerik + " " + kaynak).lower()
    puan   = 5.0

    # Borsa ise +2
    if any(k in metin for k in _BORSA_KEYWORDS):
        puan += 2.0
    # Kolay kelimeler +1.5
    kolay = sum(1 for k in _KOLAY_KEYWORDS if k in metin)
    puan += min(kolay * 0.4, 1.5)
    # Zor kelimeler -2.5
    zor = sum(1 for k in _ZOR_KEYWORDS if k in metin)
    puan -= min(zor * 0.8, 2.5)
    # Türkçe içerik +0.5
    if any(k in metin for k in ["türk", "türkiye", "kampanya", "kazan", "üye"]):
        puan += 0.5

    return round(max(0.0, min(10.0, puan)), 1)

def _tarih_parse(s: str):
    """Metinden tarih çıkarmaya çalışır."""
    if not s:
        return None
    patterns = [
        r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{2,4})",
        r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})",
        r"(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{1,2})[,\s]+(\d{4})",
        r"(\d{1,2})\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})",
    ]
    aylar_en = {"january":1,"february":2,"march":3,"april":4,"may":5,"june":6,
                "july":7,"august":8,"september":9,"october":10,"november":11,"december":12}
    s_low = s.lower()
    for pat in patterns:
        m = re.search(pat, s_low)
        if m:
            try:
                g = m.groups()
                if len(g) == 3:
                    if g[0].isdigit() and g[1].isdigit() and g[2].isdigit():
                        d, mo, y = int(g[0]), int(g[1]), int(g[2])
                        if y < 100: y += 2000
                        if mo > 12: d, mo = mo, d
                        return datetime(y, mo, d).date()
                    elif g[0] in aylar_en:
                        return datetime(int(g[2]), aylar_en[g[0]], int(g[1])).date()
                    elif g[1] in aylar_en:
                        return datetime(int(g[2]), aylar_en[g[1]], int(g[0])).date()
            except:
                pass
    return None

def _temizle_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&[a-z#0-9]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()

# ── Kaynak Tanımları ──

AIRDROP_KAYNAKLARI = [
    # ── BORSA RSS/Blog ──
    {
        "isim"    : "MEXC Blog TR",
        "tur"     : "rss",
        "url"     : "https://www.mexc.com/tr-TR/blog/rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.mexc.com/tr-TR/blog/rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "MEXC Blog EN",
        "tur"     : "rss",
        "url"     : "https://www.mexc.com/en-US/blog/rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.mexc.com/en-US/blog/rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "OKX Announcements",
        "tur"     : "rss",
        "url"     : "https://www.okx.com/help-center/rss.xml",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.okx.com/help-center/rss.xml",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "Binance TR Duyurular",
        "tur"     : "rss",
        "url"     : "https://www.binance.com/tr/support/announcement/rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.binance.com/tr/support/announcement/rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "Binance EN Announcements",
        "tur"     : "rss",
        "url"     : "https://www.binance.com/en/support/announcement/rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.binance.com/en/support/announcement/rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "Bybit Announcements",
        "tur"     : "rss",
        "url"     : "https://announcements.bybit.com/en-US/?feed=rss2&cat=2",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://announcements.bybit.com/en-US/?feed=rss2",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "Gate.io Duyurular",
        "tur"     : "rss",
        "url"     : "https://www.gate.io/rss/tr/article.rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.gate.io/rss/tr/article.rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "KuCoin Blog",
        "tur"     : "rss",
        "url"     : "https://www.kucoin.com/blog/rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.kucoin.com/blog/rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    {
        "isim"    : "Bitget Promotions",
        "tur"     : "rss",
        "url"     : "https://www.bitget.com/blog/rss",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.bitget.com/blog/rss",
        "oncelik" : 1,
        "kaynak_tur": "borsa",
    },
    # ── Kripto Haber Sitelerindeki Airdrop Haberleri ──
    {
        "isim"    : "CoinTelegraph Airdrop",
        "tur"     : "rss_filtreli",
        "url"     : "https://cointelegraph.com/rss/tag/airdrop",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://cointelegraph.com/rss/tag/airdrop",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    {
        "isim"    : "CoinTelegraph TR Airdrop",
        "tur"     : "rss_filtreli",
        "url"     : "https://tr.cointelegraph.com/rss/tag/airdrop",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://tr.cointelegraph.com/rss/tag/airdrop",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    {
        "isim"    : "BeInCrypto TR Airdrop",
        "tur"     : "rss_filtreli",
        "url"     : "https://tr.beincrypto.com/feed/?tag=airdrop",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://tr.beincrypto.com/feed/?tag=airdrop",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    {
        "isim"    : "KriptoKoin Airdrop",
        "tur"     : "rss_filtreli",
        "url"     : "https://kriptokoin.com/feed/?tag=airdrop",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://kriptokoin.com/feed/?tag=airdrop",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    {
        "isim"    : "BTCHaber Kampanya",
        "tur"     : "rss_filtreli",
        "url"     : "https://www.btchaber.com/feed/?s=airdrop+kampanya",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://www.btchaber.com/feed/",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    {
        "isim"    : "CoinTurk Kampanya",
        "tur"     : "rss_filtreli",
        "url"     : "https://cointurk.com/feed/?tag=kampanya",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://cointurk.com/feed/?tag=kampanya",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    {
        "isim"    : "Decrypt Airdrop",
        "tur"     : "rss_filtreli",
        "url"     : "https://decrypt.co/feed/tag/airdrop",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://decrypt.co/feed/tag/airdrop",
        "oncelik" : 2,
        "kaynak_tur": "haber",
    },
    # ── Airdrop Takip Siteleri (RSS) ──
    {
        "isim"    : "Airdrops.io RSS",
        "tur"     : "rss",
        "url"     : "https://airdrops.io/feed/",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://airdrops.io/feed/",
        "oncelik" : 3,
        "kaynak_tur": "takip",
    },
    {
        "isim"    : "CoinMarketCap Airdrop",
        "tur"     : "api",
        "url"     : "https://api.coinmarketcap.com/dex/v3/airdrops?start=1&limit=20&status=ONGOING",
        "oncelik" : 3,
        "kaynak_tur": "takip",
    },
    {
        "isim"    : "CryptoRank Airdrops",
        "tur"     : "rss",
        "url"     : "https://cryptorank.io/upcoming-ico/feed",
        "proxy"   : "https://api.rss2json.com/v1/api.json?rss_url=https://cryptorank.io/upcoming-ico/feed",
        "oncelik" : 3,
        "kaynak_tur": "takip",
    },
]

# ── Çekme Fonksiyonları ──

async def _airdrop_rss(sess: aiohttp.ClientSession, kaynak: dict, limit: int = 20) -> list:
    """feedparser ile RSS çeker."""
    import feedparser as _fp

    headers = {"User-Agent": "Mozilla/5.0 (compatible; KriptoDropBot/2.0)"}

    # Önce doğrudan dene
    xml = None
    try:
        async with sess.get(
            kaynak["url"], headers=headers,
            timeout=aiohttp.ClientTimeout(total=15), allow_redirects=True
        ) as r:
            if r.status == 200:
                xml = await r.text(errors="ignore")
    except Exception as e:
        log.debug(f"[airdrop-direct] {kaynak['isim']}: {e}")

    # Başarısız → proxy dene
    if not xml and kaynak.get("proxy"):
        try:
            async with sess.get(
                kaynak["proxy"],
                timeout=aiohttp.ClientTimeout(total=15)
            ) as r:
                data = await r.json(content_type=None)
                # rss2json formatı
                items = data.get("items", [])
                out   = []
                for h in items[:limit]:
                    baslik = _temizle_html(h.get("title", ""))
                    icerik = _temizle_html(h.get("description", h.get("content", "")))[:1500]
                    url    = h.get("link", "")
                    if not baslik or not url:
                        continue
                    zaman_str = h.get("pubDate", "")
                    try:
                        from datetime import datetime as _dt
                        zaman = _dt.strptime(zaman_str[:25], "%a, %d %b %Y %H:%M:%S").date() if zaman_str else datetime.now().date()
                    except:
                        zaman = datetime.now().date()
                    out.append({
                        "baslik"    : baslik,
                        "icerik"    : icerik,
                        "url"       : url,
                        "kaynak"    : kaynak["isim"],
                        "kaynak_tur": kaynak.get("kaynak_tur","haber"),
                        "zaman"     : zaman,
                        "oncelik"   : kaynak.get("oncelik", 3),
                    })
                return out
        except Exception as e:
            log.debug(f"[airdrop-proxy] {kaynak['isim']}: {e}")
        return []

    if not xml:
        return []

    feed = _fp.parse(xml)
    out  = []
    for entry in feed.entries[:limit]:
        baslik = _temizle_html(entry.get("title", ""))
        icerik = _temizle_html(
            entry.get("summary", "") or
            (entry.get("content", [{}])[0].get("value","") if entry.get("content") else "")
        )[:1500]
        url = entry.get("link","")
        if not baslik or not url:
            continue
        try:
            from email.utils import parsedate_to_datetime
            zaman = parsedate_to_datetime(entry.get("published","")).date()
        except:
            zaman = datetime.now().date()
        out.append({
            "baslik"    : baslik,
            "icerik"    : icerik,
            "url"       : url,
            "kaynak"    : kaynak["isim"],
            "kaynak_tur": kaynak.get("kaynak_tur","haber"),
            "zaman"     : zaman,
            "oncelik"   : kaynak.get("oncelik", 3),
        })
    return out


async def _airdrop_cmc(sess: aiohttp.ClientSession, kaynak: dict) -> list:
    """CoinMarketCap Airdrop API."""
    try:
        headers = {
            "User-Agent" : "Mozilla/5.0",
            "Accept"     : "application/json",
        }
        async with sess.get(
            kaynak["url"], headers=headers,
            timeout=aiohttp.ClientTimeout(total=12)
        ) as r:
            if r.status != 200:
                return []
            data = await r.json(content_type=None)

        out = []
        for item in (data.get("data") or data.get("results") or []):
            baslik = item.get("name","") or item.get("title","")
            icerik = item.get("description","")
            url    = item.get("projectLink","") or item.get("url","") or \
                     f"https://coinmarketcap.com/airdrop/{item.get('id','')}"
            if not baslik:
                continue
            # Bitiş tarihi
            end_raw = item.get("endDate","") or item.get("end_date","")
            bitis   = None
            if end_raw:
                try:
                    bitis = datetime.fromisoformat(str(end_raw)[:10]).date()
                except:
                    pass
            out.append({
                "baslik"    : baslik,
                "icerik"    : _temizle_html(icerik)[:1000],
                "url"       : url,
                "kaynak"    : kaynak["isim"],
                "kaynak_tur": "takip",
                "zaman"     : datetime.now().date(),
                "bitis_date": bitis,
                "oncelik"   : kaynak.get("oncelik", 3),
            })
        return out
    except Exception as e:
        log.debug(f"[airdrop-cmc] {e}")
        return []


def _filtre_airdrop(items: list) -> list:
    """
    Haber/RSS listesinden airdrop ile ilgili olanları filtreler.
    Borsa duyurularından airdrop/kampanya içerenleri seçer.
    """
    anahtar = [
        "airdrop", "kampanya", "campaign", "giveaway", "reward",
        "bonus", "trading competition", "trading bonus",
        "deposit bonus", "referral", "davet", "yeni üye", "new user",
        "sign up", "kayıt", "register", "free token", "token dağıtım",
        "promotion", "promo", "ödül", "kazan", "earn free",
        "zero fee", "sıfır komisyon", "welcome",
    ]
    out = []
    for item in items:
        metin = (item.get("baslik","") + " " + item.get("icerik","")).lower()
        if any(k in metin for k in anahtar):
            out.append(item)
    return out


def _airdrop_to_card(item: dict) -> dict:
    """
    Ham RSS/API verisini standart airdrop kartına dönüştürür.
    """
    baslik  = item["baslik"][:80]
    icerik  = item.get("icerik","")
    kaynak  = item.get("kaynak","")
    url     = item.get("url","#")
    puan    = _puan_hesapla(baslik, icerik, kaynak)
    k_tur   = item.get("kaynak_tur","haber")

    # Bitiş tarihi
    bitis_date = item.get("bitis_date")
    if not bitis_date:
        # Metinden çıkarmaya çalış
        bitis_date = _tarih_parse(icerik[:500])
    # Tarih geçmişse atla
    if bitis_date and bitis_date < datetime.now().date():
        return None
    # Eğer tarih yoksa varsayılan: 30 gün
    if not bitis_date:
        bitis_date = (datetime.now() + timedelta(days=30)).date()

    bitis_str = bitis_date.strftime("%d.%m.%Y")

    # Ödül çıkarmaya çalış
    odul_pat = re.search(
        r"(\$[\d,.]+\s*[\w]*|[\d,.]+\s*usdt|[\d,.]+\s*\$|[\d,.]+\s*token|[\d,.]+\s*[A-Z]{2,6})",
        icerik[:300], re.IGNORECASE
    )
    odul = odul_pat.group(1).strip() if odul_pat else "Belirtilmemiş"

    # Kaynak türüne göre ikon
    ikon = {"borsa": "🏦", "haber": "📰", "takip": "🎯"}.get(k_tur, "🎁")

    aid = _airdrop_hash(baslik, url)

    return {
        "id"        : "auto_" + aid[:8],
        "hash"      : aid,
        "baslik"    : baslik,
        "odül"      : odul,
        "baslangic" : item.get("zaman", datetime.now().date()).strftime("%d.%m.%Y")
                      if hasattr(item.get("zaman", None), "strftime")
                      else str(item.get("zaman", datetime.now().date())),
        "bitis"     : bitis_str,
        "puan"      : puan,
        "link"      : url,
        "durum"     : "aktif",
        "kaynak"    : kaynak,
        "kaynak_tur": k_tur,
        "ikon"      : ikon,
        "oncelik"   : item.get("oncelik", 3),
        "eklendi"   : datetime.now(),
        "auto"      : True,
    }


async def fetch_auto_airdrops(force: bool = False) -> list:
    """
    Tüm kaynaklardan airdropları çeker, önbellekler.
    Öncelik sırası: borsa (1) > haber (2) > takip (3)
    """
    global _airdrop_cache, auto_airdrops

    if (not force
            and _airdrop_cache["data"]
            and _airdrop_cache["fetched_at"]
            and datetime.utcnow() - _airdrop_cache["fetched_at"] < _AIRDROP_CACHE_TTL):
        log.info(f"Airdrop önbelleğinden döndü ({len(_airdrop_cache['data'])})")
        return _airdrop_cache["data"]

    log.info(f"Airdroplar çekiliyor: {len(AIRDROP_KAYNAKLARI)} kaynak")
    tum_ham = []

    async with aiohttp.ClientSession() as sess:
        gorevler = []
        for k in AIRDROP_KAYNAKLARI:
            if k["tur"] in ("rss", "rss_filtreli"):
                gorevler.append(_airdrop_rss(sess, k))
            elif k["tur"] == "api":
                gorevler.append(_airdrop_cmc(sess, k))

        sonuclar = await asyncio.gather(*gorevler, return_exceptions=True)

    for kaynak, sonuc in zip(AIRDROP_KAYNAKLARI, sonuclar):
        if isinstance(sonuc, Exception):
            log.debug(f"Airdrop kaynak hatası {kaynak['isim']}: {sonuc}")
            continue
        # rss_filtreli → airdrop kelimesi içerenleri filtrele
        if kaynak["tur"] == "rss_filtreli":
            sonuc = _filtre_airdrop(sonuc)
        # borsa RSS → tüm duyurular arasından filtrele
        elif kaynak["tur"] == "rss" and kaynak.get("kaynak_tur") == "borsa":
            sonuc = _filtre_airdrop(sonuc)
        tum_ham.extend(sonuc)
        log.info(f"✅ {kaynak['isim']}: {len(sonuc)} airdrop")

    # Kart oluştur + tekilleştir
    kartlar, seen = [], set()
    for item in tum_ham:
        kart = _airdrop_to_card(item)
        if not kart:
            continue
        if kart["hash"] in seen:
            continue
        seen.add(kart["hash"])
        kartlar.append(kart)

    # Önce borsa, sonra puan, sonra tarih
    kartlar.sort(key=lambda x: (x["oncelik"], -x["puan"],
                                 x["bitis"]))

    _airdrop_cache = {"data": kartlar, "fetched_at": datetime.utcnow()}
    auto_airdrops  = kartlar
    log.info(f"Toplam {len(kartlar)} benzersiz airdrop")
    return kartlar


def get_auto_airdrops_active(min_puan: float = 0.0) -> list:
    """Aktif (süresi geçmemiş) ve puanı yeterli otomatik airdropları döner."""
    now = datetime.now().date()
    sonuc = []
    for a in auto_airdrops:
        try:
            bitis = datetime.strptime(a["bitis"], "%d.%m.%Y").date()
            if bitis < now:
                continue
        except:
            pass
        if a.get("puan", 0) >= min_puan:
            sonuc.append(a)
    return sonuc


def auto_airdrop_card(a: dict) -> str:
    """Otomatik airdrop kartı formatla."""
    p    = a.get("puan", 0)
    ikon = a.get("ikon", "🎁")
    k    = a.get("kaynak_tur","")
    tur_label = {"borsa": "🏦 Borsa", "haber": "📰 Haber", "takip": "🎯 Takip"}.get(k, "🎁 Airdrop")

    # Kalan gün
    try:
        bitis_d = datetime.strptime(a["bitis"], "%d.%m.%Y").date()
        kalan   = (bitis_d - datetime.now().date()).days
        if kalan == 0:   kalan_str = " ⚠️ *Bugün bitiyor!*"
        elif kalan <= 3: kalan_str = f" ⚡ *{kalan} gün kaldı!*"
        elif kalan < 0:  return ""
        else:            kalan_str = f" ({kalan} gün)"
    except:
        kalan_str = ""

    satirlar = [
        f"{ikon} *{a['baslik']}*",
        f"{puan_renk(p)} Puan: `{p}/10`  {puan_yildiz(p)}",
        f"💎 Tür: {tur_label}  |  Kaynak: _{a.get('kaynak','')}_ ",
        f"💰 Ödül: {a['odül']}",
        f"⏳ Bitiş: {a['bitis']}{kalan_str}",
        f"🔗 [Katıl / Detay]({a['link']})",
    ]
    return "\n".join(satirlar)


# ── Haber önbelleği ──
_news_cache: dict = {"data": [], "fetched_at": None}
_NEWS_CACHE_TTL = timedelta(minutes=10)

haber_ayarlari: dict = {
    "aktif"        : True,
    "interval_saat": 6,
    "son_dk_aktif" : True,
    "son_dk_esik"  : 30,
    "kanal_tag"    : "@KriptoDropTR",
    "ozet_stili"   : "standart",
    "max_per_run"  : 1,       # otomatik çalışmada max kaç haber paylaşılsın
    "kaynak_limit" : 15,      # kaynak başına max haber
}

bekleyen_haberler: dict = {}

OZET_STILLERI = {
    "standart": {
        "isim"  : "📝 Standart",
        "prompt": "Haberi Türkçe olarak 3-4 cümleyle özetle. Açık, akıcı ve bilgilendirici bir dil kullan. Teknik terimleri sadeleştir.",
    },
    "detayli": {
        "isim"  : "📄 Detaylı",
        "prompt": "Haberi Türkçe olarak 5-7 cümleyle detaylı özetle. Arka planı, nedenleri ve olası sonuçlarını da açıkla. Kripto piyasasına etkisini belirt.",
    },
    "kisaca": {
        "isim"  : "⚡ Kısaca",
        "prompt": "Haberi Türkçe olarak maksimum 2 kısa cümleyle özetle. Sadece en önemli bilgiyi ver. Çok kısa ve net ol.",
    },
    "bullet": {
        "isim"  : "📌 Madde Madde",
        "prompt": "Haberi Türkçe olarak 3-4 madde halinde özetle. Her madde tek cümle olsun. Yanıtta maddeleri '• ' ile başlat.",
    },
}

# ── Haber Kaynakları ──
# Her kaynak için doğrudan RSS URL ve yedek rss2json URL tanımlanmıştır.
# feedparser ile önce doğrudan çekilir, başarısız olursa rss2json proxy kullanılır.
HABER_KAYNAKLARI = [
    {
        "isim"   : "CoinTelegraph TR",
        "rss"    : "https://tr.cointelegraph.com/rss",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://tr.cointelegraph.com/rss",
        "aktif"  : True,
    },
    {
        "isim"   : "BTCHaber",
        "rss"    : "https://www.btchaber.com/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://www.btchaber.com/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "KriptoKoin",
        "rss"    : "https://kriptokoin.com/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://kriptokoin.com/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "CoinTurk",
        "rss"    : "https://cointurk.com/feed",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://cointurk.com/feed",
        "aktif"  : True,
    },
    {
        "isim"   : "KriptoPara",
        "rss"    : "https://kriptopara.com/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://kriptopara.com/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "Coin-Turk",
        "rss"    : "https://coin-turk.com/feed",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://coin-turk.com/feed",
        "aktif"  : True,
    },
    {
        "isim"   : "Kriptom",
        "rss"    : "https://www.kriptom.com/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://www.kriptom.com/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "Kripto.com.tr",
        "rss"    : "https://kripto.com.tr/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://kripto.com.tr/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "Borsagundem",
        "rss"    : "https://www.borsagundem.com/feed",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://www.borsagundem.com/feed",
        "aktif"  : True,
    },
    {
        "isim"   : "TeknoKripto",
        "rss"    : "https://teknokripto.com/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://teknokripto.com/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "CoinDesk (EN)",
        "rss"    : "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://www.coindesk.com/arc/outboundfeeds/rss/",
        "aktif"  : True,
    },
    {
        "isim"   : "CoinTelegraph (EN)",
        "rss"    : "https://cointelegraph.com/rss",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://cointelegraph.com/rss",
        "aktif"  : True,
    },
    {
        "isim"   : "Decrypt (EN)",
        "rss"    : "https://decrypt.co/feed",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://decrypt.co/feed",
        "aktif"  : True,
    },
    {
        "isim"   : "The Block (EN)",
        "rss"    : "https://www.theblock.co/rss.xml",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://www.theblock.co/rss.xml",
        "aktif"  : True,
    },
    {
        "isim"   : "BeInCrypto TR",
        "rss"    : "https://tr.beincrypto.com/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://tr.beincrypto.com/feed/",
        "aktif"  : True,
    },
    {
        "isim"   : "Crypto.news (EN)",
        "rss"    : "https://crypto.news/feed/",
        "proxy"  : "https://api.rss2json.com/v1/api.json?rss_url=https://crypto.news/feed/",
        "aktif"  : True,
    },
]

# ── Son Dakika Anahtar Kelimeleri (AI öncesi hızlı filtre) ──
SON_DK_KEYWORDS = [
    # Olaylar
    "hack", "saldırı", "exploit", "çalındı", "ihlal", "durduruldu", "askıya",
    "iflas", "çöküş", "çöktü", "borsası kapandı", "acil", "uyarı",
    # Kurumsal
    "sec", "cftc", "fbi", "interpol", "dava", "tutuklama", "gözaltı",
    "düzenleyici", "yasak", "yasaklandı", "kararname",
    # Piyasa
    "ath", "rekor", "yüzde 10", "yüzde 15", "yüzde 20", "%10", "%15", "%20",
    "ani düşüş", "ani yükseliş", "çöküş", "kriz",
    # Kurumsal
    "etf onaylandı", "etf reddedildi", "spot etf", "merkez bankası",
    "bitcoin reserve", "ulusal rezerv",
    # İngilizce (EN kaynaklar için)
    "hack", "exploit", "breach", "seized", "arrested", "banned", "collapse",
    "record high", "all time high", "emergency", "breaking", "urgent",
    "etf approved", "etf rejected",
]

def _haber_id(h: dict) -> str:
    """Haber için benzersiz ID oluşturur."""
    raw = h.get("url", "") + h.get("baslik", "")
    return hashlib.md5(raw.encode()).hexdigest()

def _temizle(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"&[a-z]+;", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def _parse_date(s: str) -> datetime:
    if not s:
        return datetime.utcnow()
    for fmt in [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=None) if dt.tzinfo else dt
        except:
            pass
    return datetime.utcnow()

def _son_dk_hizli(baslik: str, icerik: str) -> bool:
    """AI çağırmadan anahtar kelime ile son dakika tespiti."""
    metin = (baslik + " " + icerik).lower()
    return any(k in metin for k in SON_DK_KEYWORDS)

# ── Kaynak Çekme: feedparser (doğrudan) + rss2json (yedek) ──

async def _fetch_direct(sess: aiohttp.ClientSession, kaynak: dict, limit: int) -> list:
    """feedparser ile doğrudan RSS çeker."""
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; KriptoBot/1.0)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*",
        }
        async with sess.get(
            kaynak["rss"],
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=15),
            allow_redirects=True,
        ) as resp:
            if resp.status != 200:
                return []
            xml = await resp.text(errors="ignore")

        feed = feedparser.parse(xml)
        if not feed.entries:
            return []

        out = []
        for entry in feed.entries[:limit]:
            link    = entry.get("link", "")
            guid    = entry.get("id", link)
            baslik  = _temizle(entry.get("title", ""))
            icerik  = _temizle(
                entry.get("summary", "") or
                entry.get("content", [{}])[0].get("value", "") if entry.get("content") else ""
            )[:2000]
            zaman   = _parse_date(entry.get("published", entry.get("updated", "")))
            if not baslik or not link:
                continue
            out.append({
                "id"     : guid,
                "baslik" : baslik,
                "icerik" : icerik,
                "url"    : link,
                "kaynak" : kaynak["isim"],
                "zaman"  : zaman,
                "hash"   : _haber_id({"url": link, "baslik": baslik}),
            })
        return out
    except Exception as e:
        log.debug(f"[direct] {kaynak['isim']}: {e}")
        return []

async def _fetch_proxy(sess: aiohttp.ClientSession, kaynak: dict, limit: int) -> list:
    """rss2json proxy ile çeker (yedek)."""
    try:
        async with sess.get(
            kaynak["proxy"],
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            data = await resp.json(content_type=None)

        out = []
        for h in data.get("items", [])[:limit]:
            link   = h.get("link", "")
            guid   = h.get("guid", link)
            baslik = _temizle(h.get("title", ""))
            icerik = _temizle(h.get("description", h.get("content", "")))[:2000]
            zaman  = _parse_date(h.get("pubDate", ""))
            if not baslik or not link:
                continue
            out.append({
                "id"     : guid,
                "baslik" : baslik,
                "icerik" : icerik,
                "url"    : link,
                "kaynak" : kaynak["isim"],
                "zaman"  : zaman,
                "hash"   : _haber_id({"url": link, "baslik": baslik}),
            })
        return out
    except Exception as e:
        log.debug(f"[proxy] {kaynak['isim']}: {e}")
        return []

async def _fetch_kaynak(sess: aiohttp.ClientSession, kaynak: dict, limit: int) -> list:
    """Önce doğrudan, başarısız olursa proxy ile çeker."""
    sonuc = await _fetch_direct(sess, kaynak, limit)
    if not sonuc:
        log.info(f"[direct] {kaynak['isim']} başarısız, proxy deneniyor...")
        sonuc = await _fetch_proxy(sess, kaynak, limit)
    if sonuc:
        log.info(f"✅ {kaynak['isim']}: {len(sonuc)} haber")
    else:
        log.warning(f"❌ {kaynak['isim']}: haber alınamadı")
    return sonuc

async def fetch_crypto_news(force: bool = False, limit: int = 50) -> list:
    """
    Tüm kaynaklardan haberleri çeker, önbellekler.
    force=True ile önbelleği atlar.
    """
    global _news_cache

    # Önbellek kontrolü
    if (not force
            and _news_cache["data"]
            and _news_cache["fetched_at"]
            and datetime.utcnow() - _news_cache["fetched_at"] < _NEWS_CACHE_TTL):
        log.info(f"Haber önbelleğinden döndü ({len(_news_cache['data'])} haber)")
        return _news_cache["data"]

    aktif_kaynaklar = [k for k in HABER_KAYNAKLARI if k.get("aktif", True)]
    kaynak_limit    = haber_ayarlari.get("kaynak_limit", 15)

    log.info(f"Haberler çekiliyor: {len(aktif_kaynaklar)} kaynak, limit={kaynak_limit}")

    async with aiohttp.ClientSession() as sess:
        sonuclar = await asyncio.gather(
            *[_fetch_kaynak(sess, k, kaynak_limit) for k in aktif_kaynaklar],
            return_exceptions=True
        )

    # Birleştir ve tekilleştir
    tum, seen_urls, seen_hash = [], set(), set()
    for sonuc in sonuclar:
        if isinstance(sonuc, Exception):
            continue
        for h in sonuc:
            if h["url"] in seen_urls or h["hash"] in seen_hash:
                continue
            seen_urls.add(h["url"])
            seen_hash.add(h["hash"])
            tum.append(h)

    # Tarihe göre sırala (en yeni en üstte)
    tum.sort(key=lambda x: x["zaman"], reverse=True)
    tum = tum[:limit]

    # Önbelleğe al
    _news_cache = {"data": tum, "fetched_at": datetime.utcnow()}
    log.info(f"Toplam {len(tum)} benzersiz haber önbelleğe alındı")
    return tum

def _yeni_haberler(liste: list) -> list:
    """Daha önce paylaşılmamış haberleri döner."""
    return [
        h for h in liste
        if h["id"] not in posted_news
        and h["url"] not in posted_news
        and h["hash"] not in posted_news
    ]

# ── OpenAI Özet ──

async def openai_ozet(metin: str, baslik: str = "", stil: str = "standart") -> dict:
    """OpenAI ile haber özetler. API yoksa ham metni döner."""
    if not OPENAI_KEY:
        son_dk = _son_dk_hizli(baslik, metin)
        return {
            "baslik"   : baslik,
            "ozet"     : metin[:400] + ("..." if len(metin) > 400 else ""),
            "son_dk"   : son_dk,
            "etiketler": [],
        }

    stil_p = OZET_STILLERI.get(stil, OZET_STILLERI["standart"])["prompt"]
    prompt = (
        "Sana bir kripto para haberi veriyorum.\n\n"
        f"1. Başlık: Kısa ve çarpıcı Türkçe başlık yaz.\n"
        f"2. Özet: {stil_p}\n"
        "3. Son Dakika: Acil/kritik mi? "
        "(büyük hack/iflas, ülke kararı, SEC/CFTC kararı, ETF kararı, BTC/ETH %10+ ani hareket) → true/false\n"
        "4. Etiketler: max 3 kripto etiketi (#BTC gibi)\n\n"
        'YALNIZCA JSON: {"baslik":"...","ozet":"...","son_dk":false,"etiketler":["#BTC"]}\n\n'
        f"BAŞLIK: {baslik}\nİÇERİK: {metin[:2000]}"
    )
    payload = {
        "model"          : "gpt-4o-mini",
        "messages"       : [{"role": "user", "content": prompt}],
        "max_tokens"     : 700,
        "temperature"    : 0.35,
        "response_format": {"type": "json_object"},
    }
    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_KEY}",
            "Content-Type" : "application/json",
        }
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                "https://api.openai.com/v1/chat/completions",
                json=payload, headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                data = await r.json()
                res  = _json.loads(data["choices"][0]["message"]["content"])
                # Hızlı kontrol ile doğrula
                son_dk = bool(res.get("son_dk", False)) or _son_dk_hizli(baslik, metin)
                return {
                    "baslik"   : res.get("baslik", baslik),
                    "ozet"     : res.get("ozet", metin[:400]),
                    "son_dk"   : son_dk,
                    "etiketler": res.get("etiketler", []),
                }
    except Exception as e:
        log.warning(f"OpenAI hatası: {e}")
        son_dk = _son_dk_hizli(baslik, metin)
        return {
            "baslik"   : baslik,
            "ozet"     : metin[:400] + ("..." if len(metin) > 400 else ""),
            "son_dk"   : son_dk,
            "etiketler": [],
        }

def _haber_posted(h: dict):
    """Haberi yayınlandı olarak işaretle."""
    posted_news.add(h["id"])
    posted_news.add(h["url"])
    posted_news.add(h["hash"])

def haber_mesaj_formatla(h: dict, ai: dict, son_dk: bool = False) -> str:
    et  = " ".join(ai.get("etiketler", []))
    hdr = "🚨 *SON DAKİKA* 🚨" if son_dk else "📰 *Kripto Haber*"
    zm  = h["zaman"].strftime("%d.%m.%Y %H:%M") if h.get("zaman") else ""
    t   = f"{hdr}\n━━━━━━━━━━━━━━━━━━━━\n📌 *{ai['baslik']}*\n\n📝 {ai['ozet']}\n\n"
    t  += f"🇹🇷 {h['kaynak']}  🕐 {zm}\n🔗 [Haberin tamamı]({h['url']})"
    if et:
        t += f"\n\n{et}"
    t += f"\n\n━━━━━━━━━━━━━━\n🤖 {haber_ayarlari['kanal_tag']}"
    return t

# ══════════════════════════════════════════
#  AIRDROP & KULLANICI YARDIMCILARI
# ══════════════════════════════════════════

def is_admin(uid): return uid in ADMIN_IDS

def reset_periods():
    global today, week_start, month_start, daily_users, weekly_users, monthly_users
    nd = datetime.now().date()
    if nd != today: daily_users.clear(); today = nd
    nw = nd - timedelta(days=nd.weekday())
    if nw != week_start: weekly_users.clear(); week_start = nw
    nm = nd.replace(day=1)
    if nm != month_start: monthly_users.clear(); month_start = nm

def register_user(user):
    reset_periods(); uid = user.id
    daily_users.add(uid); weekly_users.add(uid)
    monthly_users.add(uid); all_time_users.add(uid)
    join_log.append({"user_id": uid, "date": datetime.now().date(), "name": user.full_name})

def get_active_airdrops():
    now = datetime.now().date(); result = []
    for a in airdrops:
        if a["durum"] != "aktif": continue
        try:
            if datetime.strptime(a["bitis"], "%d.%m.%Y").date() < now:
                a["durum"] = "bitti"; continue
        except: pass
        result.append(a)
    return result

def get_airdrop_by_id(aid): return next((a for a in airdrops if a["id"] == aid), None)

def _bitis_gun(a):
    try: return datetime.strptime(a["bitis"], "%d.%m.%Y").date()
    except: return None

def puan_yildiz(p):
    t = int(p); y = 1 if (p - t) >= 0.5 else 0; b = 10 - t - y
    return "⭐" * t + ("✨" if y else "") + "☆" * b

def puan_renk(p): return "🟢" if p >= 8 else "🟡" if p >= 5 else "🔴"

def kalan_gun(a):
    bg = _bitis_gun(a)
    if not bg: return ""
    k = (bg - datetime.now().date()).days
    if k < 0:  return " *(Süresi doldu)*"
    if k == 0: return " ⚠️ *Bugün bitiyor!*"
    if k <= 3: return f" ⚡ *{k} gün kaldı!*"
    return f" ({k} gün)"

def airdrop_card(a, detay=False):
    p = a.get("puan", 0)
    d = "✅" if a["durum"] == "aktif" else "❌"
    s = [
        f"{d} *#{a['id']} — {a['baslik']}*",
        f"{puan_renk(p)} Puan: `{p}/10`  {puan_yildiz(p)}",
        f"💰 Ödül: {a['odül']}",
        f"📅 Başlangıç: {a.get('baslangic', '—')}",
        f"⏳ Bitiş: {a['bitis']}{kalan_gun(a)}",
        f"🔗 [Katıl]({a['link']})",
    ]
    return "\n".join(s)

# ── Klavyeler ──
def ana_menu_kb(adm):
    rows = [
        [InlineKeyboardButton("🎁 Airdroplar",  callback_data="menu_airdrops"),
         InlineKeyboardButton("🏆 En İyiler",   callback_data="menu_topairdrops")],
        [InlineKeyboardButton("🔍 Filtrele",    callback_data="menu_filtre"),
         InlineKeyboardButton("📰 Haberler",    callback_data="menu_haberler")],
        [InlineKeyboardButton("📊 İstatistik",  callback_data="menu_istatistik"),
         InlineKeyboardButton("❓ Yardım",       callback_data="menu_yardim")],
    ]
    if adm:
        rows.append([InlineKeyboardButton("⚙️ Admin Paneli", callback_data="adm_ana")])
    return InlineKeyboardMarkup(rows)

def filtre_kb(geri="menu_ana"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Puan ≥ 8",         callback_data="filtre_8"),
         InlineKeyboardButton("🟡 Puan ≥ 5",         callback_data="filtre_5"),
         InlineKeyboardButton("📋 Tümü",             callback_data="filtre_0")],
        [InlineKeyboardButton("🏦 Sadece Borsalar",  callback_data="filtre_borsalar"),
         InlineKeyboardButton("⏰ Bugün Bitiyor",    callback_data="filtre_bugun"),
         InlineKeyboardButton("📅 Bu Hafta",         callback_data="filtre_hafta")],
        [InlineKeyboardButton("🔄 Yenile",           callback_data="auto_airdrop_ara"),
         InlineKeyboardButton("🔙 Geri",             callback_data=geri)],
    ])

def adm_ana_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎁 Airdrop Yönetimi", callback_data="adm_airdrop"),
         InlineKeyboardButton("📰 Haber Yönetimi",   callback_data="adm_haber")],
        [InlineKeyboardButton("⚙️ Haber Ayarları",   callback_data="adm_haber_ayar"),
         InlineKeyboardButton("📊 İstatistikler",    callback_data="adm_istat")],
        [InlineKeyboardButton("📣 Duyuru Gönder",    callback_data="adm_duyuru_info"),
         InlineKeyboardButton("👥 Üye Raporu",       callback_data="adm_uye_rapor")],
        [InlineKeyboardButton("🔄 Haberleri Yenile", callback_data="adm_haber_yenile"),
         InlineKeyboardButton("📡 Kaynak Durumu",    callback_data="adm_kaynak_durum")],
        [InlineKeyboardButton("🔙 Ana Menü",         callback_data="menu_ana")],
    ])

def adm_airdrop_kb():
    cache_str = ""
    if _airdrop_cache.get("fetched_at"):
        age = int((datetime.utcnow() - _airdrop_cache["fetched_at"]).total_seconds() / 60)
        cache_str = f" ({age}dk)"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Otomatik Çek" + cache_str, callback_data="adm_air_otomatik_cek")],
        [InlineKeyboardButton("➕ Nasıl Eklerim?",     callback_data="adm_air_ekle_info"),
         InlineKeyboardButton("📋 Tüm Airdroplar",    callback_data="adm_air_tumu")],
        [InlineKeyboardButton("✅ Sadece Aktif",       callback_data="adm_air_aktif"),
         InlineKeyboardButton("❌ Biten Airdroplar",  callback_data="adm_air_bitti")],
        [InlineKeyboardButton("🏦 Borsa Kampanyaları",callback_data="adm_air_borsalar"),
         InlineKeyboardButton("🏆 Puana Göre",        callback_data="adm_air_puan")],
        [InlineKeyboardButton("📊 Kaynak Durumu",     callback_data="adm_air_kaynak_durum"),
         InlineKeyboardButton("🗑 Önbellek Temizle",  callback_data="adm_air_cache_temizle")],
        [InlineKeyboardButton("🔙 Admin Paneli",       callback_data="adm_ana")],
    ])

def adm_haber_kb():
    oto = "✅ Oto Haber Açık" if haber_ayarlari["aktif"] else "❌ Oto Haber Kapalı"
    sdk = "✅ Son Dk Açık"    if haber_ayarlari["son_dk_aktif"] else "❌ Son Dk Kapalı"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("👁 Önizle & Paylaş",      callback_data="adm_haber_onizle")],
        [InlineKeyboardButton(oto,                        callback_data="adm_haber_toggle")],
        [InlineKeyboardButton(sdk,                        callback_data="adm_sondk_toggle")],
        [InlineKeyboardButton("📊 Durum & Kaynaklar",    callback_data="adm_haber_durum"),
         InlineKeyboardButton("🗑 Geçmişi Temizle",      callback_data="adm_haber_temizle")],
        [InlineKeyboardButton("🔄 Önbelleği Yenile",     callback_data="adm_haber_yenile"),
         InlineKeyboardButton("📡 Kaynak Durumu",        callback_data="adm_kaynak_durum")],
        [InlineKeyboardButton("🔙 Admin Paneli",          callback_data="adm_ana")],
    ])

def adm_haber_ayar_kb():
    st = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili", "standart"), {}).get("isim", "")
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"📝 Özet Stili: {st}", callback_data="adm_stil_menu")],
        [InlineKeyboardButton("⏱ 1sa",  callback_data="adm_sure_1"),
         InlineKeyboardButton("⏱ 3sa",  callback_data="adm_sure_3"),
         InlineKeyboardButton("⏱ 6sa",  callback_data="adm_sure_6"),
         InlineKeyboardButton("⏱ 12sa", callback_data="adm_sure_12"),
         InlineKeyboardButton("⏱ 24sa", callback_data="adm_sure_24")],
        [InlineKeyboardButton("📦 Maks 1 Haber/Run", callback_data="adm_max_1"),
         InlineKeyboardButton("📦 Maks 3 Haber/Run", callback_data="adm_max_3"),
         InlineKeyboardButton("📦 Maks 5 Haber/Run", callback_data="adm_max_5")],
        [InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")],
    ])

def adm_stil_kb():
    mev = haber_ayarlari.get("ozet_stili", "standart")
    rows = [
        [InlineKeyboardButton(("✅ " if k == mev else "") + v["isim"], callback_data=f"adm_stil_{k}")]
        for k, v in OZET_STILLERI.items()
    ]
    rows.append([InlineKeyboardButton("🔙 Geri", callback_data="adm_haber_ayar")])
    return InlineKeyboardMarkup(rows)

def onizleme_kb(idx, toplam):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gruba Gönder",   callback_data="hab_onayla"),
         InlineKeyboardButton("⏭ Sonrakine Geç",  callback_data="hab_sonraki")],
        [InlineKeyboardButton("🔄 Yenile",          callback_data="hab_yenile"),
         InlineKeyboardButton("🔙 İptal",           callback_data="adm_haber")],
    ])

def get_welcome_message():
    text = (
        "🎉 *KriptoDropTR 🎁 Kanalımıza Hoş Geldiniz!* 🎉\n\n"
        "🚀 Güncel *Airdrop* fırsatlarından haberdar olmak için\n"
        "📢 *KriptoDropTR DUYURU 🔊* kanalımıza katılmayı\n"
        "🔔 Kanal bildirimlerini açmayı unutmayın!\n\n💎 Bol kazançlar dileriz!"
    )
    kb = [
        [InlineKeyboardButton("📢 KriptoDropTR DUYURU 🔊", url="https://t.me/kriptodropduyuru")],
        [InlineKeyboardButton("📜 Kurallar", url="https://t.me/kriptodropduyuru/46")],
        [InlineKeyboardButton("❓ SSS",      url="https://t.me/kriptodropduyuru/47")],
    ]
    return text, InlineKeyboardMarkup(kb)

# ══════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════

async def welcome(update: Update, context: ContextTypes.DEFAULT_TYPE):
    for m in update.message.new_chat_members:
        if m.is_bot: continue
        register_user(m)
        t, kb = get_welcome_message()
        await update.message.reply_text(t, reply_markup=kb, parse_mode="Markdown")

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user; adm = is_admin(user.id)
    if update.effective_chat.type == "private":
        await update.message.reply_text(
            f"👋 Merhaba *{user.first_name}*!\n\n🤖 *KriptoDropTR Bot*'a hoş geldin:",
            parse_mode="Markdown", reply_markup=ana_menu_kb(adm))
    else:
        await update.message.reply_text(
            "👋 Merhaba! Airdroplar ve haberler için bana *DM* yaz 👉 @KriptoDropTR_bot",
            parse_mode="Markdown")

async def cmd_yardim(update: Update, context: ContextTypes.DEFAULT_TYPE):
    adm = is_admin(update.effective_user.id)
    t = (
        "📖 *KriptoDropTR Bot — Komutlar*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🌐 *Genel:*\n/start — Ana menü\n/airdrops — Aktif airdroplar\n"
        "/topairdrops — Puan ≥ 8 olanlar\n/airdrop `<id>` — Detay\n"
        "/haberler — Son Türkçe kripto haberleri\n/istatistik — İstatistikler\n"
    )
    if adm:
        t += (
            "\n🔧 *Admin (DM):*\n"
            "/airdropekle `Başlık | Ödül | Baş | Bitiş | Puan | Link`\n"
            "/airdropduzenle `<id> | alan | değer`\n"
            "/airdropbitir `<id>` — Sonlandır\n/airdropsil `<id>` — Sil\n"
            "/haberler_paylas — Önizleme ile paylaş\n/haberayar — Haber ayarları\n"
            "/duyuru `<metin>` — Gruba duyuru\n"
        )
    await update.message.reply_text(
        t, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Ana Menü", callback_data="menu_ana")]]))

async def cmd_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("⏳ Airdroplar yükleniyor...")
    manuel   = get_active_airdrops()
    otomatik = get_auto_airdrops_active()

    if not manuel and not otomatik:
        await msg.edit_text(
            "🎁 Şu an aktif airdrop bulunamadı.\n\n💡 Yeniden aramak için 🔄 butonuna basın.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Şimdi Ara", callback_data="auto_airdrop_ara"),
                InlineKeyboardButton("🔙 Menü",      callback_data="menu_ana"),
            ]]))
        return

    bolumler = []
    if manuel:
        bolumler.append("📌 *Manuel Eklenenler*")
        bolumler += [airdrop_card(a) for a in manuel]

    if otomatik:
        borsalar = [a for a in otomatik if a.get("kaynak_tur") == "borsa"]
        diger    = [a for a in otomatik if a.get("kaynak_tur") != "borsa"]
        if borsalar:
            bolumler.append("\n🏦 *Borsa Kampanyaları*")
            bolumler += [auto_airdrop_card(a) for a in borsalar[:5] if auto_airdrop_card(a)]
        if diger:
            bolumler.append("\n🎯 *Diğer Airdroplar*")
            bolumler += [auto_airdrop_card(a) for a in diger[:5] if auto_airdrop_card(a)]

    toplam = len(manuel) + len(otomatik)
    t  = "🎁 *Aktif Airdrop & Kampanyalar*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    t += "\n\n".join(s for s in bolumler if s)
    t += f"\n\n📌 {toplam} kampanya  |  🔄 3 saatte bir güncellenir"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Puan ≥ 8",        callback_data="filtre_8"),
         InlineKeyboardButton("🏦 Sadece Borsalar",  callback_data="filtre_borsalar")],
        [InlineKeyboardButton("📋 Tümü",             callback_data="filtre_0"),
         InlineKeyboardButton("🔄 Yenile",           callback_data="auto_airdrop_ara")],
        [InlineKeyboardButton("🔙 Menü",             callback_data="menu_ana")],
    ])
    await msg.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=kb)

async def cmd_top_airdrops(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mp = 7.0
    if context.args:
        try: mp = float(context.args[0])
        except: pass
    msg = await update.message.reply_text("⏳ En iyi airdroplar aranıyor...")
    manuel   = sorted([a for a in get_active_airdrops() if a.get("puan",0) >= mp],
                      key=lambda x: x.get("puan",0), reverse=True)
    otomatik = sorted([a for a in get_auto_airdrops_active(min_puan=mp)],
                      key=lambda x: x.get("puan",0), reverse=True)
    if not manuel and not otomatik:
        await msg.edit_text(
            f"😕 Puan ≥ {mp} olan aktif airdrop yok.\n\n"
            "💡 `/topairdrops 5` ile eşiği düşürebilirsiniz.",
            parse_mode="Markdown"); return
    satirlar = []
    borsalar = [a for a in otomatik if a.get("kaynak_tur")=="borsa"]
    diger_oto = [a for a in otomatik if a.get("kaynak_tur")!="borsa"]
    if borsalar:
        satirlar.append("🏦 *Borsa Kampanyaları*")
        satirlar += [auto_airdrop_card(a) for a in borsalar[:4] if auto_airdrop_card(a)]
    if diger_oto:
        satirlar.append("\n🎯 *Diğer Yüksek Puanlı*")
        satirlar += [auto_airdrop_card(a) for a in diger_oto[:4] if auto_airdrop_card(a)]
    if manuel:
        satirlar.append("\n📌 *Manuel Eklenenler*")
        satirlar += [airdrop_card(a) for a in manuel]
    toplam = len(manuel)+len(otomatik)
    t  = f"🏆 *En İyi Airdroplar (Puan ≥ {mp})*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    t += "\n\n".join(s for s in satirlar if s)
    t += f"\n\n📊 {toplam} kampanya listelendi"
    await msg.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

async def cmd_airdrop_detay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: `/airdrop <id>`", parse_mode="Markdown"); return
    a = get_airdrop_by_id(int(context.args[0]))
    if not a:
        await update.message.reply_text("❌ Bulunamadı."); return
    await update.message.reply_text(
        airdrop_card(a), parse_mode="Markdown", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔗 Katıl", url=a["link"]),
            InlineKeyboardButton("🔙 Liste", callback_data="menu_airdrops"),
        ]]))

async def cmd_haberler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("📰 Türkçe haberler alınıyor... ⏳")
    haberler = await fetch_crypto_news()
    if not haberler:
        await msg.edit_text("❌ Haberler alınamadı. Kaynaklar geçici olarak erişilemiyor olabilir."); return
    await msg.delete()
    stil = haber_ayarlari.get("ozet_stili", "standart")
    # Kullanıcıya en son 5 haberi göster
    for h in haberler[:5]:
        ai = await openai_ozet(h["icerik"], h["baslik"], stil)
        await update.message.reply_text(
            haber_mesaj_formatla(h, ai, ai.get("son_dk", False)),
            parse_mode="Markdown", disable_web_page_preview=True)
        await asyncio.sleep(0.8)

async def cmd_istatistik(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reset_periods()
    td = datetime.now().date(); gd = {}
    for e in join_log:
        if (td - e["date"]).days < 7:
            l = e["date"].strftime("%d.%m"); gd[l] = gd.get(l, 0) + 1
    ds = "".join(f"  {g}: {'█'*min(s,15)} {s}\n" for g, s in sorted(gd.items()))
    t = (
        f"📊 *KriptoDropTR — İstatistikler*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📅 Bugün: {len(daily_users)}  📆 Bu Hafta: {len(weekly_users)}\n"
        f"🗓 Bu Ay: {len(monthly_users)}  🏆 Toplam: {len(all_time_users)}\n\n"
        f"🎁 Aktif: {len(get_active_airdrops())}  |  Toplam: {len(airdrops)}\n"
        f"📰 Paylaşılan Haber: {len(posted_news)}\n\n"
        f"📈 *Son 7 Gün:*\n{ds if ds else '  Veri yok.'}"
    )
    await update.message.reply_text(
        t, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

async def cmd_airdrop_ara(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """İnternetten airdrop/kampanya ara."""
    msg = await update.message.reply_text(
        "🔄 İnternetten airdrop & kampanyalar aranıyor...\n"
        "⏳ Bu işlem 15-30 saniye sürebilir."
    )
    try:
        kartlar  = await fetch_auto_airdrops(force=True)
        borsalar = [a for a in kartlar if a.get("kaynak_tur")=="borsa"]
        haberler = [a for a in kartlar if a.get("kaynak_tur")=="haber"]
        takip    = [a for a in kartlar if a.get("kaynak_tur")=="takip"]

        bolumler = []
        if borsalar:
            bolumler.append("🏦 *Borsa Kampanyaları*")
            bolumler += [auto_airdrop_card(a) for a in borsalar[:5] if auto_airdrop_card(a)]
        if haberler:
            bolumler.append("\n📰 *Haber Sitelerinden*")
            bolumler += [auto_airdrop_card(a) for a in haberler[:3] if auto_airdrop_card(a)]
        if takip:
            bolumler.append("\n🎯 *Takip Sitelerinden*")
            bolumler += [auto_airdrop_card(a) for a in takip[:2] if auto_airdrop_card(a)]

        if not bolumler:
            await msg.edit_text(
                "😕 Şu an aktif airdrop bulunamadı.\n\n"
                "Kaynaklar geçici olarak erişilemiyor olabilir. Daha sonra tekrar deneyin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))
            return

        t  = f"🎁 *Airdrop & Kampanya Taraması*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        t += "\n\n".join(s for s in bolumler if s)
        t += (f"\n\n📊 Toplam: *{len(kartlar)}* kampanya\n"
              f"🏦 Borsa: {len(borsalar)}  📰 Haber: {len(haberler)}  🎯 Takip: {len(takip)}")
        await msg.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=filtre_kb())
    except Exception as e:
        log.error(f"cmd_airdrop_ara: {e}")
        await msg.edit_text(
            f"❌ Arama sırasında hata oluştu.\n`{e}`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))


async def cmd_airdrop_ekle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("⛔ DM'den kullan."); return
    global airdrop_counter
    ornek = (
        "📝 *Kullanım:*\n`/airdropekle Başlık | Ödül | Başlangıç | Bitiş | Puan | Link`\n\n"
        "📌 Tarih: `GG.AA.YYYY`  |  Puan: `0–10`\n\n*Örnek:*\n"
        "`/airdropekle Layer3 | 50 USDT | 01.01.2025 | 31.03.2025 | 9 | https://layer3.xyz`"
    )
    if not context.args:
        await update.message.reply_text(ornek, parse_mode="Markdown"); return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) < 6:
        await update.message.reply_text("❌ 6 alan gerekli.\n\n" + ornek, parse_mode="Markdown"); return
    try:
        puan = float(parts[4]); assert 0 <= puan <= 10
    except:
        await update.message.reply_text("❌ Puan 0–10 olmalı."); return
    airdrop_counter += 1
    yeni = {
        "id"       : airdrop_counter, "baslik": parts[0], "odül": parts[1],
        "baslangic": parts[2],        "bitis" : parts[3], "puan": puan,
        "link"     : parts[5],        "durum" : "aktif",  "eklendi": datetime.now(),
    }
    airdrops.append(yeni)
    await update.message.reply_text(
        f"✅ *Airdrop Eklendi!*\n\n{airdrop_card(yeni)}",
        parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_airdrop_duzenle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    if not context.args:
        await update.message.reply_text(
            "Kullanım: `/airdropduzenle <id> | alan | değer`\n"
            "Alanlar: `baslik odül baslangic bitis puan link durum`",
            parse_mode="Markdown"); return
    parts = [p.strip() for p in " ".join(context.args).split("|")]
    if len(parts) < 3 or not parts[0].isdigit():
        await update.message.reply_text("❌ Format hatası."); return
    a = get_airdrop_by_id(int(parts[0]))
    if not a:
        await update.message.reply_text("❌ Bulunamadı."); return
    alan, deger = parts[1].lower(), parts[2]
    if alan == "puan":
        try: deger = float(deger)
        except: await update.message.reply_text("❌ Puan sayı olmalı."); return
    if alan not in a:
        await update.message.reply_text(f"❌ Geçersiz alan: `{alan}`", parse_mode="Markdown"); return
    a[alan] = deger
    await update.message.reply_text(
        f"✅ Güncellendi!\n\n{airdrop_card(a)}", parse_mode="Markdown", disable_web_page_preview=True)

async def cmd_airdrop_bitir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: `/airdropbitir <id>`", parse_mode="Markdown"); return
    a = get_airdrop_by_id(int(context.args[0]))
    if not a:
        await update.message.reply_text("❌ Bulunamadı."); return
    a["durum"] = "bitti"
    await update.message.reply_text(f"❌ *#{a['id']} — {a['baslik']}* sonlandırıldı.", parse_mode="Markdown")

async def cmd_airdrop_sil(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Kullanım: `/airdropsil <id>`", parse_mode="Markdown"); return
    a = get_airdrop_by_id(int(context.args[0]))
    if not a:
        await update.message.reply_text("❌ Bulunamadı."); return
    airdrops.remove(a)
    await update.message.reply_text(f"🗑 *#{a['id']} — {a['baslik']}* silindi.", parse_mode="Markdown")

# ── Haber Önizleme ──
async def _onizle_gonder(uid: int, send_func, context, idx: int = 0, force: bool = False):
    haberler = await fetch_crypto_news(force=force)
    yeni = _yeni_haberler(haberler)
    if not yeni:
        await send_func(
            f"ℹ️ *Yeni haber yok.*\n\n"
            f"{len(haberler)} haber çekildi, tamamı daha önce paylaşılmış.\n\n"
            "💡 'Geçmişi Temizle' veya 'Önbelleği Yenile' ile sıfırlayabilirsin.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Yenile",    callback_data="adm_haber_yenile"),
                InlineKeyboardButton("🔙 Admin",     callback_data="adm_haber"),
            ]])); return
    if idx >= len(yeni): idx = 0
    h = yeni[idx]
    stil = haber_ayarlari.get("ozet_stili", "standart")
    ai   = await openai_ozet(h["icerik"], h["baslik"], stil)
    text = haber_mesaj_formatla(h, ai, ai.get("son_dk", False))
    bekleyen_haberler[uid] = {
        "text"     : text,
        "haber_id" : h["id"],
        "haber_url": h["url"],
        "haber_hash": h["hash"],
        "index"    : idx,
        "toplam"   : len(yeni),
    }
    son_dk_tag = "🚨 SON DAKİKA  |  " if ai.get("son_dk") else ""
    oniz = (
        f"👁 *HABER ÖNİZLEME* ({idx+1}/{len(yeni)})\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{son_dk_tag}Stil: {OZET_STILLERI.get(stil,{}).get('isim','')}  |  Kaynak: {h['kaynak']}\n"
        f"🕐 {h['zaman'].strftime('%d.%m.%Y %H:%M')}\n\n" + text
    )
    await send_func(oniz, parse_mode="Markdown", disable_web_page_preview=True,
                    reply_markup=onizleme_kb(idx, len(yeni)))

async def cmd_haber_paylas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("⛔ DM'den kullan."); return
    if GROUP_ID == 0:
        await update.message.reply_text("❌ GROUP_ID ayarlanmamış."); return
    msg = await update.message.reply_text("📰 Haberler alınıyor... ⏳")
    await msg.delete()
    await _onizle_gonder(update.effective_user.id, update.message.reply_text, context, idx=0)

async def cmd_haber_ayar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    args = context.args
    if args and args[0].lower() == "temizle":
        n = len(posted_news); posted_news.clear()
        await update.message.reply_text(f"✅ Haber geçmişi temizlendi ({n} kayıt silindi)."); return
    oto  = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
    sdk  = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
    stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"), {}).get("isim","")
    cache_info = ""
    if _news_cache["fetched_at"]:
        age = int((datetime.utcnow() - _news_cache["fetched_at"]).total_seconds() / 60)
        cache_info = f"🗄 Önbellek: {len(_news_cache['data'])} haber, {age} dk önce\n"
    t = (
        f"⚙️ *Haber Sistemi Ayarları*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📰 Otomatik Haber: {oto}\n⏱ Sıklık: Her {haber_ayarlari['interval_saat']} saatte bir\n"
        f"🚨 Son Dakika: {sdk} (eşik: {haber_ayarlari['son_dk_esik']} dk)\n"
        f"📝 Özet Stili: {stil}\n"
        f"📊 Paylaşılan: {len(posted_news)} haber\n"
        f"{cache_info}\n"
        f"📡 Kaynak: {len([k for k in HABER_KAYNAKLARI if k.get('aktif',True)])} aktif\n\n"
        "Admin panelinden de yönetebilirsin:"
    )
    await update.message.reply_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

async def cmd_duyuru(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("⛔ Yetki yok."); return
    if update.effective_chat.type != "private":
        await update.message.reply_text("⛔ DM'den kullan."); return
    if not context.args:
        await update.message.reply_text("Kullanım: `/duyuru <metin>`", parse_mode="Markdown"); return
    if GROUP_ID == 0:
        await update.message.reply_text("❌ GROUP_ID ayarlanmamış."); return
    metin = " ".join(context.args)
    t = f"📣 *DUYURU*\n━━━━━━━━━━━━━━\n\n{metin}\n\n━━━━━━━━━━━━━━\n🤖 {haber_ayarlari['kanal_tag']}"
    await context.bot.send_message(GROUP_ID, t, parse_mode="Markdown")
    await update.message.reply_text("✅ Duyuru gruba gönderildi.")

# ══════════════════════════════════════════
#  CALLBACK HANDLER
# ══════════════════════════════════════════

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query; data = q.data; uid = q.from_user.id; adm = is_admin(uid)
    await q.answer()

    # ── Kullanıcı Menüsü ──
    if data == "menu_ana":
        await q.message.edit_text(
            "👋 *KriptoDropTR Bot*\n\nAşağıdan işlem seç:",
            parse_mode="Markdown", reply_markup=ana_menu_kb(adm))

    elif data == "menu_airdrops":
        await q.message.edit_text("⏳ Airdroplar yükleniyor...")
        manuel   = get_active_airdrops()
        otomatik = get_auto_airdrops_active()
        if not manuel and not otomatik:
            await q.message.edit_text(
                "🎁 Şu an aktif airdrop bulunamadı.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Şimdi Ara", callback_data="auto_airdrop_ara")],
                    [InlineKeyboardButton("🔙 Menü",      callback_data="menu_ana")],
                ])); return
        bolumler = []
        if manuel:
            bolumler.append("📌 *Manuel Eklenenler*")
            bolumler += [airdrop_card(a) for a in manuel]
        borsalar = [a for a in otomatik if a.get("kaynak_tur")=="borsa"]
        diger    = [a for a in otomatik if a.get("kaynak_tur")!="borsa"]
        if borsalar:
            bolumler.append("\n🏦 *Borsa Kampanyaları*")
            bolumler += [auto_airdrop_card(a) for a in borsalar[:4] if auto_airdrop_card(a)]
        if diger:
            bolumler.append("\n🎯 *Diğer Airdroplar*")
            bolumler += [auto_airdrop_card(a) for a in diger[:3] if auto_airdrop_card(a)]
        t  = "🎁 *Airdrop & Kampanyalar*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        t += "\n\n".join(s for s in bolumler if s)
        t += f"\n\n📌 {len(manuel)+len(otomatik)} kampanya"
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

    elif data == "menu_topairdrops":
        await q.message.edit_text("⏳ En iyi kampanyalar aranıyor...")
        manuel_top = sorted([a for a in get_active_airdrops() if a.get("puan",0)>=7],
                            key=lambda x:x.get("puan",0), reverse=True)
        oto_top    = sorted([a for a in get_auto_airdrops_active(min_puan=7)],
                            key=lambda x:x.get("puan",0), reverse=True)
        bolumler   = []
        borsalar   = [a for a in oto_top if a.get("kaynak_tur")=="borsa"]
        diger_oto  = [a for a in oto_top if a.get("kaynak_tur")!="borsa"]
        if borsalar:
            bolumler.append("🏦 *Borsa Kampanyaları*")
            bolumler += [auto_airdrop_card(a) for a in borsalar[:4] if auto_airdrop_card(a)]
        if diger_oto:
            bolumler.append("\n🎯 *Diğer Yüksek Puanlı*")
            bolumler += [auto_airdrop_card(a) for a in diger_oto[:3] if auto_airdrop_card(a)]
        if manuel_top:
            bolumler.append("\n📌 *Manuel Eklenenler*")
            bolumler += [airdrop_card(a) for a in manuel_top]
        if not bolumler:
            await q.message.edit_text("😕 Yüksek puanlı airdrop yok.", reply_markup=filtre_kb()); return
        toplam = len(manuel_top)+len(oto_top)
        t  = f"🏆 *En İyi Kampanyalar (≥7)*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        t += "\n\n".join(s for s in bolumler if s)
        t += f"\n\n📊 {toplam} kampanya"
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

    elif data == "menu_filtre":
        await q.message.edit_text("🔍 *Filtrele:*", parse_mode="Markdown", reply_markup=filtre_kb())

    elif data.startswith("filtre_"):
        now = datetime.now().date(); aktif = get_active_airdrops()
        if data == "filtre_8":
            liste = sorted([a for a in aktif if a.get("puan",0) >= 8], key=lambda x: x.get("puan",0), reverse=True); bas = "🟢 Puan ≥ 8"
        elif data == "filtre_5":
            liste = sorted([a for a in aktif if a.get("puan",0) >= 5], key=lambda x: x.get("puan",0), reverse=True); bas = "🟡 Puan ≥ 5"
        elif data == "filtre_0":
            liste = sorted(aktif, key=lambda x: x.get("puan",0), reverse=True); bas = "📋 Tüm Aktif"
        elif data == "filtre_borsalar":
            liste_oto = [a for a in get_auto_airdrops_active() if a.get("kaynak_tur")=="borsa"]
            bas = "🏦 Borsa Kampanyaları"
            t = f"🔍 *{bas}*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            if liste_oto:
                t += "\n\n".join(auto_airdrop_card(a) for a in liste_oto[:10] if auto_airdrop_card(a))
                t += f"\n\n📌 {len(liste_oto)} borsa kampanyası"
            else:
                t += "😕 Şu an borsa kampanyası bulunamadı.\n\n💡 '🔄 Yenile' ile tekrar deneyin."
            await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())
            return
        elif data == "filtre_bugun":
            liste = [a for a in aktif if _bitis_gun(a) == now]; bas = "⏰ Bugün Bitiyor"
        elif data == "filtre_hafta":
            liste = [a for a in aktif if _bitis_gun(a) and (_bitis_gun(a) - now).days <= 7]; bas = "📅 Bu Hafta"
        else:
            liste = aktif; bas = "Airdroplar"
        t = (f"🔍 *{bas}*\n━━━━━━━━━━━━━━━━━━━━━\n\n" +
             "\n\n".join(airdrop_card(a) for a in liste) +
             f"\n\n📌 {len(liste)} airdrop") if liste else f"😕 *{bas}* — sonuç yok."
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True, reply_markup=filtre_kb())

    elif data == "menu_istatistik":
        reset_periods()
        t = (f"📊 *İstatistikler*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📅 Bugün: {len(daily_users)}  📆 Bu Hafta: {len(weekly_users)}\n"
             f"🗓 Bu Ay: {len(monthly_users)}  🏆 Toplam: {len(all_time_users)}\n\n"
             f"🎁 Aktif Airdrop: {len(get_active_airdrops())}  |  Toplam: {len(airdrops)}\n"
             f"📰 Paylaşılan Haber: {len(posted_news)}")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    elif data == "menu_haberler":
        await q.message.edit_text("📰 Haberler alınıyor... ⏳")
        haberler = await fetch_crypto_news()
        if not haberler:
            await q.message.edit_text("❌ Haberler alınamadı.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]])); return
        h    = haberler[0]
        stil = haber_ayarlari.get("ozet_stili","standart")
        ai   = await openai_ozet(h["icerik"], h["baslik"], stil)
        await q.message.edit_text(
            haber_mesaj_formatla(h, ai, ai.get("son_dk", False)),
            parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    elif data == "menu_yardim":
        t = ("📖 *Komutlar*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             "/airdrops — Aktif airdroplar\n/topairdrops — En iyiler\n"
             "/airdrop `<id>` — Detay\n/haberler — Son haberler\n/istatistik — İstatistik\n")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    # ── Admin Ana ──
    elif data == "adm_ana":
        if not adm: await q.answer("⛔ Yetki yok!", show_alert=True); return
        oto  = "✅" if haber_ayarlari["aktif"] else "❌"
        sdk  = "✅" if haber_ayarlari["son_dk_aktif"] else "❌"
        stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
        cache_age = ""
        if _news_cache["fetched_at"]:
            age = int((datetime.utcnow() - _news_cache["fetched_at"]).total_seconds() / 60)
            cache_age = f"  |  Önbellek: {age} dk"
        t = (f"⚙️ *Admin Paneli*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📰 Oto Haber: {oto}  🚨 Son Dk: {sdk}\n"
             f"⏱ Sıklık: {haber_ayarlari['interval_saat']} saat  📝 Stil: {stil}\n"
             f"📊 Paylaşılan: {len(posted_news)}{cache_age}\n"
             f"🎁 Aktif Airdrop: {len(get_active_airdrops())}/{len(airdrops)}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_ana_kb())

    # ── Admin Airdrop ──
    elif data == "adm_airdrop":
        if not adm: await q.answer("⛔", show_alert=True); return
        t = (f"🎁 *Airdrop Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"✅ Aktif: {len(get_active_airdrops())}\n"
             f"❌ Biten: {len([a for a in airdrops if a['durum']=='bitti'])}\n"
             f"📋 Toplam: {len(airdrops)}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_airdrop_kb())

    elif data == "adm_air_ekle_info":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text(
            "➕ *Airdrop Ekle*\n\n`/airdropekle Başlık | Ödül | Başlangıç | Bitiş | Puan | Link`\n\n"
            "*Örnek:*\n`/airdropekle Layer3 | 50 USDT | 01.01.2025 | 31.03.2025 | 9 | https://layer3.xyz`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data in ("adm_air_tumu", "adm_air_aktif", "adm_air_bitti", "adm_air_puan", "adm_air_tarih"):
        if not adm: await q.answer("⛔", show_alert=True); return
        if data == "adm_air_tumu":
            liste = airdrops; bas = "📋 Tüm Manuel Airdroplar"
        elif data == "adm_air_aktif":
            liste = get_active_airdrops(); bas = "✅ Aktif Airdroplar"
        elif data == "adm_air_bitti":
            liste = [a for a in airdrops if a["durum"]=="bitti"]; bas = "❌ Biten Airdroplar"
        elif data == "adm_air_puan":
            liste = sorted(airdrops, key=lambda x: x.get("puan",0), reverse=True); bas = "🏆 Puana Göre"
        else:
            def tk(a):
                b = _bitis_gun(a); return b if b else datetime.max.date()
            liste = sorted(airdrops, key=tk); bas = "📅 Tarihe Göre"
        t = (f"*{bas}*\n━━━━━━━━━━━━━━━━━━━━━\n\n" +
             ("\n\n".join(airdrop_card(a) for a in liste) if liste else "Airdrop yok.") +
             (f"\n\n📌 {len(liste)} airdrop" if liste else ""))
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Airdrop Yönetimi", callback_data="adm_airdrop")]]))

    elif data == "adm_air_borsalar":
        if not adm: await q.answer("⛔", show_alert=True); return
        borsalar = [a for a in get_auto_airdrops_active() if a.get("kaynak_tur")=="borsa"]
        if not borsalar:
            await q.message.edit_text(
                "🏦 Borsa kampanyası bulunamadı.\n\nÖnbellek boş olabilir, 🔄 otomatik çek butonunu deneyin.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="adm_airdrop")]])); return
        t = f"🏦 *Borsa Kampanyaları ({len(borsalar)})*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
        t += "\n\n".join(auto_airdrop_card(a) for a in borsalar if auto_airdrop_card(a))
        await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="adm_airdrop")]]))

    elif data == "adm_air_otomatik_cek":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text("🔄 Airdroplar internet'ten çekiliyor...\n⏳ Bu işlem 15-30 saniye sürebilir.")
        try:
            kartlar = await fetch_auto_airdrops(force=True)
            borsalar = len([a for a in kartlar if a.get("kaynak_tur")=="borsa"])
            haberler = len([a for a in kartlar if a.get("kaynak_tur")=="haber"])
            takip    = len([a for a in kartlar if a.get("kaynak_tur")=="takip"])
            t = (f"✅ *Airdrop Çekme Tamamlandı!*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
                 f"📊 Toplam: *{len(kartlar)}* airdrop bulundu\n\n"
                 f"🏦 Borsa Kampanyaları: *{borsalar}*\n"
                 f"📰 Haber Sitelerinden: *{haberler}*\n"
                 f"🎯 Takip Sitelerinden: *{takip}*\n\n"
                 f"⏰ Önbellek: 3 saat geçerli")
            await q.message.edit_text(t, parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🏦 Borsa Kampanyaları", callback_data="adm_air_borsalar")],
                    [InlineKeyboardButton("🔙 Airdrop Yönetimi",  callback_data="adm_airdrop")],
                ]))
        except Exception as e:
            await q.message.edit_text(f"❌ Hata: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="adm_airdrop")]]))

    elif data == "adm_air_kaynak_durum":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text("📡 Airdrop kaynakları test ediliyor... ⏳")
        satirlar = []
        async with aiohttp.ClientSession() as sess:
            for k in AIRDROP_KAYNAKLARI[:10]:  # ilk 10 kaynak test et
                sonuc = await _airdrop_rss(sess, k, limit=1)
                durum = f"✅ {k['isim']} ({k.get('kaynak_tur','')})" if sonuc else f"❌ {k['isim']}"
                satirlar.append(durum)
        t = "📡 *Airdrop Kaynak Durumu*\n━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(satirlar)
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Geri", callback_data="adm_airdrop")]]))

    elif data == "adm_air_cache_temizle":
        if not adm: await q.answer("⛔", show_alert=True); return
        global auto_airdrops, _airdrop_cache
        n = len(auto_airdrops)
        auto_airdrops = []
        _airdrop_cache = {"data": [], "fetched_at": None}
        seen_airdrop_ids.clear()
        await q.answer(f"✅ {n} airdrop önbelleği temizlendi!")
        await q.message.edit_text(f"✅ Airdrop önbelleği temizlendi ({n} kayıt).",
            reply_markup=adm_airdrop_kb())

    elif data == "auto_airdrop_ara":
        await q.message.edit_text("🔄 Airdroplar internet'ten çekiliyor...\n⏳ 15-30 saniye...")
        try:
            kartlar = await fetch_auto_airdrops(force=False)
            borsalar = [a for a in kartlar if a.get("kaynak_tur")=="borsa"]
            diger    = [a for a in kartlar if a.get("kaynak_tur")!="borsa"]
            bolumler = []
            if borsalar:
                bolumler.append("🏦 *Borsa Kampanyaları*")
                bolumler += [auto_airdrop_card(a) for a in borsalar[:5] if auto_airdrop_card(a)]
            if diger:
                bolumler.append("\n🎯 *Diğer Airdroplar*")
                bolumler += [auto_airdrop_card(a) for a in diger[:5] if auto_airdrop_card(a)]
            if not bolumler:
                await q.message.edit_text(
                    "😕 Airdrop bulunamadı. Kaynaklar geçici olarak erişilemiyor olabilir.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))
                return
            t  = f"🎁 *Bulunan Airdrop & Kampanyalar ({len(kartlar)})*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
            t += "\n\n".join(s for s in bolumler if s)
            t += f"\n\n📌 {len(kartlar)} kampanya bulundu"
            await q.message.edit_text(t, parse_mode="Markdown", disable_web_page_preview=True,
                reply_markup=filtre_kb())
        except Exception as e:
            await q.message.edit_text(f"❌ Hata oluştu: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Menü", callback_data="menu_ana")]]))

    # ── Admin Haber ──
    elif data == "adm_haber":
        if not adm: await q.answer("⛔", show_alert=True); return
        oto  = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk  = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        aktif_k = len([k for k in HABER_KAYNAKLARI if k.get("aktif", True)])
        cache_info = ""
        if _news_cache["fetched_at"]:
            age = int((datetime.utcnow() - _news_cache["fetched_at"]).total_seconds() / 60)
            cache_info = f"\n🗄 Önbellek: {len(_news_cache['data'])} haber, {age} dk önce"
        t = (f"📰 *Haber Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Otomatik: {oto}  |  Son Dakika: {sdk}\n"
             f"Paylaşılan: {len(posted_news)}{cache_info}\n\n"
             f"📡 Aktif Kaynak: {aktif_k}/{len(HABER_KAYNAKLARI)}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_haber_onizle":
        if not adm: await q.answer("⛔", show_alert=True); return
        if GROUP_ID == 0:
            await q.message.edit_text("❌ GROUP_ID ayarlanmamış.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_ana")]])); return
        await q.message.edit_text("📰 Haberler alınıyor... ⏳")
        await _onizle_gonder(uid, q.message.edit_text, context, idx=0)

    elif data == "hab_yenile":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text("📰 Haberler yenileniyor... ⏳")
        await _onizle_gonder(uid, q.message.edit_text, context, idx=0, force=True)

    elif data == "hab_onayla":
        if not adm: await q.answer("⛔", show_alert=True); return
        bek = bekleyen_haberler.get(uid)
        if not bek: await q.answer("⚠️ Oturum sona erdi, tekrar dene.", show_alert=True); return
        try:
            await context.bot.send_message(
                GROUP_ID, bek["text"], parse_mode="Markdown", disable_web_page_preview=True)
            # Üç şekilde işaretle
            posted_news.add(bek["haber_id"])
            posted_news.add(bek["haber_url"])
            posted_news.add(bek.get("haber_hash",""))
            bekleyen_haberler.pop(uid, None)
            await q.message.edit_text(
                "✅ *Haber gruba gönderildi!*", parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📰 Bir Daha Paylaş", callback_data="adm_haber_onizle")],
                    [InlineKeyboardButton("🔙 Admin Paneli",    callback_data="adm_ana")],
                ]))
        except Exception as e:
            await q.message.edit_text(f"❌ Gönderilemedi: {e}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin", callback_data="adm_ana")]]))

    elif data == "hab_sonraki":
        if not adm: await q.answer("⛔", show_alert=True); return
        bek = bekleyen_haberler.get(uid)
        idx = (bek["index"] + 1) if bek else 0
        await q.message.edit_text("📰 Sonraki haber yükleniyor... ⏳")
        await _onizle_gonder(uid, q.message.edit_text, context, idx=idx)

    elif data == "adm_haber_toggle":
        if not adm: await q.answer("⛔", show_alert=True); return
        haber_ayarlari["aktif"] = not haber_ayarlari["aktif"]
        d = "✅ açıldı" if haber_ayarlari["aktif"] else "❌ kapatıldı"
        await q.answer(f"Otomatik haber {d}!")
        # Paneli yenile
        oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        aktif_k = len([k for k in HABER_KAYNAKLARI if k.get("aktif", True)])
        await q.message.edit_text(
            f"📰 *Haber Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Otomatik: {oto}  |  Son Dakika: {sdk}\n"
            f"Paylaşılan: {len(posted_news)}\n"
            f"📡 Aktif Kaynak: {aktif_k}/{len(HABER_KAYNAKLARI)}",
            parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_sondk_toggle":
        if not adm: await q.answer("⛔", show_alert=True); return
        haber_ayarlari["son_dk_aktif"] = not haber_ayarlari["son_dk_aktif"]
        d = "✅ açıldı" if haber_ayarlari["son_dk_aktif"] else "❌ kapatıldı"
        await q.answer(f"Son dakika {d}!")
        oto = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        aktif_k = len([k for k in HABER_KAYNAKLARI if k.get("aktif", True)])
        await q.message.edit_text(
            f"📰 *Haber Yönetimi*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Otomatik: {oto}  |  Son Dakika: {sdk}\n"
            f"Paylaşılan: {len(posted_news)}\n"
            f"📡 Aktif Kaynak: {aktif_k}/{len(HABER_KAYNAKLARI)}",
            parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_haber_durum":
        if not adm: await q.answer("⛔", show_alert=True); return
        oto  = "✅ Açık" if haber_ayarlari["aktif"] else "❌ Kapalı"
        sdk  = "✅ Açık" if haber_ayarlari["son_dk_aktif"] else "❌ Kapalı"
        stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
        cache_info = "Önbellek boş"
        if _news_cache["fetched_at"]:
            age = int((datetime.utcnow() - _news_cache["fetched_at"]).total_seconds() / 60)
            cache_info = f"{len(_news_cache['data'])} haber, {age} dk önce güncellendi"
        aktif_k = [k for k in HABER_KAYNAKLARI if k.get("aktif", True)]
        pasif_k = [k for k in HABER_KAYNAKLARI if not k.get("aktif", True)]
        t = (f"📊 *Haber Sistemi Durumu*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Otomatik: {oto}  |  Son Dk: {sdk}\n"
             f"Sıklık: {haber_ayarlari['interval_saat']} saat  |  Eşik: {haber_ayarlari['son_dk_esik']} dk\n"
             f"Stil: {stil}  |  Kanal: {haber_ayarlari['kanal_tag']}\n"
             f"Paylaşılan: {len(posted_news)}\n"
             f"🗄 Önbellek: {cache_info}\n\n"
             f"📡 *Aktif Kaynaklar ({len(aktif_k)}):*\n" +
             "".join(f"• {k['isim']}\n" for k in aktif_k) +
             (f"\n🚫 *Pasif Kaynaklar ({len(pasif_k)}):*\n" +
              "".join(f"• {k['isim']}\n" for k in pasif_k) if pasif_k else ""))
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_kb())

    elif data == "adm_haber_temizle":
        if not adm: await q.answer("⛔", show_alert=True); return
        n = len(posted_news); posted_news.clear()
        await q.answer(f"✅ {n} kayıt temizlendi!")
        await q.message.edit_text(f"✅ Haber geçmişi temizlendi ({n} kayıt silindi).", reply_markup=adm_haber_kb())

    elif data == "adm_haber_yenile":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text("🔄 Önbellek yenileniyor... ⏳")
        haberler = await fetch_crypto_news(force=True)
        yeni_say = len(_yeni_haberler(haberler))
        await q.message.edit_text(
            f"✅ Önbellek yenilendi!\n\n"
            f"📰 Toplam: {len(haberler)} haber\n"
            f"🆕 Yeni: {yeni_say} haber\n"
            f"📡 Kaynak: {len([k for k in HABER_KAYNAKLARI if k.get('aktif',True)])} aktif",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👁 Önizle",      callback_data="adm_haber_onizle")],
                [InlineKeyboardButton("🔙 Admin",       callback_data="adm_haber")],
            ]))

    elif data == "adm_kaynak_durum":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text("📡 Kaynaklar test ediliyor... ⏳")
        # Her kaynaktan 1 haber çekerek test et
        sonuclar = []
        async with aiohttp.ClientSession() as sess:
            for k in HABER_KAYNAKLARI:
                if not k.get("aktif", True):
                    sonuclar.append(f"⚫ {k['isim']} (pasif)")
                    continue
                test = await _fetch_direct(sess, k, 1)
                if test:
                    age = int((datetime.utcnow() - test[0]["zaman"]).total_seconds() / 3600)
                    sonuclar.append(f"✅ {k['isim']} ({age}sa önce)")
                else:
                    test2 = await _fetch_proxy(sess, k, 1)
                    if test2:
                        sonuclar.append(f"🟡 {k['isim']} (proxy ile)")
                    else:
                        sonuclar.append(f"❌ {k['isim']} (ulaşılamıyor)")
        t = "📡 *Kaynak Durumu*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n" + "\n".join(sonuclar)
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Haber Yönetimi", callback_data="adm_haber")]]))

    # ── Haber Ayarları ──
    elif data == "adm_haber_ayar":
        if not adm: await q.answer("⛔", show_alert=True); return
        stil = OZET_STILLERI.get(haber_ayarlari.get("ozet_stili","standart"),{}).get("isim","")
        t = (f"⚙️ *Haber Ayarları*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"Özet Stili: {stil}\n"
             f"Sıklık: Her {haber_ayarlari['interval_saat']} saatte bir\n"
             f"Maks Haber/Run: {haber_ayarlari.get('max_per_run',1)}\n\n"
             "Sıklık ve limit butonlarına basınca anında değişir:")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())

    elif data == "adm_stil_menu":
        if not adm: await q.answer("⛔", show_alert=True); return
        t = ("📝 *Özet Stili Seç*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             "📝 *Standart* — 3-4 cümle, bilgilendirici\n"
             "📄 *Detaylı* — 5-7 cümle, arka plan ve etki\n"
             "⚡ *Kısaca* — 2 cümle, sadece özet\n"
             "📌 *Madde Madde* — bullet point formatı\n\n"
             f"Şu an: {OZET_STILLERI.get(haber_ayarlari.get('ozet_stili','standart'),{}).get('isim','')}")
        await q.message.edit_text(t, parse_mode="Markdown", reply_markup=adm_stil_kb())

    elif data.startswith("adm_stil_"):
        if not adm: await q.answer("⛔", show_alert=True); return
        k = data.replace("adm_stil_","")
        if k in OZET_STILLERI:
            haber_ayarlari["ozet_stili"] = k
            await q.answer(f"✅ Stil: {OZET_STILLERI[k]['isim']}")
            await q.message.edit_text(
                f"✅ Özet stili *{OZET_STILLERI[k]['isim']}* olarak ayarlandı.",
                parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())

    elif data.startswith("adm_sure_"):
        if not adm: await q.answer("⛔", show_alert=True); return
        try:
            s = int(data.replace("adm_sure_",""))
            haber_ayarlari["interval_saat"] = s
            await q.answer(f"✅ Sıklık: {s} saat")
            await q.message.edit_text(
                f"✅ Sıklık *{s} saat* olarak ayarlandı.\n⚠️ Bir sonraki döngüde geçerli olur.",
                parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())
        except: await q.answer("❌ Hata!")

    elif data.startswith("adm_max_"):
        if not adm: await q.answer("⛔", show_alert=True); return
        try:
            m = int(data.replace("adm_max_",""))
            haber_ayarlari["max_per_run"] = m
            await q.answer(f"✅ Maks: {m} haber/run")
            await q.message.edit_text(
                f"✅ Otomatik çalışmada maks *{m} haber* paylaşılacak.",
                parse_mode="Markdown", reply_markup=adm_haber_ayar_kb())
        except: await q.answer("❌ Hata!")

    # ── Admin Diğer ──
    elif data == "adm_istat":
        if not adm: await q.answer("⛔", show_alert=True); return
        reset_periods()
        t = (f"📊 *Detaylı İstatistik*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📅 Bugün: {len(daily_users)}\n📆 Bu Hafta: {len(weekly_users)}\n"
             f"🗓 Bu Ay: {len(monthly_users)}\n🏆 Tüm Zamanlar: {len(all_time_users)}\n\n"
             f"🎁 Aktif Airdrop: {len(get_active_airdrops())}\n"
             f"❌ Biten: {len([a for a in airdrops if a['durum']=='bitti'])}\n"
             f"📋 Toplam Airdrop: {len(airdrops)}\n\n"
             f"📰 Paylaşılan Haber: {len(posted_news)}")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")]]))

    elif data == "adm_uye_rapor":
        if not adm: await q.answer("⛔", show_alert=True); return
        reset_periods(); td = datetime.now().date(); gd = {}
        for e in join_log:
            if (td - e["date"]).days < 7:
                l = e["date"].strftime("%d.%m"); gd[l] = gd.get(l,0) + 1
        ds = "".join(f"  {g}: {'█'*min(s,15)} {s}\n" for g, s in sorted(gd.items()))
        t = (f"👥 *Üye Raporu*\n━━━━━━━━━━━━━━━━━━━━━━━\n\n"
             f"📅 Bugün: {len(daily_users)}  📆 Bu Hafta: {len(weekly_users)}\n"
             f"🗓 Bu Ay: {len(monthly_users)}  🏆 Toplam: {len(all_time_users)}\n\n"
             f"📈 *Son 7 Gün:*\n{ds if ds else '  Veri yok.'}")
        await q.message.edit_text(t, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")]]))

    elif data == "adm_duyuru_info":
        if not adm: await q.answer("⛔", show_alert=True); return
        await q.message.edit_text(
            "📣 *Duyuru Gönder*\n\nDM'den:\n`/duyuru <metin>`\n\n*Örnek:*\n"
            "`/duyuru 🎉 Yeni airdrop fırsatı! Detay için /airdrops yazın.`",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Paneli", callback_data="adm_ana")]]))

# ══════════════════════════════════════════
#  OTOMATİK HABER JOBS
# ══════════════════════════════════════════

async def auto_airdrop_job(context: ContextTypes.DEFAULT_TYPE):
    """
    Periyodik otomatik airdrop çekimi (3 saatte bir).
    Yeni borsa kampanyaları bulunursa gruba bildirir.
    """
    if GROUP_ID == 0:
        return
    try:
        # Önbellek doluysa tekrar çekme
        if (_airdrop_cache.get("fetched_at") and
                datetime.utcnow() - _airdrop_cache["fetched_at"] < _AIRDROP_CACHE_TTL):
            log.info("auto_airdrop_job: önbellek taze, atlandı")
            return

        kartlar  = await fetch_auto_airdrops(force=True)
        yeniler  = [a for a in kartlar if a["hash"] not in seen_airdrop_ids]

        if not yeniler:
            log.info("auto_airdrop_job: yeni airdrop yok")
            return

        # Sadece borsa kampanyalarını ve puan ≥ 6 olanları bildir
        bildir = sorted(
            [a for a in yeniler if a.get("puan",0) >= 6],
            key=lambda x: (x["oncelik"], -x["puan"])
        )[:3]  # en fazla 3 bildirim

        for a in bildir:
            seen_airdrop_ids.add(a["hash"])
            kart_text = auto_airdrop_card(a)
            if not kart_text:
                continue
            tur = a.get("kaynak_tur","")
            baslik_ikon = "🏦" if tur=="borsa" else "🎁"
            text = (
                f"{baslik_ikon} *YENİ KAMPANYA BULUNDu!*\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{kart_text}\n\n"
                f"🤖 Kaynak: {a.get('kaynak','')}"
            )
            try:
                await context.bot.send_message(
                    GROUP_ID, text,
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
                log.info(f"Yeni airdrop bildirildi: {a['baslik'][:50]}")
                await asyncio.sleep(2)
            except Exception as e:
                log.warning(f"Airdrop bildirim hatası: {e}")

        # Geri kalan yenileri de seen'e ekle (sadece bildirilmeyenler için spam engeli)
        for a in yeniler:
            seen_airdrop_ids.add(a["hash"])

    except Exception as e:
        log.error(f"auto_airdrop_job hatası: {e}")


async def auto_haber_job(context: ContextTypes.DEFAULT_TYPE):
    """Periyodik otomatik haber paylaşımı."""
    if GROUP_ID == 0: return
    if not haber_ayarlari.get("aktif", True): return

    haberler = await fetch_crypto_news()
    yeni     = _yeni_haberler(haberler)
    if not yeni:
        log.info("auto_haber_job: yeni haber yok"); return

    max_run = haber_ayarlari.get("max_per_run", 1)
    stil    = haber_ayarlari.get("ozet_stili", "standart")
    paylasildi = 0

    for h in yeni[:max_run]:
        ai = await openai_ozet(h["icerik"], h["baslik"], stil)
        text = haber_mesaj_formatla(h, ai, son_dk=False)
        try:
            await context.bot.send_message(GROUP_ID, text, parse_mode="Markdown", disable_web_page_preview=True)
            _haber_posted(h)
            paylasildi += 1
            log.info(f"Oto haber: {h['baslik'][:60]}")
            if paylasildi < max_run:
                await asyncio.sleep(3)
        except Exception as e:
            log.warning(f"Oto haber hatası: {e}")

async def son_dk_haber_job(context: ContextTypes.DEFAULT_TYPE):
    """Son dakika haber tespiti ve paylaşımı."""
    if GROUP_ID == 0: return
    if not haber_ayarlari.get("son_dk_aktif", True): return

    esik    = haber_ayarlari.get("son_dk_esik", 30)
    su_an   = datetime.utcnow()
    haberler = await fetch_crypto_news()

    for h in haberler:
        # Daha önce paylaşıldıysa atla
        if h["id"] in posted_news or h["url"] in posted_news or h["hash"] in posted_news:
            continue
        # Yaş kontrolü
        try:
            yas_dk = (su_an - h["zaman"]).total_seconds() / 60
        except:
            continue
        if yas_dk > esik:
            continue

        # Önce hızlı anahtar kelime kontrolü
        hizli_sdk = _son_dk_hizli(h["baslik"], h["icerik"])

        if hizli_sdk:
            # Anahtar kelime eşleşti → direkt AI ile özetle
            ai = await openai_ozet(h["icerik"], h["baslik"], "kisaca")
            ai["son_dk"] = True
        else:
            # AI ile kontrol et
            ai = await openai_ozet(h["icerik"], h["baslik"], "kisaca")
            if not ai.get("son_dk", False):
                # Son dakika değil, sadece paylaşıldı olarak işaretle (normal akışa bırak)
                continue

        text = haber_mesaj_formatla(h, ai, son_dk=True)
        try:
            await context.bot.send_message(GROUP_ID, text, parse_mode="Markdown", disable_web_page_preview=True)
            _haber_posted(h)
            log.info(f"SON DAKİKA: {h['baslik'][:60]}")
            await asyncio.sleep(2)
        except Exception as e:
            log.warning(f"Son dk hatası: {e}")

# ══════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════

async def post_init(app):
    await app.bot.set_my_commands([
        BotCommand("start",       "Ana menü"),
        BotCommand("airdrops",    "Aktif airdroplar & kampanyalar"),
        BotCommand("airdropara",  "İnternetten airdrop ara"),
        BotCommand("topairdrops", "En iyi airdroplar"),
        BotCommand("haberler",    "Son Türkçe kripto haberleri"),
        BotCommand("istatistik",  "İstatistikler"),
        BotCommand("yardim",      "Yardım"),
    ])
    log.info("KriptoDropTR Bot v5 hazır 🚀")

def main():
    app = ApplicationBuilder().token(TOKEN).post_init(post_init).build()
    hi  = haber_ayarlari.get("interval_saat", 6) * 3600
    app.job_queue.run_repeating(auto_airdrop_job, interval=10800, first=120)  # her 3 saatte bir
    app.job_queue.run_repeating(auto_haber_job,   interval=hi,   first=300)
    app.job_queue.run_repeating(son_dk_haber_job, interval=300,  first=60)
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, welcome))
    app.add_handler(CommandHandler("start",           cmd_start))
    app.add_handler(CommandHandler("yardim",          cmd_yardim))
    app.add_handler(CommandHandler("airdrops",        cmd_airdrops))
    app.add_handler(CommandHandler("airdropara",      cmd_airdrop_ara))
    app.add_handler(CommandHandler("topairdrops",     cmd_top_airdrops))
    app.add_handler(CommandHandler("airdrop",         cmd_airdrop_detay))
    app.add_handler(CommandHandler("haberler",        cmd_haberler))
    app.add_handler(CommandHandler("istatistik",      cmd_istatistik))
    app.add_handler(CommandHandler("airdropekle",     cmd_airdrop_ekle))
    app.add_handler(CommandHandler("airdropduzenle",  cmd_airdrop_duzenle))
    app.add_handler(CommandHandler("airdropbitir",    cmd_airdrop_bitir))
    app.add_handler(CommandHandler("airdropsil",      cmd_airdrop_sil))
    app.add_handler(CommandHandler("haberler_paylas", cmd_haber_paylas))
    app.add_handler(CommandHandler("haberayar",       cmd_haber_ayar))
    app.add_handler(CommandHandler("duyuru",          cmd_duyuru))
    app.add_handler(CallbackQueryHandler(button_handler))
    log.info("BOT AKTIF")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
