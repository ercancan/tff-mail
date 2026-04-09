import os
import re
import imaplib
import email
import logging
from email.header import decode_header
from datetime import datetime, timedelta
from html import escape
from threading import Thread

import requests
from bs4 import BeautifulSoup
from flask import Flask
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

VERSION = "v8.0 MAIL BOT FINAL"

TOKEN = os.getenv("TOKEN")

# --- TELEGRAM ---
CHAT_ID = "1292276069"

# --- GMAIL ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = {CHAT_ID}

# --- STATE ---
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
    "tff",
    "fifa",
    "türkiye",
    "turkiye",
    "futbol",
    "federasyon",
    "taraftar",
    "milli takım",
    "milli takim",
    "bilet",
    "kupa",
    "dünya",
    "dunya",
]

ANAHTAR_IFADELER = [
    "fifa code",
    "verification code",
    "security code",
]

# --- WEB KEEPALIVE ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return f"Mail bot çalışıyor - {VERSION}", 200


def run_web():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


# --- UTIL ---
def decode_mime_text(value):
    if not value:
        return ""

    parts = decode_header(value)
    sonuc = ""

    for text, enc in parts:
        if isinstance(text, bytes):
            sonuc += text.decode(enc or "utf-8", errors="ignore")
        else:
            sonuc += text

    return sonuc.strip()


def imap_baglan():
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
    return mail


