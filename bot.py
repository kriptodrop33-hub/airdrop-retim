import logging, sys, os, json, re, random, time, asyncio, httpx
from datetime import datetime

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s",
                    level=logging.INFO, stream=sys.stdout)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY   = os.getenv("GROQ_API_KEY", "")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
ADMIN_CHAT_ID  = int(os.getenv("ADMIN_CHAT_ID", "0"))
GROUP_CHAT_ID  = int(os.getenv("GROUP_CHAT_ID", "0"))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (Application, CommandHandler, CallbackQueryHandler,
                           MessageHandler, filters, ContextTypes)

ref_code_store = {"code": None}
stats_store    = {"sent": 0, "last": None}
pending_posts  = {}   # önizleme bekleyenler
sent_posts     = {}   # gruba gönderilmiş postlar {key: {msg_id, text, image_url, name, airdrop}}
shown_names    = set()


# ════════════════════════════════════════════════════════════════════════════
# GROQ
# ════════════════════════════════════════════════════════════════════════════
async def llm(prompt: str, tokens: int = 1500) -> str:
    async with httpx.AsyncClient(timeout=90) as c:
        r = await c.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}",
                     "Content-Type": "application/json"},
            json={"model": "llama-3.3-70b-versatile",
                  "messages": [{"role": "user", "content": prompt}],
                  "max_tokens": tokens, "temperature": 0.6}
        )
        d = r.json()
        if "error" in d:
            raise Exception(d["error"]["message"])
        return d["choices"][0]["message"]["content"].strip()


# ════════════════════════════════════════════════════════════════════════════
# TAVILY
# ════════════════════════════════════════════════════════════════════════════
async def web_search(query: str, n: int = 6) -> list[dict]:
    if not TAVILY_API_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.post(
                "https://api.tavily.com/search",
                json={"api_key": TAVILY_API_KEY, "query": query,
                      "search_depth": "advanced", "max_results": n,
                      "include_answer": False,
                      "include_raw_content": False}
            )
            return r.json().get("results", [])
    except Exception as e:
        logger.warning(f"Tavily: {e}")
        return []


# ════════════════════════════════════════════════════════════════════════════
# LİNK KONTROL — çalışıyor mu?
# ════════════════════════════════════════════════════════════════════════════
async def check_url(url: str) -> tuple[bool, int]:
    """(çalışıyor_mu, status_code)"""
    if not url or not url.startswith("http"):
        return False, 0
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.head(url)
            ok = r.status_code < 400
            return ok, r.status_code
    except Exception:
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0"}) as c:
                r = await c.get(url)
                ok = r.status_code < 400
                return ok, r.status_code
        except Exception:
            return False, 0


# ════════════════════════════════════════════════════════════════════════════
# OG IMAGE
# ════════════════════════════════════════════════════════════════════════════
async def og_image(url: str) -> str | None:
    if not url or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 TelegramBot"}) as c:
            r = await c.get(url)
            for pat in [
                r'property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']',
                r'content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'name=["\']twitter:image["\'][^>]+content=["\'](https?://[^"\']+)["\']',
            ]:
                m = re.search(pat, r.text, re.I)
                if m:
                    return m.group(1)
    except Exception as e:
        logger.warning(f"OG image: {e}")
    return None


# ════════════════════════════════════════════════════════════════════════════
# PLATFORM TARAMA — çok geniş kaynak listesi
# ════════════════════════════════════════════════════════════════════════════

SEARCH_QUERIES = [
    # Büyük borsalar — kampanya değil coin odaklı
    "Binance Launchpool new token airdrop staking 2025 live",
    "OKX Jumpstart new coin campaign airdrop claim 2025",
    "Bybit Launchpad new token distribution event 2025",
    "KuCoin Spotlight new token airdrop event 2025",
    "Gate.io Startup new token airdrop live 2025",
    "MEXC new token airdrop kickstarter 2025",
    "Bitget new listing airdrop campaign 2025",
    "HTX Huobi new token airdrop distribution 2025",

    # Görev platformları
    "Galxe active campaign airdrop quest reward 2025",
    "Zealy sprint airdrop new crypto project 2025",
    "Layer3 quest airdrop active reward 2025",
    "Intract quest campaign airdrop 2025",
    "Crew3 airdrop task campaign 2025",

    # Zincir / ekosistem airdropları
    "new Layer2 testnet airdrop eligible 2025 active",
    "Arbitrum ecosystem airdrop new project 2025",
    "Base ecosystem new token airdrop 2025",
    "Solana new project airdrop campaign 2025",
    "zkSync era new airdrop project 2025",
    "Starknet ecosystem airdrop 2025 new",
    "Sui Move ecosystem airdrop 2025",
    "Aptos ecosystem new airdrop 2025",

    # DeFi / protokol
    "new DeFi protocol airdrop early user reward 2025",
    "restaking liquid staking new token airdrop 2025",
    "DEX new token airdrop referral bonus 2025",
    "bridge cross-chain new airdrop 2025 live",

    # Takip siteleri
    "airdrops.io new confirmed airdrop 2025",
    "airdropalert.com active airdrop 2025",
    "earnifi airdrop eligible wallet 2025",
    "coinmarketcap airdrop new live 2025",
    "coingecko airdrop event active 2025",

    # Genel güncel
    "crypto airdrop claim now referral system 2025 new",
    "best crypto airdrop this week 2025 active",
    "crypto airdrop free token today 2025",
]


async def run_platform_scans() -> list[dict]:
    """Rastgele seçilmiş sorgularla web tara"""
    # Her taramada 8 farklı sorgu seç
    selected = random.sample(SEARCH_QUERIES, min(8, len(SEARCH_QUERIES)))
    tasks    = [web_search(q, n=4) for q in selected]
    results  = await asyncio.gather(*tasks, return_exceptions=True)

    raw = []
    for res in results:
        if isinstance(res, Exception):
            continue
        for r in res:
            raw.append({
                "title":   r.get("title", ""),
                "url":     r.get("url", ""),
                "snippet": r.get("content", "")[:500],
            })
    return raw


