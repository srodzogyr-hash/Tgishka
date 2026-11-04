import os
import time
import json
import re
import threading
import requests
from io import BytesIO

# Global in-memory storage of chats
# Format:
# CHATS = {
#     "12345678": {
#         "uid": "12345678",
#         "name": "Имя Фамилия",
#         "username": "username",
#         "last_seen": 1730748000.0,
#         "last_message": "Текст сообщения",
#         "last_time": "20:30",
#         "unread": 0,
#         "messages": [
#             {
#                 "id": 1,
#                 "sender": "user" | "bot",
#                 "sender_name": "Имя",
#                 "text": "Текст",
#                 "time": "20:30",
#                 "timestamp": 1730748000.0
#             }
#         ]
#     }
# }

CHATS = {}
AVATAR_CACHE = {}
_BOT_INSTANCE = None
_SAVE_LOCK = threading.Lock()

LOCAL_DB_FILE = os.path.join(os.path.dirname(__file__), 'chats_data.json')
FIREBASE_URL = os.environ.get('FIREBASE_URL', '').rstrip('/')

def set_bot_instance(bot):
    global _BOT_INSTANCE
    _BOT_INSTANCE = bot

def get_bot_instance():
    global _BOT_INSTANCE
    if _BOT_INSTANCE:
        return _BOT_INSTANCE
    # Try getting from sys.modules
    import sys
    if 'bot' in sys.modules and hasattr(sys.modules['bot'], 'bot'):
        _BOT_INSTANCE = sys.modules['bot'].bot
    elif '__main__' in sys.modules and hasattr(sys.modules['__main__'], 'bot'):
        _BOT_INSTANCE = sys.modules['__main__'].bot
    return _BOT_INSTANCE

