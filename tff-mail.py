import os
import re
import time
import socket
import imaplib
import email
import logging
import threading
import requests
from email.header import decode_header
from datetime import datetime, timedelta
from html import escape
from bs4 import BeautifulSoup

VERSION = "v6.0 NO POLLING STABLE"

TOKEN = os.getenv("TOKEN")
CHAT_ID = int(os.getenv("CHAT_ID", "1292276069"))

EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_APP_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")

aktif_kullanicilar = {CHAT_ID}
son_5_mail_idleri = []
last_summary_time = None
alert_mode_until = None
ilk_kurulum_tamamlandi = False

socket.setdefaulttimeout(30)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

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


def telegram_mesaj_gonder(mesaj):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    for chat_id in aktif_kullanicilar:
        try:
            response = requests.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": mesaj,
                    "parse_mode": "HTML",
                },
                timeout=20
            )
            logging.info(f"Telegram mesaj gönderildi: {chat_id} | status={response.status_code}")
        except Exception as e:
            logging.exception(f"Telegram mesaj gönderim hatası ({chat_id}): {e}")


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
    logging.info("IMAP bağlantısı kuruluyor...")
    mail = imaplib.IMAP4_SSL("imap.gmail.com", timeout=30)
    mail.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
    logging.info("IMAP giriş başarılı")
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

            except Exception as e:
                logging.warning(f"Mail body parse hatası: {e}")
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
        except Exception as e:
            logging.warning(f"Tek parça mail parse hatası: {e}")

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


def mail_kontrol():
    global son_5_mail_idleri, last_summary_time, alert_mode_until, ilk_kurulum_tamamlandi

    logging.info("Mail kontrol başladı")

    try:
        mailler = mailleri_getir()
    except Exception as e:
        logging.exception(f"Mail çekme hatası: {e}")
        telegram_mesaj_gonder(f"❌ <b>Mail çekme hatası:</b> {escape(str(e))}")
        return

    simdi = datetime.now()
    son5 = mailler[:5]
    ids = [m["id"] for m in son5]

    if not ilk_kurulum_tamamlandi:
        son_5_mail_idleri = ids.copy()
        last_summary_time = simdi
        ilk_kurulum_tamamlandi = True
        logging.info("İlk kurulum tamamlandı, mevcut mailler hafızaya alındı")
        return

    yeni_mail_var = ids != son_5_mail_idleri

    if yeni_mail_var:
        son_5_mail_idleri = ids.copy()
        alert_mode_until = simdi + timedelta(minutes=10)

        mesaj = (
            "🚨 <b>Yeni mail bildirimi</b>\n\n"
            "📌 <b>Son 3 mail:</b>\n\n"
            f"{son_mail_basliklari(mailler, 3)}\n\n"
            "⏱ <b>Alarm modu:</b> 10 dakika boyunca her dakika son 5 konu"
        )
        telegram_mesaj_gonder(mesaj)

    if alert_mode_until and simdi < alert_mode_until:
        mesaj = (
            "🚨 <b>SON 5 MAİL KONUSU</b>\n\n"
            f"{son_mail_basliklari(mailler, 5)}"
        )
        telegram_mesaj_gonder(mesaj)

    if alert_mode_until and simdi >= alert_mode_until:
        alert_mode_until = None
        telegram_mesaj_gonder("✅ <b>Alarm modu sona erdi</b>")

    if last_summary_time and (simdi - last_summary_time).total_seconds() >= 900:
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

        telegram_mesaj_gonder(mesaj)
        last_summary_time = simdi
        logging.info("15 dakikalık özet gönderildi")

    logging.info("Mail kontrol tamamlandı")


def surekli_mail_dongusu():
    telegram_mesaj_gonder(f"🤖 Bot aktif ({VERSION})")
    logging.info("Sürekli mail döngüsü başlatıldı")

    while True:
        try:
            mail_kontrol()
        except Exception as e:
            logging.exception(f"Döngü içi kritik hata: {e}")

        logging.info("60 saniye bekleniyor...")
        time.sleep(60)


def main():
    worker_thread = threading.Thread(target=surekli_mail_dongusu, daemon=False)
    worker_thread.start()
    worker_thread.join()


if __name__ == "__main__":
    main()