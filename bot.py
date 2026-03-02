import logging
import sys
import os
import json
import re
import random
import time
import hashlib
import httpx
from datetime import datetime

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

from dotenv import load_dotenv
load_dotenv()

BOT_TOKEN           = os.getenv("BOT_TOKEN", "")
GROQ_API_KEY        = os.getenv("GROQ_API_KEY", "")
TAVILY_API_KEY      = os.getenv("TAVILY_API_KEY", "")
ADMIN_CHAT_ID       = int(os.getenv("ADMIN_CHAT_ID", "0"))
GROUP_CHAT_ID       = int(os.getenv("GROUP_CHAT_ID", "0"))
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes
)

# ── State ──────────────────────────────────────────────
ref_code_store  = {"code": None}
stats_store     = {"sent": 0, "last": None}
pending_posts   = {}
shown_airdrops  = set()


# ══════════════════════════════════════════════════════
# GROQ  (ücretsiz LLM)
# ══════════════════════════════════════════════════════

async def gpt_text(prompt: str, max_tokens: int = 1000) -> str:
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.85
            }
        )
        data = resp.json()
        if "error" in data:
            raise Exception(data["error"]["message"])
        return data["choices"][0]["message"]["content"].strip()


# ══════════════════════════════════════════════════════
# TAVILY WEB SEARCH (ücretsiz, 1000 istek/ay)
# ══════════════════════════════════════════════════════

async def tavily_search(query: str, max_results: int = 5) -> list[dict]:
    """Tavily API ile web araması yap"""
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY yok, web araması atlanıyor")
        return []
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": max_results,
                    "include_answer": True,
                    "include_raw_content": False,
                }
            )
            data = resp.json()
            return data.get("results", [])
    except Exception as e:
        logger.warning(f"Tavily arama hatası: {e}")
        return []


# ══════════════════════════════════════════════════════
# OPEN GRAPH — link önizleme görseli
# ══════════════════════════════════════════════════════

async def get_og_image(url: str) -> str | None:
    """URL'den Open Graph görselini çek"""
    if not url or not url.startswith("http"):
        return None
    try:
        async with httpx.AsyncClient(
            timeout=10,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TelegramBot/1.0)"}
        ) as client:
            resp = await client.get(url)
            html = resp.text

            # og:image meta tagını bul
            patterns = [
                r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\'](https?://[^"\']+)["\']',
                r'<meta[^>]+content=["\'](https?://[^"\']+)["\'][^>]+property=["\']og:image["\']',
                r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\'](https?://[^"\']+)["\']',
            ]
            for pattern in patterns:
                match = re.search(pattern, html, re.IGNORECASE)
                if match:
                    img_url = match.group(1)
                    logger.info(f"OG image bulundu: {img_url[:80]}")
                    return img_url
    except Exception as e:
        logger.warning(f"OG image çekme hatası ({url[:50]}): {e}")
    return None


# ══════════════════════════════════════════════════════
# AIRDROP TARAMA — Tavily + CoinGecko + GPT analiz
# ══════════════════════════════════════════════════════

async def search_live_airdrops() -> str:
    """Tavily ile güncel airdrop haberlerini tara"""
    queries = [
        "active crypto airdrop 2025 claim now referral",
        "new crypto airdrop campaign testnet rewards 2025",
        "defi airdrop eligible tasks galxe zealy 2025",
    ]
    # Her çağrıda farklı sorgu
    query = random.choice(queries)
    results = await tavily_search(query, max_results=6)

    if not results:
        return ""

    summary = "Web'den bulunan güncel airdrop haberleri:\n"
    for r in results:
        summary += f"- {r.get('title','')}: {r.get('url','')}\n  {r.get('content','')[:200]}\n"
    return summary


