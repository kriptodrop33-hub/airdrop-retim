# 🚀 Airdrop Bot v2 — Gelişmiş Admin Paneli

## ✨ Yeni Özellikler

### 🔒 Admin-Only DM
Bot yalnızca `ADMIN_CHAT_ID` sahibinin özel mesajında çalışır. Grup veya yabancıların erişimi tamamen engellenir.

### 🔬 Derin Araştırma
- **Airdrop adı yazınca:** 3 farklı Tavily sorgusu, 10+ kaynak, AI analizi
- **URL atınca:** Sayfa içeriği çekilir + ek araştırma yapılır, proje adı otomatik tespit edilir

### 📣 Emoji'li Güzel Postlar
Sabit şablon ile her post tutarlı ve dikkat çekici görünür:
```
🚨 *PROJE AİRDROP* 🚨
━━━━━━━━━━━━━━━━━━━━
🏆 ÖDÜL | ⛓ ZİNCİR | 👥 UYGUNLUK
━━━━━━━━━━━━━━━━━━━━
📋 GÖREVLER (✅ listesi)
⏰ SON TARİH
🔗 REFERANS LİNKİ BURAYA
#Airdrop #Kripto
```

### ⏰ Otomatik Tarama
Her 8 saatte bir interneti tarayıp admin'e aktif airdrop listesi gönderir.

---

## 📋 Komutlar

| Komut | Açıklama |
|-------|----------|
| `/start` | Ana menü |
| `/scan` | İnterneti tara, aktif airdropları listele |
| `/post Arbitrum` | İsme göre derin araştır & post oluştur |
| `/sendgroup` | Son postu gruba gönder |
| `/help` | Komut listesi |

**Direkt mesaj:**
- `Arbitrum` → derin araştırma başlar
- `https://arbitrum.io/airdrop` → URL'den araştırma

---

## 🔄 Kullanım Akışı

```
1. /post [isim] veya direkt airdrop adı yaz
      ↓
2. Bot araştırır (20-40 sn)
      ↓
3. Araştırma raporu gösterilir
      ↓
4. Otomatik post hazırlanır
      ↓
5. [🔗 REFERANS LİNKİ BURAYA] → kendi linkini yapıştır
      ↓
6. "Gruba Gönder" butonuna bas ✅
```

---

## ⚙️ Railway Değişkenler

```
BOT_TOKEN           = Telegram bot token
GROQ_API_KEY        = Groq API key (llama-3.3-70b)
TAVILY_API_KEY      = Tavily search API key
UNSPLASH_ACCESS_KEY = Unsplash API key
ADMIN_CHAT_ID       = Kendi Telegram user ID'n
GROUP_CHAT_ID       = Grubun chat ID'si (negatif sayı)
```

## 📌 ID Alma
- **ADMIN_CHAT_ID:** @userinfobot'a mesaj at
- **GROUP_CHAT_ID:** Botu gruba ekle, @getidsbot'u gruba ekle
