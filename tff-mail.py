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

VERSION = "v2.1"

TOKEN = os.getenv("TOKEN")

# --- TELEGRAM ---
CHAT_ID = "1292276069"

# --- GMAIL ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = set()
aktif_kullanicilar.add(CHAT_ID)

son_yeni_mail_yok_mesaji = None
son_saatlik_ozet = None
son_3_mail_idleri = []

# Tek kelimeler
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
    "dunya"
]

# Daha güçlü phrase filtreleri
ANAHTAR_IFADELER = [
    "fifa code",
    "verification code",
    "security code"
]

# --- WEB KEEPALIVE ---
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return f"Mail bot çalışıyor - {VERSION}", 200


def run_web():
    port = int(os.getenv("PORT", 10000))
    web_app.run(host="0.0.0.0", port=port)


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
                content_type = msg.get_content_type()

                if content_type == "text/html":
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
    gonderen_l = (gonderen or "").lower()
    konu_l = (konu or "").lower()
    govde_l = (govde or "").lower()

    tum_metin = f"{gonderen_l} {konu_l} {govde_l}"

    kelime_eslesmesi = any(
        kelime in tum_metin for kelime in ANAHTAR_KELIMELER
    )

    ifade_eslesmesi = any(
        ifade in tum_metin for ifade in ANAHTAR_IFADELER
    )

    return kelime_eslesmesi or ifade_eslesmesi


def mailleri_getir():
    bulunan_mailler = []

    try:
        mail = imap_baglan()
        klasorler = ["INBOX", "[Gmail]/Spam"]

        for klasor in klasorler:
            try:
                status, _ = mail.select(klasor)
                if status != "OK":
                    continue

                since_date = (datetime.now() - timedelta(days=3)).strftime("%d-%b-%Y")
                status, data = mail.uid("search", None, f'(SINCE "{since_date}")')

                if status != "OK":
                    continue

                uid_list = data[0].split()

                for uid in uid_list[-50:]:
                    status, msg_data = mail.uid("fetch", uid, "(RFC822)")
                    if status != "OK" or not msg_data or not msg_data[0]:
                        continue

                    raw_email = msg_data[0][1]
                    msg = email.message_from_bytes(raw_email)

                    konu = decode_mime_text(msg.get("Subject", ""))
                    gonderen = decode_mime_text(msg.get("From", ""))
                    tarih = decode_mime_text(msg.get("Date", ""))
                    govde = govdeyi_al(msg)

                    if not ilgili_mail_mi(gonderen, konu, govde):
                        continue

                    if len(govde) > 500:
                        govde = govde[:500] + "..."

                    kimlik = f"{klasor}:{uid.decode()}"

                    bulunan_mailler.append({
                        "id": kimlik,
                        "konu": konu or "(Konu yok)",
                        "gonderen": gonderen or "(Gönderen yok)",
                        "tarih": tarih or "",
                        "govde": govde or "(İçerik yok)",
                        "klasor": klasor
                    })

            except Exception:
                continue

        mail.logout()

    except Exception as e:
        print(f"Mail okuma hatası: {e}")

    bulunan_mailler.reverse()
    return bulunan_mailler


async def tum_kullanicilara_gonder(context, mesaj):
    for chat_id in aktif_kullanicilar:
        await context.bot.send_message(
            chat_id=chat_id,
            text=mesaj,
            parse_mode="HTML"
        )


# --- MAIL KONTROL ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_yeni_mail_yok_mesaji
    global son_saatlik_ozet
    global son_3_mail_idleri

    try:
        simdi = datetime.now()
        mailler = mailleri_getir()
        son_3_mail = mailler[:3]
        yeni_son_3_idleri = [mail_item["id"] for mail_item in son_3_mail]

        # İlk çalışmada hafızaya al, bildirim atma
        if not son_3_mail_idleri:
            son_3_mail_idleri = yeni_son_3_idleri.copy()

        # Son 3 mail değiştiyse yeni mail bildirimi at
        elif yeni_son_3_idleri != son_3_mail_idleri:
            son_3_mail_idleri = yeni_son_3_idleri.copy()

            if son_3_mail:
                en_yeni_mail = son_3_mail[0]
                klasor_adi = "Spam" if "Spam" in en_yeni_mail["klasor"] else "Inbox"

                mesaj = (
                    f"📩 <b>Yeni mail geldi!</b>\n\n"
                    f"📌 <b>Konu:</b> {escape(en_yeni_mail['konu'])}\n"
                    f"👤 <b>Gönderen:</b> {escape(en_yeni_mail['gonderen'])}\n"
                    f"📂 <b>Klasör:</b> {escape(klasor_adi)}\n"
                    f"📝 <b>İçerik:</b> {escape(en_yeni_mail['govde'])}"
                )

                await tum_kullanicilara_gonder(context, mesaj)

        # Son 3 değişmediyse sadece 15 dakikada bir bilgi ver
        else:
            if (
                son_yeni_mail_yok_mesaji is None
                or (simdi - son_yeni_mail_yok_mesaji) >= timedelta(minutes=15)
            ):
                await tum_kullanicilara_gonder(
                    context,
                    f"ℹ️ Yeni ilgili mail yok... ({VERSION})"
                )
                son_yeni_mail_yok_mesaji = simdi

        # Saatte 1 kez son 3 mail özeti
        bu_saat = simdi.replace(minute=0, second=0, microsecond=0)

        if son_saatlik_ozet != bu_saat:
            son_saatlik_ozet = bu_saat

            if son_3_mail:
                mesaj = f"🕐 <b>Son 3 ilgili mail</b> ({VERSION})\n\n"

                for i, mail_item in enumerate(son_3_mail, start=1):
                    klasor_adi = "Spam" if "Spam" in mail_item["klasor"] else "Inbox"
                    mesaj += f"{i}. <b>{escape(mail_item['konu'])}</b>\n"
                    mesaj += f"👤 {escape(mail_item['gonderen'])}\n"
                    mesaj += f"📂 {escape(klasor_adi)}\n"
                    mesaj += f"📝 {escape(mail_item['govde'])}\n\n"

                await tum_kullanicilara_gonder(context, mesaj)

    except Exception as e:
        for chat_id in aktif_kullanicilar:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Hata: {escape(str(e))}",
                parse_mode="HTML"
            )


# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global son_3_mail_idleri

    chat_id = str(update.effective_chat.id)
    aktif_kullanicilar.add(chat_id)

    await update.message.reply_text(f"🚀 Mail takibi başlatıldı! ({VERSION})")

    mailler = mailleri_getir()
    son_3_mail_idleri = [mail_item["id"] for mail_item in mailler[:3]]


# --- /version ---
async def version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📌 Aktif sürüm: {VERSION}")


# --- BAŞLANGIÇ MESAJI ---
async def baslangic_mesaji(app):
    for chat_id in aktif_kullanicilar:
        await app.bot.send_message(
            chat_id=chat_id,
            text=f"🤖 Bot aktif ({VERSION})"
        )


# --- BOT BAŞLAT ---
Thread(target=run_web, daemon=True).start()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("version", version))

# 60 saniyede bir çalışır
app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)

app.post_init = baslangic_mesaji

print(f"🤖 Mail bot çalışıyor... {VERSION}")
app.run_polling()