async def fetch_coingecko_trending() -> str:
    """CoinGecko trending coinleri çek"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "https://api.coingecko.com/api/v3/search/trending",
                headers={"accept": "application/json"}
            )
            if r.status_code == 200:
                coins = r.json().get("coins", [])[:8]
                summary = "CoinGecko'da trend olan projeler:\n"
                for c in coins:
                    item = c.get("item", {})
                    summary += f"- {item.get('name')} ({item.get('symbol')}) rank:{item.get('market_cap_rank','?')}\n"
                return summary
    except Exception as e:
        logger.warning(f"CoinGecko hatası: {e}")
    return ""


async def gpt_find_airdrops() -> list[dict]:
    """Web araması + CoinGecko verisiyle GPT'den airdrop listesi al"""

    # Paralel veri çek
    import asyncio
    web_data, trending_data = await asyncio.gather(
        search_live_airdrops(),
        fetch_coingecko_trending(),
        return_exceptions=True
    )
    if isinstance(web_data, Exception):
        web_data = ""
    if isinstance(trending_data, Exception):
        trending_data = ""

    # Daha önce gösterilenleri GPT'ye söyle
    exclude = ", ".join(list(shown_airdrops)[-10:]) if shown_airdrops else "yok"

    # Konu çeşitlendirmesi
    topics = [
        "Layer2 rollup ve ZK projeleri",
        "DEX, AMM ve DeFi protokolleri",
        "CEX borsası trading ödülleri",
        "Cross-chain bridge projeleri",
        "Web3 cüzdan ve altyapı",
        "Restaking ve liquid staking",
        "AI + kripto projeleri",
        "Galxe/Zealy üzerindeki aktif kampanyalar",
        "Solana/Sui/Aptos ekosistemi",
        "NFT ve GameFi projeleri",
    ]
    random.seed(int(time.time() / 7200))
    focus = random.sample(topics, 2)

    prompt = f"""Sen 2025 kripto airdrop uzmanısın. Aşağıdaki GÜNCEL web verilerini kullanarak 5 adet aktif airdrop listele.

{web_data}

{trending_data}

ODAK KONULAR: {', '.join(focus)}
DAHA ÖNCE GÖSTERİLENLER (bunları TEKRARLAMA): {exclude}

ZORUNLU KURALLAR:
1. Sadece GERÇEK ve VAR OLAN projeleri yaz
2. URL'ler çalışan resmi siteler olmalı
3. Yukarıdaki web verilerindeki projeleri öncelikle kullan
4. Özellikle AKTİF REFERANS SİSTEMİ olan airdroplar
5. Türkiye'den katılılabilen

SADECE JSON döndür, açıklama ekleme:
[
  {{
    "name": "Proje Adı",
    "category": "DEX/CEX/L2/Bridge/DeFi/GameFi",
    "url": "https://resmi-site.com",
    "campaign_url": "https://katilim-linki.com",
    "description": "Ne yapıyor ve neden önemli (Türkçe, 2 cümle)",
    "how_to_join": "1. Adım\\n2. Adım\\n3. Adım (Türkçe)",
    "reward": "Tahmini ödül",
    "referral": true,
    "referral_bonus": "Davet bonusu",
    "difficulty": "Kolay/Orta/Zor",
    "time_required": "X dakika",
    "deadline": "Tarih veya Sürekli",
    "score": 8,
    "score_reason": "Kısa neden"
  }}
]"""

    raw = await gpt_text(prompt, max_tokens=2500)
    raw = raw.strip()
    if "```" in raw:
        for part in raw.split("```"):
            part = part.strip().lstrip("json").strip()
            if part.startswith("["):
                raw = part
                break
    if not raw.startswith("["):
        s, e = raw.find("["), raw.rfind("]") + 1
        if s != -1:
            raw = raw[s:e]

    airdrops = json.loads(raw)

    # Filtre: geçersiz URL'leri at
    valid = [
        a for a in airdrops
        if a.get("url", "").startswith("http")
        and "example.com" not in a.get("url", "")
    ]
    return valid if valid else airdrops


# ══════════════════════════════════════════════════════
# KAMPANYA GEÇERLİLİK KONTROLÜ
# ══════════════════════════════════════════════════════

async def verify_campaign(url: str, name: str) -> dict:
    """Tavily ile kampanyanın gerçek ve aktif olup olmadığını kontrol et"""
    if not TAVILY_API_KEY:
        return {"valid": None, "note": "Doğrulama yapılamadı (Tavily key yok)"}

    results = await tavily_search(f"{name} airdrop campaign 2025 active", max_results=3)

    if not results:
        return {"valid": None, "note": "Arama sonucu bulunamadı"}

    context = "\n".join([f"- {r.get('title','')}: {r.get('content','')[:300]}" for r in results])

    verdict = await gpt_text(
        f"""Bu kripto airdrop kampanyasını değerlendir:
Proje: {name}
URL: {url}

Web'den bulunan bilgiler:
{context}

Şunları değerlendir:
- Kampanya hâlâ aktif mi?
- URL güvenilir mi?
- Dolandırıcılık riski var mı?
- Genel güvenilirlik

Kısa ve net yanıt ver (Türkçe, 3-4 cümle). Başına ✅ (güvenilir), ⚠️ (dikkatli ol) veya ❌ (şüpheli) koy.""",
        max_tokens=200
    )
    return {"valid": True, "note": verdict}


