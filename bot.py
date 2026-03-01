import logging
import sys
import os
import json
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
ADMIN_CHAT_ID       = int(os.getenv("ADMIN_CHAT_ID", "0"))
GROUP_CHAT_ID       = int(os.getenv("GROUP_CHAT_ID", "0"))
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes

# ── State ──────────────────────────────────────────────
ref_code_store = {"code": None}
stats_store    = {"sent": 0, "last": None}
pending_posts  = {}


# ══════════════════════════════════════════════════════
# GROQ API (Ücretsiz)
# ══════════════════════════════════════════════════════

async def gpt_text(prompt: str, max_tokens: int = 800) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",  # Groq'un en iyi ücretsiz modeli
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens,
                "temperature": 0.8
            }
        )
        data = resp.json()
        if "error" in data:
            raise Exception(data["error"]["message"])
        return data["choices"][0]["message"]["content"].strip()


# ══════════════════════════════════════════════════════
# UNSPLASH GÖRSEL
# ══════════════════════════════════════════════════════

FALLBACK_IMAGES = [
    "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=1200",
    "https://images.unsplash.com/photo-1621761191319-c6fb62004040?w=1200",
    "https://images.unsplash.com/photo-1622630998477-20aa696ecb05?w=1200",
    "https://images.unsplash.com/photo-1518546305927-5a555bb7020d?w=1200",
]

async def unsplash_image(query: str) -> str:
    if UNSPLASH_ACCESS_KEY:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://api.unsplash.com/search/photos",
                    params={
                        "query": f"{query} cryptocurrency crypto blockchain",
                        "per_page": 1,
                        "orientation": "landscape",
                        "client_id": UNSPLASH_ACCESS_KEY
                    }
                )
                results = resp.json().get("results", [])
                if results:
                    return results[0]["urls"]["regular"]
        except Exception as e:
            logger.warning(f"Unsplash hatası: {e}")

    # Fallback: sabit görsel listesinden seç
    import hashlib
    idx = int(hashlib.md5(query.encode()).hexdigest(), 16) % len(FALLBACK_IMAGES)
    return FALLBACK_IMAGES[idx]


# ══════════════════════════════════════════════════════
# GPT FONKSİYONLARI
# ══════════════════════════════════════════════════════

async def fetch_real_airdrops() -> list[dict]:
    """CoinGecko + DeFiLlama ücretsiz API ile gerçek proje verisi çek"""
    projects = []
    
    # CoinGecko — trending coinleri çek (ücretsiz, key yok)
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://api.coingecko.com/api/v3/search/trending",
                headers={"accept": "application/json"}
            )
            data = resp.json()
            for item in data.get("coins", [])[:8]:
                c = item["item"]
                projects.append({
                    "name": c.get("name", ""),
                    "symbol": c.get("symbol", ""),
                    "coingecko_id": c.get("id", ""),
                    "thumb": c.get("large", ""),
                    "market_cap_rank": c.get("market_cap_rank", 999),
                })
    except Exception as e:
        logger.warning(f"CoinGecko trending hatası: {e}")

    # DeFiLlama — en yüksek TVL kazançlı protokoller
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get("https://yields.llama.fi/pools")
            pools = resp.json().get("data", [])
            # En yüksek APY'li ilk 5 pool
            top = sorted(
                [p for p in pools if p.get("apy", 0) > 50 and p.get("tvlUsd", 0) > 1_000_000],
                key=lambda x: x.get("apy", 0), reverse=True
            )[:5]
            for p in top:
                projects.append({
                    "name": p.get("project", ""),
                    "symbol": p.get("symbol", ""),
                    "chain": p.get("chain", ""),
                    "apy": round(p.get("apy", 0), 1),
                    "tvl": p.get("tvlUsd", 0),
                    "type": "yield"
                })
    except Exception as e:
        logger.warning(f"DeFiLlama hatası: {e}")

    return projects


