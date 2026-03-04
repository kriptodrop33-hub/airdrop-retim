# 🚀 Airdrop Telegram Bot

## Özellikler
- 🔍 **Airdrop Tarama**: Tavily API ile interneti tarayarak aktif airdropları bulur
- 🤖 **AI Analiz**: Groq (LLaMA 70B) ile bulunan airdropları analiz eder
- ✍️ **Post Oluşturma**: Platform/airdrop adı verince otomatik Telegram postu hazırlar
- 🖼️ **Görsel**: Unsplash'tan otomatik kripto görseli ekler
- 📢 **Gruba Gönder**: Hazırlanan postları tek tıkla gruba gönderir
- ⏰ **Otomatik Tarama**: Her 6 saatte bir admin'e yeni airdrop raporu gönderir

---

## Railway Kurulum

### 1. Environment Variables (Zaten ayarlandı)
```
BOT_TOKEN=your_telegram_bot_token
GROQ_API_KEY=your_groq_api_key
TAVILY_API_KEY=your_tavily_api_key
UNSPLASH_ACCESS_KEY=your_unsplash_key
ADMIN_CHAT_ID=your_telegram_user_id
GROUP_CHAT_ID=your_group_chat_id
```

### 2. Deploy
- `bot.py`, `requirements.txt`, `Procfile` dosyalarını Railway'e yükle
- Railway otomatik deploy eder

---

## Komutlar

| Komut | Açıklama |
|-------|----------|
| `/start` | Ana menü |
| `/scan` | Genel airdrop taraması |
| `/scan Solana` | Belirli konu taraması |
| `/post Arbitrum` | Platform için hızlı post |
| `/createpost` | Detaylı bilgi ile post |
| `/sendgroup` | Son postu gruba gönder |
| `/help` | Yardım |

---

## Kullanım Akışı

1. `/scan` ile airdropları tara
2. İlgini çeken airdrop için `/post [isim]` yaz
3. Post hazırlanır, `[🔗 KATILIM LİNKİ BURAYA]` yerine kendi referans linkini ekle
4. `/sendgroup` veya "Gruba Gönder" butonuna bas

---

## ADMIN_CHAT_ID Nasıl Bulunur?
Telegram'da @userinfobot'a mesaj at, ID'ni verir.

## GROUP_CHAT_ID Nasıl Bulunur?
Botu gruba ekle, ardından @getidsbot'u da gruba ekle, group ID'yi verir.
