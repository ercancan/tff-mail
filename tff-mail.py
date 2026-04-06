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

VERSION = "v2.4 FIX"

TOKEN = os.getenv("TOKEN")

CHAT_ID = "1292276069"

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = {CHAT_ID}
son_3_mail_idleri = []

# --- WEB ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return f"Bot aktif {VERSION}", 200

def run_web():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- MAIL ---
def decode_text(val):
    if not val:
        return ""
    parts = decode_header(val)
    out = ""
    for t, enc in parts:
        if isinstance(t, bytes):
            out += t.decode(enc or "utf-8", errors="ignore")
        else:
            out += t
    return out

def html_to_text(html):
    soup = BeautifulSoup(html, "html.parser")
    return soup.get_text()

def get_body(msg):
    if msg.is_multipart():
        for part in msg.walk():
            try:
                payload = part.get_payload(decode=True)
                if not payload:
                    continue
                text = payload.decode(part.get_content_charset() or "utf-8", errors="ignore")
                if part.get_content_type() == "text/html":
                    return html_to_text(text)
                else:
                    return text
            except:
                continue
    return ""

def get_mails():
    mails = []
    mail = imaplib.IMAP4_SSL("imap.gmail.com")
    mail.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)

    mail.select("INBOX")
    _, data = mail.uid("search", None, "ALL")

    for uid in data[0].split()[-20:]:
        _, msg_data = mail.uid("fetch", uid, "(RFC822)")
        raw = msg_data[0][1]
        msg = email.message_from_bytes(raw)

        konu = decode_text(msg.get("Subject"))
        gonderen = decode_text(msg.get("From"))
        govde = get_body(msg)

        mails.append({
            "id": uid.decode(),
            "konu": konu,
            "gonderen": gonderen,
            "govde": govde[:300]
        })

    mail.logout()
    mails.reverse()
    return mails

# --- TELEGRAM ---
async def gonder(context, mesaj):
    for chat_id in aktif_kullanicilar:
        await context.bot.send_message(chat_id=chat_id, text=mesaj)

# --- ANA ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_3_mail_idleri

    try:
        await gonder(context, f"📩 Sistem çalışıyor ({VERSION})")

        mails = get_mails()
        await gonder(context, f"📊 Mail sayısı: {len(mails)}")

        son3 = mails[:3]
        ids = [m["id"] for m in son3]

        if not son_3_mail_idleri:
            son_3_mail_idleri = ids

        elif ids != son_3_mail_idleri:
            son_3_mail_idleri = ids

            m = son3[0]

            await gonder(context,
                f"🚨 YENİ MAIL\n\n"
                f"{escape(m['konu'])}\n"
                f"{escape(m['gonderen'])}"
            )

    except Exception as e:
        await gonder(context, f"❌ HATA: {str(e)}")

# --- KOMUT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Bot başladı {VERSION}")

# --- MAIN ---
Thread(target=run_web, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)

print("BOT START")
app.run_polling()