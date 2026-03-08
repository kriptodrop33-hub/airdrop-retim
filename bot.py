import os
import re
import asyncio
import logging
import requests
from groq import Groq
from tavily import TavilyClient
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters
)
from telegram.constants import ParseMode, ChatAction

# ══════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
#  ENV
# ══════════════════════════════════════════════════════════
BOT_TOKEN           = os.environ["BOT_TOKEN"]
GROQ_API_KEY        = os.environ["GROQ_API_KEY"]
TAVILY_API_KEY      = os.environ["TAVILY_API_KEY"]
UNSPLASH_ACCESS_KEY = os.environ["UNSPLASH_ACCESS_KEY"]
ADMIN_CHAT_ID       = int(os.environ["ADMIN_CHAT_ID"])
GROUP_CHAT_ID       = int(os.environ["GROUP_CHAT_ID"])

groq_client   = Groq(api_key=GROQ_API_KEY)
tavily_client = TavilyClient(api_key=TAVILY_API_KEY)

# ══════════════════════════════════════════════════════════
#  ADMIN GUARD
# ══════════════════════════════════════════════════════════
def admin_only(func):
    """Decorator: yalnızca admin DM'den erişebilir. Grupları ve yabancıları sessizce yoksay."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id   = update.effective_user.id
        chat_type = update.effective_chat.type
        # Grup/kanal ise hiç cevap verme
        if chat_type in ("group", "supergroup", "channel"):
            return
        # Admin değilse sessiz kal (uyarı mesajı yazma)
        if user_id != ADMIN_CHAT_ID:
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

def admin_only_callback(func):
    """Decorator: callback butonlar için admin guard."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_CHAT_ID:
            await update.callback_query.answer("⛔ Yetkisiz erişim.", show_alert=True)
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper

# ══════════════════════════════════════════════════════════
#  GROQ — AI ÜRETME
# ══════════════════════════════════════════════════════════
def ai(system: str, user: str, tokens: int = 1800, temp: float = 0.75) -> str:
    try:
        r = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "system", "content": system},
                      {"role": "user",   "content": user}],
            max_tokens=tokens,
            temperature=temp,
        )
        return r.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"Groq hata: {e}")
        return "❌ AI yanıt üretemedi."

# ══════════════════════════════════════════════════════════
#  TAVILY — DERIN ARAMA
# ══════════════════════════════════════════════════════════
AIRDROP_DOMAINS = [
    "airdrops.io", "earnifi.com", "cryptorank.io", "dappradar.com",
    "coinmarketcap.com", "coingecko.com", "coindesk.com", "decrypt.co",
    "cointelegraph.com", "theblock.co", "beincrypto.com", "cryptoslate.com",
    "blockworks.co", "twitter.com", "x.com", "medium.com", "mirror.xyz"
]

def deep_search(query: str, max_results: int = 10) -> list[dict]:
    """Tavily advanced search – tüm kaynakları tara."""
    try:
        r = tavily_client.search(
            query=query,
            search_depth="advanced",
            max_results=max_results,
            include_answer=True,
        )
        return r.get("results", [])
    except Exception as e:
        logger.error(f"Tavily hata: {e}")
        return []

def fetch_url_content(url: str) -> str:
    """URL içeriğini Tavily extract ile çek."""
    try:
        r = tavily_client.extract(urls=[url])
        results = r.get("results", [])
        if results:
            return results[0].get("raw_content", "")[:3000]
    except Exception as e:
        logger.error(f"URL çekme hata: {e}")
    return ""

def is_url(text: str) -> bool:
    return bool(re.match(r"https?://\S+", text.strip()))

# ══════════════════════════════════════════════════════════
#  UNSPLASH — GÖRSEL
# ══════════════════════════════════════════════════════════
def get_image(query: str = "cryptocurrency airdrop") -> str | None:
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            params={"query": query, "per_page": 6, "orientation": "landscape",
                    "client_id": UNSPLASH_ACCESS_KEY},
            timeout=10,
        )
        results = r.json().get("results", [])
        if results:
            import random
            return random.choice(results[:4])["urls"]["regular"]
    except Exception as e:
        logger.error(f"Unsplash hata: {e}")
    return None

