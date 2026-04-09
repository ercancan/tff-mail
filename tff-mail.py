import os
import re
import imaplib
import email
import logging
import socket
from email.header import decode_header
from datetime import datetime, timedelta
from html import escape
from threading import Thread

import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- GLOBAL AYARLAR ---
# Ağ işlemlerinin sonsuza kadar takılı kalmasını engellemek için 30 saniye sınırı
socket.setdefaulttimeout(30)

VERSION = "v8.1 MAIL BOT FIXED"
TOKEN = os.getenv("TOKEN")
CHAT_ID = "1292276069"
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

# --- STATE ---
aktif_kullanicilar = {CHAT_ID}
son_5_mail_idleri = []
son_ozet_zamani = None
alarm_bitis_zamani = None
ilk_kurulum_tamamlandi = False

# --- LOGGING ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# --- FILTRELER ---
ANAHTAR_KELIMELER = [
    "tff", "fifa", "türkiye", "turkiye", "futbol", "federasyon", 
    "taraftar", "milli takım", "milli takim", "bilet", "kupa", "dünya", "dunya"
]

ANAHTAR_IFADELER = ["fifa code", "verification code", "security code"]

# --- WEB SUNUCU (RENDER İÇİN) ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    # UptimeRobot buraya vurduğunda botun uyanık kalmasını sağlar
    return f"BOT AKTIF - {VERSION} - {datetime.now().strftime('%H:%M:%S')}", 200

def run_web():
    port = int(os.getenv("PORT", 10000))
    # Render'ın sağlıklı görmesi için debug=False ve reloader=False önemli
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)

# --- UTIL FONKSİYONLAR ---
def decode_mime_text(value):
    if not value: return ""
    try:
        parts = decode_header(value)
        sonuc = ""
        for text, enc in parts:
            if isinstance(text, bytes):
                sonuc += text.decode(enc or "utf-8", errors="ignore")
            else:
                sonuc += text
        return sonuc.strip()
    except:
        return str(value)

def imap_baglan():
    # SSL bağlantısı için timeout direkt burada da kullanılabilir
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
    return mail

def html_to_text(html_content):
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        for tag in soup(["script", "style", "meta", "head", "title"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        return text.strip()
    except:
        return "HTML parse hatası"

def govdeyi_al(msg):
    plain_text = ""
    html_text = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            if "attachment" in str(part.get("Content-Disposition") or "").lower():
                continue
            try:
                payload = part.get_payload(decode=True)
                if not payload: continue
                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="ignore").strip()
                if content_type == "text/plain":
                    plain_text = decoded
                elif content_type == "text/html":
                    html_text = html_to_text(decoded)
            except: continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="ignore").strip()
                if msg.get_content_type() == "text/html":
                    html_text = html_to_text(decoded)
                else:
                    plain_text = decoded
        except: pass
    
    govde = plain_text if plain_text else html_text
    return (govde or "(İçerik yok)").strip()

def ilgili_mail_mi(gonderen, konu, govde):
    tum = f"{gonderen} {konu} {govde}".lower()
    return any(k in tum for k in ANAHTAR_KELIMELER) or any(i in tum for i in ANAHTAR_IFADELER)

def mailleri_getir():
    bulunan = []
    mail = None
    try:
        mail = imap_baglan()
        for klasor in ["INBOX", "[Gmail]/Spam"]:
            try:
                status, _ = mail.select(klasor, readonly=True)
                if status != "OK": continue

                status, data = mail.uid("search", None, "ALL")
                if status != "OK" or not data[0]: continue

                uid_list = data[0].split()
                # Sadece son 50 maili tara (Hız ve performans için)
                for uid in uid_list[-50:]:
                    try:
                        status, msg_data = mail.uid("fetch", uid, "(RFC822)")
                        if status != "OK" or not msg_data or not msg_data[0]: continue

                        raw = msg_data[0][1]
                        msg = email.message_from_bytes(raw)
                        konu = decode_mime_text(msg.get("Subject", ""))
                        gonderen = decode_mime_text(msg.get("From", ""))
                        govde = govdeyi_al(msg)

                        if ilgili_mail_mi(gonderen, konu, govde):
                            bulunan.append({
                                "id": f"{klasor}:{uid.decode()}",
                                "konu": konu or "(Konu yok)",
                                "gonderen": gonderen or "(Gönderen yok)",
                                "govde": govde[:500],
                                "klasor": klasor,
                            })
                    except: continue
            except Exception as e:
                logging.error(f"{klasor} tarama hatası: {e}")
    except Exception as e:
        logging.error(f"Genel IMAP hatası: {e}")
    finally:
        if mail:
            try:
                mail.logout() # Logout her zaman bağlantıyı düzgün kapatır
            except: pass
    
    bulunan.reverse()
    return bulunan

