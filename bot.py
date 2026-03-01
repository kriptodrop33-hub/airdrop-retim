import logging
import sys
import os
import json
import base64
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

BOT_TOKEN      = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ADMIN_CHAT_ID  = int(os.getenv("ADMIN_CHAT_ID", "0"))
GROUP_CHAT_ID  = int(os.getenv("GROUP_CHAT_ID", "0"))

from openai import AsyncOpenAI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# ── State ──────────────────────────────────────────────
ref_code_store = {"code": None}
stats_store    = {"sent": 0, "last": None}
pending_posts  = {}   # key: admin_msg_id → value: {text, image_url}


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
    """GPT'den güncel airdrop listesi iste"""
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

Sadece JSON döndür, başka hiçbir şey yazma. Gerçekçi ve güncel projeler seç.
"""
    raw = await gpt_text(prompt, max_tokens=1200)
    # JSON bloğunu temizle
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    raw = raw.strip().rstrip("```").strip()
    return json.loads(raw)


async def gpt_make_post(airdrop: dict, ref: str = None) -> str:
    """Airdrop için Telegram post metni üret"""
    prompt = f"""
Şu kripto airdrop için Türkçe, emojili, heyecan verici bir Telegram duyurusu yaz.
HTML formatı kullan (<b>, <i>, <code> kabul edilir).

Proje: {airdrop['name']}
URL: {airdrop['url']}
Açıklama: {airdrop['description']}
Tahmini Ödül: {airdrop['reward']}
Zorluk: {airdrop['difficulty']}
Puan: {airdrop['score']}/10
Puan Sebebi: {airdrop['score_reason']}

Format:
🚀 Başlık
Kısa çarpıcı giriş
📋 Nasıl Katılınır (adımlar)
💰 Ödül bilgisi
⭐ Puan ve yorum
{'🔗 Referans: ' + ref if ref else ''}
Link ve kanal etiketi sona gelsin.
"""
    text = await gpt_text(prompt, max_tokens=700)
    if ref:
        text += f"\n\n🎯 <b>Referans Kodu:</b> <code>{ref}</code>"
    text += f"\n\n🔗 <a href='{airdrop['url']}'>👉 Hemen Katıl</a>"
    text += "\n\n📢 @kriptodropptr"
    return text


async def gpt_score_url(url: str) -> str:
    """Verilen URL'yi analiz et ve puanla"""
    prompt = f"""
Bu kripto airdrop projesini analiz et ve puanla: {url}

Şunları değerlendir:
⭐ Genel Puan: X/10
🔒 Güvenilirlik: 
💰 Kazanç Potansiyeli:
⚡ Zorluk Seviyesi:
⚠️ Risk Faktörleri:
✅ Artıları:
❌ Eksileri:
🎯 Tavsiyem:

Türkçe, emojili ve net yaz.
"""
    return await gpt_text(prompt, max_tokens=500)


async def dalle_generate_image(project_name: str) -> str | None:
    """DALL-E ile proje için görsel üret, base64 döndür"""
    try:
        prompt = (
            f"Futuristic cryptocurrency airdrop promotional banner for '{project_name}'. "
            "Dark background with glowing neon blue and gold colors, crypto coins flying, "
            "blockchain network visualization, professional and eye-catching. No text."
        )
        response = await openai_client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
            response_format="url"
        )
        return response.data[0].url
    except Exception as e:
        logger.warning(f"DALL-E görsel üretilemedi: {e}")
        return None


# ══════════════════════════════════════════════════════
# ADMIN KONTROL
# ══════════════════════════════════════════════════════

def is_admin(update: Update) -> bool:
    return update.effective_chat.id == ADMIN_CHAT_ID