# ══════════════════════════════════════════════════════
# POST OLUŞTURMA
# ══════════════════════════════════════════════════════

async def gpt_make_post(airdrop: dict, ref: str = None, custom_link: str = None) -> str:
    score_line = f"Puan: {airdrop.get('score','?')}/10 — {airdrop.get('score_reason','')}" if airdrop.get('score') not in ('?', None) else ""

    prompt = f"""Türk kripto topluluğu için bu airdrop hakkında emojili, heyecan verici Telegram duyurusu yaz.
HTML formatı: <b>kalın</b>, <i>italik</i>, <code>kod</code>

Proje: {airdrop['name']} [{airdrop.get('category','')}]
Açıklama: {airdrop.get('description','')}
Nasıl Katılınır: {airdrop.get('how_to_join','')}
Ödül: {airdrop.get('reward','?')}
Zorluk: {airdrop.get('difficulty','?')} • Süre: {airdrop.get('time_required','?')}
Referans: {'VAR — ' + airdrop.get('referral_bonus','') if airdrop.get('referral') else 'Yok'}
{score_line}

Format: 🚀 Başlık → Çarpıcı giriş → 📋 Katılım adımları → 💰 Ödül → ⭐ Puan
ÖNEMLİ: Post içine hiçbir URL veya http adresi yazma."""

    text = await gpt_text(prompt, max_tokens=700)
    text = re.sub(r'https?://\S+', '', text).strip()

    if ref:
        text += f"\n\n🎯 <b>Referans Kodu:</b> <code>{ref}</code>"

    # Link satırı — DÜZENLENECEK PLACEHOLDER
    link = custom_link or airdrop.get("campaign_url") or airdrop.get("url") or ""
    if link and link.startswith("http"):
        text += f"\n\n🔗 <a href='{link}'>👉 Hemen Katıl</a>"
    else:
        text += f"\n\n🔗 <b>[ LİNK BURAYA — düzenle ve gönder ]</b>"

    text += "\n\n📢 @kriptodropptr"
    return text


async def gpt_score_url(url: str) -> str:
    results = await tavily_search(f"site:{url} OR \"{url}\" crypto airdrop review", max_results=3)
    context = "\n".join([r.get("content", "")[:200] for r in results]) if results else ""

    return await gpt_text(
        f"Bu kripto projeyi analiz et ve puanla: {url}\n\n"
        f"Web'den bulunan bilgiler:\n{context}\n\n"
        "⭐ Puan /10 | 🔒 Güvenilirlik | 💰 Kazanç | ⚡ Zorluk | ⚠️ Risk | ✅ Artı | ❌ Eksi | 🎯 Tavsiye\n"
        "Türkçe, emojili yaz.",
        max_tokens=500
    )


# ══════════════════════════════════════════════════════
# YARDIMCI
# ══════════════════════════════════════════════════════

def is_admin(update) -> bool:
    return update.effective_chat.id == ADMIN_CHAT_ID


