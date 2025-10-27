import telebot
import os
import yt_dlp
import requests
from urllib.parse import quote

MTOW = "8389421211:AAFpS885ESGYHyEz4dxuXz0_nnYg1BFNDr8"
bot = telebot.TeleBot(MTOW, parse_mode="Markdown")


@bot.message_handler(commands=['start'])
def start(msg):
    user_name = msg.from_user.username
    bot.send_message(
        msg.chat.id,
        f"Hoş geldin {user_name} \n\n"
        "Kullanım örneği:\n"
        "/music sezen zalim\n"
        "/music https://youtu.be/link\n\n"
        "Format: .m4a ve ya ffmpeg yüklü değilse .webm ile atar"
    )

@bot.message_handler(commands=['help'])
def help(msg):
    chat_id = msg.chat.id
    bot.send_message(chat_id, "admin @mtowi")


@bot.message_handler(commands=['music'])
def music(msg):
    try:
        args = msg.text.split(' ', 1)
        if len(args) < 2:
            bot.reply_to(msg, "Lütfen şarkı adını veya YouTube bağlantısını yaz.\n\nÖrnek: /music sezen zalim")
            return
        mtowi = args[1].strip()
        hal = bot.send_message(msg.chat.id, "🔍 Şarkı aranıyor, lütfen bekle...")
        if "youtube.com" in mtowi or "youtu.be" in mtowi:
            url = mtowi
        else:
            url = youtube_ara(mtowi)
        if not url:
            bot.edit_message_text("Şarkı bulunamadı, başka bir isim dene.", msg.chat.id, hal.message_id)
            return
        bot.edit_message_text("🎧 Şarkı indiriliyor...", msg.chat.id, hal.message_id)
        try:
            ydl_opts = {
                'format': 'bestaudio[ext=m4a]',
                'outtmpl': '%(title)s.%(ext)s',
                'noplaylist': True,
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                dosya_adi = ydl.prepare_filename(info)
        except Exception:
            ydl_opts = {
                'format': 'bestaudio',
                'outtmpl': '%(title)s.%(ext)s',
                'noplaylist': True,
                'quiet': True,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                dosya_adi = ydl.prepare_filename(info)

        isim = info.get("title", "Bilinmeyen Şarkı")
        sarkıcı = info.get("uploader", "Bilinmeyen")
        sure = info.get("duration", 0)
        thumbnail = info.get("thumbnail")
        caption = (
            f"🎵 {isim}\n"
            f"👤 Sanatçı: {sarkıcı}\n"
            f"⏱ Süre: {int(sure // 60)}:{int(sure % 60):02d}\n"
            f"🔗 [YouTube]({url})"
        )
        bot.edit_message_text(f"✅ {isim} indirildi, gönderiliyor...", msg.chat.id, hal.message_id)
        thumb_path = None
        if thumbnail:
            try:
                thumb_data = requests.get(thumbnail).content
                thumb_path = f"{isim}.jpg"
                with open(thumb_path, "wb") as f:
                    f.write(thumb_data)
            except:
                thumb_path = None

        with open(dosya_adi, "rb") as sarki:
            bot.send_audio(
                msg.chat.id,
                sarki,
                caption=caption,
                title=isim,
                performer=sarkıcı,
                thumb=open(thumb_path, "rb") if thumb_path else None
            )
        os.remove(dosya_adi)
        if thumb_path and os.path.exists(thumb_path):
            os.remove(thumb_path)
        bot.send_message(msg.chat.id, "✅ Şarkı başarıyla gönderildi!")
    except Exception as e:
        bot.send_message(msg.chat.id, f"Bir hata oluştu:\n`{e}`")


def youtube_ara(mtowi):
    try:
        q = quote(mtowi)
        html = requests.get(f"https://www.youtube.com/results?search_query={q}", timeout=10).text
        idx = html.find("/watch?v=")
        if idx != -1:
            video_id = html[idx:idx + 20]
            return "https://www.youtube.com" + video_id
    except:
        pass
    return None


bot.infinity_polling()