async def gpt_find_airdrops() -> list[dict]:
    """Gerçek verilerle zenginleştirilmiş airdrop listesi üret"""
    real_data = await fetch_real_airdrops()
    
    # Gerçek veriyi GPT'ye ver, analiz ettir
    real_summary = ""
    if real_data:
        real_summary = "\n\nŞu an trend olan gerçek projeler (bunları kullanabilirsin):\n"
        for p in real_data[:6]:
            if p.get("type") == "yield":
                real_summary += f"- {p['name']} ({p['symbol']}) — Chain: {p.get('chain','')} APY: %{p.get('apy',0)}\n"
            else:
                real_summary += f"- {p['name']} ({p['symbol']}) — CoinGecko rank: {p.get('market_cap_rank','')}\n"

    prompt = f"""Sen bir kripto para airdrop uzmanısın. 2024-2025 döneminde aktif olan GERÇEK kripto airdroplarından 5 tanesini öner.

KRİTERLER:
- Platform bazlı değil, kripto ekosistemindeki GERÇEK projeler (Layer2, DeFi, DEX, CEX, bridge, wallet)
- Özellikle REFERANS sistemi olan airdroplar (arkadaş davet et → ekstra puan/token kazan)
- Türkiye'den katılılabilen
- Somut katılım adımları olan (görev yap, token al mantığı)
- Örnek türler: borsa airdropları, L2 testnet, DeFi protokolü, NFT mint, galxe/zealy görevi
{real_summary}

Her biri için detaylı bilgi ver. SADECE JSON döndür:
[
  {{
    "name": "Gerçek Proje Adı",
    "category": "DEX/CEX/L2/Bridge/Wallet/DeFi",
    "url": "https://gerçek-resmi-site.com",
    "campaign_url": "https://doğrudan-katılım-linki.com",
    "description": "Projenin ne yaptığı ve neden önemli olduğu (2-3 cümle)",
    "how_to_join": "Adım adım nasıl katılınır",
    "reward": "Tahmini token/$ ödül miktarı",
    "referral": true,
    "referral_bonus": "Referans başına ne kadar bonus",
    "difficulty": "Kolay/Orta/Zor",
    "time_required": "Tahmini süre (ör: 15 dakika)",
    "deadline": "Son tarih veya sürekli",
    "score": 8,
    "score_reason": "Neden bu puan: güvenilirlik + potansiyel"
  }}
]"""

    raw = await gpt_text(prompt, max_tokens=2000)
    raw = raw.strip()
    if "```" in raw:
        parts = raw.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("["):
                raw = part
                break
    raw = raw.strip()
    if not raw.startswith("["):
        start = raw.find("[")
        end   = raw.rfind("]") + 1
        if start != -1:
            raw = raw[start:end]
    return json.loads(raw)



async def gpt_make_post(airdrop: dict, ref: str = None) -> str:
    import re
    score_line = f"Puan: {airdrop.get('score','?')}/10 — {airdrop.get('score_reason','')}" if airdrop.get('score') != '?' else ""
    prompt = f"""Türk kripto topluluğu için bu airdrop hakkında emojili, heyecan verici Telegram duyurusu yaz.
HTML formatı kullan: <b>kalın</b>, <i>italik</i>, <code>kod</code>

Proje: {airdrop['name']}
Açıklama: {airdrop.get('description','')}
Ödül: {airdrop.get('reward','?')}
Zorluk: {airdrop.get('difficulty','?')}
{score_line}

Format: 🚀 Başlık → Giriş → 📋 Adımlar → 💰 Ödül → ⭐ Puan
ÖNEMLİ: Post içine kesinlikle hiçbir URL veya http adresi yazma. Sadece metin ve emoji.
Kısa ve çarpıcı tut."""

    text = await gpt_text(prompt, max_tokens=600)

    # GPT yine de link yazmışsa temizle
    text = re.sub(r'https?://\S+', '', text).strip()

    # Referans kodu
    if ref:
        text += f"\n\n🎯 <b>Referans Kodu:</b> <code>{ref}</code>"

    # Link — kullanıcının girdiği URL varsa ekle, yoksa placeholder
    url = airdrop.get('url', '')
    if url and url not in ('?', '', None):
        text += f"\n\n🔗 <a href='{url}'>👉 Hemen Katıl</a>"
    else:
        text += "\n\n🔗 <b>[ LİNK BURAYA ]</b>"

    text += "\n\n📢 @kriptodropptr"
    return text