async def _prepare_and_show(update, ctx: ContextTypes.DEFAULT_TYPE, airdrop: dict, custom_link: str = None):
    ref = ref_code_store.get("code")

    async def reply_text(text, **kw):
        if hasattr(update, "message") and update.message:
            return await update.message.reply_text(text, **kw)
        return await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, **kw)

    async def reply_photo(photo, caption, **kw):
        if hasattr(update, "message") and update.message:
            return await update.message.reply_photo(photo=photo, caption=caption, **kw)
        return await ctx.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=photo, caption=caption, **kw)

    status_msg = await reply_text(
        f"⚙️ <b>{airdrop['name']}</b> hazırlanıyor...\n✍️ Post yazılıyor...",
        parse_mode="HTML"
    )

    post_text = await gpt_make_post(airdrop, ref, custom_link)

    # Link önizleme görseli
    link = custom_link or airdrop.get("campaign_url") or airdrop.get("url", "")
    image_url = None
    if link and link.startswith("http"):
        await status_msg.edit_text(
            f"⚙️ <b>{airdrop['name']}</b> hazırlanıyor...\n✅ Post yazıldı\n🖼️ Sayfa görseli çekiliyor...",
            parse_mode="HTML"
        )
        image_url = await get_og_image(link)

    await status_msg.edit_text(
        f"✅ <b>{airdrop['name']}</b> hazır! Önizleme geliyor...",
        parse_mode="HTML"
    )

    await reply_text("─── 👁️ ÖNİZLEME ───")

    try:
        if image_url:
            preview_msg = await reply_photo(image_url, post_text, parse_mode="HTML")
        else:
            preview_msg = await reply_text(post_text, parse_mode="HTML", disable_web_page_preview=False)
    except Exception:
        preview_msg = await reply_text(post_text, parse_mode="HTML", disable_web_page_preview=False)

    key = str(preview_msg.message_id)
    pending_posts[key] = {
        "text": post_text,
        "image_url": image_url,
        "name": airdrop["name"],
        "airdrop": airdrop,
        "ref": ref,
    }

    score_info = f"⭐ {airdrop.get('score','?')}/10 — {airdrop.get('score_reason','')}\n\n" if airdrop.get("score") not in ("?", None) else ""

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Gruba Gönder",   callback_data=f"send|{key}"),
            InlineKeyboardButton("✏️ Yeniden Yaz",    callback_data=f"rewrite|{key}"),
        ],
        [
            InlineKeyboardButton("🔗 Linki Değiştir", callback_data=f"changelink|{key}"),
            InlineKeyboardButton("❌ İptal",            callback_data=f"cancel|{key}"),
        ]
    ])

    await reply_text(
        f"⬆️ <b>{airdrop['name']}</b> önizlemesi\n\n{score_info}"
        "🔗 <i>Linki değiştirmek için 'Linki Değiştir' butonuna bas.</i>\n\nNe yapmak istersin?",
        parse_mode="HTML",
        reply_markup=keyboard
    )


# ══════════════════════════════════════════════════════
# KOMUTLAR
# ══════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    adm = uid == ADMIN_CHAT_ID
    msg = "🪂 <b>KriptoDropptr Bot</b>\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"{'✅ Admin paneline hoş geldin!' if adm else '⛔ Yetkisiz erişim.'}\n\n"
    if adm:
        ref  = ref_code_store.get("code") or "Ayarlanmamış"
        tw   = "✅ Aktif" if TAVILY_API_KEY else "❌ Key yok"
        msg += (
            f"📊 <b>Durum</b>\n"
            f"├ Ref Kodu: <code>{ref}</code>\n"
            f"├ Gönderim: <code>{stats_store['sent']}</code>\n"
            f"├ Web Arama (Tavily): {tw}\n"
            f"└ Son: <code>{stats_store['last'] or '—'}</code>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🤖 <b>AI AIRDROP MOTORU</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/scan → Web tara, güncel airdropları getir\n"
            "/autopick → AI en iyisini seçer\n"
            "/analyze &lt;url&gt; → Projeyi web'den araştır, puanla\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📢 <b>PAYLAŞIM</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/newairdrop &lt;proje&gt; &lt;url&gt; → Post hazırla\n"
            "/quickdrop &lt;url&gt; → Sadece URL ver\n"
            "/scheduledrop &lt;proje&gt; &lt;url&gt; &lt;dk&gt; → Zamanlı\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🔗 <b>REFERANS</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/setref &lt;kod&gt; · /clearref · /showref\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📣 <b>MESAJ &amp; ARAÇLAR</b>\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/broadcast · /boldcast · /pin\n"
            "/translate · /hashtag\n\n"
            "📈 /stats · /status"
        )
    await update.message.reply_text(msg, parse_mode="HTML")