# ══════════════════════════════════════════════════════════
#  ARAŞTIRMA FONKSİYONLARI
# ══════════════════════════════════════════════════════════

def research_airdrop_by_name(name: str) -> dict:
    """
    Airdrop adına göre derin araştırma:
    - 3 farklı arama sorgusu
    - AI ile kapsamlı analiz
    """
    queries = [
        f"{name} airdrop how to claim eligibility 2025",
        f"{name} token airdrop rewards tasks deadline",
        f"{name} crypto project tokenomics community airdrop guide",
    ]
    all_results = []
    for q in queries:
        all_results.extend(deep_search(q, max_results=5))

    # Tekrar edenleri filtrele
    seen_urls = set()
    unique = []
    for item in all_results:
        url = item.get("url", "")
        if url not in seen_urls:
            seen_urls.add(url)
            unique.append(item)

    raw_text = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:500]}"
        for i, r in enumerate(unique[:10])
    ])

    return {"name": name, "raw": raw_text, "sources": unique[:10]}


def research_airdrop_by_url(url: str) -> dict:
    """
    URL'ye göre derin araştırma:
    - URL içeriğini çek
    - Ek arama sorgularıyla zenginleştir
    """
    content = fetch_url_content(url)

    # İçerikten proje adı çıkar (AI ile)
    name_hint = ai(
        "Extract the project or airdrop name from the text. Reply with ONLY the name, nothing else.",
        content[:500] if content else url,
        tokens=50, temp=0.1
    )

    extra = deep_search(f"{name_hint} airdrop claim guide tasks 2025", max_results=6)
    extra_text = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\nURL: {r.get('url','')}\n{r.get('content','')[:400]}"
        for i, r in enumerate(extra[:6])
    ])

    raw = f"=== SAYFA İÇERİĞİ ===\n{content}\n\n=== EK KAYNAKLAR ===\n{extra_text}"
    return {"name": name_hint.strip(), "raw": raw, "sources": extra[:6], "url": url}


def analyze_research(data: dict) -> str:
    """AI ile araştırma verisini analiz et."""
    system = """Sen deneyimli bir kripto airdrop araştırmacısısın.
Verilen ham veriyi analiz edip şu başlıkları doldur:

PROJE HAKKINDA: (2-3 cümle özet)
TOKEN BİLGİSİ: (sembol, zincir, toplam arz varsa)
AIRDROP ÖDÜLÜ: (miktarı, değeri varsa)
UYGUNLUK KOŞULLARI: (kimler alabilir)
GÖREVLER: (adım adım liste)
SON TARİH: (varsa)
KATILIM LİNKİ: (varsa bul)
RİSK SKORU: (Düşük / Orta / Yüksek) + kısa gerekçe
GÜVENİLİRLİK: ⭐⭐⭐⭐⭐ (1-5 yıldız)

Eğer bilgi yoksa "Bilinmiyor" yaz. Türkçe yaz."""

    return ai(system, f"Proje: {data['name']}\n\n{data['raw']}", tokens=1500)


def scan_active_airdrops() -> str:
    """İnterneti tara, aktif airdropları bul ve AI ile özetle."""
    queries = [
        "best active crypto airdrop claim free tokens 2025",
        "new airdrop this week ethereum solana layer2 2025",
        "upcoming airdrop allocation snapshot eligible 2025",
    ]
    all_results = []
    for q in queries:
        all_results.extend(deep_search(q, max_results=6))

    seen = set()
    unique = []
    for r in all_results:
        u = r.get("url", "")
        if u not in seen:
            seen.add(u)
            unique.append(r)

    raw = "\n\n".join([
        f"[{i+1}] {r.get('title','')}\n{r.get('url','')}\n{r.get('content','')[:400]}"
        for i, r in enumerate(unique[:12])
    ])

    system = """Sen kripto airdrop listesi hazırlayan bir analistsin.
Verilen arama sonuçlarından FARKLI 5-8 aktif airdrop tespit et.
Her biri için şunu yaz:

🪂 *[PROJE ADI]*
├ 💰 Ödül: ...
├ ⛓ Zincir: ...
├ 📋 Görev: ...
├ ⏰ Son Tarih: ...
├ ⭐ Güvenilirlik: (1-5)
└ 🔗 Link: ...

Türkçe yaz. Tekrar eden projeleri çıkar. Bilinmiyorsa "?" yaz."""

    return ai(system, raw, tokens=2000)