async def extract_airdrops(raw: list[dict], exclude: set) -> list[dict]:
    if not raw:
        return []

    # Context oluştur
    ctx_text = ""
    seen_urls = set()
    for item in raw:
        u = item["url"]
        if u in seen_urls:
            continue
        seen_urls.add(u)
        ctx_text += f"\nKaynak URL: {u}\nBaşlık: {item['title']}\nİçerik: {item['snippet']}\n{'─'*40}"

    excl = ", ".join(list(exclude)[-20:]) if exclude else "yok"

    prompt = f"""Sen kripto airdrop araştırmacısısın. Aşağıdaki web arama sonuçlarını analiz et.

WEB SONUÇLARI:
{ctx_text[:6000]}

BUNLARI EKLEME (zaten gösterildi): {excl}

GÖREV:
Bu sonuçlardan GERÇEK ve AKTİF kripto airdroplarını çıkar.

ÖNEMLİ KURALLAR:
1. COIN/TOKEN adını yaz, platform adını değil
   ❌ YANLIŞ: "OKX Jumpstart Airdrobu"
   ✅ DOĞRU: "XYZ Token Airdrobu (OKX Jumpstart'ta)"
   ❌ YANLIŞ: "Binance Launchpool Airdrobu"  
   ✅ DOĞRU: "ABC Coin Airdrobu (Binance Launchpool)"

2. Kampanya hâlâ devam ediyor mu? Tarih geçmişse EKLEME
3. URL'ler gerçek ve erişilebilir olmalı
4. Bilgi yoksa "Belirtilmemiş" yaz, UYDURMA
5. Dağıtılan coin/token miktarını yaz (varsa)
6. Referans/davet sistemi olanları işaretle

SADECE JSON döndür (başka hiçbir şey yazma):
[
  {{
    "coin_name": "Dağıtılan Coin/Token Adı",
    "coin_symbol": "SEMBOL",
    "host_platform": "Nerede yapılıyor (Binance/Galxe/Resmi Site/vs)",
    "url": "https://kaynak-url.com",
    "campaign_url": "https://direkt-katilim-linki.com",
    "description": "Bu coin nedir ve neden airdrop yapıyor (Türkçe, 2 cümle)",
    "how_to_join": "Katılım adımları (Türkçe, kısa)",
    "reward": "Tam olarak ne kadar coin/token dağıtılıyor",
    "referral": true,
    "referral_bonus": "Referans bonusu detayı",
    "start_date": "GG.AA.YYYY veya Belirtilmemiş",
    "end_date": "GG.AA.YYYY veya Sürekli veya Belirtilmemiş",
    "status": "Aktif",
    "score": 8,
    "score_reason": "Neden güvenilir/değerli"
  }}
]"""

    raw_resp = await llm(prompt, tokens=3000)
    raw_resp = raw_resp.strip()

    if "```" in raw_resp:
        for part in raw_resp.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("["):
                raw_resp = part
                break

    if not raw_resp.startswith("["):
        s, e = raw_resp.find("["), raw_resp.rfind("]") + 1
        if s != -1:
            raw_resp = raw_resp[s:e]

    try:
        items = json.loads(raw_resp)
    except Exception:
        return []

    # coin_name yoksa name'den doldur
    for item in items:
        if not item.get("coin_name"):
            item["coin_name"] = item.get("name", "Bilinmeyen")
        if not item.get("name"):
            item["name"] = item["coin_name"]

    return items


async def verify_links(airdrops: list[dict]) -> list[dict]:
    """Her airdrop için link kontrolü yap"""
    verified = []
    for a in airdrops:
        url = a.get("campaign_url") or a.get("url", "")
        if url and url.startswith("http"):
            ok, code = await check_url(url)
            a["link_ok"]   = ok
            a["link_code"] = code
        else:
            a["link_ok"]   = None
            a["link_code"] = 0
        verified.append(a)
    return verified


# ════════════════════════════════════════════════════════════════════════════
# POST OLUŞTURMA
# ════════════════════════════════════════════════════════════════════════════
async def make_post(airdrop: dict, ref: str = None, link: str = None) -> str:
    coin    = airdrop.get("coin_name") or airdrop.get("name", "")
    symbol  = airdrop.get("coin_symbol", "")
    host    = airdrop.get("host_platform", "")
    sym_txt = f" ({symbol})" if symbol else ""

    prompt = f"""Kripto airdrop için profesyonel, bilgilendirici Telegram duyurusu yaz.

Coin/Token: {coin}{sym_txt}
Platform: {host}
Açıklama: {airdrop.get('description','')}
Nasıl Katılınır: {airdrop.get('how_to_join','')}
Ödül: {airdrop.get('reward','?')}
Başlangıç: {airdrop.get('start_date','?')}
Bitiş: {airedrop.get('end_date','?') if False else airdrop.get('end_date','?')}
Referans: {'VAR — ' + str(airdrop.get('referral_bonus','')) if airdrop.get('referral') else 'Yok'}
Puan: {airdrop.get('score','?')}/10

FORMAT — HTML kullan, Türkçe yaz:

🚀 <b>[COIN ADI] AİRDROP!</b>
<i>Kısa çarpıcı giriş cümlesi</i>

━━━━━━━━━━━━━━━━━━━━━━━━━

📋 <b>NASIL KATILIRSIN?</b>
1. Adım
2. Adım
3. Adım

━━━━━━━━━━━━━━━━━━━━━━━━━

💰 <b>ÖDÜL</b>
Ödül detayları

📅 <b>TARİH</b>
Başlangıç → Bitiş

⭐ <b>DEĞERLENDIRME</b>
Puan ve kısa yorum

#hashtag1 #hashtag2 #hashtag3

NOT: Post içine kesinlikle URL veya http adresi yazma."""

    text = await llm(prompt, tokens=900)
    text = re.sub(r'https?://\S+', '', text).strip()

    if ref:
        text += f"\n\n🎯 <b>Referans Kodun:</b> <code>{ref}</code>"

    final_link = link or airdrop.get("campaign_url") or airdrop.get("url", "")
    if final_link and final_link.startswith("http"):
        text += f"\n\n🔗 <b><a href='{final_link}'>👉 HEMEN KATIL</a></b>"
    else:
        text += "\n\n🔗 <b>[ LİNK BURAYA ]</b>"

    text += "\n\n📢 <b>@kriptodropptr</b>"
    return text