async def scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    msg = await update.message.reply_text(
        "🔍 İnternet taranıyor...\n"
        "📡 Güncel airdrop haberleri aranıyor...\n"
        "🤖 AI analiz yapıyor..."
    )
    try:
        airdrops = await gpt_find_airdrops()

        # Gösterilenleri filtrele
        new = [a for a in airdrops if a.get("name") not in shown_airdrops]
        if not new:
            shown_airdrops.clear()
            new = airdrops
        for a in new:
            shown_airdrops.add(a.get("name", ""))
        airdrops = new

        await msg.delete()

        for i, a in enumerate(airdrops):
            score  = a.get("score", "?")
            stars  = "⭐" * min(int(score), 5) if isinstance(score, int) else "⭐"
            ref_badge = "🔁 <b>Referans Sistemi VAR</b>" if a.get("referral") else "➖ Referans yok"
            campaign = a.get("campaign_url") or a.get("url", "")

            card = (
                f"{'━'*28}\n"
                f"🪂 <b>{i+1}. {a['name']}</b>  <code>[{a.get('category','')}]</code>\n"
                f"{'━'*28}\n\n"
                f"📝 {a.get('description','')}\n\n"
                f"📋 <b>Nasıl Katılınır:</b>\n{a.get('how_to_join','')}\n\n"
                f"💰 <b>Ödül:</b> {a.get('reward','?')}\n"
                f"{ref_badge}\n"
            )
            if a.get("referral_bonus"):
                card += f"🎁 <b>Referans Bonusu:</b> {a['referral_bonus']}\n"
            card += (
                f"⚡ <b>Zorluk:</b> {a.get('difficulty','?')}  •  "
                f"⏱ <b>Süre:</b> {a.get('time_required','?')}\n"
                f"📅 <b>Son Tarih:</b> {a.get('deadline','?')}\n\n"
                f"{stars} <b>Puan: {score}/10</b> — {a.get('score_reason','')}\n"
            )
            if campaign:
                card += f"\n🔗 <a href='{campaign}'>Kampanya Sayfası</a>"

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(f"📢 Paylaş ({score}/10)", callback_data=f"prepare|{i}"),
                    InlineKeyboardButton("🔍 Doğrula", callback_data=f"verify|{i}"),
                ]
            ])
            await update.message.reply_text(
                card, parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=keyboard
            )

        await update.message.reply_text(
            f"✅ <b>{len(airdrops)} airdrop bulundu.</b>\n\n"
            "⚠️ <i>Paylaşmadan önce linkleri kontrol et veya 🔍 Doğrula butonunu kullan.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🤖 AI En İyisini Seçsin", callback_data="autopick_from_scan")],
                [InlineKeyboardButton("🔄 Farklı Airdroplar Getir", callback_data="rescan")]
            ])
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")


async def autopick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("🤖 AI en iyi airdropu seçiyor...")
    try:
        airdrops = await gpt_find_airdrops()
        best = max(airdrops, key=lambda x: x.get("score", 0))
        await _prepare_and_show(update, ctx, best)
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")


async def post_airdrop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if len(ctx.args) < 2:
        await update.message.reply_text("Kullanım: /newairdrop <proje_adı> <url>"); return
    airdrop = {
        "name": ctx.args[0], "url": ctx.args[1], "campaign_url": ctx.args[1],
        "description": "", "reward": "?", "difficulty": "?",
        "score": None, "score_reason": "", "referral": False,
        "how_to_join": "", "time_required": "?", "deadline": "?", "category": ""
    }
    await _prepare_and_show(update, ctx, airdrop, custom_link=ctx.args[1])


async def quick_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /quickdrop <url>"); return
    url = ctx.args[0]
    domain = url.split("/")[2].replace("www.", "").split(".")[0].capitalize()
    airdrop = {
        "name": domain, "url": url, "campaign_url": url,
        "description": "", "reward": "?", "difficulty": "?",
        "score": None, "score_reason": "", "referral": False,
        "how_to_join": "", "time_required": "?", "deadline": "?", "category": ""
    }
    await _prepare_and_show(update, ctx, airdrop, custom_link=url)


async def analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analyze <url>"); return
    await update.message.reply_text("🔍 Web'den araştırılıyor...")
    result = await gpt_score_url(ctx.args[0])
    await update.message.reply_text(f"📊 <b>Analiz:</b>\n\n{result}", parse_mode="HTML")


async def schedule_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if len(ctx.args) < 3:
        await update.message.reply_text("Kullanım: /scheduledrop <proje> <url> <dakika>"); return
    project_name, url = ctx.args[0], ctx.args[1]
    try: minutes = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("Dakika sayı olmalı."); return
    ref = ref_code_store.get("code")

    async def send_later(context):
        try:
            airdrop = {"name": project_name, "url": url, "campaign_url": url, "description": "",
                       "reward": "?", "difficulty": "?", "score": None, "referral": False,
                       "how_to_join": "", "time_required": "?", "deadline": "?", "category": ""}
            text      = await gpt_make_post(airdrop, ref, url)
            image_url = await get_og_image(url)
            try:
                if image_url:
                    await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=image_url, caption=text, parse_mode="HTML")
                else:
                    await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
            except Exception:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"✅ Zamanlı gönderi: <b>{project_name}</b>", parse_mode="HTML")
        except Exception as e:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Zamanlı gönderi hatası: {e}")

    ctx.job_queue.run_once(send_later, when=minutes * 60)
    await update.message.reply_text(f"⏰ <b>{project_name}</b> {minutes} dakika sonra gönderilecek!", parse_mode="HTML")