async def gpt_score_url(url: str) -> str:
    return await gpt_text(
        f"Bu kripto airdrop projesini Türkçe analiz et ve puanla: {url}\n\n"
        "⭐ Puan /10 | 🔒 Güvenilirlik | 💰 Kazanç | ⚡ Zorluk | ⚠️ Risk | ✅ Artı | ❌ Eksi | 🎯 Tavsiye\n"
        "Emojili ve net yaz.",
        max_tokens=500
    )


# ══════════════════════════════════════════════════════
# YARDIMCI
# ══════════════════════════════════════════════════════

def is_admin(update: Update) -> bool:
    return update.effective_chat.id == ADMIN_CHAT_ID


async def _prepare_and_show(update, ctx: ContextTypes.DEFAULT_TYPE, airdrop: dict):
    ref = ref_code_store.get("code")

    async def reply_text(text, **kwargs):
        if hasattr(update, "message") and update.message:
            return await update.message.reply_text(text, **kwargs)
        return await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=text, **kwargs)

    async def reply_photo(photo, caption, **kwargs):
        if hasattr(update, "message") and update.message:
            return await update.message.reply_photo(photo=photo, caption=caption, **kwargs)
        return await ctx.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=photo, caption=caption, **kwargs)

    status_msg = await reply_text(
        f"⚙️ *{airdrop['name']}* hazırlanıyor...\n✍️ Post yazılıyor...",
        parse_mode="Markdown"
    )

    post_text  = await gpt_make_post(airdrop, ref)

    await status_msg.edit_text(
        f"⚙️ *{airdrop['name']}* hazırlanıyor...\n✅ Post yazıldı\n🖼️ Görsel aranıyor...",
        parse_mode="Markdown"
    )

    image_url = await unsplash_image(airdrop["name"])

    await status_msg.edit_text(
        f"✅ *{airdrop['name']}* hazır! Önizleme geliyor...",
        parse_mode="Markdown"
    )

    await reply_text("─── 👁️ ÖNİZLEME ───")

    try:
        preview_msg = await reply_photo(image_url, post_text, parse_mode="HTML")
    except Exception:
        preview_msg = await reply_text(post_text, parse_mode="HTML", disable_web_page_preview=False)

    key = str(preview_msg.message_id)
    pending_posts[key] = {"text": post_text, "image_url": image_url, "name": airdrop["name"]}

    score_info = f"⭐ {airdrop.get('score','?')}/10 — {airdrop.get('score_reason','')}\n\n" if airdrop.get("score") not in ("?", None) else ""

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Gruba Gönder", callback_data=f"send|{key}"),
        InlineKeyboardButton("✏️ Yeniden Yaz",  callback_data=f"rewrite|{key}"),
        InlineKeyboardButton("❌ İptal",          callback_data=f"cancel|{key}")
    ]])

    await reply_text(
        f"⬆️ *{airdrop['name']}* önizlemesi\n\n{score_info}Ne yapmak istersin?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ══════════════════════════════════════════════════════
# KOMUTLAR
# ══════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_chat.id
    adm  = uid == ADMIN_CHAT_ID
    ref  = ref_code_store.get("code") or "Ayarlanmamış"

    msg = "🪂 *KriptoDropptr Bot*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"{'✅ Admin paneline hoş geldin!' if adm else '⛔ Yetkisiz erişim.'}\n\n"

    if adm:
        msg += (
            f"📊 *Durum*\n├ Ref Kodu: `{ref}`\n├ Gönderim: `{stats_store['sent']}`\n└ Son: `{stats_store['last'] or '—'}`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🤖 *AI AIRDROP MOTORU*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/scan → AI 5 airdrop tara, seç\n"
            "/autopick → AI en iyisini seçer, hazırlar\n"
            "/analyze `<url>` → Projeyi puanla\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📢 *PAYLAŞIM*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/newairdrop `<proje>` `<url>`\n"
            "/quickdrop `<url>`\n"
            "/scheduledrop `<proje>` `<url>` `<dk>`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🔗 *REFERANS*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/setref `<kod>` · /clearref · /showref\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📣 *MESAJ & ARAÇLAR*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/broadcast · /boldcast · /pin\n"
            "/translate · /hashtag\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📈 *İSTATİSTİK*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/stats · /status"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    msg = await update.message.reply_text(
        "🔍 Kripto ekosistemi taranıyor...\n"
        "📡 CoinGecko & DeFiLlama verisi çekiliyor...\n"
        "🤖 AI analiz yapıyor..."
    )
    try:
        airdrops = await gpt_find_airdrops()
        ctx.user_data["scan_results"] = airdrops
        await msg.delete()

        for i, a in enumerate(airdrops):
            score     = a.get("score", "?")
            stars     = "⭐" * min(int(score), 10) if isinstance(score, int) else "⭐"
            ref_badge = "🔁 Referans Sistemi VAR" if a.get("referral") else "➖ Referans yok"
            cat       = a.get("category", "")
            deadline  = a.get("deadline", "Belirtilmemiş")
            time_req  = a.get("time_required", "?")
            campaign  = a.get("campaign_url") or a.get("url", "")

            card = (
                f"{'━'*30}\n"
                f"🪂 <b>{i+1}. {a['name']}</b>  <code>[{cat}]</code>\n"
                f"{'━'*30}\n\n"
                f"📝 {a.get('description','')}\n\n"
                f"📋 <b>Nasıl Katılınır:</b>\n{a.get('how_to_join','')}\n\n"
                f"💰 <b>Ödül:</b> {a.get('reward','?')}\n"
                f"{ref_badge}\n"
            )
            if a.get("referral_bonus"):
                card += f"🎁 <b>Referans Bonusu:</b> {a['referral_bonus']}\n"
            card += (
                f"⚡ <b>Zorluk:</b> {a.get('difficulty','?')}  •  "
                f"⏱ <b>Süre:</b> {time_req}\n"
                f"📅 <b>Son Tarih:</b> {deadline}\n\n"
                f"{stars} <b>Puan: {score}/10</b> — {a.get('score_reason','')}\n"
            )
            if campaign:
                card += f"\n🔗 <a href='{campaign}'>Kampanya Sayfası</a>"

            # Her airdrop için ayrı kart + buton
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton(f"📢 Bu Airdropu Paylaş ({score}/10)", callback_data=f"prepare|{i}")
            ]])
            await update.message.reply_text(
                card,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_markup=keyboard
            )

        # Alt özet + autopick butonu
        await update.message.reply_text(
            f"✅ <b>{len(airdrops)} airdrop bulundu.</b>\n\n"
            "Yukarıdan istediğini seç veya AI en iyisini seçsin:",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🤖 AI En İyisini Seçsin", callback_data="autopick_from_scan")
            ]])
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}\n\nGroq API veya JSON parse hatası olabilir, tekrar dene.")


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
    await _prepare_and_show(update, ctx, {"name": ctx.args[0], "url": ctx.args[1], "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""})


async def quick_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /quickdrop <url>"); return
    url = ctx.args[0]
    domain = url.split("/")[2].replace("www.", "").split(".")[0].capitalize()
    await _prepare_and_show(update, ctx, {"name": domain, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""})


async def analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analyze <url>"); return
    await update.message.reply_text("🔍 Analiz ediliyor...")
    result = await gpt_score_url(ctx.args[0])
    await update.message.reply_text(f"📊 *Analiz:*\n\n{result}", parse_mode="Markdown")


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
            airdrop = {"name": project_name, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
            text      = await gpt_make_post(airdrop, ref)
            image_url = await unsplash_image(project_name)
            try:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=image_url, caption=text, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"✅ Zamanlı gönderi: *{project_name}*", parse_mode="Markdown")
        except Exception as e:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Zamanlı gönderi hatası: {e}")

    ctx.job_queue.run_once(send_later, when=minutes * 60)
    await update.message.reply_text(f"⏰ *{project_name}* {minutes} dakika sonra gönderilecek!", parse_mode="Markdown")


async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data.startswith("prepare|"):
        idx = int(data.split("|")[1])
        airdrops = ctx.user_data.get("scan_results", [])
        if idx < len(airdrops):
            await query.edit_message_reply_markup(None)
            airdrop = airdrops[idx]
            # campaign_url veya url varsa direkt kullan, yoksa sor
            url = airdrop.get("campaign_url") or airdrop.get("url", "")
            if url and url.startswith("http"):
                airdrop["url"] = url
                ctx.user_data["pending_airdrop"] = None
                await _prepare_and_show(query, ctx, airdrop)
            else:
                ctx.user_data["pending_airdrop"] = airdrop
                ctx.user_data["awaiting_link"] = True
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🔗 <b>{airdrop['name']}</b> için katılım linkini gönder:\n\n"
                         f"(Kampanya veya katılım linkini yapıştır)",
                    parse_mode="HTML"
                )
        return

    if data == "autopick_from_scan":
        airdrops = ctx.user_data.get("scan_results", [])
        if airdrops:
            best = max(airdrops, key=lambda x: x.get("score", 0))
            await query.edit_message_reply_markup(None)
            url = best.get("campaign_url") or best.get("url", "")
            if url and url.startswith("http"):
                best["url"] = url
                ctx.user_data["pending_airdrop"] = None
                await _prepare_and_show(query, ctx, best)
            else:
                ctx.user_data["pending_airdrop"] = best
                ctx.user_data["awaiting_link"] = True
                await ctx.bot.send_message(
                    chat_id=ADMIN_CHAT_ID,
                    text=f"🤖 AI en iyi projeyi seçti: <b>{best['name']}</b>\n\n"
                         f"🔗 Katılım linkini gönder:",
                    parse_mode="HTML"
                )
        return

    if data.startswith("send|"):
        key  = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await query.edit_message_text("❌ Post bulunamadı, tekrar dene."); return
        try:
            try:
                await ctx.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=post["image_url"], caption=post["text"], parse_mode="HTML")
            except Exception:
                await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=post["text"], parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            pending_posts.pop(key, None)
            await query.edit_message_text(f"✅ *{post['name']}* gruba gönderildi!", parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Gönderilemedi: {e}")
        return

    if data.startswith("rewrite|"):
        key  = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await query.edit_message_text("❌ Post bulunamadı."); return
        await query.edit_message_text("✍️ Yeniden yazılıyor...")
        try:
            new_text = await gpt_text(
                f"Bu Telegram airdrop postunu daha çarpıcı ve emojili yeniden yaz, HTML formatını koru:\n\n{post['text']}",
                max_tokens=700
            )
            pending_posts[key]["text"] = new_text
            keyboard = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Gruba Gönder", callback_data=f"send|{key}"),
                InlineKeyboardButton("✏️ Tekrar",       callback_data=f"rewrite|{key}"),
                InlineKeyboardButton("❌ İptal",          callback_data=f"cancel|{key}")
            ]])
            try:
                await ctx.bot.send_photo(chat_id=ADMIN_CHAT_ID, photo=post["image_url"], caption=new_text, parse_mode="HTML", reply_markup=keyboard)
            except Exception:
                await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=new_text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=False)
        except Exception as e:
            await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Yeniden yazma hatası: {e}")
        return

    if data.startswith("cancel|"):
        pending_posts.pop(data.split("|")[1], None)
        await query.edit_message_text("❌ İptal edildi.")
        return