# ════════════════════════════════════════════════════════════════════════════
# YARDIMCI
# ════════════════════════════════════════════════════════════════════════════
def is_admin(u) -> bool:
    return u.effective_chat.id == ADMIN_CHAT_ID

async def send_to(upd, ctx, text, **kw):
    if hasattr(upd, "message") and upd.message:
        return await upd.message.reply_text(text, **kw)
    return await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, **kw)

async def show_preview(upd, ctx, airdrop: dict, custom_link: str = None):
    ref  = ref_code_store.get("code")
    link = custom_link or airdrop.get("campaign_url") or airdrop.get("url", "")
    coin = airdrop.get("coin_name") or airdrop.get("name", "")

    s = await send_to(upd, ctx,
        f"⚙️ <b>{coin}</b> hazırlanıyor...\n✍️ Post yazılıyor...", parse_mode="HTML")

    post_text = await make_post(airdrop, ref, link)

    og = None
    if link and link.startswith("http"):
        await s.edit_text(
            f"⚙️ <b>{coin}</b>\n✅ Post yazıldı\n🖼️ Sayfa görseli çekiliyor...",
            parse_mode="HTML")
        og = await og_image(link)

    await s.edit_text(f"✅ <b>{coin}</b> hazır!", parse_mode="HTML")
    await send_to(upd, ctx, "─── 👁️ ÖNİZLEME ───")

    try:
        if og:
            if hasattr(upd, "message") and upd.message:
                pm = await upd.message.reply_photo(photo=og, caption=post_text, parse_mode="HTML")
            else:
                pm = await ctx.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=og, caption=post_text, parse_mode="HTML")
        else:
            pm = await send_to(upd, ctx, post_text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception:
        pm = await send_to(upd, ctx, post_text, parse_mode="HTML", disable_web_page_preview=False)

    key = str(pm.message_id)
    pending_posts[key] = {"text": post_text, "image_url": og,
                          "name": coin, "airdrop": airdrop, "ref": ref}

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Gruba Gönder",   callback_data=f"send|{key}"),
         InlineKeyboardButton("✏️ Yeniden Yaz",    callback_data=f"rewrite|{key}")],
        [InlineKeyboardButton("🔗 Linki Değiştir", callback_data=f"editlink|{key}"),
         InlineKeyboardButton("❌ İptal",            callback_data=f"cancel|{key}")]
    ])
    await send_to(upd, ctx,
        f"⬆️ <b>{coin}</b> önizlemesi\n\n"
        "🔗 <i>Kendi linkini eklemek için → Linki Değiştir</i>",
        parse_mode="HTML", reply_markup=kb)


# ════════════════════════════════════════════════════════════════════════════
# /start
# ════════════════════════════════════════════════════════════════════════════
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    adm = uid == ADMIN_CHAT_ID
    msg = "🪂 <b>KriptoDropptr Bot</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += "✅ <b>Hoş geldin!</b>\n\n" if adm else "⛔ Yetkisiz.\n\n"
    if adm:
        tw = "✅ Aktif" if TAVILY_API_KEY else "❌ Key yok"
        msg += (
            f"📊 <b>Durum</b>\n"
            f"├ Ref: <code>{ref_code_store.get('code') or 'Yok'}</code>\n"
            f"├ Gönderim: <code>{stats_store['sent']}</code>\n"
            f"├ Web Arama: {tw}\n"
            f"└ Son: <code>{stats_store['last'] or '—'}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🤖 <b>AIRDROP MOTORU</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/scan — 30+ platformu tara, güncel airdropları getir\n"
            "/analyze &lt;url&gt; — URL araştır ve puanla\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📢 <b>MANUEL</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/newairdrop &lt;coin&gt; &lt;url&gt;\n"
            "/quickdrop &lt;url&gt;\n"
            "/scheduledrop &lt;coin&gt; &lt;url&gt; &lt;dakika&gt;\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🔗 <b>REFERANS</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/setref &lt;kod&gt; · /clearref · /showref\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📣 <b>ARAÇLAR</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/broadcast · /boldcast · /pin\n"
            "/translate · /hashtag\n"
            "/stats · /status\n\n""━━━━━━━━━━━━━━━━━━━━━\n🔬 <b>ARAŞTIR &amp; DÜZENLE</b>\n━━━━━━━━━━━━━━━━━━━━━\n""/research &lt;link veya başlık&gt; — Araştır, post hazırla\n""/posts — Gönderilen postları listele ve düzenle"
        )
    await update.message.reply_text(msg, parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════════
# /scan
# ════════════════════════════════════════════════════════════════════════════
async def scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return

    sm = await update.message.reply_text(
        "🔍 <b>30+ platform taranıyor...</b>\n\n"
        "🏦 Binance · OKX · Bybit · KuCoin · Gate\n"
        "🎯 Galxe · Zealy · Layer3 · Intract\n"
        "⛓️ Arbitrum · Base · Solana · zkSync · Sui\n"
        "📰 Airdrops.io · CoinMarketCap · CoinGecko\n\n"
        "⏳ <i>10-25 saniye bekle...</i>",
        parse_mode="HTML"
    )

    try:
        raw      = await run_platform_scans()
        await sm.edit_text("🤖 <b>AI analiz ediyor, linkler kontrol ediliyor...</b>",
                           parse_mode="HTML")

        airdrops = await extract_airdrops(raw, shown_names)

        if not airdrops:
            await sm.edit_text(
                "❌ Sonuç çıkarılamadı.\n\nTavily API key ekli mi? /status ile kontrol et.")
            return

        # Link kontrolü (paralel)
        airdrops = await verify_links(airdrops)

        # Daha önce gösterilenleri filtrele
        fresh = [a for a in airdrops
                 if (a.get("coin_name") or a.get("name","")) not in shown_names]
        if not fresh:
            shown_names.clear()
            fresh = airdrops
        for a in fresh:
            shown_names.add(a.get("coin_name") or a.get("name", ""))

        ctx.user_data["scan_results"] = fresh
        await sm.delete()

        for i, a in enumerate(fresh):
            coin    = a.get("coin_name") or a.get("name", "")
            symbol  = a.get("coin_symbol", "")
            host    = a.get("host_platform", "")
            score   = a.get("score", "?")
            stars   = "⭐" * min(int(score), 5) if isinstance(score, int) else "⭐"
            ref_txt = "🔁 <b>Referans Sistemi VAR</b>" if a.get("referral") else "➖ Referans yok"
            link_ok = a.get("link_ok")
            link_badge = (
                "✅ Link aktif" if link_ok is True
                else "❌ Link çalışmıyor" if link_ok is False
                else "⚠️ Link kontrol edilemedi"
            )
            s_date = a.get("start_date", "Belirtilmemiş")
            e_date = a.get("end_date", "Belirtilmemiş")
            src    = a.get("campaign_url") or a.get("url", "")

            sym_part = f" <code>{symbol}</code>" if symbol else ""
            host_part = f" — <i>{host}</i>" if host else ""

            card = (
                f"{'━'*30}\n"
                f"🪂 <b>{coin}</b>{sym_part}{host_part}\n"
                f"{'━'*30}\n\n"
                f"📝 {a.get('description','')}\n\n"
                f"📋 <b>Katılım:</b> {a.get('how_to_join','')}\n\n"
                f"💰 <b>Ödül:</b> {a.get('reward','?')}\n"
                f"{ref_txt}\n"
            )
            if a.get("referral_bonus"):
                card += f"🎁 <b>Bonus:</b> {a['referral_bonus']}\n"
            card += (
                f"\n📅 <b>Başlangıç:</b> {s_date}\n"
                f"📅 <b>Bitiş:</b> {e_date}\n\n"
                f"{stars} <b>Puan: {score}/10</b> — <i>{a.get('score_reason','')}</i>\n\n"
                f"{link_badge}"
            )
            if src:
                card += f"\n🌐 <a href='{src}'>Kaynak Sayfa</a>"

            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "📝 Post Hazırla", callback_data=f"prepare|{i}")
            ]])
            await update.message.reply_text(
                card, parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=kb)

        await update.message.reply_text(
            f"✅ <b>{len(fresh)} airdrop bulundu.</b>\n\n"
            "👆 Post hazırlamak istediğinin altındaki butona bas.\n"
            "<i>Kendi linkini post sonrası ekleyebilirsin.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Farklı Airdroplar Getir", callback_data="rescan")
            ]])
        )

    except Exception as e:
        logger.exception(e)
        try:
            await sm.edit_text(f"❌ Hata: {e}")
        except Exception:
            await update.message.reply_text(f"❌ Hata: {e}")