# ══════════════════════════════════════════════════════
# CALLBACK HANDLER
# ══════════════════════════════════════════════════════

async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    # ── Rescan
    if data == "rescan":
        await query.edit_message_reply_markup(None)
        class FakeUpdate:
            effective_chat = query.message.chat
            message = query.message
        await scan(FakeUpdate(), ctx)
        return

    # ── Doğrula
    if data.startswith("verify|"):
        idx = int(data.split("|")[1])
        airdrops = ctx.user_data.get("scan_results", [])
        if idx < len(airdrops):
            a = airdrops[idx]
            await query.answer("🔍 Doğrulanıyor...", show_alert=False)
            url = a.get("campaign_url") or a.get("url", "")
            result = await verify_campaign(url, a["name"])
            await ctx.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🔍 <b>{a['name']} Doğrulama Sonucu:</b>\n\n{result['note']}",
                parse_mode="HTML"
            )
        return

    # ── Seç (scan'dan)
    if data.startswith("prepare|"):
        idx = int(data.split("|")[1])
        airdrops = ctx.user_data.get("scan_results", [])
        if idx < len(airdrops):
            await query.edit_message_reply_markup(None)
            airdrop = airdrops[idx]
            url = airdrop.get("campaign_url") or airdrop.get("url", "")
            if url and url.startswith("http"):
                await _prepare_and_show(query, ctx, airdrop, custom_link=url)
            else:
                ctx.user_data["pending_airdrop"] = airdrop
                ctx.user_data["awaiting_link"] = True
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🔗 <b>{airdrop['name']}</b> için katılım linkini gönder:",
                    parse_mode="HTML"
                )
        return

    # ── Autopick
    if data == "autopick_from_scan":
        airdrops = ctx.user_data.get("scan_results", [])
        if airdrops:
            best = max(airdrops, key=lambda x: x.get("score", 0))
            await query.edit_message_reply_markup(None)
            url = best.get("campaign_url") or best.get("url", "")
            if url and url.startswith("http"):
                await _prepare_and_show(query, ctx, best, custom_link=url)
            else:
                ctx.user_data["pending_airdrop"] = best
                ctx.user_data["awaiting_link"] = True
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🤖 Seçilen: <b>{best['name']}</b>\n\n🔗 Katılım linkini gönder:",
                    parse_mode="HTML"
                )
        return

    # ── Gruba gönder
    if data.startswith("send|"):
        key  = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await query.edit_message_text("❌ Post bulunamadı, tekrar dene."); return
        try:
            if post.get("image_url"):
                try:
                    await ctx.bot.send_photo(
                        chat_id=GROUP_CHAT_ID, photo=post["image_url"],
                        caption=post["text"], parse_mode="HTML"
                    )
                except Exception:
                    await ctx.bot.send_message(
                        chat_id=GROUP_CHAT_ID, text=post["text"],
                        parse_mode="HTML", disable_web_page_preview=False
                    )
            else:
                await ctx.bot.send_message(
                    chat_id=GROUP_CHAT_ID, text=post["text"],
                    parse_mode="HTML", disable_web_page_preview=False
                )
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            pending_posts.pop(key, None)
            await query.edit_message_text(f"✅ <b>{post['name']}</b> gruba gönderildi!", parse_mode="HTML")
        except Exception as e:
            await query.edit_message_text(f"❌ Gönderilemedi: {e}")
        return

    # ── Yeniden yaz
    if data.startswith("rewrite|"):
        key  = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await query.edit_message_text("❌ Post bulunamadı."); return
        await query.edit_message_text("✍️ Yeniden yazılıyor...")
        try:
            new_text = await gpt_text(
                f"Bu Telegram airdrop postunu daha çarpıcı ve emojili yeniden yaz, HTML formatını koru, URL ekleme:\n\n{post['text']}",
                max_tokens=700
            )
            new_text = re.sub(r'https?://\S+', '', new_text).strip()
            pending_posts[key]["text"] = new_text

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Gruba Gönder",   callback_data=f"send|{key}"),
                    InlineKeyboardButton("✏️ Tekrar",         callback_data=f"rewrite|{key}"),
                ],
                [
                    InlineKeyboardButton("🔗 Linki Değiştir", callback_data=f"changelink|{key}"),
                    InlineKeyboardButton("❌ İptal",            callback_data=f"cancel|{key}"),
                ]
            ])
            try:
                if post.get("image_url"):
                    await ctx.bot.send_photo(
                        chat_id=ADMIN_CHAT_ID, photo=post["image_url"],
                        caption=new_text, parse_mode="HTML", reply_markup=keyboard
                    )
                else:
                    await ctx.bot.send_message(
                        chat_id=ADMIN_CHAT_ID, text=new_text,
                        parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=False
                    )
            except Exception:
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT_ID, text=new_text,
                    parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=False
                )
        except Exception as e:
            await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Yeniden yazma hatası: {e}")
        return

    # ── Linki değiştir
    if data.startswith("changelink|"):
        key = data.split("|")[1]
        ctx.user_data["changelink_key"] = key
        ctx.user_data["awaiting_link"]  = True
        await query.edit_message_reply_markup(None)
        await ctx.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text="🔗 Yeni linki gönder:\n\n<i>(http ile başlayan tam URL)</i>",
            parse_mode="HTML"
        )
        return

    # ── İptal
    if data.startswith("cancel|"):
        pending_posts.pop(data.split("|")[1], None)
        await query.edit_message_text("❌ İptal edildi.")
        return