# ══════════════════════════════════════════════════════
# KOMUTLAR
# ══════════════════════════════════════════════════════

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    is_adm = uid == ADMIN_CHAT_ID
    ref  = ref_code_store.get("code") or "Ayarlanmamış"
    sent = stats_store["sent"]
    last = stats_store["last"] or "—"

    msg = (
        "🪂 *KriptoDropptr Bot*\n"
        "━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{'✅ Admin paneline hoş geldin!' if is_adm else '⛔ Yetkisiz erişim.'}\n\n"
    )

    if is_adm:
        msg += (
            "📊 *Durum*\n"
            f"├ Aktif Ref Kodu: `{ref}`\n"
            f"├ Toplam Gönderim: `{sent}`\n"
            f"└ Son Paylaşım: `{last}`\n\n"

            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 *AI AIRDROP MOTORU*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "/scan → AI airdrop tara, listele, seç\n"
            "/autopick → AI en iyi 1 airdropu seçer, hazırlar, sana sunar\n"
            "/analyze `<url>` → URL'yi analiz et, puanla\n\n"

            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📢 *AIRDROP PAYLAŞIM*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "/newairdrop `<proje>` `<url>` → AI özet + görsel + gruba gönder\n"
            "/quickdrop `<url>` → Sadece URL ver, AI halleder\n"
            "/preview `<proje>` `<url>` → Önce sana göster, onayla\n"
            "/scheduledrop `<proje>` `<url>` `<dk>` → Zamanlı gönder\n\n"

            "━━━━━━━━━━━━━━━━━━━━━\n"
            "🔗 *REFERANS*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "/setref `<kod>` → Referans kodunu ayarla\n"
            "/clearref → Referans kodunu sıfırla\n"
            "/showref → Aktif kodu göster\n\n"

            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📣 *MESAJ & ARAÇLAR*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "/broadcast `<mesaj>` → Gruba düz mesaj\n"
            "/boldcast `<mesaj>` → Kalın duyuru\n"
            "/pin `<mesaj>` → Gönder ve sabitle\n"
            "/translate `<metin>` → Türkçeye çevir\n"
            "/hashtag `<proje>` → Hashtag üret\n\n"

            "━━━━━━━━━━━━━━━━━━━━━\n"
            "📈 *İSTATİSTİK*\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "/stats → Gönderim istatistikleri\n"
            "/status → Bot durum raporu\n"
        )

    await update.message.reply_text(msg, parse_mode="Markdown")


