import logging
import sys
from telegram.ext import Application, CommandHandler
from config import BOT_TOKEN
from handlers.admin import (
    start, post_airdrop, set_ref_code, broadcast,
    help_command
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout  # Railway logları stdout'tan okur
)
logger = logging.getLogger(__name__)


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN bulunamadı! Railway Variables'ı kontrol et.")
        sys.exit(1)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("newairdrop", post_airdrop))
    app.add_handler(CommandHandler("setref", set_ref_code))
    app.add_handler(CommandHandler("broadcast", broadcast))

    logger.info("✅ Bot başlatıldı, polling başlıyor...")
    app.run_polling(allowed_updates=["message"])


if __name__ == "__main__":
    main()
