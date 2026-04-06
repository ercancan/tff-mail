import os
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
from flask import Flask
from threading import Thread
from html import escape
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

TOKEN = os.getenv("TOKEN")

# --- TELEGRAM ---
CHAT_ID = "1292276069"

# --- GMAIL ---
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = set()
aktif_kullanicilar.add(CHAT_ID)

gonderilen_mailler = set()
son_yeni_mail_yok_mesaji = None
yeni_mail_bildirim_bitis = None
son_saatlik_ozet = None

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
    return "Mail bot çalışıyor", 200

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


def govdeyi_al(msg):
    govde = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition"))

            if content_type == "text/plain" and "attachment" not in content_disposition:
                try:
                    govde = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8",
                        errors="ignore"
                    ).strip()
                    break
                except:
                    pass
    else:
        try:
            govde = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8",
                errors="ignore"
            ).strip()
        except:
            govde = ""

    return govde


def tff_mail_mi(gonderen, konu, govde):
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

                    if not tff_mail_mi(gonderen, konu, govde):
                        continue

                    if len(govde) > 300:
                        govde = govde[:300] + "..."

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

async def bot_canli(context: ContextTypes.DEFAULT_TYPE):
    for chat_id in aktif_kullanicilar:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🤖 Bot çalışıyor (test)"
        )
        
# --- MAIL KONTROL ---
async def mail_kontrol(context: ContextTypes.DEFAULT_TYPE):
    global son_yeni_mail_yok_mesaji
    global yeni_mail_bildirim_bitis
    global son_saatlik_ozet

    try:
        simdi = datetime.now()
        mailler = mailleri_getir()

        yeni_mail_var = False

        for mail_item in mailler:
            if mail_item["id"] not in gonderilen_mailler:
                gonderilen_mailler.add(mail_item["id"])
                yeni_mail_var = True

        if yeni_mail_var:
            yeni_mail_bildirim_bitis = simdi + timedelta(minutes=15)
            await tum_kullanicilara_gonder(
                context,
                "📩 <b>Yeni ilgili mail geldi, gözden kaçırma!</b>"
            )

        elif yeni_mail_bildirim_bitis and simdi < yeni_mail_bildirim_bitis:
            await tum_kullanicilara_gonder(
                context,
                "📩 <b>Yeni ilgili mail geldi, gözden kaçırma!</b>"
            )

        else:
            if (
                son_yeni_mail_yok_mesaji is None
                or (simdi - son_yeni_mail_yok_mesaji) >= timedelta(minutes=15)
            ):
                await tum_kullanicilara_gonder(
                    context,
                    "ℹ️ Yeni ilgili mail yok..."
                )
                son_yeni_mail_yok_mesaji = simdi

        bu_saat = simdi.replace(minute=0, second=0, microsecond=0)

        if son_saatlik_ozet != bu_saat:
            son_saatlik_ozet = bu_saat

            son_3_mail = mailler[:3]

            if son_3_mail:
                mesaj = "🕐 <b>Son 3 ilgili mail</b>\n\n"
                for i, mail_item in enumerate(son_3_mail, start=1):
                    klasor_adi = "Spam" if "Spam" in mail_item["klasor"] else "Inbox"
                    mesaj += f"{i}. <b>{mail_item['konu']}</b>\n"
                    mesaj += f"👤 {mail_item['gonderen']}\n"
                    mesaj += f"📂 {klasor_adi}\n"
                    mesaj += f"📝 {mail_item['govde']}\n\n"

                await tum_kullanicilara_gonder(context, mesaj)

    except Exception as e:
        for chat_id in aktif_kullanicilar:
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Hata: {e}"
            )


# --- /start ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    aktif_kullanicilar.add(chat_id)

    await update.message.reply_text("🚀 Mail takibi başlatıldı!")

    mailler = mailleri_getir()
    for mail_item in mailler:
        gonderilen_mailler.add(mail_item["id"])


# --- BOT BAŞLAT ---
Thread(target=run_web).start()

app = ApplicationBuilder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))

# 60 saniyede bir çalışır
app.job_queue.run_repeating(mail_kontrol, interval=60, first=10)
app.job_queue.run_repeating(bot_canli, interval=60, first=5)

print("🤖 Mail bot çalışıyor...")
app.run_polling()