def son_mail_basliklari(mailler, adet=3):
    secilen = mailler[:adet]
    if not secilen: return "Mail bulunamadı."
    satirlar = []
    for i, m in enumerate(secilen, 1):
        klasor = "Spam" if "Spam" in m["klasor"] else "Inbox"
        satirlar.append(f"{i}. <b>{escape(m['konu'])}</b>\n   👤 {escape(m['gonderen'])}\n   📂 {escape(klasor)}")
    return "\n\n".join(satirlar)

async def tum_kullanicilara_gonder(context, mesaj):
    for chat_id in aktif_kullanicilar:
        try:
            await context.bot.send_message(chat_id=chat_id, text=mesaj, parse_mode="HTML")
        except Exception as e:
            logging.error(f"Mesaj gönderme hatası ({chat_id}): {e}")

# --- ANA DÖNGÜ ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_5_mail_idleri, son_ozet_zamani, alarm_bitis_zamani, ilk_kurulum_tamamlandi

    try:
        simdi = datetime.now()
        mailler = mailleri_getir()
        ids = [m["id"] for m in mailler[:5]]

        if not ilk_kurulum_tamamlandi:
            son_5_mail_idleri = ids.copy()
            son_ozet_zamani = simdi
            ilk_kurulum_tamamlandi = True
            
            inbox = len([m for m in mailler if "INBOX" in m["klasor"]])
            spam = len([m for m in mailler if "Spam" in m["klasor"]])
            mesaj = (f"✅ <b>Bot Başlatıldı</b>\n\n📥 Inbox: {inbox}\n🚫 Spam: {spam}\n\n"
                     f"📌 <b>Son Mailler:</b>\n{son_mail_basliklari(mailler, 3)}")
            await tum_kullanicilara_gonder(context, mesaj)
            return

        yeni_mail_var = ids != son_5_mail_idleri

        if yeni_mail_var:
            son_5_mail_idleri = ids.copy()
            alarm_bitis_zamani = simdi + timedelta(minutes=10)
            mesaj = (f"🚨 <b>YENİ MAİL!</b>\n\n{son_mail_basliklari(mailler, 3)}\n\n"
                     f"⏱ 10 dk boyunca her dakika güncelleme gelecek.")
            await tum_kullanicilara_gonder(context, mesaj)

        elif alarm_bitis_zamani and simdi < alarm_bitis_zamani:
            await tum_kullanicilara_gonder(context, f"🚨 <b>ALARM MODU</b>\n\n{son_mail_basliklari(mailler, 5)}")

        elif alarm_bitis_zamani and simdi >= alarm_bitis_zamani:
            alarm_bitis_zamani = None
            await tum_kullanicilara_gonder(context, "✅ Alarm modu bitti.")

        if son_ozet_zamani is None or (simdi - son_ozet_zamani) >= timedelta(minutes=15):
            inbox = len([m for m in mailler if "INBOX" in m["klasor"]])
            spam = len([m for m in mailler if "Spam" in m["klasor"]])
            await tum_kullanicilara_gonder(context, f"📊 <b>Özet:</b> Inbox({inbox}) Spam({spam})\n\n{son_mail_basliklari(mailler, 2)}")
            son_ozet_zamani = simdi

    except Exception as e:
        logging.exception(f"Mail kontrol hatası: {e}")

# --- KOMUTLAR ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    aktif_kullanicilar.add(str(update.effective_chat.id))
    await update.message.reply_text("🚀 Takip aktif. Render uykusunu engellemek için UptimeRobot kurmayı unutmayın!")

# --- ÇALIŞTIR ---
if __name__ == "__main__":
    # Web sunucusunu ayrı thread'de başlat
    Thread(target=run_web, daemon=True).start()

    # Botu oluştur
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))

    # 60 saniyede bir kontrol (first=10 ile başlar başlamaz çalışmaz, 10sn bekler)
    app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)

    logging.info(f"Bot çalışıyor... {VERSION}")
    app.run_polling()