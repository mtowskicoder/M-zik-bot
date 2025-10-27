# ---------- PART 1/3: Temel altyapı, bahis/iddia/yt/risk/bonus, veri yükleme ----------
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, threading, time, random, html
from datetime import datetime
import telebot
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ---------- AYARLAR (TOKEN'I BURAYA YAZ) ----------
API_TOKEN = "7808920707:AAEbr_jqg7Yv5iyhuHWbALAujiZQp207wBg"  # <<< buraya token'ını koy
ADMINS = ['6126105727', '7738678238']
CHANNEL_ID = '-1002660178883'

INITIAL_BALANCE = 10_000
DEFAULT_WIN_CHANCE = 0.5

BONUS_MIN = 10_000
BONUS_MAX = 10_000_000
BONUS_COOLDOWN_SECONDS = 24 * 60 * 60

# Dosya isimleri
BALANCE_FILE = 'balances.json'
USERS_FILE = 'users.json'
SETTINGS_FILE = 'settings.json'
BONUS_FILE = 'bonuses.json'
INVENTORY_FILE = 'inventory.json'
BETS_FILE = 'bets.json'
CREDITS_FILE = 'credits.json'  # kredi kayıtları

USD_RATE = 34.50
TL_DISPLAY_CAP = 999_000_000_000

# ---------- BOT ----------
bot = telebot.TeleBot(API_TOKEN)

# ---------- GLOBAL VERİLER & KİLİTLER ----------
file_lock = threading.Lock()
bets_lock = threading.Lock()
waiting_lock = threading.Lock()
inv_lock = threading.Lock()
bonus_lock = threading.Lock()
credit_lock = threading.Lock()

balances = {}
users = {}
settings = {}
bonuses = {}
inventory = {}
bets = {}
waiting_for_guess = {}  # user_id -> {'chat_id':..., 'timestamp': datetime}
credits = {}  # kredi kayıtları: credit_id -> {...}

# ---------- MARKET KATALOGU (Part2 kullanacak) ----------
MARKET = {
    "ev":      {"name": "Ev", "price": 5_000_000, "emoji": "🏠", "desc": "Standart daire"},
    "luks_ev": {"name": "Lüks Ev", "price": 50_000_000, "emoji": "🏡", "desc": "Villa / rezidans"},
    "araba":   {"name": "Araba", "price": 2_000_000, "emoji": "🚗", "desc": "Orta sınıf otomobil"},
    "spor":    {"name": "Spor Araba", "price": 15_000_000, "emoji": "🏎️", "desc": "Lüks performans aracı"},
    "altin":   {"name": "Altın (1kg)", "price": 500_000, "emoji": "🪙", "desc": "1 kg altın (örnek)"},
    "elmas":   {"name": "Elmas", "price": 5_000_000, "emoji": "💎", "desc": "Nadir mücevher"},
    "sirket":  {"name": "Şirket", "price": 100_000_000, "emoji": "🏢", "desc": "Gelir getiren yatırım"},
}

# ---------- KÜÇÜK YARDIMCI FONKSİYONLAR (hiç log yok) ----------
def load_json(path, default):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, type(default)):
                    return data
        return default
    except Exception:
        return default

def save_json(path, data):
    try:
        with file_lock:
            tmp = path + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=4)
            os.replace(tmp, path)
    except Exception:
        pass

def format_money(amount):
    try:
        return f"{int(amount):,}"
    except Exception:
        return str(amount)

def initialize_user(user_id, user_info=None):
    uid = str(user_id)
    if user_info is None:
        user_info = {}
    changed = False
    if uid not in balances:
        balances[uid] = INITIAL_BALANCE
        changed = True
    if uid not in users:
        users[uid] = {
            'first_name': user_info.get('first_name', 'Bilinmeyen'),
            'username': user_info.get('username'),
            'join_date': str(datetime.now()),
            'admin_notified': False
        }
        changed = True
    if uid not in inventory:
        inventory[uid] = {}
        changed = True
    if changed:
        try:
            save_json(BALANCE_FILE, balances)
            save_json(USERS_FILE, users)
            save_json(INVENTORY_FILE, inventory)
        except Exception:
            pass