# ════════════════════════════════════════════════════════════════════════════
# DİĞER KOMUTLAR
# ════════════════════════════════════════════════════════════════════════════
async def post_airdrop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if len(ctx.args) < 2:
        await update.message.reply_text("Kullanım: /newairdrop <coin_adı> <url>"); return
    a = {"coin_name": ctx.args[0], "name": ctx.args[0], "url": ctx.args[1],
         "campaign_url": ctx.args[1], "description": "", "reward": "?",
         "how_to_join": "", "referral": False, "host_platform": "Manuel",
         "start_date": "?", "end_date": "?", "score": None}
    await show_preview(update, ctx, a, custom_link=ctx.args[1])

async def quick_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /quickdrop <url>"); return
    url  = ctx.args[0]
    name = url.split("/")[2].replace("www.", "").split(".")[0].capitalize()
    a = {"coin_name": name, "name": name, "url": url, "campaign_url": url,
         "description": "", "reward": "?", "how_to_join": "", "referral": False,
         "host_platform": "Manuel", "start_date": "?", "end_date": "?", "score": None}
    await show_preview(update, ctx, a, custom_link=url)

async def analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analyze <url>"); return
    url = ctx.args[0]
    await update.message.reply_text("🔍 Araştırılıyor...")
    ok, code = await check_url(url)
    results  = await web_search(f"{url} airdrop review legit 2025", n=4)
    ctx_txt  = "\n".join([r.get("content", "")[:250] for r in results])
    result   = await llm(
        f"Bu kripto projeyi analiz et ve puanla: {url}\n"
        f"Link durumu: {'Aktif ✅' if ok else 'Çalışmıyor ❌'} (HTTP {code})\n\n"
        f"Web bilgisi:\n{ctx_txt}\n\n"
        "⭐ Puan /10 | 🔒 Güvenilirlik | 💰 Kazanç | ⚡ Zorluk | ⚠️ Risk | ✅ Artı | ❌ Eksi | 🎯 Tavsiye\n"
        "Türkçe, emojili.", 600)
    await update.message.reply_text(f"📊 <b>Analiz:</b>\n\n{result}", parse_mode="HTML")

async def schedule_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if len(ctx.args) < 3:
        await update.message.reply_text("Kullanım: /scheduledrop <coin> <url> <dakika>"); return
    name, url = ctx.args[0], ctx.args[1]
    try: mins = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("Dakika sayı olmalı."); return
    ref = ref_code_store.get("code")
    async def later(context):
        try:
            a = {"coin_name": name, "name": name, "url": url, "campaign_url": url,
                 "description": "", "reward": "?", "how_to_join": "", "referral": False,
                 "host_platform": "Manuel", "start_date": "?", "end_date": "?", "score": None}
            txt = await make_post(a, ref, url)
            img = await og_image(url)
            if img:
                try:
                    await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=img,
                                                 caption=txt, parse_mode="HTML")
                except Exception:
                    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=txt,
                                                   parse_mode="HTML", disable_web_page_preview=False)
            else:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=txt,
                                               parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID,
                text=f"✅ Zamanlı: <b>{name}</b>", parse_mode="HTML")
        except Exception as e:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Hata: {e}")
    ctx.job_queue.run_once(later, when=mins * 60)
    await update.message.reply_text(
        f"⏰ <b>{name}</b> {mins} dakika sonra gönderilecek!", parse_mode="HTML")


