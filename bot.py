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

BOT_TOKEN         = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY", "")
ADMIN_CHAT_ID     = int(os.getenv("ADMIN_CHAT_ID", "0"))
GROUP_CHAT_ID     = int(os.getenv("GROUP_CHAT_ID", "0"))
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ── State ──────────────────────────────────────────────
ref_code_store = {"code": None}
stats_store    = {"sent": 0, "last": None}
pending_posts  = {}


# ══════════════════════════════════════════════════════
# UNSPLASH GÖRSEL
# ══════════════════════════════════════════════════════

async def unsplash_image(query: str) -> str | None:
    if not UNSPLASH_ACCESS_KEY:
        logger.warning("UNSPLASH_ACCESS_KEY tanımlı değil, varsayılan görsel kullanılıyor.")
        return "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=1200"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                "https://api.unsplash.com/search/photos",
                params={
                    "query": f"{query} cryptocurrency blockchain",
                    "per_page": 1,
                    "orientation": "landscape",
                    "client_id": UNSPLASH_ACCESS_KEY
                }
            )
            data = resp.json()
            results = data.get("results", [])
            if results:
                return results[0]["urls"]["regular"]
    except Exception as e:
        logger.warning(f"Unsplash hatası: {e}")

    # Fallback: sabit kripto görseli
    return "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=1200"


# ══════════════════════════════════════════════════════
# GPT YARDIMCILARI
# ══════════════════════════════════════════════════════

async def gpt_text(prompt: str, max_tokens: int = 800) -> str:
    response = await openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=max_tokens
    )
    return response.choices[0].message.content.strip()


async def gpt_find_airdrops() -> list[dict]:
    prompt = """
Sen bir kripto para airdrop uzmanısın. Şu an aktif veya yakında başlayacak,
Türkiye'den katılılabilecek 5 adet dikkat çekici kripto airdrop öner.

Her biri için JSON formatında dön:
[
  {
    "name": "Proje Adı",
    "url": "https://...",
    "description": "2 cümle açıklama",
    "reward": "Tahmini ödül bilgisi",
    "difficulty": "Kolay/Orta/Zor",
    "score": 8,
    "score_reason": "Neden bu puanı aldı"
  }
]

Sadece JSON döndür, başka hiçbir şey yazma. Gerçekçi projeler seç.
"""
    raw = await gpt_text(prompt, max_tokens=1200)
    raw = raw.strip().strip("```").strip()
    if raw.startswith("json"):
        raw = raw[4:].strip()
    return json.loads(raw)


async def gpt_make_post(airdrop: dict, ref: str = None) -> str:
    prompt = f"""
Şu kripto airdrop için Türkçe, emojili, heyecan verici bir Telegram duyurusu yaz.
HTML formatı kullan (<b>, <i>, <code> kabul edilir).

Proje: {airdrop['name']}
URL: {airdrop['url']}
Açıklama: {airdrop.get('description', '')}
Tahmini Ödül: {airdrop.get('reward', '?')}
Zorluk: {airdrop.get('difficulty', '?')}
Puan: {airdrop.get('score', '?')}/10

Format:
🚀 Çarpıcı başlık
Kısa giriş (1-2 cümle)
📋 Nasıl Katılınır (adım adım)
💰 Ödül bilgisi
⭐ Puan ve kısa yorum
Link ve kanal etiketi sona gelsin.
"""
    text = await gpt_text(prompt, max_tokens=700)
    if ref:
        text += f"\n\n🎯 <b>Referans Kodu:</b> <code>{ref}</code>"
    text += f"\n\n🔗 <a href='{airdrop['url']}'>👉 Hemen Katıl</a>"
    text += "\n\n📢 @kriptodropptr"
    return text