# ══════════════════════════════════════════════════════
# MESAJ HANDLER — link bekleniyor
# ══════════════════════════════════════════════════════

async def link_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_chat.id != ADMIN_CHAT_ID: return
    if not ctx.user_data.get("awaiting_link"): return

    text = update.message.text.strip()
    if not text.startswith("http"):
        await update.message.reply_text("⚠️ Geçerli bir link gönder (http ile başlamalı)"); return

    ctx.user_data["awaiting_link"] = False

    # changelink — mevcut postu güncelle
    changelink_key = ctx.user_data.pop("changelink_key", None)
    if changelink_key and changelink_key in pending_posts:
        post = pending_posts[changelink_key]
        airdrop = post.get("airdrop", {"name": post["name"]})
        ref = post.get("ref")

        await update.message.reply_text("⏳ Link güncelleniyor, görsel çekiliyor...")
        new_text = await gpt_make_post(airdrop, ref, custom_link=text)
        image_url = await get_og_image(text)

        pending_posts[changelink_key]["text"] = new_text
        pending_posts[changelink_key]["image_url"] = image_url

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Gruba Gönder",   callback_data=f"send|{changelink_key}"),
                InlineKeyboardButton("✏️ Yeniden Yaz",    callback_data=f"rewrite|{changelink_key}"),
            ],
            [
                InlineKeyboardButton("🔗 Linki Değiştir", callback_data=f"changelink|{changelink_key}"),
                InlineKeyboardButton("❌ İptal",            callback_data=f"cancel|{changelink_key}"),
            ]
        ])

        await update.message.reply_text("─── 👁️ YENİ ÖNİZLEME ───")
        try:
            if image_url:
                await update.message.reply_photo(photo=image_url, caption=new_text, parse_mode="HTML", reply_markup=keyboard)
            else:
                await update.message.reply_text(new_text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=False)
        except Exception:
            await update.message.reply_text(new_text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=False)
        return

    # scan'dan gelen airdrop için link bekliyordu
    airdrop = ctx.user_data.pop("pending_airdrop", {})
    if not airdrop:
        await update.message.reply_text("❌ Bekleyen airdrop bulunamadı. /scan ile tekrar dene."); return

    airdrop["url"] = text
    airdrop["campaign_url"] = text
    await update.message.reply_text("✅ Link alındı! Post hazırlanıyor...")
    await _prepare_and_show(update, ctx, airdrop, custom_link=text)


# ══════════════════════════════════════════════════════
# DİĞER KOMUTLAR
# ══════════════════════════════════════════════════════