def html_to_text(html_content):
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup(["script", "style", "meta", "head", "title"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def govdeyi_al(msg):
    plain_text = ""
    html_text = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition") or "").lower()

            if "attachment" in content_disposition:
                continue

            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue

                charset = part.get_content_charset() or "utf-8"
                decoded = payload.decode(charset, errors="ignore").strip()

                if content_type == "text/plain" and not plain_text:
                    plain_text = decoded
                elif content_type == "text/html" and not html_text:
                    html_text = html_to_text(decoded)

            except Exception:
                continue
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
        except Exception:
            pass

    govde = plain_text if plain_text else html_text

    if not govde:
        govde = "(İçerik yok)"

    govde = re.sub(r"\n\s*\n+", "\n\n", govde).strip()
    return govde


def ilgili_mail_mi(gonderen, konu, govde):
    tum = f"{gonderen} {konu} {govde}".lower()

    kelime_eslesmesi = any(k in tum for k in ANAHTAR_KELIMELER)
    ifade_eslesmesi = any(i in tum for i in ANAHTAR_IFADELER)

    return kelime_eslesmesi or ifade_eslesmesi


def mailleri_getir():
    bulunan = []
    mail = None

    try:
        mail = imap_baglan()

        for klasor in ["INBOX", "[Gmail]/Spam"]:
            try:
                logging.info(f"Klasör kontrol ediliyor: {klasor}")
                status, _ = mail.select(klasor, readonly=True)

                if status != "OK":
                    logging.warning(f"Klasör seçilemedi: {klasor}")
                    continue

                status, data = mail.uid("search", None, "ALL")
                if status != "OK":
                    logging.warning(f"UID search başarısız: {klasor}")
                    continue

                uid_list = data[0].split()
                logging.info(f"{klasor} içinde son {min(len(uid_list), 50)} mail taranacak")

                for uid in uid_list[-50:]:
                    status, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    konu = decode_mime_text(msg.get("Subject", ""))
                    gonderen = decode_mime_text(msg.get("From", ""))
                    govde = govdeyi_al(msg)

                    if not ilgili_mail_mi(gonderen, konu, govde):
                        continue

                    bulunan.append({
                        "id": f"{klasor}:{uid.decode()}",
                        "konu": konu or "(Konu yok)",
                        "gonderen": gonderen or "(Gönderen yok)",
                        "govde": (govde or "(İçerik yok)")[:500],
                        "klasor": klasor,
                    })

                try:
                    mail.close()
                except Exception:
                    pass

            except Exception as e:
                logging.exception(f"{klasor} klasör hatası: {e}")

    except Exception as e:
        logging.exception(f"Genel mail çekme hatası: {e}")

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    bulunan.reverse()
    logging.info(f"Filtreye takılan mail sayısı: {len(bulunan)}")
    return bulunan


def son_mail_basliklari(mailler, adet=3):
    secilen = mailler[:adet]

    if not secilen:
        return "Mail bulunamadı."

    satirlar = []
    for i, m in enumerate(secilen, 1):
        klasor = "Spam" if "Spam" in m["klasor"] else "Inbox"
        satirlar.append(
            f"{i}. <b>{escape(m['konu'])}</b>\n"
            f"   👤 {escape(m['gonderen'])}\n"
            f"   📂 {escape(klasor)}"
        )

    return "\n\n".join(satirlar)


async def tum_kullanicilara_gonder(context, mesaj):
    for chat_id in aktif_kullanicilar:
        await context.bot.send_message(
            chat_id=chat_id,
            text=mesaj,
            parse_mode="HTML"
        )


# --- MAIL KONTROL ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_5_mail_idleri
    global son_ozet_zamani
    global alarm_bitis_zamani
    global ilk_kurulum_tamamlandi

    try:
        simdi = datetime.now()
        mailler = mailleri_getir()

        son5 = mailler[:5]
        ids = [m["id"] for m in son5]

        # İlk açılışta mevcut durumu hafızaya al
        if not ilk_kurulum_tamamlandi:
            son_5_mail_idleri = ids.copy()
            son_ozet_zamani = simdi
            ilk_kurulum_tamamlandi = True

            inbox = len([m for m in mailler if "INBOX" in m["klasor"]])
            spam = len([m for m in mailler if "Spam" in m["klasor"]])

            mesaj = (
                f"✅ <b>Bot ilk kontrolü tamamladı</b>\n\n"
                f"📥 <b>Inbox:</b> {inbox}\n"
                f"🚫 <b>Spam:</b> {spam}\n"
                f"📦 <b>Toplam:</b> {len(mailler)}\n\n"
                f"📌 <b>Son 3 mail:</b>\n\n"
                f"{son_mail_basliklari(mailler, 3)}"
            )

            await tum_kullanicilara_gonder(context, mesaj)
            logging.info("İlk açılış özeti gönderildi")
            return

        yeni_mail_var = ids != son_5_mail_idleri

        # Yeni mail gelirse alarm başlat
        if yeni_mail_var:
            son_5_mail_idleri = ids.copy()
            alarm_bitis_zamani = simdi + timedelta(minutes=10)

            mesaj = (
                "🚨 <b>Yeni mail bildirimi</b>\n\n"
                "📌 <b>Son 3 mail:</b>\n\n"
                f"{son_mail_basliklari(mailler, 3)}\n\n"
                "⏱ <b>Alarm modu:</b> 10 dakika boyunca her dakika son 5 konu"
            )
            await tum_kullanicilara_gonder(context, mesaj)

        # Alarm süresince her dakika son 5 mail
        elif alarm_bitis_zamani and simdi < alarm_bitis_zamani:
            mesaj = (
                "🚨 <b>SON 5 MAİL KONUSU</b>\n\n"
                f"{son_mail_basliklari(mailler, 5)}"
            )
            await tum_kullanicilara_gonder(context, mesaj)

        # Alarm bitti
        elif alarm_bitis_zamani and simdi >= alarm_bitis_zamani:
            alarm_bitis_zamani = None
            await tum_kullanicilara_gonder(context, "✅ <b>Alarm modu sona erdi</b>")

        # 15 dakikada bir özet
        if (
            son_ozet_zamani is None
            or (simdi - son_ozet_zamani) >= timedelta(minutes=15)
        ):
            inbox = len([m for m in mailler if "INBOX" in m["klasor"]])
            spam = len([m for m in mailler if "Spam" in m["klasor"]])

            mesaj = (
                f"📊 <b>Mail Durum Özeti</b>\n\n"
                f"📥 <b>Inbox:</b> {inbox}\n"
                f"🚫 <b>Spam:</b> {spam}\n"
                f"📦 <b>Toplam:</b> {len(mailler)}\n\n"
                f"📌 <b>Son 3 mail:</b>\n\n"
                f"{son_mail_basliklari(mailler, 3)}"
            )

            await tum_kullanicilara_gonder(context, mesaj)
            son_ozet_zamani = simdi
            logging.info("15 dakikalık özet gönderildi")

    except Exception as e:
        logging.exception(f"Mail kontrol hatası: {e}")
        await tum_kullanicilara_gonder(context, f"❌ <b>Hata:</b> {escape(str(e))}")


# --- KOMUTLAR ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    aktif_kullanicilar.add(chat_id)

    await update.message.reply_text(f"🚀 Mail takibi başlatıldı! ({VERSION})")

    # mevcut mailleri hafızaya al
    mailler = mailleri_getir()
    ids = [m["id"] for m in mailler[:5]]

    global son_5_mail_idleri, ilk_kurulum_tamamlandi
    son_5_mail_idleri = ids.copy()
    ilk_kurulum_tamamlandi = True


async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📌 Aktif sürüm: {VERSION}")


# --- BAŞLAT ---
Thread(target=run_web).start()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("version", version))

# 60 saniyede bir çalışır
app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)

print(f"🤖 Mail bot çalışıyor... ({VERSION})")
app.run_polling()