def load_chats():
    """Загружает историю чатов из локального файла или Firebase."""
    global CHATS
    # 1. Попытка загрузить из локального файла
    if os.path.exists(LOCAL_DB_FILE):
        try:
            with open(LOCAL_DB_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    CHATS.update(data)
                    print(f"💬 Загружено {len(CHATS)} чатов из локальной базы.")
        except Exception as e:
            print(f"⚠️ Ошибка чтения локальной базы чатов: {e}")

    # 2. Попытка подгрузить/синхронизировать из Firebase
    if FIREBASE_URL:
        try:
            r = requests.get(f"{FIREBASE_URL}/web_chats.json", timeout=6)
            if r.status_code == 200 and isinstance(r.json(), dict):
                fb_data = r.json()
                for uid, chat in fb_data.items():
                    if uid not in CHATS or chat.get('last_seen', 0) > CHATS[uid].get('last_seen', 0):
                        CHATS[uid] = chat
                print(f"🔥 Синхронизировано {len(CHATS)} чатов с Firebase.")
        except Exception as e:
            print(f"⚠️ Ошибка синхронизации чатов с Firebase: {e}")

def save_chats_async(target_uid=None):
    """Асинхронно сохраняет состояние чатов в локальный файл и Firebase."""
    def _worker():
        with _SAVE_LOCK:
            # 1. Локальное сохранение
            try:
                with open(LOCAL_DB_FILE, 'w', encoding='utf-8') as f:
                    json.dump(CHATS, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"⚠️ Ошибка записи chats_data.json: {e}")

            # 2. Firebase
            if FIREBASE_URL:
                try:
                    if target_uid and str(target_uid) in CHATS:
                        requests.put(
                            f"{FIREBASE_URL}/web_chats/{target_uid}.json",
                            json=CHATS[str(target_uid)],
                            timeout=6
                        )
                    else:
                        requests.put(
                            f"{FIREBASE_URL}/web_chats.json",
                            json=CHATS,
                            timeout=8
                        )
                except Exception as e:
                    pass

    threading.Thread(target=_worker, daemon=True).start()

def handle_incoming_pm(user, text, media_type=None):
    """Регистрирует входящее сообщение от пользователя в ЛС бота."""
    if not user:
        return
    uid = str(user.id)
    fname = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Пользователь"
    uname = (user.username or '').strip().lstrip('@')

    now = time.time()
    t_str = time.strftime('%H:%M', time.localtime(now))

    display_text = text
    if not display_text and media_type:
        display_text = f"[{media_type.capitalize()}]"
    elif media_type and media_type != 'text':
        display_text = f"[{media_type.capitalize()}] {display_text}"

    chat = CHATS.setdefault(uid, {
        'uid': uid,
        'name': fname,
        'username': uname,
        'last_seen': now,
        'last_message': display_text,
        'last_time': t_str,
        'unread': 0,
        'messages': []
    })

    chat['name'] = fname
    if uname:
        chat['username'] = uname
    chat['last_seen'] = now
    chat['last_message'] = display_text
    chat['last_time'] = t_str
    chat['unread'] = chat.get('unread', 0) + 1

    msg_id = len(chat['messages']) + 1
    chat['messages'].append({
        'id': msg_id,
        'sender': 'user',
        'sender_name': fname,
        'text': display_text,
        'time': t_str,
        'timestamp': now
    })

    # Ограничиваем историю чата последними 100 сообщениями
    if len(chat['messages']) > 100:
        chat['messages'] = chat['messages'][-100:]

    save_chats_async(target_uid=uid)

def handle_outgoing_pm(uid, text):
    """Регистрирует исходящий ответ от лица бота/админа."""
    uid = str(uid)
    if uid not in CHATS:
        CHATS[uid] = {
            'uid': uid,
            'name': f"Пользователь {uid}",
            'username': '',
            'last_seen': time.time(),
            'last_message': text,
            'last_time': time.strftime('%H:%M'),
            'unread': 0,
            'messages': []
        }

    chat = CHATS[uid]
    now = time.time()
    t_str = time.strftime('%H:%M', time.localtime(now))

    chat['last_seen'] = now
    chat['last_message'] = f"Вы: {text}"
    chat['last_time'] = t_str

    msg_id = len(chat['messages']) + 1
    chat['messages'].append({
        'id': msg_id,
        'sender': 'bot',
        'sender_name': 'Вы (от имени Бота)',
        'text': text,
        'time': t_str,
        'timestamp': now
    })

    if len(chat['messages']) > 100:
        chat['messages'] = chat['messages'][-100:]

    save_chats_async(target_uid=uid)

def send_reply(uid, text):
    """Отправляет сообщение пользователю в Telegram от имени бота."""
    uid = str(uid)
    bot = get_bot_instance()
    if not bot:
        return False, "Экземпляр бота не запущен или недоступен."

    try:
        sent = bot.send_message(int(uid), text)
        if sent:
            handle_outgoing_pm(uid, text)
            return True, None
    except Exception as e:
        err_msg = str(e)
        if "bot was blocked by the user" in err_msg.lower():
            return False, "Пользователь заблокировал бота в Telegram."
        return False, f"Ошибка отправки: {err_msg}"
    return False, "Неизвестная ошибка отправки."

def get_chats_list():
    """Возвращает отсортированный список всех чатов."""
    res = []
    for uid, c in CHATS.items():
        res.append({
            'uid': c['uid'],
            'name': c.get('name', f"User {uid}"),
            'username': c.get('username', ''),
            'last_message': c.get('last_message', ''),
            'last_time': c.get('last_time', ''),
            'last_seen': c.get('last_seen', 0),
            'unread': c.get('unread', 0)
        })
    res.sort(key=lambda x: x['last_seen'], reverse=True)
    return res

def get_chat_history(uid):
    """Возвращает историю конкретного диалога и сбрасывает счетчик непрочитанных."""
    uid = str(uid)
    if uid not in CHATS:
        return None
    chat = CHATS[uid]
    chat['unread'] = 0
    save_chats_async(target_uid=uid)
    return chat

def clear_chat_history(uid):
    """Очищает историю диалога с пользователем."""
    uid = str(uid)
    if uid in CHATS:
        CHATS[uid]['messages'] = []
        CHATS[uid]['last_message'] = "История очищена"
        CHATS[uid]['unread'] = 0
        save_chats_async(target_uid=uid)
        return True
    return False

def get_user_avatar(uid):
    """Получает аватарку пользователя из Telegram или генерирует fallback."""
    uid_str = str(uid)
    if uid_str in AVATAR_CACHE:
        return AVATAR_CACHE[uid_str], 'image/jpeg'

    bot = get_bot_instance()
    if bot:
        try:
            photos = bot.get_user_profile_photos(int(uid_str), limit=1)
            if photos and photos.total_count > 0:
                file_id = photos.photos[0][0].file_id
                f_info = bot.get_file(file_id)
                img_bytes = bot.download_file(f_info.file_path)
                if img_bytes:
                    AVATAR_CACHE[uid_str] = img_bytes
                    return img_bytes, 'image/jpeg'
        except Exception:
            pass

    # SVG Fallback avatar
    name = CHATS.get(uid_str, {}).get('name', 'U')
    letter = (name[:1] or 'U').upper()
    colors = ['#2980b9', '#27ae60', '#8e44ad', '#d35400', '#16a085', '#c0392b', '#f39c12']
    bg_color = colors[abs(hash(uid_str)) % len(colors)]
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="50" fill="{bg_color}" />
      <text x="50" y="63" font-size="44" font-weight="bold" fill="#ffffff" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif">{letter}</text>
    </svg>"""
    return svg.encode('utf-8'), 'image/svg+xml'

# Загружаем базу при импорте
load_chats()
