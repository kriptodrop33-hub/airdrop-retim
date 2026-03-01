import logging
import sys
from telegram.ext import Application, CommandHandler
from config import BOT_TOKEN, ADMIN_CHAT_ID, GROUP_CHAT_ID
from handlers.admin import (
    start, post_airdrop, set_ref_code, broadcast,
    help_command
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout
)
logger = logging.getLogger(__name__)


def main():
    if not BOT_TOKEN:
        logger.error("❌ BOT_TOKEN bulunamadı!")
        sys.exit(1)

    logger.info(f"✅ BOT_TOKEN yüklendi")
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