async def gpt_score_url(url: str) -> str:
    return await gpt_text(
        f"Bu kripto airdrop projesini Türkçe analiz et ve puanla: {url}\n\n"
        "⭐ Genel Puan /10, 🔒 Güvenilirlik, 💰 Kazanç Potansiyeli, "
        "⚡ Zorluk, ⚠️ Riskler, ✅ Artılar, ❌ Eksiler, 🎯 Tavsiye. "
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

    # update hem Update hem CallbackQuery olabilir
    reply = update.message.reply_text if hasattr(update, "message") and update.message else \
            update.reply_text if hasattr(update, "reply_text") else \
            (lambda *a, **k: None)

    status_msg = await reply(
        f"⚙️ *{airdrop['name']}* hazırlanıyor...\n✍️ Post yazılıyor...",
        parse_mode="Markdown"
    )

    post_text = await gpt_make_post(airdrop, ref)

    await status_msg.edit_text(
        f"⚙️ *{airdrop['name']}* hazırlanıyor...\n✅ Post yazıldı\n🖼️ Görsel aranıyor...",
        parse_mode="Markdown"
    )

    image_url = await unsplash_image(airdrop["name"])

    await status_msg.edit_text(
        f"✅ *{airdrop['name']}* hazır! Önizleme geliyor...",
        parse_mode="Markdown"
    )

    await reply("─── 👁️ ÖNİZLEME ───")

    try:
        preview_msg = await reply(
            None,
            photo=image_url,
            caption=post_text,
            parse_mode="HTML"
        ) if False else None  # foto için aşağıda özel gönderim

        if hasattr(update, "message") and update.message:
            preview_msg = await update.message.reply_photo(
                photo=image_url, caption=post_text, parse_mode="HTML"
            )
        else:
            preview_msg = await ctx.bot.send_photo(
                chat_id=ADMIN_CHAT_ID, photo=image_url, caption=post_text, parse_mode="HTML"
            )
    except Exception:
        if hasattr(update, "message") and update.message:
            preview_msg = await update.message.reply_text(post_text, parse_mode="HTML", disable_web_page_preview=False)
        else:
            preview_msg = await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=post_text, parse_mode="HTML", disable_web_page_preview=False)

    key = str(preview_msg.message_id)
    pending_posts[key] = {"text": post_text, "image_url": image_url, "name": airdrop["name"]}

    score_line = f"⭐ Puan: {airdrop.get('score','?')}/10 — {airdrop.get('score_reason','')}\n\n" if airdrop.get("score") != "?" else ""

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Gruba Gönder", callback_data=f"send|{key}"),
        InlineKeyboardButton("✏️ Yeniden Yaz",  callback_data=f"rewrite|{key}"),
        InlineKeyboardButton("❌ İptal",          callback_data=f"cancel|{key}")
    ]])

    send = update.message.reply_text if hasattr(update, "message") and update.message else \
           lambda *a, **k: ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, *a, **k)

    await send(
        f"⬆️ *{airdrop['name']}* önizlemesi\n\n{score_line}Ne yapmak istersin?",
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
    sent = stats_store["sent"]
    last = stats_store["last"] or "—"

    msg = "🪂 *KriptoDropptr Bot*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    msg += f"{'✅ Admin paneline hoş geldin!' if adm else '⛔ Yetkisiz erişim.'}\n\n"

    if adm:
        msg += (
            f"📊 *Durum*\n├ Ref Kodu: `{ref}`\n├ Gönderim: `{sent}`\n└ Son: `{last}`\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🤖 *AI AIRDROP MOTORU*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/scan → AI 5 airdrop tara, listele, seç\n"
            "/autopick → AI en iyisini seçer, hazırlar, sunar\n"
            "/analyze `<url>` → Projeyi puanla\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📢 *PAYLAŞIM*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/newairdrop `<proje>` `<url>` → Özet + görsel + onayla\n"
            "/quickdrop `<url>` → Sadece URL ver, AI halleder\n"
            "/scheduledrop `<proje>` `<url>` `<dk>` → Zamanlı gönder\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n🔗 *REFERANS*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/setref `<kod>` · /clearref · /showref\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📣 *MESAJ & ARAÇLAR*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/broadcast `<mesaj>` → Gruba düz mesaj\n"
            "/boldcast `<mesaj>` → Kalın duyuru\n"
            "/pin `<mesaj>` → Gönder ve sabitle\n"
            "/translate `<metin>` → Türkçeye çevir\n"
            "/hashtag `<proje>` → Hashtag üret\n\n"
            "━━━━━━━━━━━━━━━━━━━━━\n📈 *İSTATİSTİK*\n━━━━━━━━━━━━━━━━━━━━━\n"
            "/stats · /status"
        )
    await update.message.reply_text(msg, parse_mode="Markdown")


async def scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text("🔍 AI 5 airdrop tarıyor...")
    try:
        airdrops = await gpt_find_airdrops()
        ctx.user_data["scan_results"] = airdrops

        msg = "🪂 *Bulunan Airdroplar:*\n\n"
        buttons = []
        for i, a in enumerate(airdrops):
            score = a.get("score", "?")
            stars = "⭐" * min(int(score), 10) if isinstance(score, int) else "📌"
            msg += f"*{i+1}. {a['name']}*\n├ {a['description']}\n├ 💰 {a['reward']}\n├ ⚡ {a['difficulty']}\n└ {stars} {score}/10\n\n"
            buttons.append([InlineKeyboardButton(
                f"{i+1}. {a['name']} ({score}/10) → Hazırla",
                callback_data=f"prepare|{i}"
            )])
        buttons.append([InlineKeyboardButton("🤖 AI En İyisini Seçsin", callback_data="autopick_from_scan")])

        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(buttons))
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
    airdrop = {"name": ctx.args[0], "url": ctx.args[1], "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
    await _prepare_and_show(update, ctx, airdrop)


async def quick_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /quickdrop <url>"); return
    url = ctx.args[0]
    domain = url.split("/")[2].replace("www.", "").split(".")[0].capitalize()
    airdrop = {"name": domain, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
    await _prepare_and_show(update, ctx, airdrop)


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
    try:
        minutes = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("Dakika sayı olmalı."); return
    ref = ref_code_store.get("code")

    async def send_later(context):
        try:
            airdrop = {"name": project_name, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
            text = await gpt_make_post(airdrop, ref)
            image_url = await unsplash_image(project_name)
            try:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=image_url, caption=text, parse_mode="HTML")
            except Exception:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"✅ Zamanlı gönderi yapıldı: *{project_name}*", parse_mode="Markdown")
        except Exception as e:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Zamanlı gönderi hatası: {e}")

    ctx.job_queue.run_once(send_later, when=minutes * 60)
    await update.message.reply_text(f"⏰ *{project_name}* {minutes} dakika sonra gönderilecek!", parse_mode="Markdown")


# ── Callback ───────────────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("prepare|"):
        idx = int(data.split("|")[1])
        airdrops = ctx.user_data.get("scan_results", [])
        if idx < len(airdrops):
            await query.edit_message_reply_markup(None)
            await _prepare_and_show(query, ctx, airdrops[idx])
        return

    if data == "autopick_from_scan":
        airdrops = ctx.user_data.get("scan_results", [])
        if airdrops:
            best = max(airdrops, key=lambda x: x.get("score", 0))
            await query.edit_message_reply_markup(None)
            await _prepare_and_show(query, ctx, best)
        return

    if data.startswith("send|"):
        key = data.split("|")[1]
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
        key = data.split("|")[1]
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
        key = data.split("|")[1]
        pending_posts.pop(key, None)
        await query.edit_message_text("❌ İptal edildi.")
        return


# ── Diğer komutlar ─────────────────────────────────────
async def set_ref_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /setref <kod>"); return
    ref_code_store["code"] = ctx.args[0]
    await update.message.reply_text(f"✅ Ref kodu: `{ctx.args[0]}`", parse_mode="Markdown")

async def clear_ref(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    ref_code_store["code"] = None
    await update.message.reply_text("🗑️ Referans kodu temizlendi.")

async def show_ref(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(f"🔗 Aktif ref: `{ref_code_store.get('code') or 'Yok'}`", parse_mode="Markdown")

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /broadcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=" ".join(ctx.args))
    await update.message.reply_text("✅ Gönderildi.")

async def boldcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /boldcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"📢 <b>{' '.join(ctx.args)}</b>\n\n— @kriptodropptr", parse_mode="HTML")
    await update.message.reply_text("✅ Kalın duyuru gönderildi.")

async def pin_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /pin <mesaj>"); return
    sent = await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"📌 <b>{' '.join(ctx.args)}</b>\n\n📢 @kriptodropptr", parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent.message_id)
        await update.message.reply_text("✅ Sabitlendi.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Gönderdim ama sabitlemedim: {e}")

