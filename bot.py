import os
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
from groq import Groq
from tavily import TavilyClient
import requests
from datetime import datetime

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ENV Variables
BOT_TOKEN = os.environ.get("BOT_TOKEN")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY")
UNSPLASH_ACCESS_KEY = os.environ.get("UNSPLASH_ACCESS_KEY")
ADMIN_CHAT_ID = int(os.environ.get("ADMIN_CHAT_ID", 0))
GROUP_CHAT_ID = int(os.environ.get("GROUP_CHAT_ID", 0))

# API Clients
groq_client = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)


# ─────────────────────────────────────────────
# YARDIMCI FONKSİYONLAR
# ─────────────────────────────────────────────

def search_airdrops(query: str = None) -> list:
    """Tavily ile airdrop ara"""
    if not query:
        query = "crypto airdrop 2024 2025 free tokens claim now active"
    
    try:
        results = tavily_client.search(
            query=query,
            search_depth="advanced",
            max_results=8,
            include_domains=["coinmarketcap.com", "coingecko.com", "airdrops.io", 
                           "cryptorank.io", "coindesk.com", "decrypt.co",
                           "cointelegraph.com", "dappradar.com"]
        )
        return results.get("results", [])
    except Exception as e:
        logger.error(f"Tavily arama hatası: {e}")
        return []


def get_unsplash_image(query: str = "cryptocurrency blockchain") -> str | None:
    """Unsplash'tan ilgili görsel URL al"""
    try:
        url = f"https://api.unsplash.com/search/photos"
        params = {
            "query": query,
            "per_page": 5,
            "orientation": "landscape",
            "client_id": UNSPLASH_ACCESS_KEY
        }
        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        results = data.get("results", [])
        if results:
            import random
            photo = random.choice(results[:3])
            return photo["urls"]["regular"]
    except Exception as e:
        logger.error(f"Unsplash hatası: {e}")
    return None


def groq_generate(system_prompt: str, user_prompt: str, max_tokens: int = 1500) -> str:
    """Groq ile metin üret"""
    try:
        completion = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=max_tokens,
            temperature=0.7
        )
        return completion.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq hatası: {e}")
        return "❌ AI yanıt üretilemedi. Lütfen tekrar deneyin."


def format_airdrop_post(airdrop_info: str, platform_name: str = "") -> str:
    """Airdrop için Telegram grup postu oluştur"""
    system = """Sen bir kripto para ve airdrop uzmanısın. 
Telegram grupları için çekici, bilgilendirici airdrop paylaşım postları yazıyorsun.
Türkçe yaz. Emoji kullan. Kısa ve etkili ol.
Post şu bölümleri içermeli:
1. Başlık (büyük, dikkat çekici)
2. Proje hakkında kısa bilgi
3. Airdrop detayları (ödül, nasıl katılınır)
4. Görevler listesi (varsa)
5. Son tarih (varsa)
6. [REFERANS LİNKİ] placeholder'ı (kullanıcı kendi linkini ekleyecek)
7. Önemli notlar
Maksimum 400 kelime. Telegram Markdown formatı kullan."""

    user = f"""Şu airdrop/platform için Telegram grup postu hazırla:

{airdrop_info}

{f'Platform adı: {platform_name}' if platform_name else ''}

Referans linki için "[🔗 KATILIM LİNKİ BURAYA]" placeholder'ı ekle.
Post sonuna "#airdrop #crypto #freetoken" hashtaglerini ekle."""

    return groq_generate(system, user, max_tokens=800)


def analyze_airdrops_with_ai(search_results: list) -> str:
    """Bulunan airdropları AI ile analiz et ve özetle"""
    if not search_results:
        return "Şu anda aktif airdrop bulunamadı."
    
    results_text = "\n\n".join([
        f"Kaynak: {r.get('url', 'N/A')}\nBaşlık: {r.get('title', 'N/A')}\nİçerik: {r.get('content', 'N/A')[:300]}"
        for r in search_results[:6]
    ])
    
    system = """Sen bir kripto airdrop analistsin. 
Verilen arama sonuçlarından aktif ve değerli airdropları tespit et.
Her airdrop için şunları belirt: İsim, ödül miktarı, katılım koşulları, son tarih, güvenilirlik puanı.
Türkçe yaz. Emoji kullan."""

    user = f"""Şu arama sonuçlarından aktif airdropları analiz et ve listele:

{results_text}

Her airdrop için:
🪂 **İsim:** 
💰 **Ödül:** 
📋 **Görevler:** 
⏰ **Son Tarih:** 
⭐ **Güvenilirlik:** (1-5)
🔗 **Link:** 

formatında yaz."""

    return groq_generate(system, user, max_tokens=1200)