# ── /scan ──────────────────────────────────────────────
async def scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text("🔍 AI airdrop tarıyor, 5 proje buluyor...")
    try:
        airdrops = await gpt_find_airdrops()
        ctx.user_data["scan_results"] = airdrops

        msg = "🪂 *Bulunan Airdroplar:*\n\n"
        buttons = []
        for i, a in enumerate(airdrops):
            score = a.get("score", "?")
            diff  = a.get("difficulty", "?")
            stars = "⭐" * min(int(score), 5) if isinstance(score, int) else "⭐"
            msg  += f"*{i+1}. {a['name']}*\n"
            msg  += f"├ {a['description']}\n"
            msg  += f"├ 💰 {a['reward']}\n"
            msg  += f"├ ⚡ Zorluk: {diff}\n"
            msg  += f"└ {stars} Puan: {score}/10\n\n"
            buttons.append([InlineKeyboardButton(
                f"{'⭐'*min(int(score),5) if isinstance(score,int) else '📌'} {i+1}. {a['name']} → Hazırla",
                callback_data=f"prepare|{i}"
            )])

        buttons.append([InlineKeyboardButton("🤖 AI En İyisini Seçsin", callback_data="autopick_from_scan")])

        await update.message.reply_text(
            msg,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")


# ── /autopick ─────────────────────────────────────────
async def autopick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text("🤖 AI en iyi airdropu seçiyor ve hazırlıyor...")
    try:
        airdrops = await gpt_find_airdrops()
        # En yüksek puanlıyı seç
        best = max(airdrops, key=lambda x: x.get("score", 0))
        await _prepare_and_show(update, ctx, best)
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")


async def _prepare_and_show(update: Update, ctx: ContextTypes.DEFAULT_TYPE, airdrop: dict):
    """Airdrop için post + görsel üret, adminine önizleme sun"""
    ref = ref_code_store.get("code")

    status_msg = await update.message.reply_text(
        f"⚙️ *{airdrop['name']}* hazırlanıyor...\n\n"
        f"✍️ AI post yazıyor...",
        parse_mode="Markdown"
    )

    # Post metni üret
    post_text = await gpt_make_post(airdrop, ref)

    await status_msg.edit_text(
        f"⚙️ *{airdrop['name']}* hazırlanıyor...\n\n"
        f"✅ Post yazıldı\n"
        f"🎨 DALL-E görsel üretiyor...",
        parse_mode="Markdown"
    )

    # Görsel üret
    image_url = await dalle_generate_image(airdrop["name"])

    await status_msg.edit_text(
        f"✅ *{airdrop['name']}* hazır! Önizleme geliyor...",
        parse_mode="Markdown"
    )

    # Önizleme gönder
    await update.message.reply_text("─── 👁️ ÖNİZLEME ───")

    preview_msg = None
    if image_url:
        try:
            preview_msg = await update.message.reply_photo(
                photo=image_url,
                caption=post_text,
                parse_mode="HTML"
            )
        except Exception:
            preview_msg = await update.message.reply_text(post_text, parse_mode="HTML", disable_web_page_preview=False)
    else:
        preview_msg = await update.message.reply_text(post_text, parse_mode="HTML", disable_web_page_preview=False)

    # Pending'e kaydet
    pending_key = str(preview_msg.message_id)
    pending_posts[pending_key] = {
        "text": post_text,
        "image_url": image_url,
        "name": airdrop["name"]
    }

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Gruba Gönder", callback_data=f"send|{pending_key}"),
            InlineKeyboardButton("✏️ Yeniden Yaz",  callback_data=f"rewrite|{pending_key}"),
            InlineKeyboardButton("❌ İptal",          callback_data=f"cancel|{pending_key}")
        ]
    ])
    await update.message.reply_text(
        f"⬆️ *{airdrop['name']}* önizlemesi\n"
        f"⭐ Puan: {airdrop.get('score','?')}/10 — {airdrop.get('score_reason','')}\n\n"
        "Ne yapmak istersin?",
        parse_mode="Markdown",
        reply_markup=keyboard
    )


# ── Callback handler ───────────────────────────────────
async def callback_handler(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # Scan listesinden seçim
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

    # Gönder
    if data.startswith("send|"):
        key = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await query.edit_message_text("❌ Post bulunamadı, tekrar dene.")
            return
        try:
            if post.get("image_url"):
                await ctx.bot.send_photo(
                    chat_id=GROUP_CHAT_ID,
                    photo=post["image_url"],
                    caption=post["text"],
                    parse_mode="HTML"
                )
            else:
                await ctx.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=post["text"],
                    parse_mode="HTML",
                    disable_web_page_preview=False
                )
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            pending_posts.pop(key, None)
            await query.edit_message_text(f"✅ *{post['name']}* gruba gönderildi!", parse_mode="Markdown")
        except Exception as e:
            await query.edit_message_text(f"❌ Gönderilemedi: {e}")
        return

    # Yeniden yaz
    if data.startswith("rewrite|"):
        key = data.split("|")[1]
        post = pending_posts.get(key)
        if not post:
            await query.edit_message_text("❌ Post bulunamadı.")
            return
        await query.edit_message_text("✍️ Yeniden yazılıyor...")
        try:
            new_text = await gpt_text(
                f"Bu Telegram airdrop postunu daha çarpıcı ve emojili yeniden yaz, HTML formatını koru:\n\n{post['text']}",
                max_tokens=700
            )
            pending_posts[key]["text"] = new_text
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("✅ Gruba Gönder", callback_data=f"send|{key}"),
                    InlineKeyboardButton("✏️ Tekrar",       callback_data=f"rewrite|{key}"),
                    InlineKeyboardButton("❌ İptal",          callback_data=f"cancel|{key}")
                ]
            ])
            await ctx.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=new_text,
                parse_mode="HTML",
                reply_markup=keyboard,
                disable_web_page_preview=False
            )
        except Exception as e:
            await ctx.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Yeniden yazma hatası: {e}")
        return

    # İptal
    if data.startswith("cancel|"):
        key = data.split("|")[1]
        pending_posts.pop(key, None)
        await query.edit_message_text("❌ İptal edildi.")
        return