# ══════════════════════════════════════════════════════════
#  POST OLUŞTURMA
# ══════════════════════════════════════════════════════════

POST_SYSTEM = """Sen Telegram kripto grupları için viral airdrop postları yazan uzmansın.

KURAL:
- Türkçe yaz
- Telegram normal Markdown kullan (*bold*, _italic_) — MarkdownV2 KULLANMA
- Maksimum emojilerle dikkat çekici yap
- Post şablonunu AYNEN kullan
- Maksimum 900 karakter tut
- KESİNLİKLE hashtag (#) kullanma — hiçbir satırda etiket olmayacak
- Link için tam olarak şu metni bırak: [🔗 LİNK]

ŞABLON:
🚨 *[PROJE ADI] AİRDROP* 🚨

━━━━━━━━━━━━━━━━━━━━
🏆 *ÖDÜL:* [miktar token / tahmini değer]
⛓ *ZİNCİR:* [blockchain]
👥 *UYGUNLUK:* [kimler katılabilir]
━━━━━━━━━━━━━━━━━━━━

📋 *GÖREVLER:*
✅ [görev 1]
✅ [görev 2]
✅ [görev 3]
✅ [görev 4]

⏰ *SON TARİH:* [tarih veya "Sınırlı süre!"]
━━━━━━━━━━━━━━━━━━━━

🔥 *KATIL & KAZAN:*
👉 [🔗 LİNK]

⚠️ _Hızlı ol! Kontenjan dolmadan katıl._"""


def build_post(analysis: str, project_name: str) -> str:
    return ai(
        POST_SYSTEM,
        f"Proje: {project_name}\n\nAraştırma analizi:\n{analysis}",
        tokens=900,
        temp=0.8,
    )

# ══════════════════════════════════════════════════════════
#  TELEGRAM HELPERS
# ══════════════════════════════════════════════════════════

def main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Airdrop Tara", callback_data="scan"),
         InlineKeyboardButton("✍️ Post Oluştur", callback_data="manual_post")],
        [InlineKeyboardButton("📢 Gruba Gönder (Metin)", callback_data="send_text"),
         InlineKeyboardButton("🖼️ Görsel ile Gönder", callback_data="send_photo")],
        [InlineKeyboardButton("🔄 Yeni Araştırma", callback_data="new_research"),
         InlineKeyboardButton("❓ Yardım", callback_data="help")],
    ])

def post_actions(has_link: bool = False) -> InlineKeyboardMarkup:
    link_btn_label = "✅ Link Eklendi" if has_link else "🔗 Link Ekle"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(link_btn_label, callback_data="add_link")],
        [InlineKeyboardButton("📢 Gruba Gönder (Metin)", callback_data="send_text"),
         InlineKeyboardButton("🖼️ Görsel ile Gönder", callback_data="send_photo")],
        [InlineKeyboardButton("♻️ Postu Yenile", callback_data="regen_post"),
         InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
    ])

async def typing(update: Update):
    await update.effective_chat.send_action(ChatAction.TYPING)

def safe_md(text: str) -> str:
    """Markdown parse hatalarına karşı temizle, hashtag kaldır."""
    import re
    text = text.replace("**", "*")
    # Satır başındaki hashtag satırlarını temizle
    text = re.sub(r'(?m)^#\w[\w\s#]*$', "", text)
    # Satır içi hashtag'leri kaldır
    text = re.sub(r'#\w+', "", text)
    # Birden fazla boş satırı teke indir
    text = re.sub(r'\n{3,}', "\n\n", text)
    return text.strip()