def get_user_link(user_id, user_info):
    uid = str(user_id)
    display = html.escape(user_info.get('first_name') or user_info.get('username') or f"Kullanıcı_{uid}")
    username = user_info.get('username')
    if username:
        username = html.escape(username).lstrip('@')
        return f"https://t.me/{username}", display
    return f"tg://user?id={uid}", display

def convert_try_to_usd_fixed(try_amount):
    try:
        return float(try_amount) / USD_RATE
    except Exception:
        return None

def usd_display_from_tl(tl_amount):
    usd = convert_try_to_usd_fixed(tl_amount)
    if usd is None:
        return None
    try:
        if tl_amount > TL_DISPLAY_CAP:
            return "Servetiniz değer biçilemez 💰"
        return f"${usd:,.2f}"
    except Exception:
        return None

# ---------- KREDİ YARDIMCI (krediler için repayment çağrıları) ----------
def apply_credit_repayment_on_earn(user_id, earned_amount):
    """
    Kullanıcı para kazandığında bu fonksiyon çağrılmalı.
    - credits: her kredi kaydı -> {'id','user_id','amount','remaining','rate':repay_rate(0-1),'status'}
    - Bu fonksiyon earned_amount'ın belirli oranını (kredi.rate * earned) alıp kalan kredilere uygular.
    """
    uid = str(user_id)
    with credit_lock:
        # sıralı: eski krediler önce ödensin
        user_credits = sorted([c for c in credits.values() if c['user_id']==uid and c['status']=='active'], key=lambda x: x['created_at'])
        remaining_to_apply = 0.0
        for c in user_credits:
            repay_rate = c.get('rate', 0.1)  # default %10
            portion = earned_amount * repay_rate
            if portion <= 0:
                continue
            to_apply = min(portion, c['remaining'])
            c['remaining'] = round(c['remaining'] - to_apply)
            if c['remaining'] <= 0:
                c['status'] = 'paid'
                c['remaining'] = 0
            # save after each modification
            save_json(CREDITS_FILE, credits)
        # no returns needed