# ════════════════════════════════════════════════════════════════════════════
# CALLBACK
# ════════════════════════════════════════════════════════════════════════════
async def on_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data

    if data == "rescan":
        await q.edit_message_reply_markup(None)
        class FU:
            effective_chat = q.message.chat
            message        = q.message
        await scan(FU(), ctx)
        return

    if data.startswith("prepare|"):
        idx = int(data.split("|")[1])
        airs = ctx.user_data.get("scan_results", [])
        if idx < len(airs):
            await q.edit_message_reply_markup(None)
            a    = airs[idx]
            link = a.get("campaign_url") or a.get("url", "")
            if link and link.startswith("http"):
                await show_preview(q, ctx, a, custom_link=link)
            else:
                ctx.user_data["pending_airdrop"] = a
                ctx.user_data["await_link"]      = "new"
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🔗 <b>{a.get('coin_name','?')}</b> için katılım linkini gönder:",
                    parse_mode="HTML")
        return

    if data.startswith("send|"):
        key  = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await q.edit_message_text("❌ Post bulunamadı."); return
        try:
            if post.get("image_url"):
                try:
                    await ctx.bot.send_photo(
                        chat_id=GROUP_CHAT_ID, photo=post["image_url"],
                        caption=post["text"], parse_mode="HTML")
                except Exception:
                    await ctx.bot.send_message(
                        chat_id=GROUP_CHAT_ID, text=post["text"],
                        parse_mode="HTML", disable_web_page_preview=False)
            else:
                await ctx.bot.send_message(
                    chat_id=GROUP_CHAT_ID, text=post["text"],
                    parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            # Gönderilen postu kaydet (düzenleme için)
            sent_key = str(stats_store["sent"])
            sent_posts[sent_key] = {
                "name": post["name"],
                "text": post["text"],
                "image_url": post.get("image_url"),
                "airdrop": post.get("airdrop", {}),
                "ref": post.get("ref"),
                "sent_at": datetime.now().strftime("%d.%m.%Y %H:%M"),
            }
            pending_posts.pop(key, None)
            await q.edit_message_text(
                f"✅ <b>{post['name']}</b> gruba gönderildi!\n\n"
                f"📝 Düzenlemek için: /posts",
                parse_mode="HTML")
        except Exception as e:
            await q.edit_message_text(f"❌ Gönderilemedi: {e}")
        return

    if data.startswith("rewrite|"):
        key  = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await q.edit_message_text("❌ Post bulunamadı."); return
        await q.edit_message_text("✍️ Yeniden yazılıyor...")
        new = await llm(
            f"Bu Telegram airdrop postunu daha çarpıcı yeniden yaz. "
            f"HTML formatını ve yapıyı koru. URL ekleme:\n\n{post['text']}", 900)
        new = re.sub(r'https?://\S+', '', new).strip()
        pending_posts[key]["text"] = new
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Gruba Gönder",   callback_data=f"send|{key}"),
             InlineKeyboardButton("✏️ Tekrar",         callback_data=f"rewrite|{key}")],
            [InlineKeyboardButton("🔗 Linki Değiştir", callback_data=f"editlink|{key}"),
             InlineKeyboardButton("❌ İptal",            callback_data=f"cancel|{key}")]
        ])
        try:
            if post.get("image_url"):
                await ctx.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=post["image_url"],
                                         caption=new, parse_mode="HTML", reply_markup=kb)
            else:
                await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=new,
                                           parse_mode="HTML", reply_markup=kb,
                                           disable_web_page_preview=False)
        except Exception:
            await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=new,
                                       parse_mode="HTML", reply_markup=kb,
                                       disable_web_page_preview=False)
        return

    if data.startswith("editlink|"):
        key = data.split("|")[1]
        ctx.user_data["await_link"]      = key
        ctx.user_data["pending_airdrop"] = None
        await q.edit_message_reply_markup(None)
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="🔗 <b>Yeni linki gönder:</b>\n<i>(http ile başlayan tam URL)</i>",
            parse_mode="HTML")
        return

    if data.startswith("cancel|"):
        pending_posts.pop(data.split("|")[1], None)
        await q.edit_message_text("❌ İptal edildi.")
        return

    # ── Gönderilmiş post düzenle
    if data.startswith("editpost|"):
        key  = data.split("|")[1]
        post = sent_posts.get(key)
        if not post:
            await q.edit_message_text("❌ Post bulunamadı."); return
        await q.edit_message_reply_markup(None)
        # Önizleme gönder + düzenleme butonları
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Yazıyı Değiştir",  callback_data=f"edittext|{key}"),
             InlineKeyboardButton("🔗 Linki Değiştir",   callback_data=f"editsentlink|{key}")],
            [InlineKeyboardButton("🔄 Yeniden Yaz (AI)", callback_data=f"rewritesent|{key}"),
             InlineKeyboardButton("🗑️ Kapat",             callback_data=f"closedit|{key}")]
        ])
        preview = post["text"][:600] + ("..." if len(post["text"]) > 600 else "")
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"✏️ <b>{post['name']}</b> düzenleniyor\n"
                 f"📅 Gönderildi: {post['sent_at']}\n\n"
                 f"<i>Mevcut post önizlemesi:</i>\n\n{preview}",
            parse_mode="HTML", reply_markup=kb)
        return

    if data.startswith("edittext|"):
        key = data.split("|")[1]
        ctx.user_data["edit_sent_key"]  = key
        ctx.user_data["edit_sent_mode"] = "text"
        ctx.user_data["await_link"]     = None
        await q.edit_message_reply_markup(None)
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="✏️ <b>Yeni yazıyı gönder:</b>\n\n"
                 "<i>Tüm postu yeniden yazabilirsin. HTML kullanabilirsin.\n"
                 "İptal için: /posts</i>",
            parse_mode="HTML")
        return

    if data.startswith("editsentlink|"):
        key = data.split("|")[1]
        ctx.user_data["edit_sent_key"]  = key
        ctx.user_data["edit_sent_mode"] = "link"
        ctx.user_data["await_link"]     = None
        await q.edit_message_reply_markup(None)
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="🔗 <b>Yeni linki gönder:</b>\n<i>(http ile başlayan URL)</i>",
            parse_mode="HTML")
        return

    if data.startswith("rewritesent|"):
        key  = data.split("|")[1]
        post = sent_posts.get(key)
        if not post:
            await q.edit_message_text("❌ Post bulunamadı."); return
        await q.edit_message_text("✍️ AI yeniden yazıyor...")
        new = await llm(
            f"Bu Telegram kripto airdrop postunu daha çarpıcı ve güncel yeniden yaz.\n"
            f"HTML formatını ve yapıyı koru. URL ekleme:\n\n{post['text']}", 900)
        new = re.sub(r'https?://\S+', '', new).strip()
        sent_posts[key]["text"] = new
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✏️ Yazıyı Değiştir",  callback_data=f"edittext|{key}"),
             InlineKeyboardButton("🔗 Linki Değiştir",   callback_data=f"editsentlink|{key}")],
            [InlineKeyboardButton("🔄 Yeniden Yaz (AI)", callback_data=f"rewritesent|{key}"),
             InlineKeyboardButton("🗑️ Kapat",             callback_data=f"closedit|{key}")]
        ])
        try:
            if post.get("image_url"):
                await ctx.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=post["image_url"],
                                         caption=new[:1024], parse_mode="HTML", reply_markup=kb)
            else:
                await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=new,
                                           parse_mode="HTML", reply_markup=kb,
                                           disable_web_page_preview=False)
        except Exception:
            await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=new,
                                       parse_mode="HTML", reply_markup=kb,
                                       disable_web_page_preview=False)
        return

    if data.startswith("closedit|"):
        await q.edit_message_text("✅ Düzenleme kapatıldı.")
        return

    if data.startswith("sentupdated|"):
        key  = data.split("|")[1]
        post = sent_posts.get(key)
        if not post:
            await q.edit_message_text("❌ Post bulunamadı."); return
        try:
            if post.get("image_url"):
                try:
                    await ctx.bot.send_photo(
                        chat_id=GROUP_CHAT_ID, photo=post["image_url"],
                        caption=post["text"], parse_mode="HTML")
                except Exception:
                    await ctx.bot.send_message(
                        chat_id=GROUP_CHAT_ID, text=post["text"],
                        parse_mode="HTML", disable_web_page_preview=False)
            else:
                await ctx.bot.send_message(
                    chat_id=GROUP_CHAT_ID, text=post["text"],
                    parse_mode="HTML", disable_web_page_preview=False)
            await q.edit_message_text(
                f"✅ <b>{post['name']}</b> güncellenerek gruba tekrar gönderildi!",
                parse_mode="HTML")
        except Exception as e:
            await q.edit_message_text(f"❌ Gönderilemedi: {e}")
        return


