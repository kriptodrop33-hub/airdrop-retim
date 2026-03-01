import logging
import sys
import os

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
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)
ref_code_store = {"code": None}


def is_admin(update: Update) -> bool:
    uid = update.effective_chat.id
    logger.info(f"Komut geldi → chat_id: {uid} | ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
    return uid == ADMIN_CHAT_ID


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_chat.id
    await update.message.reply_text(
        f"👋 Bot çalışıyor!\n\n"
        f"🆔 Senin Chat ID'n: `{uid}`\n"
        f"⚙️ Kayıtlı ADMIN\\_CHAT\\_ID: `{ADMIN_CHAT_ID}`\n\n"
        f"{'✅ Admin olarak tanındın!' if uid == ADMIN_CHAT_ID else '❌ Admin değilsin! Railway Variables kontrol et.'}",
        parse_mode="Markdown"
    )


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    await update.message.reply_text(
        "📋 *Komutlar:*\n\n"
        "/setref `<kod>` → Referans kodunu kaydet\n"
        "/newairdrop `<proje>` `<url>` → GPT özet üret ve gruba gönder\n"
        "/broadcast `<mesaj>` → Gruba direkt mesaj at",
        parse_mode="Markdown"
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

    try:
        ref_section = f"\n\n🎯 <b>Referans Kodu:</b> <code>{ref}</code>" if ref else ""
        prompt = f"""Sen bir Türk kripto airdrop uzmanısın. Aşağıdaki proje için Telegram grubuna paylaşılacak, Türkçe, ilgi çekici, emojili bir duyuru yaz.

Proje: {project_name}
URL: {url}

Format:
- Başlık (büyük + emoji)
- 2-3 cümle kısa özet
- Nasıl katılınır (adımlar, emoji ile)
- Önemli notlar

HTML formatı kullan (<b>, <i>, <code> kullanabilirsin). Kısa ve etkili tut."""

        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=600
        )
        summary = response.choices[0].message.content.strip()
        summary += ref_section
        summary += f"\n\n🔗 <a href='{url}'>Katılmak için tıkla</a>"
        summary += "\n\n📢 @kriptodropptr"

        await ctx.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=summary,
            parse_mode="HTML",
            disable_web_page_preview=False
        )
        await update.message.reply_text("✅ Airdrop gruba gönderildi!")
    except Exception as e:
        logger.error(f"post_airdrop hatası: {e}")
        await update.message.reply_text(f"❌ Hata: {e}")


async def broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update):
        return
    if not ctx.args:
        await update.message.reply_text("Kullanım: /broadcast <mesaj metni>")
        return
    msg = " ".join(ctx.args)
    await ctx.bot.send_message(chat_id=GROUP_CHAT_ID, text=msg)
    await update.message.reply_text("✅ Mesaj gönderildi.")


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN bulunamadı! Railway Variables kontrol et.")
        sys.exit(1)

    logger.info(f"✅ ADMIN_CHAT_ID: {ADMIN_CHAT_ID}")
    logger.info(f"✅ GROUP_CHAT_ID: {GROUP_CHAT_ID}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("newairdrop", post_airdrop))
    app.add_handler(CommandHandler("setref", set_ref_code))
    app.add_handler(CommandHandler("broadcast", broadcast))

    logger.info("🚀 Polling başlıyor...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
