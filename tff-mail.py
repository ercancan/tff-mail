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

VERSION = "v3.0 ALERT SYSTEM"

TOKEN = os.getenv("TOKEN")

CHAT_ID = "1292276069"

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = {CHAT_ID}

son_mail_idleri = []
son_bilgi_mesaji = None

# 🚨 alarm sistemi
alarm_bitis = None

# --- WEB ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return f"Bot aktif {VERSION}", 200

def run_web():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)

# --- UTIL ---
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
    return out.strip()

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

        mails.append({
            "id": uid.decode(),
            "konu": konu or "(Konu yok)",
            "gonderen": gonderen
        })

    mail.logout()
    mails.reverse()
    return mails

# --- TELEGRAM ---
async def gonder(context, mesaj):
    for chat_id in aktif_kullanicilar:
        await context.bot.send_message(chat_id=chat_id, text=mesaj, parse_mode="HTML")

# --- ANA ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_mail_idleri, son_bilgi_mesaji, alarm_bitis

    simdi = datetime.now()

    try:
        mails = get_mails()
        ids = [m["id"] for m in mails[:5]]

        # 🚨 YENİ MAIL ALGILAMA
        yeni_mail = False

        if not son_mail_idleri:
            son_mail_idleri = ids.copy()

        elif ids != son_mail_idleri:
            son_mail_idleri = ids.copy()
            yeni_mail = True
            alarm_bitis = simdi + timedelta(minutes=5)

        # 🚨 ALARM MODU
        if alarm_bitis and simdi < alarm_bitis:
            if mails:
                await gonder(context, f"🚨 <b>Yeni mail:</b>\n{escape(mails[0]['konu'])}")
            return

        # ⏱ 15 DAKİKALIK NORMAL BİLGİ
        if son_bilgi_mesaji is None or (simdi - son_bilgi_mesaji >= timedelta(minutes=15)):
            mesaj = f"📩 Mail sistemi çalışıyor ({VERSION})\n"
            mesaj += f"📊 Mail sayısı: {len(mails)}\n\n"
            mesaj += "📄 <b>Son 5 mail:</b>\n\n"

            for i, m in enumerate(mails[:5], start=1):
                mesaj += f"{i}. {escape(m['konu'])}\n"

            await gonder(context, mesaj)

            son_bilgi_mesaji = simdi

    except Exception as e:
        await gonder(context, f"❌ HATA: {escape(str(e))}")

# --- KOMUT ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Bot başladı {VERSION}")

async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(VERSION)

# --- START ---
Thread(target=run_web, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("version", version))

app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)

print("BOT START")
app.run_polling()