# ════════════════════════════════════════════════════════════════════════════
# MESAJ HANDLER
# ════════════════════════════════════════════════════════════════════════════
async def on_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID: return

    text = update.message.text.strip()

    # Gönderilmiş post metin düzenleme
    edit_key  = ctx.user_data.get("edit_sent_key")
    edit_mode = ctx.user_data.get("edit_sent_mode")
    if edit_key and edit_mode == "text":
        ctx.user_data["edit_sent_key"]  = None
        ctx.user_data["edit_sent_mode"] = None
        post = sent_posts.get(edit_key)
        if not post:
            await update.message.reply_text("❌ Post bulunamadı."); return
        sent_posts[edit_key]["text"] = text
        # Yeni yazıyla preview + gruba tekrar gönder butonu
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Gruba Güncellemeyi Gönder",
                                 callback_data=f"sentupdated|{edit_key}"),
            InlineKeyboardButton("✏️ Tekrar Düzenle",
                                 callback_data=f"edittext|{edit_key}")
        ]])
        await update.message.reply_text(
            f"✅ Yazı güncellendi!\n\n<b>Önizleme:</b>\n\n{text[:800]}",
            parse_mode="HTML", reply_markup=kb)
        return

    # Gönderilmiş post link düzenleme
    if edit_key and edit_mode == "link":
        ctx.user_data["edit_sent_key"]  = None
        ctx.user_data["edit_sent_mode"] = None
        if not text.startswith("http"):
            await update.message.reply_text("⚠️ http ile başlayan geçerli bir link gönder."); return
        post = sent_posts.get(edit_key)
        if not post:
            await update.message.reply_text("❌ Post bulunamadı."); return
        # Yazıdaki eski linki yenisiyle değiştir
        old_text = post["text"]
        new_text = re.sub(r'href=["\']https?://[^"\']+["\']>👉 HEMEN KATIL',
                          f'href=\"{text}\">👉 HEMEN KATIL', old_text)
        if new_text == old_text:
            # href bulunamazsa sona ekle
            new_text = re.sub(r'🔗 <b>\[ LİNK BURAYA \]</b>', f'🔗 <b><a href=\"{text}\">👉 HEMEN KATIL</a></b>', old_text)
        sent_posts[edit_key]["text"]      = new_text
        sent_posts[edit_key]["image_url"] = await og_image(text)
        kb = InlineKeyboardMarkup([[
            InlineKeyboardButton("📤 Gruba Güncellemeyi Gönder",
                                 callback_data=f"sentupdated|{edit_key}"),
            InlineKeyboardButton("🔗 Linki Tekrar Değiştir",
                                 callback_data=f"editsentlink|{edit_key}")
        ]])
        await update.message.reply_text(
            f"✅ Link güncellendi: <code>{text}</code>\n\n"
            "Yeni post gruba gönderilebilir.",
            parse_mode="HTML", reply_markup=kb)
        return

    await_link = ctx.user_data.get("await_link")
    if not await_link: return

    if not text.startswith("http"):
        await update.message.reply_text("⚠️ http ile başlayan geçerli bir link gönder."); return

    ctx.user_data["await_link"] = None

    if await_link != "new":
        key  = await_link
        post = pending_posts.get(key)
        if not post:
            await update.message.reply_text("❌ Post bulunamadı."); return
        await update.message.reply_text("⏳ Link güncelleniyor, görsel çekiliyor...")
        airdrop   = post.get("airdrop", {"coin_name": post["name"], "name": post["name"]})
        new_text  = await make_post(airdrop, post.get("ref"), link=text)
        new_image = await og_image(text)
        pending_posts[key]["text"]      = new_text
        pending_posts[key]["image_url"] = new_image
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Gruba Gönder",   callback_data=f"send|{key}"),
             InlineKeyboardButton("✏️ Yeniden Yaz",    callback_data=f"rewrite|{key}")],
            [InlineKeyboardButton("🔗 Linki Değiştir", callback_data=f"editlink|{key}"),
             InlineKeyboardButton("❌ İptal",            callback_data=f"cancel|{key}")]
        ])
        await update.message.reply_text("─── 👁️ YENİ ÖNİZLEME ───")
        try:
            if new_image:
                await update.message.reply_photo(photo=new_image, caption=new_text,
                                                 parse_mode="HTML", reply_markup=kb)
            else:
                await update.message.reply_text(new_text, parse_mode="HTML",
                                                reply_markup=kb, disable_web_page_preview=False)
        except Exception:
            await update.message.reply_text(new_text, parse_mode="HTML",
                                            reply_markup=kb, disable_web_page_preview=False)
        return

    airdrop = ctx.user_data.pop("pending_airdrop", {})
    if not airdrop:
        await update.message.reply_text("❌ Bekleyen airdrop yok. /scan ile tekrar dene."); return
    airdrop["url"] = text
    airdrop["campaign_url"] = text
    await update.message.reply_text("✅ Link alındı!")
    await show_preview(update, ctx, airdrop, custom_link=text)