async def translate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /translate <metin>"); return
    result = await gpt_text(f"Türkçeye çevir ve özetle:\n\n{' '.join(ctx.args)}", max_tokens=300)
    await update.message.reply_text(f"🇹🇷 *Çeviri:*\n\n{result}", parse_mode="Markdown")

async def hashtag(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args: await update.message.reply_text("Kullanım: /hashtag <proje>"); return
    result = await gpt_text(f"{' '.join(ctx.args)} kripto projesi için 10 hashtag üret.", max_tokens=150)
    await update.message.reply_text(f"#️⃣ *Hashtagler:*\n\n{result}", parse_mode="Markdown")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        f"📈 *İstatistikler*\n\n📤 Toplam: `{stats_store['sent']}`\n🕐 Son: `{stats_store['last'] or '—'}`\n🔗 Ref: `{ref_code_store.get('code') or 'Yok'}`",
        parse_mode="Markdown"
    )

async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    unsplash_status = "✅ Aktif" if UNSPLASH_ACCESS_KEY else "⚠️ Key yok (varsayılan görsel)"
    await update.message.reply_text(
        f"🟢 *Bot Aktif*\n\n🤖 Model: GPT-4o-mini\n🖼️ Görsel: Unsplash {unsplash_status}\n"
        f"📡 Grup: `{GROUP_CHAT_ID}`\n👤 Admin: `{ADMIN_CHAT_ID}`\n🔗 Ref: `{ref_code_store.get('code') or 'Yok'}`",
        parse_mode="Markdown"
    )

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        "💡 *Hızlı Başlangıç:*\n\n"
        "1️⃣ /scan → AI 5 airdrop bulsun, seç\n"
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
    logger.info(f"✅ ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
    logger.info(f"✅ GROUP_CHAT_ID: {GROUP_CHAT_ID}")
    logger.info(f"✅ Unsplash: {'Aktif' if UNSPLASH_ACCESS_KEY else 'Varsayılan görsel'}")

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

    logger.info("🚀 Polling başlıyor...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