# ── Diğer komutlar ─────────────────────────────────────
async def set_ref_code(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /setref <kod>"); return
    ref_code_store["code"] = ctx.args[0]
    await update.message.reply_text(f"✅ Ref kodu: `{ctx.args[0]}`", parse_mode="Markdown")

async def clear_ref(update, ctx):
    if not is_admin(update): return
    ref_code_store["code"] = None
    await update.message.reply_text("🗑️ Referans kodu temizlendi.")

async def show_ref(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(f"🔗 Aktif ref: `{ref_code_store.get('code') or 'Yok'}`", parse_mode="Markdown")

async def broadcast(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /broadcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=" ".join(ctx.args))
    await update.message.reply_text("✅ Gönderildi.")

async def boldcast(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /boldcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"📢 <b>{' '.join(ctx.args)}</b>\n\n— @kriptodropptr", parse_mode="HTML")
    await update.message.reply_text("✅ Kalın duyuru gönderildi.")

async def pin_message(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /pin <mesaj>"); return
    sent = await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"📌 <b>{' '.join(ctx.args)}</b>\n\n📢 @kriptodropptr", parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent.message_id)
        await update.message.reply_text("✅ Sabitlendi.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Gönderdim ama sabitlemedim: {e}")

async def translate(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /translate <metin>"); return
    result = await gpt_text(f"Türkçeye çevir ve özetle:\n\n{' '.join(ctx.args)}", max_tokens=300)
    await update.message.reply_text(f"🇹🇷 *Çeviri:*\n\n{result}", parse_mode="Markdown")

async def hashtag(update, ctx):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /hashtag <proje>"); return
    result = await gpt_text(f"{' '.join(ctx.args)} kripto projesi için 10 hashtag üret.", max_tokens=150)
    await update.message.reply_text(f"#️⃣ *Hashtagler:*\n\n{result}", parse_mode="Markdown")

async def stats(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(
        f"📈 *İstatistikler*\n\n📤 Toplam: `{stats_store['sent']}`\n🕐 Son: `{stats_store['last'] or '—'}`\n🔗 Ref: `{ref_code_store.get('code') or 'Yok'}`",
        parse_mode="Markdown"
    )

async def status(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(
        f"🟢 *Bot Aktif*\n\n🤖 Model: Llama 3.3 70B (Groq - Ücretsiz)\n"
        f"🖼️ Görsel: {'Unsplash ✅' if UNSPLASH_ACCESS_KEY else 'Varsayılan'}\n"
        f"📡 Grup: `{GROUP_CHAT_ID}`\n👤 Admin: `{ADMIN_CHAT_ID}`\n🔗 Ref: `{ref_code_store.get('code') or 'Yok'}`",
        parse_mode="Markdown"
    )


async def link_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Kullanıcının gönderdiği linki yakala, post hazırla"""
    if update.effective_chat.id != ADMIN_CHAT_ID:
        return
    if not ctx.user_data.get("awaiting_link"):
        return

    text = update.message.text.strip()

    # Link mi kontrol et
    if not text.startswith("http"):
        await update.message.reply_text("⚠️ Geçerli bir link gönder (http ile başlamalı)")
        return

    ctx.user_data["awaiting_link"] = False
    airdrop = ctx.user_data.get("pending_airdrop", {})
    airdrop["url"] = text
    ctx.user_data["pending_airdrop"] = None

    await update.message.reply_text(f"✅ Link alındı! Post hazırlanıyor...")
    await _prepare_and_show(update, ctx, airdrop)


async def help_command(update, ctx):
    if not is_admin(update): return
    await update.message.reply_text(
        "💡 *Hızlı Başlangıç:*\n\n"
        "1️⃣ /scan → AI 5 airdrop bulsun\n"
        "2️⃣ /autopick → AI en iyisini seçsin\n"
        "3️⃣ Önizlemeyi onayla → Gruba gider\n\n"
        "Tüm komutlar: /start",
        parse_mode="Markdown"
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
    logger.info(f"✅ Groq AI aktif")

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
    # Link mesajlarını yakala (komut değil, düz mesaj)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, link_handler))

    logger.info("🚀 Polling başlıyor...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