# ---------- BAHİS (iddia) GÜNCELLEYİCİ THREAD ----------
def bets_updater_loop():
    while True:
        try:
            with bets_lock:
                for chat_id, game in list(bets.items()):
                    try:
                        remaining_time = game['duration'] - (datetime.now() - game['start_time']).total_seconds()
                        if remaining_time <= 0:
                            end_bet(chat_id)
                            continue
                        minutes = int(remaining_time // 60)
                        seconds = int(remaining_time % 60)
                        total = game['duration']
                        elapsed = total - remaining_time
                        progress = int((elapsed) / total * 10) if total else 0
                        progress = max(0, min(10, progress))
                        progress_bar = "█" * progress + "▒" * (10 - progress)
                        welcome_message = f"🎲 İddia başladı! 1-100 arası bir sayı tuttum.\nKatılmak için butona bas ve tahminini gönder.\nKalan süre: {minutes}:{seconds:02d} [{progress_bar}]"
                        try:
                            bot.edit_message_text(welcome_message, chat_id=int(chat_id), message_id=game['message_id'],
                                                  reply_markup=InlineKeyboardMarkup().add(InlineKeyboardButton("🎲 Katıl", callback_data=f"join_bet_{chat_id}")))
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception:
            pass
        time.sleep(5)

# ---------- KOMUTLAR: start / komutlar / bakiye / zenginler / borc / idm ----------
@bot.message_handler(commands=['start'])
def cmd_start(message):
    uid = str(message.from_user.id)
    initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    # notify admins once
    try:
        if not users.get(uid, {}).get('admin_notified'):
            for adm in ADMINS:
                try:
                    bot.send_message(int(adm), f"Yeni kullanıcı: {uid} - {message.from_user.first_name}", disable_web_page_preview=True)
                except Exception:
                    pass
            users[uid]['admin_notified'] = True
            save_json(USERS_FILE, users)
    except Exception:
        pass
    link, name = get_user_link(uid, {'first_name': message.from_user.first_name, 'username': message.from_user.username})
    markup = InlineKeyboardMarkup()
    try:
        markup.add(InlineKeyboardButton("Kanal", url="https://t.me/mtowski"), InlineKeyboardButton("Sahip", url="https://t.me/mtowskii"))
    except Exception:
        pass
    welcome = f'🎲 Kumar botuna hoş geldin, <a href="{link}">{name}</a>!\nOynamak için /komutlar yaz\nİyi şanslar 💸🤑'
    try:
        bot.send_message(message.chat.id, welcome, reply_markup=markup, parse_mode='HTML', disable_web_page_preview=True)
    except Exception:
        pass

@bot.message_handler(commands=['komutlar'])
def cmd_komutlar(message):
    user_count = len(users)
    text = (
        f"Kullanıcılar : {user_count}\n\n"
        "━━━━━ 𝗞𝗨𝗟𝗟𝗔𝗡𝗜𝗖𝗜 𝗞𝗢𝗠𝗨𝗧𝗟𝗔𝗥𝗜 ━━━━━\n\n"
        "/start ▶️: Oyunu başlatır 💸\n"
        "/risk 💸: Paranı katla veya kaybet\n"
        "/borc 🤝: Bir kullanıcıya para atar\n"
        "/zenginler 🏅: En zenginleri gösterir\n"
        "/bakiye 💰: Toplam paranı gösterir\n"
        "/yt 🎲: Yazı tura oyunu oynar\n"
        "/iddia 🎰: Sayı tahmin oyunu başlatır\n"
        "/idm 🆔 kişinin id'sini gösterir\n"
        "/bonus 🎁: Günlük bonus al\n\n"
        "━━━━━ 𝗔𝗗𝗠𝗜𝗡 𝗞𝗢𝗠𝗨𝗧𝗟𝗔𝗥𝗜 ━━━━━\n"
        "/sil 🧹: Kullanıcının bakiyesini sıfırlar [ADMİN]\n"
        "/gonder 🎁: Kullanıcıya para gönderir [ADMİN]\n"
        "/ceza ❌: Kullanıcıdan para eksiltir [ADMİN]\n"
        "/sans 🎯: Risk kazanma şansını ayarla [ADMİN]\n\n"
        "Diğer komutlar: /market, /envanter, /hediye, /sat, /buy, /kredi"
    )
    try:
        bot.reply_to(message, text, disable_web_page_preview=True)
    except Exception:
        pass

@bot.message_handler(commands=['bakiye'])
def cmd_bakiye(message):
    uid = str(message.from_user.id)
    initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    tl_amount = balances.get(uid, 0)
    usd_text = usd_display_from_tl(tl_amount)
    if usd_text is None:
        response = f"💰 Bakiyen: {format_money(tl_amount)} TL"
    else:
        response = f"💰 Bakiyen: {format_money(tl_amount)} TL\n💵 Tahmini değer: {usd_text}"
    try:
        bot.reply_to(message, response, parse_mode='HTML', disable_web_page_preview=True)
    except Exception:
        pass

@bot.message_handler(commands=['zenginler'])
def cmd_zenginler(message):
    if not balances:
        try:
            bot.reply_to(message, "Henüz kimsenin bakiyesi yok.", disable_web_page_preview=True)
        except Exception:
            pass
        return
    sorted_list = sorted([(uid, bal) for uid, bal in balances.items() if uid not in ADMINS], key=lambda x: x[1], reverse=True)[:10]
    lines = ["🏅 Zenginler Listesi:"]
    for i, (uid, bal) in enumerate(sorted_list, 1):
        try:
            uinfo = users.get(uid)
            if not uinfo:
                try:
                    tg = bot.get_chat(int(uid))
                    uinfo = {'first_name': getattr(tg, 'first_name', f'Kullanıcı_{uid}'), 'username': getattr(tg, 'username', None)}
                except Exception:
                    uinfo = {'first_name': f'Kullanıcı_{uid}', 'username': None}
            link, name = get_user_link(uid, uinfo)
            usd_text = usd_display_from_tl(bal)
            if usd_text is None:
                line = f"{i}. <a href='{link}'>{name}</a> - {format_money(bal)} TL"
            else:
                line = f"{i}. <a href='{link}'>{name}</a> - {format_money(bal)} TL - {usd_text}"
            lines.append(line)
        except Exception:
            lines.append(f"{i}. Kullanıcı {uid} - {format_money(bal)} TL")
    text = "\n".join(lines)
    try:
        bot.reply_to(message, text, parse_mode='HTML', disable_web_page_preview=True)
    except Exception:
        pass
    if str(message.from_user.id) in ADMINS:
        try:
            bot.send_message(int(CHANNEL_ID), text, parse_mode='HTML', disable_web_page_preview=True)
        except Exception:
            pass

@bot.message_handler(commands=['borc'])
def cmd_borc(message):
    sender = str(message.from_user.id)
    initialize_user(sender, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    parts = message.text.split()
    if len(parts) != 3 or not parts[1].isdigit() or not parts[2].isdigit():
        try:
            bot.reply_to(message, "Kullanım: /borc <kullanıcı_id> <miktar>", disable_web_page_preview=True)
        except Exception:
            pass
        return
    to_id = parts[1]; miktar = int(parts[2])
    if miktar <= 0:
        try:
            bot.reply_to(message, "Miktar sıfırdan büyük olmalı!", disable_web_page_preview=True)
        except Exception:
            pass
        return
    if balances.get(sender, 0) < miktar:
        try:
            bot.reply_to(message, "Yetersiz bakiye!", disable_web_page_preview=True)
        except Exception:
            pass
        return
    initialize_user(to_id, {'first_name': "Bilinmeyen", 'username': None})
    balances[sender] = balances.get(sender, 0) - miktar
    balances[to_id] = balances.get(to_id, 0) + miktar
    try:
        save_json(BALANCE_FILE, balances)
    except Exception:
        pass
    try:
        bot.reply_to(message, f"✅ {to_id} ID'li kullanıcıya {format_money(miktar)} TL gönderildi.", disable_web_page_preview=True)
    except Exception:
        pass

@bot.message_handler(commands=['idm'])
def cmd_idm(message):
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        try:
            bot.reply_to(message, f"🆔 {target.first_name} ID: <code>{target.id}</code>", parse_mode='HTML', disable_web_page_preview=True)
        except Exception:
            pass
    else:
        try:
            bot.reply_to(message, f"🆔 {message.from_user.first_name} ID: <code>{message.from_user.id}</code>", parse_mode='HTML', disable_web_page_preview=True)
        except Exception:
            pass

# devamı Part2 ve Part3'te (market/envanter/hediye/sat + kredi sistemi)
# ---------- PART 2/3: Market / Envanter / Hediye / Sat ----------
# (Bu kısmı Part1'den hemen sonra yapıştırıp tek dosyada devam ettir)
# MARKET tanımı Part1'de var; burada komutlar ve callbackler:

# ortak satın alma fonksiyonu (used by callback & /buy)
def purchase_item_for_user(user_id, key):
    uid = str(user_id)
    if key not in MARKET:
        return False, "Bilinmeyen ürün."
    price = MARKET[key]['price']
    if balances.get(uid, 0) < price:
        return False, "Yetersiz bakiye!"
    balances[uid] = balances.get(uid, 0) - price
    with inv_lock:
        inv = inventory.setdefault(uid, {})
        arr = inv.setdefault(key, [])
        arr.append({"from": None, "time": datetime.now().isoformat(), "note": "purchased"})
        try:
            save_json(INVENTORY_FILE, inventory)
        except Exception:
            pass
    try:
        save_json(BALANCE_FILE, balances)
    except Exception:
        pass
    return True, f"✅ {MARKET[key]['emoji']} {MARKET[key]['name']} satın alındı! Yeni bakiyen: {format_money(balances[uid])} TL"

@bot.message_handler(commands=['market'])
def cmd_market(message):
    uid = str(message.from_user.id)
    initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    markup = InlineKeyboardMarkup()
    for key, item in MARKET.items():
        label = f"{item['emoji']} {item['name']} - {format_money(item['price'])} TL"
        markup.add(InlineKeyboardButton(label, callback_data=f"buy_{key}"))
    text_lines = ["🏪 Market - satın almak için ürüne tıkla veya fallback olarak `/buy <ürün_key>` kullan:"]
    for k, it in MARKET.items():
        text_lines.append(f"{k} — {it['emoji']} {it['name']} — {format_money(it['price'])} TL")
    text = "\n".join(text_lines)
    try:
        bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode='Markdown', disable_web_page_preview=True)
    except Exception:
        try:
            bot.reply_to(message, text, disable_web_page_preview=True)
        except Exception:
            pass

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith('buy_'))
def cb_buy(call):
    uid = str(call.from_user.id)
    initialize_user(uid, {'first_name': call.from_user.first_name or "Bilinmeyen", 'username': call.from_user.username})
    parts = call.data.split('_', 1)
    if len(parts) != 2:
        try:
            bot.answer_callback_query(call.id, "Geçersiz işlem")
        except Exception:
            pass
        return
    key = parts[1]
    ok, msg = purchase_item_for_user(uid, key)
    try:
        bot.answer_callback_query(call.id, msg)
    except Exception:
        pass
    try:
        bot.send_message(call.message.chat.id, msg, disable_web_page_preview=True)
    except Exception:
        pass

