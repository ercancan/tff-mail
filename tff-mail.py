import os
import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime
from flask import Flask
from threading import Thread
from html import escape
from bs4 import BeautifulSoup

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

VERSION = "v2.5 PY313 FIX"

TOKEN = os.getenv("TOKEN")

# --- TELEGRAM ---
CHAT_ID = "1292276069"

# --- GMAIL ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = {CHAT_ID}
son_3_mail_idleri = []

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
    global son_3_mail_idleri

    try:
        await gonder(context, f"📩 Mail sistemi çalışıyor ({VERSION})")

        mailler = mailleri_getir()
        await gonder(context, f"📊 Bulunan mail sayısı: {len(mailler)}")

        son3 = mailler[:3]
        ids = [m["id"] for m in son3]

        if not son_3_mail_idleri:
            son_3_mail_idleri = ids.copy()

        elif ids != son_3_mail_idleri:
            son_3_mail_idleri = ids.copy()

            if son3:
                m = son3[0]
                klasor = "Spam" if "Spam" in m["klasor"] else "Inbox"

                await gonder(
                    context,
                    f"🚨 <b>YENİ MAIL</b>\n\n"
                    f"📌 <b>Konu:</b> {escape(m['konu'])}\n"
                    f"👤 <b>Gönderen:</b> {escape(m['gonderen'])}\n"
                    f"📂 <b>Klasör:</b> {escape(klasor)}\n"
                    f"📝 <b>İçerik:</b> {escape(m['govde'])}",
                )

    except Exception as e:
        await gonder(context, f"❌ HATA: {escape(str(e))}")


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