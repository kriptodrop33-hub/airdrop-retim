import logging
from telegram import Update
from telegram.ext import ContextTypes
from config import ADMIN_CHAT_ID, GROUP_CHAT_ID
from utils.gpt import generate_airdrop_summary
from utils.image import get_airdrop_image

logger = logging.getLogger(__name__)

# Geçici state: referans kodu
ref_code_store = {"code": None}

def is_admin(update: Update) -> bool:
    user_id = update.effective_chat.id
    logger.info(f"Mesaj geldi → chat_id: {user_id} | admin: {ADMIN_CHAT_ID} | eşleşme: {user_id == ADMIN_CHAT_ID}")
    return user_id == ADMIN_CHAT_ID


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_chat.id
    logger.info(f"/start komutu geldi → chat_id: {user_id}")
    
    # Geçici olarak ID kontrolü olmadan yanıt ver (debug için)
    await update.message.reply_text(
        f"👋 Bot çalışıyor!\n\n"
        f"🆔 Senin Chat ID'n: `{user_id}`\n"
        f"⚙️ Kayıtlı ADMIN_CHAT_ID: `{ADMIN_CHAT_ID}`\n\n"
        f"{'✅ Admin olarak tanındın!' if user_id == ADMIN_CHAT_ID else '❌ Admin değilsin! ADMIN_CHAT_ID yanlış ayarlanmış olabilir.'}",
        parse_mode="Markdown"
    )


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        await update.message.reply_text(f"⛔ Yetkisiz. Chat ID'n: `{update.effective_chat.id}`", parse_mode="Markdown")
        return
    await update.message.reply_text(
        "📋 Kullanım:\n\n"
        "1️⃣ /setref REFKODUN  → Referans kodunu kaydet\n"
        "2️⃣ /newairdrop <proje> <url>  → GPT özet üretir, görselle gruba gönderir\n"
        "3️⃣ /broadcast <metin>  → Gruba doğrudan mesaj at\n"
    )


async def set_ref_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /setref <referans_kodu>")
        return
    ref_code_store["code"] = ctx.args[0]
    await update.message.reply_text(f"✅ Referans kodu ayarlandı: `{ctx.args[0]}`", parse_mode="Markdown")


async def post_airdrop(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if len(ctx.args) < 2:
        await update.message.reply_text("Kullanım: /newairdrop <proje_adı> <url>")
        return

    project_name = ctx.args[0]
    url = ctx.args[1]
    ref = ref_code_store.get("code")

    await update.message.reply_text(f"⏳ {project_name} için özet hazırlanıyor...")

    summary = await generate_airdrop_summary(project_name, url, ref)
    image_url = await get_airdrop_image(project_name)

    try:
        if image_url:
            await ctx.bot.send_photo(
                chat_id=GROUP_CHAT_ID,
                photo=image_url,
                caption=summary,
                parse_mode="HTML"
            )
        else:
            await ctx.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=summary,
                parse_mode="HTML",
                disable_web_page_preview=False
            )
        await update.message.reply_text("✅ Airdrop gruba gönderildi!")
    except Exception as e:
        await update.message.reply_text(f"❌ Hata: {e}")


async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /broadcast <mesaj metni>")
        return
    message = " ".join(ctx.args)
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=message)
    await update.message.reply_text("✅ Mesaj gönderildi.")