@bot.message_handler(commands=['buy'])
def cmd_buy(message):
    uid = str(message.from_user.id)
    initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        try:
            bot.reply_to(message, "Kullanım: /buy <ürün_key>  (örnek: /buy araba)", disable_web_page_preview=True)
        except Exception:
            pass
        return
    key = parts[1].strip().lower()
    ok, msg = purchase_item_for_user(uid, key)
    try:
        bot.reply_to(message, msg, disable_web_page_preview=True)
    except Exception:
        pass

# Envanter görüntüleme fonksiyonu
def compile_inventory_lines_for(target_id, display_name):
    inv = inventory.get(str(target_id), {})
    lines = [f"📦 {html.escape(display_name)}'ın Envanteri:"]
    if not inv:
        lines.append("Envanter boş.")
        return lines
    for key, entries in inv.items():
        item = MARKET.get(key)
        total = len(entries)
        purchased = sum(1 for e in entries if e.get('from') is None)
        gifts = {}
        for e in entries:
            if e.get('from') is not None:
                s = str(e.get('from'))
                gifts[s] = gifts.get(s, 0) + 1
        name = item['name'] if item else key
        emoji = item['emoji'] if item else ''
        line = f"{emoji} {name} x{total}"
        sub = []
        if purchased:
            sub.append(f"satınalma: {purchased}")
        if gifts:
            parts = []
            for sid, cnt in gifts.items():
                su = users.get(sid)
                if su and su.get('username'):
                    sender_disp = f"@{su['username']}"
                elif su:
                    sender_disp = su.get('first_name', f'Kullanıcı_{sid}')
                else:
                    try:
                        tg = bot.get_chat(int(sid))
                        sender_disp = getattr(tg, 'username', getattr(tg, 'first_name', f'Kullanıcı_{sid}'))
                    except Exception:
                        sender_disp = f'Kullanıcı_{sid}'
                parts.append(f"{sender_disp}: {cnt}")
            sub.append("hediye(" + ", ".join(parts) + ")")
        if sub:
            line += " (" + "; ".join(sub) + ")"
        lines.append(line)
    return lines

