import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from html import escape
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

VERSION = "v2.6 ALERT MODE"

TOKEN = os.getenv("TOKEN")

# --- TELEGRAM ---
CHAT_ID = "1292276069"

# --- GMAIL ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = {CHAT_ID}
son_5_mail_idleri = []

last_summary_time = datetime.now()
alert_mode_until = None

# --- ANAHTAR KELİMELER ---
ANAHTAR_KELIMELER = [
    "tff",
    "fifa",
    "türkiye",
    "turkiye",
    "futbol",
    "federasyon",
    "taraftar",
    "milli",
    "takım",
    "takim",
    "taraftarkulubu",
    "taraftar kulubu",
    "kırmızı",
    "kirmizi",
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


# --- MAIL GETİR ---
def mailleri_getir():
    bulunan = []
    mail = None

    try:
        mail = imap_baglan()

        for klasor in ["INBOX", "[Gmail]/Spam"]:
            try:
                status, _ = mail.select(klasor)
                if status != "OK":
                    continue

                status, data = mail.uid("search", None, "ALL")
                if status != "OK":
                    continue

                uid_list = data[0].split()

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

            except Exception:
                continue

    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception:
                pass

    bulunan.reverse()
    return bulunan


# --- TELEGRAM ---
async def gonder(context, mesaj):
    for chat_id in aktif_kullanicilar:
        await context.bot.send_message(
            chat_id=chat_id,
            text=mesaj,
            parse_mode="HTML",
        )


# --- ANA KONTROL ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_5_mail_idleri, last_summary_time, alert_mode_until

    try:
        mailler = mailleri_getir()
        simdi = datetime.now()

        son5 = mailler[:5]
        ids = [m["id"] for m in son5]

        # İlk açılışta sadece mevcut durumu hafızaya al
        if not son_5_mail_idleri:
            son_5_mail_idleri = ids.copy()

        # Yeni mail / değişim kontrolü
        yeni_mail_var = ids != son_5_mail_idleri

        if yeni_mail_var:
            son_5_mail_idleri = ids.copy()
            alert_mode_until = simdi + timedelta(minutes=10)

            await gonder(
                context,
                "🚨 <b>Yeni mail algılandı</b>\n"
                "⏱ <b>Alarm modu başladı:</b> 10 dakika boyunca her dakika son 5 konu gönderilecek."
            )

        # Alarm modu aktifse her dakika son 5 mail konusunu gönder
        if alert_mode_until and simdi < alert_mode_until:
            if son5:
                mesaj = "🚨 <b>SON 5 MAİL KONUSU</b>\n\n"
                for i, m in enumerate(son5, 1):
                    klasor = "Spam" if "Spam" in m["klasor"] else "Inbox"
                    mesaj += f"{i}. <b>{escape(m['konu'])}</b> <i>({escape(klasor)})</i>\n"

                await gonder(context, mesaj)
            else:
                await gonder(context, "ℹ️ <b>Alarm modu aktif</b>\nAma filtreye uyan mail bulunamadı.")

        # Alarm modu bittiyse kapat
        if alert_mode_until and simdi >= alert_mode_until:
            alert_mode_until = None
            await gonder(context, "✅ <b>Alarm modu sona erdi</b>")

        # 15 dakikada bir genel özet
        if (simdi - last_summary_time).total_seconds() >= 900:
            inbox = len([m for m in mailler if "INBOX" in m["klasor"]])
            spam = len([m for m in mailler if "Spam" in m["klasor"]])

            await gonder(
                context,
                f"📊 <b>Mail Durum Özeti</b>\n\n"
                f"📥 <b>Inbox:</b> {inbox}\n"
                f"🚫 <b>Spam:</b> {spam}\n"
                f"📦 <b>Toplam:</b> {len(mailler)}\n\n"
                f"🤖 Sistem aktif çalışıyor",
            )

            last_summary_time = simdi

    except Exception as e:
        await gonder(context, f"❌ <b>HATA:</b> {escape(str(e))}")


# --- KOMUTLAR ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🚀 Başladı ({VERSION})")


async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📌 Aktif sürüm: {VERSION}")


# --- BAŞLANGIÇ MESAJI ---
async def baslangic_mesaji(app):
    for chat_id in aktif_kullanicilar:
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"🤖 Bot aktif ({VERSION})",
        )


# --- BAŞLAT ---
Thread(target=run_web, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("version", version))

app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)
app.post_init = baslangic_mesaji

print(f"BOT BAŞLADI - {VERSION}")
app.run_polling()