async def set_ref_code(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /setref <kod>"); return
    ref_code_store["code"] = ctx.args[0]
    await update.message.reply_text(f"✅ Ref kodu: <code>{ctx.args[0]}</code>", parse_mode="HTML")

async def clear_ref(update, ctx):
    if not is_admin(update): return
    ref_code_store["code"] = None
    await update.message.reply_text("🗑️ Referans kodu temizlendi.")

async def show_ref(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(f"🔗 Aktif ref: <code>{ref_code_store.get('code') or 'Yok'}</code>", parse_mode="HTML")

async def broadcast(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /broadcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=" ".join(ctx.args))
    await update.message.reply_text("✅ Gönderildi.")

async def boldcast(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /boldcast <mesaj>"); return
    await ctx.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=f"📢 <b>{' '.join(ctx.args)}</b>\n\n— @kriptodropptr",
        parse_mode="HTML"
    )
    await update.message.reply_text("✅ Gönderildi.")

async def pin_message(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /pin <mesaj>"); return
    sent = await ctx.bot.send_message(
        chat_id=GROUP_CHAT_ID,
        text=f"📌 <b>{' '.join(ctx.args)}</b>\n\n📢 @kriptodropptr",
        parse_mode="HTML"
    )
    try:
        await ctx.bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent.message_id)
        await update.message.reply_text("✅ Sabitlendi.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Gönderdim ama sabitlemedim: {e}")

async def translate(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /translate <metin>"); return
    result = await gpt_text(f"Türkçeye çevir ve özetle:\n\n{' '.join(ctx.args)}", max_tokens=300)
    await update.message.reply_text(f"🇹🇷 <b>Çeviri:</b>\n\n{result}", parse_mode="HTML")

async def hashtag(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /hashtag <proje>"); return
    result = await gpt_text(f"{' '.join(ctx.args)} kripto projesi için 10 hashtag üret.", max_tokens=150)
    await update.message.reply_text(f"#️⃣ <b>Hashtagler:</b>\n\n{result}", parse_mode="HTML")

async def stats(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(
        f"📈 <b>İstatistikler</b>\n\n"
        f"📤 Toplam: <code>{stats_store['sent']}</code>\n"
        f"🕐 Son: <code>{stats_store['last'] or '—'}</code>\n"
        f"🔗 Ref: <code>{ref_code_store.get('code') or 'Yok'}</code>",
        parse_mode="HTML"
    )

async def status(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(
        f"🟢 <b>Bot Aktif</b>\n\n"
        f"🤖 Model: Llama 3.3 70B (Groq)\n"
        f"🔍 Web Arama: {'Tavily ✅' if TAVILY_API_KEY else '❌ Key yok'}\n"
        f"🖼️ Görsel: OG Image (otomatik)\n"
        f"📡 Grup: <code>{GROUP_CHAT_ID}</code>\n"
        f"👤 Admin: <code>{ADMIN_CHAT_ID}</code>\n"
        f"🔗 Ref: <code>{ref_code_store.get('code') or 'Yok'}</code>",
        parse_mode="HTML"
    )

async def help_command(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(
        "💡 <b>Hızlı Başlangıç:</b>\n\n"
        "1️⃣ /scan → Web'den güncel airdrop bul\n"
        "2️⃣ 🔍 Doğrula → Kampanya gerçek mi kontrol et\n"
        "3️⃣ 📢 Seç → Post hazırla\n"
        "4️⃣ 🔗 Linki Değiştir → Kendi linkini ekle\n"
        "5️⃣ ✅ Gruba Gönder\n\n"
        "Tüm komutlar: /start",
        parse_mode="HTML"
    )


# ══════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN bulunamadı!")
        sys.exit(1)
    if not GROQ_API_KEY:
        logger.error("❌ GROQ_API_KEY bulunamadı!")
        sys.exit(1)

    logger.info(f"✅ ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
    logger.info(f"✅ Tavily: {'Aktif' if TAVILY_API_KEY else 'Yok (web arama devre dışı)'}")

    app = Application.builder().token(BOT_TOKEN).build()

    for cmd, fn in [
        ("start", start), ("help", help_command),
        ("scan", scan), ("autopick", autopick), ("analyze", analyze),
        ("newairdrop", post_airdrop), ("quickdrop", quick_drop),
        ("scheduledrop", schedule_drop),
        ("setref", set_ref_code), ("clearref", clear_ref), ("showref", show_ref),
        ("broadcast", broadcast), ("boldcast", boldcast), ("pin", pin_message),
        ("translate", translate), ("hashtag", hashtag),
        ("stats", stats), ("status", status),
    ]:
        app.add_handler(CommandHandler(cmd, fn))

    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))

    logger.info("🚀 Polling başlıyor...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