# ════════════════════════════════════════════════════════════════════════════
# MESAJ KOMUTLARI
# ════════════════════════════════════════════════════════════════════════════
async def set_ref(u, c):
    if not is_admin(u): return
    if not c.args: await u.message.reply_text("Kullanım: /setref <kod>"); return
    ref_code_store["code"] = c.args[0]
    await u.message.reply_text(f"✅ Ref: <code>{c.args[0]}</code>", parse_mode="HTML")

async def clear_ref(u, c):
    if not is_admin(u): return
    ref_code_store["code"] = None
    await u.message.reply_text("🗑️ Ref kodu temizlendi.")

async def show_ref(u, c):
    if not is_admin(u): return
    await u.message.reply_text(
        f"🔗 Ref: <code>{ref_code_store.get('code') or 'Yok'}</code>", parse_mode="HTML")

async def broadcast(u, c):
    if not is_admin(u): return
    if not c.args: await u.message.reply_text("Kullanım: /broadcast <mesaj>"); return
    await c.bot.send_message(chat_id=GROUP_CHAT_ID, text=" ".join(c.args))
    await u.message.reply_text("✅ Gönderildi.")

async def boldcast(u, c):
    if not is_admin(u): return
    if not c.args: await u.message.reply_text("Kullanım: /boldcast <mesaj>"); return
    await c.bot.send_message(chat_id=GROUP_CHAT_ID,
        text=f"📢 <b>{' '.join(c.args)}</b>\n\n— @kriptodropptr", parse_mode="HTML")
    await u.message.reply_text("✅ Gönderildi.")

async def pin_msg(u, c):
    if not is_admin(u): return
    if not c.args: await u.message.reply_text("Kullanım: /pin <mesaj>"); return
    sent = await c.bot.send_message(chat_id=GROUP_CHAT_ID,
        text=f"📌 <b>{' '.join(c.args)}</b>\n\n📢 @kriptodropptr", parse_mode="HTML")
    try:
        await c.bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent.message_id)
        await u.message.reply_text("✅ Sabitlendi.")
    except Exception as e:
        await u.message.reply_text(f"⚠️ Gönderdim ama sabitlemedim: {e}")

async def translate(u, c):
    if not is_admin(u): return
    if not c.args: await u.message.reply_text("Kullanım: /translate <metin>"); return
    r = await llm(f"Türkçeye çevir ve özetle:\n\n{' '.join(c.args)}", 300)
    await u.message.reply_text(f"🇹🇷 <b>Çeviri:</b>\n\n{r}", parse_mode="HTML")

async def hashtag(u, c):
    if not is_admin(u): return
    if not c.args: await u.message.reply_text("Kullanım: /hashtag <proje>"); return
    r = await llm(f"{' '.join(c.args)} kripto projesi için 10 hashtag üret.", 150)
    await u.message.reply_text(f"#️⃣ <b>Hashtagler:</b>\n\n{r}", parse_mode="HTML")

async def stats(u, c):
    if not is_admin(u): return
    await u.message.reply_text(
        f"📈 <b>İstatistikler</b>\n\n"
        f"📤 Gönderim: <code>{stats_store['sent']}</code>\n"
        f"🕐 Son: <code>{stats_store['last'] or '—'}</code>\n"
        f"🔗 Ref: <code>{ref_code_store.get('code') or 'Yok'}</code>", parse_mode="HTML")

async def status(u, c):
    if not is_admin(u): return
    await u.message.reply_text(
        f"🟢 <b>Bot Aktif</b>\n\n"
        f"🤖 Groq Llama 3.3 70B\n"
        f"🔍 Tavily: {'✅ Aktif' if TAVILY_API_KEY else '❌ Key eksik'}\n"
        f"🖼️ OG Image: Otomatik\n"
        f"🔗 Link Kontrol: Aktif\n"
        f"📡 Grup: <code>{GROUP_CHAT_ID}</code>\n"
        f"👤 Admin: <code>{ADMIN_CHAT_ID}</code>", parse_mode="HTML")

async def help_cmd(u, c):
    if not is_admin(u): return
    await u.message.reply_text(
        "💡 <b>Akış:</b>\n\n"
        "1️⃣ /scan → Platformları tara\n"
        "2️⃣ 📝 Post Hazırla → Seç\n"
        "3️⃣ 🔗 Linki Değiştir → Kendi linkini ekle\n"
        "4️⃣ ✅ Gruba Gönder\n\n"
        "/start — Tüm komutlar", parse_mode="HTML")