# ── /newairdrop ────────────────────────────────────────
async def post_airdrop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Kullanım: /newairdrop <proje_adı> <url>")
        return
    project_name, url = ctx.args[0], ctx.args[1]
    airdrop = {"name": project_name, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
    await update.message.reply_text(f"⏳ *{project_name}* hazırlanıyor...", parse_mode="Markdown")
    await _prepare_and_show(update, ctx, airdrop)


# ── /quickdrop ─────────────────────────────────────────
async def quick_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /quickdrop <url>")
        return
    url = ctx.args[0]
    domain = url.split("/")[2].replace("www.", "").split(".")[0].capitalize()
    airdrop = {"name": domain, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
    await update.message.reply_text(f"⚡ *{domain}* hızlı hazırlanıyor...", parse_mode="Markdown")
    await _prepare_and_show(update, ctx, airdrop)


# ── /analyze ───────────────────────────────────────────
async def analyze(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /analyze <url>")
        return
    url = ctx.args[0]
    await update.message.reply_text("🔍 Analiz ediliyor...")
    result = await gpt_score_url(url)
    await update.message.reply_text(f"📊 *Analiz:*\n\n{result}", parse_mode="Markdown")


# ── /preview ───────────────────────────────────────────
async def preview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Kullanım: /preview <proje_adı> <url>")
        return
    await post_airdrop(update, ctx)


# ── /scheduledrop ──────────────────────────────────────
async def schedule_drop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 3:
        await update.message.reply_text("Kullanım: /scheduledrop <proje> <url> <dakika>")
        return
    project_name, url = ctx.args[0], ctx.args[1]
    try:
        minutes = int(ctx.args[2])
    except ValueError:
        await update.message.reply_text("Dakika sayı olmalı.")
        return
    ref = ref_code_store.get("code")

    async def send_later(context):
        try:
            airdrop = {"name": project_name, "url": url, "description": "", "reward": "?", "difficulty": "?", "score": "?", "score_reason": ""}
            text = await gpt_make_post(airdrop, ref)
            image_url = await dalle_generate_image(project_name)
            if image_url:
                await context.bot.send_photo(chat_id=GROUP_CHAT_ID, photo=image_url, caption=text, parse_mode="HTML")
            else:
                await context.bot.send_message(chat_id=GROUP_CHAT_ID, text=text, parse_mode="HTML", disable_web_page_preview=False)
            stats_store["sent"] += 1
            stats_store["last"] = datetime.now().strftime("%d.%m.%Y %H:%M")
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"✅ Zamanlanmış gönderi yapıldı: *{project_name}*", parse_mode="Markdown")
        except Exception as e:
            await context.bot.send_message(chat_id=ADMIN_CHAT_ID, text=f"❌ Zamanlı gönderi hatası: {e}")

    ctx.job_queue.run_once(send_later, when=minutes * 60)
    await update.message.reply_text(f"⏰ *{project_name}* {minutes} dakika sonra gönderilecek!", parse_mode="Markdown")


# ── Diğer komutlar ─────────────────────────────────────
async def set_ref_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /setref <kod>"); return
    ref_code_store["code"] = ctx.args[0]
    await update.message.reply_text(f"✅ Ref kodu: `{ctx.args[0]}`", parse_mode="Markdown")

async def clear_ref(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    ref_code_store["code"] = None
    await update.message.reply_text("🗑️ Referans kodu temizlendi.")

async def show_ref(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    ref = ref_code_store.get("code") or "Ayarlanmamış"
    await update.message.reply_text(f"🔗 Aktif ref kodu: `{ref}`", parse_mode="Markdown")

async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /broadcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=" ".join(ctx.args))
    await update.message.reply_text("✅ Gönderildi.")

async def boldcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /boldcast <mesaj>"); return
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"📢 <b>{' '.join(ctx.args)}</b>\n\n— @kriptodropptr", parse_mode="HTML")
    await update.message.reply_text("✅ Kalın duyuru gönderildi.")

async def pin_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /pin <mesaj>"); return
    sent = await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=f"📌 <b>{' '.join(ctx.args)}</b>\n\n📢 @kriptodropptr", parse_mode="HTML")
    try:
        await ctx.bot.pin_chat_message(chat_id=GROUP_CHAT_ID, message_id=sent.message_id)
        await update.message.reply_text("✅ Sabitlendi.")
    except Exception as e:
        await update.message.reply_text(f"⚠️ Gönderdim ama sabitlemedim: {e}")

async def translate(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /translate <metin>"); return
    result = await gpt_text(f"Türkçeye çevir ve özetle:\n\n{' '.join(ctx.args)}", max_tokens=300)
    await update.message.reply_text(f"🇹🇷 *Çeviri:*\n\n{result}", parse_mode="Markdown")

async def hashtag(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /hashtag <proje>"); return
    result = await gpt_text(f"{' '.join(ctx.args)} kripto projesi için 10 hashtag üret.", max_tokens=150)
    await update.message.reply_text(f"#️⃣ *Hashtagler:*\n\n{result}", parse_mode="Markdown")

async def stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        f"📈 *İstatistikler*\n\n"
        f"📤 Toplam gönderim: `{stats_store['sent']}`\n"
        f"🕐 Son paylaşım: `{stats_store['last'] or '—'}`\n"
        f"🔗 Aktif ref: `{ref_code_store.get('code') or 'Yok'}`",
        parse_mode="Markdown"
    )

async def status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        f"🟢 *Bot Aktif*\n\n"
        f"🤖 Model: GPT-4o-mini + DALL-E 3\n"
        f"📡 Grup ID: `{GROUP_CHAT_ID}`\n"
        f"👤 Admin ID: `{ADMIN_CHAT_ID}`\n"
        f"🔗 Ref Kodu: `{ref_code_store.get('code') or 'Yok'}`",
        parse_mode="Markdown"
    )

async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update): return
    await update.message.reply_text(
        "💡 *Hızlı Başlangıç:*\n\n"
        "1️⃣ /scan → AI 5 airdrop bulsun, seç\n"
        "2️⃣ /autopick → AI en iyisini seçsin, hazırlasın\n"
        "3️⃣ Önizlemeyi onayla → Gruba gönder\n\n"
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

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",        start))
    app.add_handler(CommandHandler("help",         help_command))
    app.add_handler(CommandHandler("scan",         scan))
    app.add_handler(CommandHandler("autopick",     autopick))
    app.add_handler(CommandHandler("analyze",      analyze))
    app.add_handler(CommandHandler("newairdrop",   post_airdrop))
    app.add_handler(CommandHandler("quickdrop",    quick_drop))
    app.add_handler(CommandHandler("preview",      preview))
    app.add_handler(CommandHandler("scheduledrop", schedule_drop))
    app.add_handler(CommandHandler("setref",       set_ref_code))
    app.add_handler(CommandHandler("clearref",     clear_ref))
    app.add_handler(CommandHandler("showref",      show_ref))
    app.add_handler(CommandHandler("broadcast",    broadcast))
    app.add_handler(CommandHandler("boldcast",     boldcast))
    app.add_handler(CommandHandler("pin",          pin_message))
    app.add_handler(CommandHandler("translate",    translate))
    app.add_handler(CommandHandler("hashtag",      hashtag))
    app.add_handler(CommandHandler("stats",        stats))
    app.add_handler(CommandHandler("status",       status))
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("🚀 Polling başlıyor...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