@bot.message_handler(commands=['envanter'])
def cmd_envanter(message):
    # yanıt varsa onun envanteri, yoksa /envanter <id> veya kendi
    if message.reply_to_message:
        target = message.reply_to_message.from_user
        tid = str(target.id)
        initialize_user(tid, {'first_name': target.first_name or "Bilinmeyen", 'username': target.username})
        lines = compile_inventory_lines_for(tid, target.first_name or target.username or f"Kullanıcı_{tid}")
    else:
        parts = message.text.split(maxsplit=1)
        if len(parts) == 2 and parts[1].isdigit():
            tid = parts[1].strip()
            initialize_user(tid, {'first_name': "Bilinmeyen", 'username': None})
            try:
                tg = bot.get_chat(int(tid))
                display = getattr(tg, 'first_name', f"Kullanıcı_{tid}")
            except Exception:
                display = f"Kullanıcı_{tid}"
            lines = compile_inventory_lines_for(tid, display)
        else:
            uid = str(message.from_user.id)
            initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
            lines = compile_inventory_lines_for(uid, message.from_user.first_name or message.from_user.username or f"Kullanıcı_{uid}")
    try:
        bot.reply_to(message, "\n".join(lines), parse_mode='HTML', disable_web_page_preview=True)
    except Exception:
        pass