# ─────────────────────────────────────────────
# KOMUT İŞLEYİCİLERİ
# ─────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bot başlatma"""
    keyboard = [
        [InlineKeyboardButton("🔍 Airdrop Tara", callback_data="scan_airdrops")],
        [InlineKeyboardButton("✍️ Post Oluştur", callback_data="create_post")],
        [InlineKeyboardButton("📢 Gruba Gönder", callback_data="send_to_group")],
        [InlineKeyboardButton("❓ Yardım", callback_data="help")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(
        "🚀 *Airdrop Bot'a Hoş Geldiniz!*\n\n"
        "Bu bot ile:\n"
        "• 🔍 İnterneti tarayarak aktif airdropları bulabilirsiniz\n"
        "• ✍️ Herhangi bir platform için post oluşturabilirsiniz\n"
        "• 📢 Hazırlanan postları gruba gönderebilirsiniz\n\n"
        "Ne yapmak istersiniz?",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Yardım komutu"""
    help_text = """
🤖 *AIRDROP BOT KOMUTLARI*

*Tarama Komutları:*
/scan - Genel airdrop taraması yap
/scan [konu] - Belirli bir konuyu tara
Örnek: `/scan Solana airdrop`

*Post Oluşturma:*
/post [platform/airdrop adı] - Platform için post hazırla
Örnek: `/post Arbitrum airdrop`

*Metin ile post:*
/createpost - Detaylı bilgi vererek post oluştur
(Komutu girdikten sonra bilgileri yazın)

*Grup İşlemleri:*
/sendgroup - Son oluşturulan postu gruba gönder

*Diğer:*
/start - Ana menü
/help - Bu yardım mesajı

💡 *İpucu:* Post oluşturulduktan sonra referans linkinizi [🔗 KATILIM LİNKİ BURAYA] yazan yere ekleyin!
"""
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def scan_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Airdrop tarama komutu"""
    query = " ".join(context.args) if context.args else None
    
    msg = await update.message.reply_text("🔍 Airdroplar taranıyor, lütfen bekleyin...")
    
    # Tavily ile ara
    search_query = f"{query} airdrop crypto free tokens claim" if query else "best active crypto airdrop 2025 claim free"
    results = search_airdrops(search_query)
    
    if not results:
        await msg.edit_text("❌ Arama sonucu bulunamadı. Lütfen tekrar deneyin.")
        return
    
    # AI ile analiz et
    await msg.edit_text("🤖 Sonuçlar AI ile analiz ediliyor...")
    analysis = analyze_airdrops_with_ai(results)
    
    # Sonuçları gönder
    keyboard = [
        [InlineKeyboardButton("📝 Post Oluştur", callback_data=f"create_post_from_scan")],
        [InlineKeyboardButton("🔄 Yeniden Tara", callback_data="scan_airdrops")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await msg.edit_text(
        f"✅ *TARAMA TAMAMLANDI*\n\n{analysis}",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )
    
    # Context'e kaydet
    context.user_data["last_scan"] = analysis


async def post_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hızlı post oluşturma komutu"""
    if not context.args:
        await update.message.reply_text(
            "⚠️ Kullanım: `/post [platform veya airdrop adı]`\n\n"
            "Örnek: `/post Arbitrum airdrop` veya `/post zkSync`",
            parse_mode="Markdown"
        )
        return
    
    platform = " ".join(context.args)
    msg = await update.message.reply_text(f"🔍 *{platform}* hakkında bilgi toplanıyor...")
    
    # Tavily ile platform hakkında ara
    results = search_airdrops(f"{platform} airdrop how to claim eligibility rewards")
    
    await msg.edit_text("✍️ Post hazırlanıyor...")
    
    # Sonuçları birleştir
    context_text = "\n".join([
        f"{r.get('title', '')}: {r.get('content', '')[:200]}"
        for r in results[:4]
    ])
    
    post_content = format_airdrop_post(
        f"Platform: {platform}\n\nBulunan bilgiler:\n{context_text}",
        platform
    )
    
    # Post'u kaydet
    context.user_data["last_post"] = post_content
    context.user_data["last_post_platform"] = platform
    
    keyboard = [
        [InlineKeyboardButton("📢 Gruba Gönder", callback_data="send_to_group")],
        [InlineKeyboardButton("🖼️ Görsel ile Gönder", callback_data="send_with_image")],
        [InlineKeyboardButton("✏️ Düzenle", callback_data="edit_post")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await msg.edit_text(
        f"✅ *POST HAZIR!*\n\n{post_content}\n\n"
        "━━━━━━━━━━━━━━━\n"
        "⚠️ *Not:* `[🔗 KATILIM LİNKİ BURAYA]` kısmına referans linkinizi ekleyin!",
        parse_mode="Markdown",
        reply_markup=reply_markup
    )


async def createpost_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detaylı post oluşturma - kullanıcıdan bilgi ister"""
    context.user_data["waiting_for"] = "post_details"
    
    await update.message.reply_text(
        "✍️ *Post Oluştur*\n\n"
        "Airdrop veya platform hakkında bilgi girin:\n\n"
        "• Platform/proje adı\n"
        "• Ödül miktarı (biliyorsanız)\n"
        "• Katılım koşulları\n"
        "• Son tarih (varsa)\n\n"
        "Bilgileri tek mesajda yazabilirsiniz:",
        parse_mode="Markdown"
    )


async def sendgroup_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Son postu gruba gönder"""
    # Admin kontrolü
    if update.effective_user.id != ADMIN_CHAT_ID:
        await update.message.reply_text("⛔ Bu komutu sadece admin kullanabilir.")
        return
    
    last_post = context.user_data.get("last_post")
    if not last_post:
        await update.message.reply_text("⚠️ Henüz bir post oluşturulmadı. Önce `/post` komutunu kullanın.")
        return
    
    try:
        await context.bot.send_message(
            chat_id=GROUP_CHAT_ID,
            text=last_post,
            parse_mode="Markdown"
        )
        await update.message.reply_text("✅ Post gruba başarıyla gönderildi!")
    except Exception as e:
        await update.message.reply_text(f"❌ Gönderme hatası: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Genel mesaj işleyici"""
    text = update.message.text
    waiting_for = context.user_data.get("waiting_for")
    
    if waiting_for == "post_details":
        context.user_data["waiting_for"] = None
        msg = await update.message.reply_text("✍️ Yazıyor...")
        
        post_content = format_airdrop_post(text)
        context.user_data["last_post"] = post_content
        
        keyboard = [
            [InlineKeyboardButton("📢 Gruba Gönder", callback_data="send_to_group")],
            [InlineKeyboardButton("🖼️ Görsel ile Gönder", callback_data="send_with_image")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await msg.edit_text(
            f"✅ *POST HAZIR!*\n\n{post_content}\n\n"
            "━━━━━━━━━━━━━━━\n"
            "⚠️ `[🔗 KATILIM LİNKİ BURAYA]` kısmına referans linkinizi ekleyin!",
            parse_mode="Markdown",
            reply_markup=reply_markup
        )
    else:
        # Genel sohbet - AI ile yanıtla
        system = "Sen bir kripto ve airdrop asistanısın. Kısa ve bilgilendirici yanıtlar ver. Türkçe konuş."
        response = groq_generate(system, text, max_tokens=500)
        await update.message.reply_text(response, parse_mode="Markdown")


async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inline buton callback işleyici"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == "scan_airdrops":
        await query.message.reply_text("🔍 Tarama başlatılıyor...")
        
        msg = await query.message.reply_text("🔍 Airdroplar aranıyor...")
        results = search_airdrops()
        
        await msg.edit_text("🤖 AI analizi yapılıyor...")
        analysis = analyze_airdrops_with_ai(results)
        context.user_data["last_scan"] = analysis
        
        keyboard = [
            [InlineKeyboardButton("📝 Post Oluştur", callback_data="create_post_from_scan")],
            [InlineKeyboardButton("🔄 Yeniden Tara", callback_data="scan_airdrops")]
        ]
        
        await msg.edit_text(
            f"✅ *TARAMA TAMAMLANDI*\n\n{analysis}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "create_post":
        context.user_data["waiting_for"] = "post_details"
        await query.message.reply_text(
            "✍️ Post oluşturmak istediğiniz platform veya airdrop hakkında bilgi yazın:\n\n"
            "Örnek: *'Arbitrum airdrop, 3000 ARB ödül, görev: bridge kullan, son tarih: 31 Ocak'*",
            parse_mode="Markdown"
        )
    
    elif data == "create_post_from_scan":
        last_scan = context.user_data.get("last_scan", "")
        if last_scan:
            msg = await query.message.reply_text("✍️ Post hazırlanıyor...")
            post_content = format_airdrop_post(last_scan)
            context.user_data["last_post"] = post_content
            
            keyboard = [
                [InlineKeyboardButton("📢 Gruba Gönder", callback_data="send_to_group")],
                [InlineKeyboardButton("🖼️ Görsel ile Gönder", callback_data="send_with_image")]
            ]
            
            await msg.edit_text(
                f"✅ *POST HAZIR!*\n\n{post_content}\n\n"
                "⚠️ `[🔗 KATILIM LİNKİ BURAYA]` kısmına referans linkinizi ekleyin!",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
    
    elif data == "send_to_group":
        if update.effective_user.id != ADMIN_CHAT_ID:
            await query.message.reply_text("⛔ Sadece admin gruba gönderebilir.")
            return
        
        last_post = context.user_data.get("last_post")
        if not last_post:
            await query.message.reply_text("⚠️ Önce bir post oluşturun!")
            return
        
        try:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=last_post,
                parse_mode="Markdown"
            )
            await query.message.reply_text("✅ Post gruba başarıyla gönderildi!")
        except Exception as e:
            await query.message.reply_text(f"❌ Hata: {e}")
    
    elif data == "send_with_image":
        if update.effective_user.id != ADMIN_CHAT_ID:
            await query.message.reply_text("⛔ Sadece admin gruba gönderebilir.")
            return
        
        last_post = context.user_data.get("last_post")
        platform = context.user_data.get("last_post_platform", "cryptocurrency airdrop")
        
        if not last_post:
            await query.message.reply_text("⚠️ Önce bir post oluşturun!")
            return
        
        msg = await query.message.reply_text("🖼️ Görsel alınıyor...")
        image_url = get_unsplash_image(f"{platform} crypto blockchain")
        
        try:
            if image_url:
                await context.bot.send_photo(
                    chat_id=GROUP_CHAT_ID,
                    photo=image_url,
                    caption=last_post[:1024],  # Telegram caption limiti
                    parse_mode="Markdown"
                )
                await msg.edit_text("✅ Görsel ile post gruba gönderildi!")
            else:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=last_post,
                    parse_mode="Markdown"
                )
                await msg.edit_text("✅ Post gönderildi (görsel bulunamadı).")
        except Exception as e:
            await msg.edit_text(f"❌ Hata: {e}")
    
    elif data == "help":
        await help_command(query, context)


# ─────────────────────────────────────────────
# OTOMATIK TARAMA (Scheduler)
# ─────────────────────────────────────────────

async def auto_scan_job(context: ContextTypes.DEFAULT_TYPE):
    """Otomatik periyodik airdrop taraması"""
    logger.info("Otomatik tarama başladı...")
    
    results = search_airdrops("new crypto airdrop today 2025 claim free tokens")
    if results:
        analysis = analyze_airdrops_with_ai(results)
        
        try:
            await context.bot.send_message(
                chat_id=ADMIN_CHAT_ID,
                text=f"🔔 *OTOMATİK TARAMA RAPORU*\n_{datetime.now().strftime('%d.%m.%Y %H:%M')}_\n\n{analysis}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Auto scan bildirim hatası: {e}")


# ─────────────────────────────────────────────
# ANA FONKSİYON
# ─────────────────────────────────────────────

def main():
    """Botu başlat"""
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Komut handler'ları
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("scan", scan_command))
    app.add_handler(CommandHandler("post", post_command))
    app.add_handler(CommandHandler("createpost", createpost_command))
    app.add_handler(CommandHandler("sendgroup", sendgroup_command))
    
    # Buton callback handler
    app.add_handler(CallbackQueryHandler(button_callback))
    
    # Mesaj handler
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Otomatik tarama - her 6 saatte bir
    job_queue = app.job_queue
    job_queue.run_repeating(auto_scan_job, interval=21600, first=60)
    
    logger.info("🚀 Airdrop Bot başlatıldı!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