# ══════════════════════════════════════════════════════════
#  KOMUTLAR
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id   = update.effective_user.id
    chat_type = update.effective_chat.type
    if user_id != ADMIN_CHAT_ID or chat_type != "private":
        await update.message.reply_text(
            f"⛔ Bu bot yalnızca admin DM'inden kullanılabilir.\n\n"
            f"🆔 Senin ID'n: `{user_id}`\n"
            f"Eğer admin sensin Railway Variables'da `ADMIN_CHAT_ID = {user_id}` yap.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    context.user_data.clear()
    await update.message.reply_text(
        "🤖 *AIRDROP BOT* — Admin Paneli\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "🔍 *Airdrop Tara* → İnterneti tara, aktif airdropları listele\n"
        "✍️ *Post Oluştur* → Airdrop adı veya link at, derin araştır\n"
        "📢 *Gruba Gönder* → Hazır postu gruba gönder\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 _Airdrop adı veya linki direkt yazabilirsin._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )

@admin_only
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *KOMUTLAR*\n\n"
        "/start — Ana menü\n"
        "/scan — İnterneti tara, aktif airdropları listele\n"
        "/post `[isim]` — İsme göre araştır & post oluştur\n"
        "/sendgroup — Son postu gruba gönder\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Direkt mesaj:*\n"
        "• Bir URL at → sayfa derin araştırılır\n"
        "• Airdrop adı yaz → derin araştırma başlar\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ Post hazır olunca *🔗 Link Ekle* butonuna bas,\n"
        "linki yapıştır — post'a otomatik eklenir.",
        parse_mode=ParseMode.MARKDOWN,
    )

@admin_only
async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text(
        "🌐 *İnternet taranıyor...*\n_Aktif airdroplar aranıyor, lütfen bekle (20-30 sn)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    await update.effective_chat.send_action(ChatAction.TYPING)

    result = scan_active_airdrops()
    context.user_data["last_scan"] = result

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")],
        [InlineKeyboardButton("🔄 Yeniden Tara", callback_data="scan"),
         InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
    ])

    await msg.edit_text(
        f"✅ *TARAMA TAMAMLANDI*\n\n{safe_md(result)}",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )

@admin_only
async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "⚠️ Kullanım: `/post [airdrop adı]`\nÖrnek: `/post Arbitrum`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    name = " ".join(context.args)
    await _do_research(update, context, name)

@admin_only
async def cmd_sendgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _send_to_group(update, context, with_photo=False)

# ══════════════════════════════════════════════════════════
#  MESAJ İŞLEYİCİ — URL veya Airdrop Adı
# ══════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Grup / kanal mesajlarını tamamen yoksay
    if update.effective_chat.type in ("group", "supergroup", "channel"):
        return
    # Sadece admin DM'i işle, başkasına sessiz kal
    if update.effective_user.id != ADMIN_CHAT_ID:
        return
    text = update.message.text.strip()
    waiting = context.user_data.get("waiting_for")

    # Link ekleme state
    if waiting == "add_link":
        context.user_data["waiting_for"] = None
        post = context.user_data.get("last_post", "")
        # [🔗 LİNK] placeholder'ını gerçek linkle değiştir
        updated = post.replace("[🔗 LİNK]", text.strip())
        context.user_data["final_post"] = updated
        context.user_data["has_link"] = True
        preview = (
            f"✅ *Link eklendi!*\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"📣 *GÜNCEL POST:*\n\n"
            f"{safe_md(updated)}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Hazır! Gruba gönderebilirsin."
        )
        if len(preview) > 4096:
            preview = preview[:4086] + "_"
        await update.message.reply_text(
            preview,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_actions(has_link=True),
        )
        return

    # Post düzenleme (eski compat)
    if waiting == "edit_post":
        context.user_data["waiting_for"] = None
        context.user_data["final_post"] = text
        await update.message.reply_text(
            "✅ *Post güncellendi!*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=post_actions(),
        )
        return

    # URL veya airdrop adı
    await _do_research(update, context, text)


async def _do_research(update: Update, context: ContextTypes.DEFAULT_TYPE, input_text: str):
    """Derin araştırma → AI analiz → Post oluştur."""
    msg = await update.effective_message.reply_text(
        "🔬 *Derin araştırma başlıyor...*\n\n"
        f"📌 Girdi: `{input_text[:80]}`\n\n"
        "_Bu işlem 20-40 saniye sürebilir..._",
        parse_mode=ParseMode.MARKDOWN,
    )
    await update.effective_chat.send_action(ChatAction.TYPING)

    # 1. Araştır
    if is_url(input_text):
        await msg.edit_text("🔗 *URL içeriği çekiliyor...*", parse_mode=ParseMode.MARKDOWN)
        data = research_airdrop_by_url(input_text)
    else:
        await msg.edit_text(
            f"🔍 *'{input_text}' için derin arama yapılıyor...*\n_3 farklı sorgu çalışıyor..._",
            parse_mode=ParseMode.MARKDOWN,
        )
        data = research_airdrop_by_name(input_text)

    project_name = data.get("name", input_text)

    # 2. Analiz et
    await msg.edit_text("🤖 *AI analizi yapılıyor...*", parse_mode=ParseMode.MARKDOWN)
    analysis = analyze_research(data)
    context.user_data["last_analysis"] = analysis
    context.user_data["last_project"] = project_name

    # 3. Post oluştur
    await msg.edit_text("✍️ *Telegram postu yazılıyor...*", parse_mode=ParseMode.MARKDOWN)
    post = build_post(analysis, project_name)
    context.user_data["last_post"] = post
    context.user_data["final_post"] = post
    context.user_data["last_post_platform"] = project_name

    # 4. Analizi göster
    analysis_msg = (
        f"📊 *ARAŞTIRMA RAPORU — {project_name.upper()}*\n\n"
        f"{safe_md(analysis)}"
    )

    # Uzun ise böl
    if len(analysis_msg) > 4000:
        analysis_msg = analysis_msg[:3990] + "\n_...devamı kırpıldı_"

    await msg.edit_text(analysis_msg, parse_mode=ParseMode.MARKDOWN)

    # 5. Postu göster
    post_preview = (
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📣 *HAZIRLANAN POST:*\n\n"
        f"{safe_md(post)}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"👇 *🔗 Link Ekle* butonuna bas, linki yapıştır, sonra gruba gönder."
    )

    if len(post_preview) > 4096:
        post_preview = post_preview[:4086] + "_"

    context.user_data["has_link"] = False
    await update.effective_message.reply_text(
        post_preview,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=post_actions(has_link=False),
    )

# ══════════════════════════════════════════════════════════
#  GRUBA GÖNDERME
# ══════════════════════════════════════════════════════════

async def _send_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE, with_photo: bool):
    post = context.user_data.get("final_post") or context.user_data.get("last_post")
    if not post:
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text("⚠️ Önce bir post oluştur!")
        return

    platform = context.user_data.get("last_post_platform", "cryptocurrency airdrop")

    try:
        if with_photo:
            img_url = get_image(f"{platform} crypto blockchain token")
            caption = post[:1024] if len(post) > 1024 else post
            if img_url:
                await context.bot.send_photo(
                    chat_id=GROUP_CHAT_ID,
                    photo=img_url,
                    caption=safe_md(caption),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await context.bot.send_message(
                    chat_id=GROUP_CHAT_ID,
                    text=safe_md(post),
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            await context.bot.send_message(
                chat_id=GROUP_CHAT_ID,
                text=safe_md(post),
                parse_mode=ParseMode.MARKDOWN,
            )

        confirm = "✅ *Post gruba gönderildi!*" + (" 🖼️ (görsel ile)" if with_photo else "")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(confirm, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())

    except Exception as e:
        logger.error(f"Gönderme hatası: {e}")
        target = update.callback_query.message if update.callback_query else update.message
        await target.reply_text(f"❌ Gönderim hatası: `{e}`", parse_mode=ParseMode.MARKDOWN)

# ══════════════════════════════════════════════════════════
#  CALLBACK BUTONLAR
# ══════════════════════════════════════════════════════════

@admin_only_callback
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    if data == "home":
        await q.message.reply_text(
            "🏠 *Ana Menü*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )

    elif data == "help":
        await q.message.reply_text(
            "📖 *KOMUTLAR*\n\n"
            "/scan — Aktif airdropları tara\n"
            "/post `[isim]` — Araştır & post oluştur\n"
            "/sendgroup — Son postu gruba gönder\n\n"
            "💡 Direkt airdrop adı veya link yazabilirsin.",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "scan":
        msg = await q.message.reply_text(
            "🌐 *İnternet taranıyor...*\n_20-30 saniye sürebilir_",
            parse_mode=ParseMode.MARKDOWN,
        )
        await update.effective_chat.send_action(ChatAction.TYPING)
        result = scan_active_airdrops()
        context.user_data["last_scan"] = result

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")],
            [InlineKeyboardButton("🔄 Yeniden Tara", callback_data="scan"),
             InlineKeyboardButton("🏠 Ana Menü", callback_data="home")],
        ])
        text = f"✅ *TARAMA TAMAMLANDI*\n\n{safe_md(result)}"
        if len(text) > 4096:
            text = text[:4086] + "_"
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=keyboard)

    elif data == "manual_post":
        context.user_data["waiting_for"] = None
        await q.message.reply_text(
            "✍️ *Manuel Araştırma*\n\n"
            "Aşağıdakilerden birini yaz:\n"
            "• Airdrop / proje adı\n"
            "• Airdrop URL'si\n\n"
            "_Örnek: `Arbitrum` veya `https://arbitrum.io/airdrop`_",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "add_link":
        context.user_data["waiting_for"] = "add_link"
        await q.message.reply_text(
            "🔗 *Referans linkini yaz veya yapıştır:*\n\n"
            "_Örnek: `https://app.galxe.com/quest/...`_",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "send_text":
        await _send_to_group(update, context, with_photo=False)

    elif data == "send_photo":
        await q.message.reply_text("🖼️ Görsel aranıyor...", parse_mode=ParseMode.MARKDOWN)
        await _send_to_group(update, context, with_photo=True)

    elif data == "regen_post":
        analysis = context.user_data.get("last_analysis")
        project  = context.user_data.get("last_project", "")
        if not analysis:
            await q.message.reply_text("⚠️ Yenilemek için önce bir araştırma yap.")
            return
        msg = await q.message.reply_text("♻️ *Post yeniden yazılıyor...*", parse_mode=ParseMode.MARKDOWN)
        post = build_post(analysis, project)
        context.user_data["last_post"]   = post
        context.user_data["final_post"]  = post
        context.user_data["has_link"]    = False
        preview = (
            f"📣 *YENİLENEN POST:*\n\n{safe_md(post)}\n\n"
            f"👇 *🔗 Link Ekle* butonuna bas, linki yapıştır, sonra gruba gönder."
        )
        if len(preview) > 4096:
            preview = preview[:4086] + "_"
        await msg.edit_text(preview, parse_mode=ParseMode.MARKDOWN, reply_markup=post_actions(has_link=False))

    elif data == "new_research":
        context.user_data["waiting_for"] = None
        await q.message.reply_text(
            "🔬 *Yeni araştırma için airdrop adı veya linkini yaz:*",
            parse_mode=ParseMode.MARKDOWN,
        )

# ══════════════════════════════════════════════════════════
#  OTOMATİK TARAMA — Her 8 Saatte Bir Admin'e Bildir
# ══════════════════════════════════════════════════════════

async def auto_scan_job(context: ContextTypes.DEFAULT_TYPE):
    logger.info("Otomatik tarama başladı...")
    result = scan_active_airdrops()
    ts = datetime.now().strftime("%d.%m.%Y %H:%M")

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✍️ Birini Seç & Post Oluştur", callback_data="manual_post")],
    ])

    try:
        await context.bot.send_message(
            chat_id=ADMIN_CHAT_ID,
            text=f"🔔 *OTOMATİK TARAMA* — _{ts}_\n\n{safe_md(result)}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=keyboard,
        )
    except Exception as e:
        logger.error(f"Auto scan bildirim hata: {e}")

# ══════════════════════════════════════════════════════════
#  ANA
# ══════════════════════════════════════════════════════════

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("scan",      cmd_scan))
    app.add_handler(CommandHandler("post",      cmd_post))
    app.add_handler(CommandHandler("sendgroup", cmd_sendgroup))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Otomatik tarama: 8 saatte bir
    app.job_queue.run_repeating(auto_scan_job, interval=28800, first=120)

    logger.info("🚀 Airdrop Bot başlatıldı.")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