@bot.message_handler(commands=['hediye'])
def cmd_hediye(message):
    if not message.reply_to_message:
        try:
            bot.reply_to(message, "Hediye göndermek için bir kullanıcıya yanıt ver ve `/hediye <ürün>` yaz.", disable_web_page_preview=True)
        except Exception:
            pass
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        try:
            bot.reply_to(message, "Kullanım: /hediye <ürün_key> (örnek: /hediye araba)", disable_web_page_preview=True)
        except Exception:
            pass
        return
    raw = parts[1].strip().lower()
    key = None
    if raw in MARKET:
        key = raw
    else:
        for k, it in MARKET.items():
            if raw == it['name'].lower() or raw == it['name'].lower().replace(' ', '_'):
                key = k
                break
    if not key:
        try:
            bot.reply_to(message, "Bilinmeyen ürün. /market ile kontrol et.", disable_web_page_preview=True)
        except Exception:
            pass
        return
    sender = str(message.from_user.id)
    receiver = message.reply_to_message.from_user
    receiver_id = str(receiver.id)
    initialize_user(sender, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    initialize_user(receiver_id, {'first_name': receiver.first_name or "Bilinmeyen", 'username': receiver.username})
    with inv_lock:
        s_inv = inventory.get(sender, {})
        s_list = s_inv.get(key, [])
        if not s_list:
            try:
                bot.reply_to(message, "Hediye göndermek için bu ürüne sahip değilsin.", disable_web_page_preview=True)
            except Exception:
                pass
            return
        s_list.pop()
        if not s_list:
            s_inv.pop(key, None)
        r_inv = inventory.setdefault(receiver_id, {})
        r_list = r_inv.setdefault(key, [])
        r_list.append({"from": sender, "time": datetime.now().isoformat(), "note": "gift"})
        try:
            save_json(INVENTORY_FILE, inventory)
            save_json(BALANCE_FILE, balances)
        except Exception:
            pass
    try:
        bot.reply_to(message, f"🎁 Başarılı! {MARKET[key]['emoji']} {MARKET[key]['name']} gönderildi.", disable_web_page_preview=True)
    except Exception:
        pass
    try:
        link, dname = get_user_link(receiver_id, {'first_name': receiver.first_name or "Kullanıcı", 'username': receiver.username})
        bot.send_message(int(receiver_id), f"<a href='{link}'>{html.escape(receiver.first_name or receiver.username or dname)}</a>, sana bir hediye gönderildi: {MARKET[key]['emoji']} {MARKET[key]['name']} (gönderen: {message.from_user.first_name})", parse_mode='HTML', disable_web_page_preview=True)
    except Exception:
        pass

# SAT: envanterdeki ürünü sat (satış fiyatı = %60 of original)
@bot.message_handler(commands=['sat'])
def cmd_sat(message):
    # kullanım: /sat <ürün_key>
    uid = str(message.from_user.id)
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2:
        try:
            bot.reply_to(message, "Kullanım: /sat <ürün_key> (örnek: /sat araba)", disable_web_page_preview=True)
        except Exception:
            pass
        return
    key = parts[1].strip().lower()
    if key not in MARKET:
        try:
            bot.reply_to(message, "Bilinmeyen ürün.", disable_web_page_preview=True)
        except Exception:
            pass
        return
    initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    with inv_lock:
        inv = inventory.get(uid, {})
        lst = inv.get(key, [])
        if not lst:
            try:
                bot.reply_to(message, "Satmak için bu ürüne sahip değilsin.", disable_web_page_preview=True)
            except Exception:
                pass
            return
        # çıkar ve bakiye ekle
        lst.pop()
        if not lst:
            inv.pop(key, None)
        sale_price = int(MARKET[key]['price'] * 0.6)
        balances[uid] = balances.get(uid, 0) + sale_price
        try:
            save_json(INVENTORY_FILE, inventory)
            save_json(BALANCE_FILE, balances)
        except Exception:
            pass
    try:
        bot.reply_to(message, f"💰 {MARKET[key]['emoji']} {MARKET[key]['name']} satıldı. Elde edilen: {format_money(sale_price)} TL\nYeni bakiyen: {format_money(balances[uid])} TL", disable_web_page_preview=True)
    except Exception:
        pass
    # kredi varsa repayment uygula; earned_amount = sale_price
    try:
        apply_credit_repayment_on_earn(uid, sale_price)
    except Exception:
        pass
# ---------- PART 3/3: Kredi sistemi ve startup ----------
# (Bu kısmı Part2'den hemen sonra yapıştır)

# credits dict yapısı:
# credits = {
#   credit_id: {
#       'id': credit_id,
#       'user_id': uid,
#       'amount': int,
#       'remaining': int,
#       'rate': float (oran, örn 0.1),
#       'status': 'pending'|'active'|'paid'|'cancelled',
#       'created_at': iso,
#       'admin_id': admin_who_approved_or_none
#   }
# }

def ensure_files():
    for path, default in [
        (BALANCE_FILE, {}),
        (USERS_FILE, {}),
        (SETTINGS_FILE, {'win_chance': DEFAULT_WIN_CHANCE, 'credit_repay_rate': 0.1}),
        (BONUS_FILE, {}),
        (INVENTORY_FILE, {}),
        (BETS_FILE, {}),
        (CREDITS_FILE, {}),
    ]:
        if not os.path.exists(path):
            try:
                save_json(path, default)
            except Exception:
                pass

# ---------- KREDI TALEP: /kredi <miktar> ----------
@bot.message_handler(commands=['kredi'])
def cmd_kredi(message):
    parts = message.text.split()
    uid = str(message.from_user.id)
    if len(parts) != 2 or not parts[1].isdigit():
        try:
            bot.reply_to(message, "Kullanım: /kredi <miktar> (ör: /kredi 4000)", disable_web_page_preview=True)
        except Exception:
            pass
        return
    amount = int(parts[1])
    if amount <= 0:
        try:
            bot.reply_to(message, "Miktar sıfırdan büyük olmalı.", disable_web_page_preview=True)
        except Exception:
            pass
        return
    initialize_user(uid, {'first_name': message.from_user.first_name or "Bilinmeyen", 'username': message.from_user.username})
    # oluştur pending kredi
    credit_id = str(int(time.time() * 1000)) + "_" + uid
    rate = settings.get('credit_repay_rate', 0.1)
    credit = {
        'id': credit_id,
        'user_id': uid,
        'amount': amount,
        'remaining': amount,
        'rate': rate,
        'status': 'pending',
        'created_at': datetime.now().isoformat(),
        'admin_id': None
    }
    with credit_lock:
        credits[credit_id] = credit
        save_json(CREDITS_FILE, credits)
    # adminlere mesaj gönder: onay/iptal butonlu
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("Onayla", callback_data=f"credit_approve_{credit_id}"), InlineKeyboardButton("İptal", callback_data=f"credit_cancel_{credit_id}"))
    for adm in ADMINS:
        try:
            bot.send_message(int(adm), f"Kredi talebi:\nKullanıcı: {message.from_user.first_name} (<code>{uid}</code>)\nMiktar: {format_money(amount)} TL\nID: {credit_id}", parse_mode='HTML', reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
    try:
        bot.reply_to(message, "Kredi talebiniz admin onayına gönderildi. Onaylanınca bilgilendirileceksiniz.", disable_web_page_preview=True)
    except Exception:
        pass

# ---------- ADMIN CALLBACK: kredi onay / iptal ----------
@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith('credit_'))
def cb_credit_admin(call):
    data = call.data.split('_', 2)
    action = data[1] if len(data) > 1 else None
    credit_id = data[2] if len(data) > 2 else None
    admin_id = str(call.from_user.id)
    if admin_id not in ADMINS:
        try:
            bot.answer_callback_query(call.id, "Bu işlem sadece adminlere özeldir.")
        except Exception:
            pass
        return
    with credit_lock:
        credit = credits.get(credit_id)
        if not credit:
            try:
                bot.answer_callback_query(call.id, "Kredi kaydı bulunamadı.")
            except Exception:
                pass
            return
        if action == 'approve':
            if credit['status'] != 'pending':
                try:
                    bot.answer_callback_query(call.id, "Bu kredi zaten işlenmiş.")
                except Exception:
                    pass
                return
            # onayla: parayı kullanıcıya gönder, kredi aktif et
            uid = credit['user_id']
            initialize_user(uid, {'first_name': users.get(uid, {}).get('first_name','Bilinmeyen'), 'username': users.get(uid, {}).get('username')})
            balances[uid] = balances.get(uid, 0) + credit['amount']
            credit['status'] = 'active'
            credit['admin_id'] = admin_id
            credit['approved_at'] = datetime.now().isoformat()
            save_json(BALANCE_FILE, balances)
            save_json(CREDITS_FILE, credits)
            try:
                bot.answer_callback_query(call.id, "Kredi onaylandı ve kullanıcının hesabına aktarıldı.")
            except Exception:
                pass
            # bildir: kullanıcıya
            try:
                bot.send_message(int(uid), f"Kredi talebiniz onaylandı: {format_money(credit['amount'])} TL hesabınıza aktarıldı.", disable_web_page_preview=True)
            except Exception:
                pass
        elif action == 'cancel':
            if credit['status'] != 'pending':
                try:
                    bot.answer_callback_query(call.id, "Bu kredi zaten işlenmiş.")
                except Exception:
                    pass
                return
            credit['status'] = 'cancelled'
            credit['admin_id'] = admin_id
            credit['cancelled_at'] = datetime.now().isoformat()
            save_json(CREDITS_FILE, credits)
            try:
                bot.answer_callback_query(call.id, "Kredi talebi iptal edildi.")
            except Exception:
                pass
            # bildir: kullanıcıya
            try:
                bot.send_message(int(credit['user_id']), f"Kredi talebiniz admin tarafından iptal edildi.", disable_web_page_preview=True)
            except Exception:
                pass
        else:
            try:
                bot.answer_callback_query(call.id, "Bilinmeyen işlem.")
            except Exception:
                pass

# ---------- KREDİ OTOMATİK TAHSİLAT NOTU ----------
# apply_credit_repayment_on_earn fonksiyonu Part1'de tanımlı — kazanç sonrası çağrılacaktır.

# ---------- DOSYA YÜKLEME BAŞLAT ----------
if __name__ == "__main__":
    ensure_files()
    balances = load_json(BALANCE_FILE, {})
    users = load_json(USERS_FILE, {})
    settings = load_json(SETTINGS_FILE, {'win_chance': DEFAULT_WIN_CHANCE, 'credit_repay_rate': 0.1})
    bonuses = load_json(BONUS_FILE, {})
    inventory = load_json(INVENTORY_FILE, {})
    bets = {}
    credits = load_json(CREDITS_FILE, {})

    # başlat bets updater thread
    updater_thread = threading.Thread(target=bets_updater_loop, daemon=True)
    updater_thread.start()

    # sonsuz polling
    while True:
        try:
            bot.infinity_polling(timeout=60, long_polling_timeout=60)
        except Exception:
            time.sleep(5)