# ════════════════════════════════════════════════════════════════════════════
# /research — link veya başlık ver, araştır, post hazırla
# ════════════════════════════════════════════════════════════════════════════
async def research(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text(
            "Kullanım:\n"
            "/research &lt;link&gt; — URL araştır, post hazırla\n"
            "/research &lt;proje adı&gt; — İsimle araştır, post hazırla\n\n"
            "Örnek:\n"
            "<code>/research https://app.galxe.com/quest/xyz</code>\n"
            "<code>/research Hyperlane airdrop</code>",
            parse_mode="HTML"); return

    query = " ".join(ctx.args)
    is_url = query.startswith("http")

    sm = await update.message.reply_text(
        f"🔍 <b>Araştırılıyor:</b> <code>{query[:60]}</code>\n\n"
        "📡 Web'den bilgi toplanıyor...",
        parse_mode="HTML")

    try:
        # Web araması yap
        search_q = query if is_url else f"{query} crypto airdrop 2025 how to join reward"
        results  = await web_search(search_q, n=6)

        # URL'yse link kontrolü de yap
        link_status = ""
        if is_url:
            ok, code = await check_url(query)
            link_status = f"Link Durumu: {'✅ Aktif' if ok else '❌ Çalışmıyor'} (HTTP {code})\n"

        # Sonuçlardan içerik derle
        ctx_text = ""
        for r in results:
            ctx_text += f"URL: {r.get('url','')}\nBaşlık: {r.get('title','')}\nİçerik: {r.get('content','')[:400]}\n---\n"

        await sm.edit_text(
            f"🔍 <b>Araştırılıyor:</b> <code>{query[:60]}</code>\n\n"
            "🤖 AI analiz ediyor...", parse_mode="HTML")

        # GPT ile airdrop bilgisi çıkar
        extract_prompt = f"""Web araması yapıldı. Bu airdrop hakkında bilgileri çıkar.

Aranan: {query}
{link_status}
Web Sonuçları:
{ctx_text[:4000]}

Aşağıdaki bilgileri çıkar. Bilgi yoksa "Belirtilmemiş" yaz, UYDURMA.
SADECE JSON döndür:
{{
  "coin_name": "Token/Coin adı",
  "coin_symbol": "SEMBOL",
  "host_platform": "Nerede yapılıyor",
  "campaign_url": "{query if is_url else ''}",
  "description": "Proje ne yapıyor ve neden airdrop yapıyor (Türkçe, 2 cümle)",
  "how_to_join": "Katılım adımları (Türkçe, numaralı)",
  "reward": "Ne kadar token/coin dağıtılıyor",
  "referral": true,
  "referral_bonus": "Referans bonusu (varsa)",
  "start_date": "GG.AA.YYYY veya Belirtilmemiş",
  "end_date": "GG.AA.YYYY veya Belirtilmemiş",
  "score": 7,
  "score_reason": "Neden bu puan"
}}"""

        raw = await llm(extract_prompt, tokens=1200)
        raw = raw.strip()
        if "```" in raw:
            for part in raw.split("```"):
                part = part.strip().lstrip("json").strip()
                if part.startswith("{"):
                    raw = part
                    break
        if not raw.startswith("{"):
            s, e = raw.find("{"), raw.rfind("}") + 1
            if s != -1: raw = raw[s:e]

        airdrop = json.loads(raw)
        airdrop["url"] = query if is_url else (results[0].get("url","") if results else "")

        # Özet bilgi göster
        coin = airdrop.get("coin_name","?")
        sym  = f" ({airdrop.get('coin_symbol','')})" if airdrop.get("coin_symbol") else ""
        info = (
            f"✅ <b>Bulundu: {coin}{sym}</b>\n"
            f"🏢 Platform: {airdrop.get('host_platform','?')}\n"
            f"💰 Ödül: {airdrop.get('reward','?')}\n"
            f"📅 Bitiş: {airdrop.get('end_date','?')}\n"
            f"⭐ Puan: {airdrop.get('score','?')}/10\n\n"
            "Post hazırlanıyor..."
        )
        await sm.edit_text(info, parse_mode="HTML")

        await show_preview(update, ctx, airdrop,
                           custom_link=airdrop.get("campaign_url") or airdrop.get("url",""))

    except Exception as e:
        logger.exception(e)
        await sm.edit_text(f"❌ Araştırma hatası: {e}")


# ════════════════════════════════════════════════════════════════════════════
# /posts — gönderilen postları listele ve düzenle
# ════════════════════════════════════════════════════════════════════════════
async def posts_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not sent_posts:
        await update.message.reply_text(
            "📭 Henüz gönderilmiş post yok.\n\nGönder: /scan → post hazırla → ✅ Gruba Gönder")
        return

    msg = "📋 <b>Gönderilen Postlar:</b>\n\n"
    buttons = []
    # Son 10 postu göster (en yeniden eskiye)
    for key in sorted(sent_posts.keys(), reverse=True)[:10]:
        p = sent_posts[key]
        msg += f"<b>{key}.</b> {p['name']} — <i>{p['sent_at']}</i>\n"
        buttons.append([InlineKeyboardButton(
            f"✏️ #{key} — {p['name'][:25]}",
            callback_data=f"editpost|{key}"
        )])

    await update.message.reply_text(msg, parse_mode="HTML",
                                    reply_markup=InlineKeyboardMarkup(buttons))

# ════════════════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════════════════
def main():
    if not BOT_TOKEN:    logger.error("❌ BOT_TOKEN yok!"); sys.exit(1)
    if not GROQ_API_KEY: logger.error("❌ GROQ_API_KEY yok!"); sys.exit(1)
    logger.info(f"✅ Admin:{ADMIN_CHAT_ID} Tavily:{'var' if TAVILY_API_KEY else 'YOK'}")

    app = Application.builder().token(BOT_TOKEN).build()
    for cmd, fn in [
        ("start", start), ("help", help_cmd),
        ("scan", scan), ("analyze", analyze),
        ("newairdrop", post_airdrop), ("quickdrop", quick_drop),
        ("scheduledrop", schedule_drop),
        ("setref", set_ref), ("clearref", clear_ref), ("showref", show_ref),
        ("broadcast", broadcast), ("boldcast", boldcast), ("pin", pin_msg),
        ("translate", translate), ("hashtag", hashtag),
        ("stats", stats), ("status", status),
        ("research", research), ("posts", posts_list),
    ]:
        app.add_handler(CommandHandler(cmd, fn))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    logger.info("🚀 Polling başlıyor...")
    app.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
