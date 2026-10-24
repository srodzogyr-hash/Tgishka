import html
import io
import os
import re
import base64
import time
import random
import zipfile
import xml.etree.ElementTree as ET
import requests
import threading

try:
    import telebot
except ImportError:
    os.system('pip install -U pyTelegramBotAPI requests')
    import telebot

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardRemove

# --- НАСТРОЙКИ И ЗАГРУЗКА ПУЛА КЛЮЧЕЙ (ДО 15 ШТ.) ---
TOKEN = os.environ.get('TG_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')  # Ключ Groq
FIREBASE_URL = os.environ.get('FIREBASE_URL')

def load_gemini_keys():
    """Собирает все ключи Gemini из переменных окружения (до 15 ключей)."""
    keys = []
    env_names = ['GROQ_KEY', 'GEMINI_KEYS', 'GEMINI_KEY'] + [f'GEMINI_KEY_{i}' for i in range(1, 16)]
    for name in env_names:
        val = os.environ.get(name, '')
        if val:
            for part in re.split(r'[,\s]+', val.strip()):
                if part and part not in keys:
                    keys.append(part)
    return keys

GEMINI_KEYS = load_gemini_keys()
CURRENT_KEY_INDEX = 0
EXHAUSTED_QUOTAS = {}
KEY_STATS = {i: {'ok': 0, '429': 0, 'err': 0, 'last_err': ''} for i in range(len(GEMINI_KEYS))}

CREATOR_USERNAME = 'wrtnoq'
CREATOR_ID = None

MODEL_HIERARCHY = [
    'gemini-3.8-flash',
    'gemini-3.6-flash',
    'gemini-3.5-flash-lite',
    'gemini-3.1-flash-lite',
    'gemini-3-flash',
    'gemini-3.1-pro'
]
DEFAULT_IMAGE_MODEL = 'imagen-3.0-generate-001'
DEFAULT_CONTEXT_LIMIT = 100
DEFAULT_SYSTEM_PROMPT = "Ты — умный, живой и остроумный собеседник. Отвечай четко, естественно и на русском языке."

GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GROQ_API_BASE = 'https://api.groq.com/openai/v1'

ACTIVE_MODEL = MODEL_HIERARCHY[0]
OLD_KEYBOARD_CLEARED = False
TRAINING_MODE_ACTIVE = False  # Режим диалогового обучения в админке

CONFIG = {
    'auto_reply_enabled': True,
    'spy_enabled': True,
    'stealth_mode': False,            # Режим «Человек» (без плашек ИИ и без редактирования)
    'human_reply_chance': 100,        # Шанс ответа автоответчика в % (10..100)
    'third_wheel_interval': 0,        # Через сколько сообщений вставлять «5 копеек» (0 = выкл)
    'learned_persona': '',            # Правила стиля, выученные в режиме обучения
    'voice_auto_all': False,
    'voice_auto_users': {},
    'known_chats': {},                # {username: {'chat_id': id, 'biz_id': biz_id, 'name': name}}
    'creator_id': None,
    'context_limit': DEFAULT_CONTEXT_LIMIT,
    'afk_minutes': 10,
    'cooldown_seconds': 5,
    'system_prompt': DEFAULT_SYSTEM_PROMPT,
    'image_model': DEFAULT_IMAGE_MODEL,
    'blacklist': {}
}

LAST_OWNER_ACTIVITY = {}
LAST_REQUEST_TIME = {}
CHAT_MEMORY = {}
CHAT_LOG = {}
CHAT_MSG_COUNTER = {}
PENDING_AUTO_REPLY = {}
ACTIVE_ALARMS = []

BOT_BUSINESS_MESSAGES = set()
MEDIA_GROUP_BUFFER = {}

MESSAGE_CACHE = {}
MAX_CACHE_SIZE = 500

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)

thread_local = threading.local()

def get_http_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session

# --- АВТО-ПРОВЕРКА ДОСТУПНЫХ МОДЕЛЕЙ ПРИ СТАРТЕ ---
def sync_available_models_on_startup():
    global MODEL_HIERARCHY, ACTIVE_MODEL
    if not GEMINI_KEYS:
        return
    try:
        r = get_http_session().get(
            f"{GEMINI_API_BASE}/models",
            headers={'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_KEYS[0]},
            timeout=8
        )
        if r.status_code == 200:
            available = set()
            for m in r.json().get('models', []):
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    available.add(m['name'].replace('models/', ''))
            if available:
                valid_hierarchy = [m for m in MODEL_HIERARCHY if m in available]
                if valid_hierarchy:
                    MODEL_HIERARCHY = valid_hierarchy
                    if ACTIVE_MODEL not in MODEL_HIERARCHY and not ACTIVE_MODEL.startswith('groq/'):
                        ACTIVE_MODEL = MODEL_HIERARCHY[0]
                    print(f"✅ Проверенная очередь моделей: {MODEL_HIERARCHY}")
    except Exception as e:
        print(f"⚠️ Не удалось сверить список моделей при старте: {e}")

# --- БАЗА ДАННЫХ (FIREBASE) ---
def init_firebase():
    global CREATOR_ID
    if not FIREBASE_URL:
        return
    s = get_http_session()
    try:
        r = s.get(f'{FIREBASE_URL}/config.json', timeout=8)
        if r.status_code == 200 and isinstance(r.json(), dict):
            data = r.json()
            data.pop('exhausted_quotas', None)
            CONFIG.update(data)
            if CONFIG.get('creator_id'):
                CREATOR_ID = int(CONFIG['creator_id'])
            for dict_key in ('voice_auto_users', 'blacklist', 'known_chats'):
                if not isinstance(CONFIG.get(dict_key), dict):
                    CONFIG[dict_key] = {}
            print("🔥 Настройки и обученный профиль загружены из Firebase.")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки настроек Firebase: {e}")

    try:
        r_mem = s.get(f'{FIREBASE_URL}/chat_memory.json', timeout=8)
        if r_mem.status_code == 200 and isinstance(r_mem.json(), dict):
            for k, v in r_mem.json().items():
                if isinstance(v, list):
                    try:
                        CHAT_MEMORY[int(k)] = v
                    except ValueError:
                        pass
            print(f"🧠 Память чатов загружена из Firebase ({len(CHAT_MEMORY)} диалогов).")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки памяти Firebase: {e}")

def save_config():
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            get_http_session().patch(f'{FIREBASE_URL}/config.json', json=CONFIG, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

def save_chat_memory_async(chat_id, history):
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            get_http_session().put(f'{FIREBASE_URL}/chat_memory/{chat_id}.json', json=history, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

def clear_all_memory_firebase():
    CHAT_MEMORY.clear()
    CHAT_LOG.clear()
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            get_http_session().delete(f'{FIREBASE_URL}/chat_memory.json', timeout=5)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

def register_creator_id(user):
    global CREATOR_ID
    if user and (user.username or '').lower() == CREATOR_USERNAME:
        if CREATOR_ID != user.id:
            CREATOR_ID = user.id
            CONFIG['creator_id'] = CREATOR_ID
            save_config()

def remove_old_reply_keyboard(chat_id, force=False):
    global OLD_KEYBOARD_CLEARED
    if OLD_KEYBOARD_CLEARED and not force:
        return
    OLD_KEYBOARD_CLEARED = True
    try:
        bot.send_message(
            chat_id,
            "🧹 <i>Старые кнопки снизу убраны.</i>",
            reply_markup=ReplyKeyboardRemove()
        )
    except Exception:
        pass

init_firebase()
sync_available_models_on_startup()

def check_cooldown(chat_id):
    cd = CONFIG.get('cooldown_seconds', 5)
    if cd <= 0:
        return True
    now = time.time()
    if now - LAST_REQUEST_TIME.get(chat_id, 0) < cd:
        return False
    LAST_REQUEST_TIME[chat_id] = now
    return True

# --- УЧЕТ РОЛЕЙ И ИСТОРИИ ЖИВОГО ЧАТА ---
def get_speaker_tag(user, is_owner):
    """Формирует понятную для ИИ метку, кто именно пишет сообщение."""
    if is_owner:
        return f"[Владелец аккаунта @{CREATOR_USERNAME}]"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Собеседник"
    uname = f"@{user.username}" if user.username else f"id:{user.id}"
    return f"[Собеседник {full_name} ({uname})]"

def record_chat_log(message, is_owner, custom_text=None):
    """Записывает каждое сообщение чата в буфер последних 60 реплик с указанием автора."""
    chat_id = message.chat.id
    user = message.from_user
    if not user:
        return

    if not is_owner and user.username:
        uname_clean = user.username.lower()
        full_name = f"{user.first_name or ''} {user.last_name or ''}".strip()
        if uname_clean not in CONFIG['known_chats'] or CONFIG['known_chats'][uname_clean].get('biz_id') != message.business_connection_id:
            CONFIG['known_chats'][uname_clean] = {
                'chat_id': chat_id,
                'biz_id': message.business_connection_id,
                'name': full_name
            }
            save_config()

    text = custom_text if custom_text is not None else (message.text or message.caption or "")
    if not text:
        if message.content_type == 'photo':
            text = "[Отправил(а) фото]"
        elif message.content_type == 'voice':
            text = "[Отправил(а) голосовое сообщение]"
        elif message.content_type == 'video_note':
            text = "[Отправил(а) видео-кружочек]"
        elif message.content_type == 'document':
            text = f"[Отправил(а) файл: {message.document.file_name if message.document else 'документ'}]"
        else:
            return

    speaker = get_speaker_tag(user, is_owner)
    time_str = time.strftime('%H:%M')
    log_line = f"[{time_str}] {speaker}: {text}"

    log_list = CHAT_LOG.setdefault(chat_id, [])
    log_list.append(log_line)
    if len(log_list) > 60:
        CHAT_LOG[chat_id] = log_list[-60:]

    mem_list = CHAT_MEMORY.setdefault(chat_id, [])
    mem_list.append({"role": "user", "content": f"{speaker}: {text}"})
    limit = CONFIG.get('context_limit', 100)
    if len(mem_list) > limit:
        CHAT_MEMORY[chat_id] = mem_list[-limit:]

def build_full_system_instruction(human_imitation=False):
    """Собирает системную инструкцию с пониманием Telegram, ролей собеседников и обученного стиля."""
    base = CONFIG.get('system_prompt') or DEFAULT_SYSTEM_PROMPT
    learned = (CONFIG.get('learned_persona') or '').strip()

    tg_context = (
        "ВАЖНЫЙ КОНТЕКСТ СРЕДЫ:\n"
        f"1. Ты находишься внутри мессенджера Telegram и подключен через Telegram Business к личному аккаунту владельца @{CREATOR_USERNAME}.\n"
        f"2. В истории чата перед каждым сообщением указана роль автора:\n"
        f"   • `[Владелец аккаунта @{CREATOR_USERNAME}]` — это хозяин аккаунта (твой создатель и босс).\n"
        f"   • `[Собеседник Имя (@юзернейм)]` — это другой человек, с которым переписывается владелец.\n"
        "3. Всегда четко различай, кто именно к тебе обращается в текущем сообщении, и никогда не путай владельца и собеседника.\n"
        "4. Никогда не пиши в начале своего ответа метки вроде `[Бот]:` или `[Ответ]:` — пиши сразу чистый текст ответа."
    )

    parts = [base, tg_context]

    if learned:
        parts.append(
            f"ОБУЧЕННЫЙ СТИЛЬ И ПРАВИЛА ПОВЕДЕНИЯ (СТРОГО СОБЛЮДАЙ ЭТИ УКАЗАНИЯ ВЛАДЕЛЬЦА):\n{learned}"
        )

    if human_imitation or CONFIG.get('stealth_mode', False):
        parts.append(
            "РЕЖИМ ИМИТАЦИИ ЧЕЛОВЕКА (СТЕЛС):\n"
            "• Веди себя максимально как живой человек в переписке Telegram.\n"
            "• Ни при каких обстоятельствах не признавайся, что ты ИИ, бот или нейросеть.\n"
            "• Не используй канцелярит, длинные нумерованные списки и шаблонные фразы ИИ («Чем могу помочь?», «Конечно!»).\n"
            "• Пиши живо, естественно и соразмерно длине сообщений собеседника."
        )

    return "\n\n".join(parts)

# --- ФОРМАТИРОВАНИЕ И ОТПРАВКА ---
def format_markdown_to_html(text):
    escaped = html.escape(text)
    escaped = re.sub(r'```(?:\w+)?\n?(.*?)```', r'<pre>\1</pre>', escaped, flags=re.DOTALL)
    escaped = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', escaped)
    escaped = re.sub(r'(?m)^#{1,6}\s+(.+)$', r'<b>\1</b>', escaped)
    escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)
    return escaped

def split_text_into_chunks(text, max_len=3600):
    if len(text) <= max_len:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > max_len:
        split_pos = remaining.rfind('\n\n', 0, max_len)
        if split_pos == -1 or split_pos < max_len // 2:
            split_pos = remaining.rfind('\n', 0, max_len)
        if split_pos == -1 or split_pos < max_len // 2:
            split_pos = remaining.rfind(' ', 0, max_len)
        if split_pos == -1:
            split_pos = max_len

        chunks.append(remaining[:split_pos].strip())
        remaining = remaining[split_pos:].strip()

    if remaining:
        chunks.append(remaining)
    return chunks

def safe_edit_message(text, chat_id, message_id, business_id=None, reply_markup=None):
    if len(text) > 4050:
        text = text[:4050] + "..."
    try:
        bot.edit_message_text(text, chat_id, message_id, business_connection_id=business_id, reply_markup=reply_markup)
    except Exception:
        try:
            bot.edit_message_text(text, chat_id, message_id, business_connection_id=business_id, parse_mode=None, reply_markup=reply_markup)
        except Exception:
            pass

def send_long_response(reply_text, footer_text, chat_id, loading_msg_id=None, business_id=None, reply_to_msg_id=None):
    reply_text = re.sub(r'^\[(Бот|Ассистент|ИИ|Владелец[^\]]*)\]:\s*', '', reply_text.strip(), flags=re.IGNORECASE)

    raw_chunks = split_text_into_chunks(reply_text, max_len=3600)
    total = len(raw_chunks)

    for idx, chunk in enumerate(raw_chunks):
        formatted_chunk = format_markdown_to_html(chunk)
        plain_chunk = chunk

        if idx == total - 1 and footer_text:
            formatted_chunk += f"\n\n{footer_text}"
            plain_chunk += f"\n\n{footer_text}"
        elif total > 1 and footer_text:
            part_label = f"<i>(Часть {idx + 1}/{total})</i>\n\n"
            formatted_chunk = part_label + formatted_chunk
            plain_chunk = f"(Часть {idx + 1}/{total})\n\n" + plain_chunk

        if idx == 0 and loading_msg_id is not None:
            try:
                bot.edit_message_text(formatted_chunk, chat_id, loading_msg_id, business_connection_id=business_id)
            except Exception:
                try:
                    bot.edit_message_text(plain_chunk, chat_id, loading_msg_id, business_connection_id=business_id, parse_mode=None)
                except Exception:
                    pass
        else:
            if idx > 0:
                time.sleep(0.35)
            try:
                sent = bot.send_message(
                    chat_id,
                    formatted_chunk,
                    business_connection_id=business_id,
                    reply_to_message_id=reply_to_msg_id if idx == 0 else None
                )
                if business_id and sent:
                    BOT_BUSINESS_MESSAGES.add(sent.message_id)
            except Exception:
                try:
                    sent = bot.send_message(
                        chat_id,
                        plain_chunk,
                        business_connection_id=business_id,
                        parse_mode=None,
                        reply_to_message_id=reply_to_msg_id if idx == 0 else None
                    )
                    if business_id and sent:
                        BOT_BUSINESS_MESSAGES.add(sent.message_id)
                except Exception:
                    pass

def send_typing_action(chat_id, business_id=None):
    try:
        bot.send_chat_action(chat_id, 'typing', business_connection_id=business_id)
    except Exception:
        pass

def mask_key(key_str):
    if len(key_str) <= 10:
        return "***"
    return f"{key_str[:6]}...{key_str[-4:]}"

# --- ПОЛНЫЙ ТЕКСТ ВСЕХ КОМАНД (СТАРЫХ И НОВЫХ) ---
def get_full_commands_text():
    return (
        "📜 <b>Полный список всех команд бота:</b>\n\n"
        "🔹 <b>1. Классические команды (Нейросеть и Модели):</b>\n"
        "• <code>/panel</code> (или <code>панель</code>) — главный пульт с кнопками\n"
        "• <code>модели</code> — выбрать текстовую модель (Gemini / Groq)\n"
        "• <code>фото-модели</code> — выбрать модель для рисования\n"
        "• <code>гпт [текст]</code> — задать вопрос ИИ (или Reply на фото/файл/ГС)\n"
        "• <code>нарисуй [описание]</code> — сгенерировать картинку\n"
        "• <code>контекст 100</code> — изменить глубину памяти диалога\n"
        "• <code>сброс</code> (или <code>гпт сброс</code>) — очистить память текущего чата\n"
        "• <code>сброс все</code> — очистить память вообще всех чатов\n"
        "• <code>характер [текст]</code> — задать кастомный промпт/роль для Gemini\n"
        "• <code>характер сброс</code> — вернуть стандартный характер\n"
        "• <code>ключи</code> — статус всех ключей, их паузы и пинг-тест\n"
        "• <code>список</code> — посмотреть ЧС, список авто-ГС, промпт и будильники\n\n"
        "🔹 <b>2. Автоответчик, Шпион и Доступ:</b>\n"
        "• <code>автоответчик вкл</code> / <code>автоответчик выкл</code> — переключить автоответчик\n"
        "• <code>таймер 5</code> — через сколько минут молчания включается автоответчик\n"
        "• <code>кд 3</code> — задержка анти-спама между запросами в секундах (0 = без КД)\n"
        "• <code>шпион вкл</code> / <code>шпион выкл</code> — перехват удаленных и измененных СМС\n"
        "• <code>запретить @юзер</code> — добавить человека в ЧС бота\n"
        "• <code>разрешить @юзер</code> — убрать человека из ЧС\n\n"
        "🔹 <b>3. Голосовые и Кружки (рс + живой ответ):</b>\n"
        "• Ответь <code>рс</code> на ГС/кружок — чистая текстовая расшифровка без воды\n"
        "• Ответь <code>рс [вопрос/задача]</code> — например: <code>рс перескажи кратко</code>\n"
        "• <code>рс @юзер</code> — вкл/выкл постоянную авто-расшифровку для этого юзера\n"
        "• <code>рс все вкл</code> / <code>рс все выкл</code> — авто-расшифровка для всех чатов\n"
        "• <i>Фишка: если ответить на сообщение бота голосовым — он сам расшифрует его и ответит в диалоге!</i>\n\n"
        "🔹 <b>4. Обучение, Стелс (Человек), Третий собеседник и Будильник:</b>\n"
        "• <code>обучение</code> — войти в режим диалогового обучения автоответчика (выход: <code>стоп</code>)\n"
        "• <code>обучение сброс</code> — стереть выученные правила\n"
        "• <code>стелс вкл</code> / <code>стелс выкл</code> — режим Человека (убирает подпись model/context, часики и пометку «изменено»)\n"
        "• <code>шанс 75</code> — вероятность ответа автоответчика в % (от 10 до 100)\n"
        "• <code>вставка 25</code> (или <code>вставка выкл</code>) — раз в N сообщений бот сам вставляет свои «5 копеек» в чат\n"
        "• <code>рассуди</code> (или <code>кто прав</code>) — прочитать последние 50 СМС чата и рассудить спор\n"
        "• <code>5 копеек</code> — вставить комментарий по последним сообщениям чата\n"
        "• <code>анализ</code> — сделать разбор последних 50 сообщений переписки\n"
        "• <code>будильник 30 [задача]</code> — (в чате или админке) через 30 мин бот сам напишет сообщение\n"
        "• <code>будильник @юзер 60 [задача]</code> — завести будильник для конкретного юзера из админки"
    )

# --- КЭШИРОВАНИЕ И РЕЖИМ «ШПИОНА» ---
def cache_incoming_message(message, is_owner):
    if len(MESSAGE_CACHE) >= MAX_CACHE_SIZE:
        oldest_key = next(iter(MESSAGE_CACHE))
        MESSAGE_CACHE.pop(oldest_key, None)

    user = message.from_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Без имени"
    uname = f"@{user.username}" if user.username else f"id:{user.id}"
    sender_label = f"{html.escape(full_name)} ({uname})"

    file_id = None
    file_name = None
    c_type = message.content_type

    if c_type == 'photo' and message.photo:
        file_id = message.photo[-1].file_id
    elif c_type == 'voice' and message.voice:
        file_id = message.voice.file_id
    elif c_type == 'video_note' and message.video_note:
        file_id = message.video_note.file_id
    elif c_type == 'video' and message.video:
        file_id = message.video.file_id
    elif c_type == 'audio' and message.audio:
        file_id = message.audio.file_id
    elif c_type == 'document' and message.document:
        file_id = message.document.file_id
        file_name = message.document.file_name

    entry = {
        'sender_label': sender_label,
        'is_owner': is_owner,
        'content_type': c_type,
        'text': message.text or message.caption or "",
        'file_id': file_id,
        'file_name': file_name,
        'file_bytes': None,
        'time': time.time()
    }
    MESSAGE_CACHE[(message.chat.id, message.message_id)] = entry

    if file_id and c_type in ['photo', 'voice', 'video_note'] and not is_owner:
        def _download_backup():
            try:
                f_info = bot.get_file(file_id)
                if f_info.file_size and f_info.file_size < 8 * 1024 * 1024:
                    entry['file_bytes'] = bot.download_file(f_info.file_path)
            except Exception:
                pass
        threading.Thread(target=_download_backup, daemon=True).start()

@bot.edited_business_message_handler(content_types=['text', 'photo', 'voice', 'video_note', 'document', 'video'])
def handle_edited_business_message(message):
    register_creator_id(message.from_user)
    if not CONFIG.get('spy_enabled', True) or not CREATOR_ID:
        return

    is_owner = (message.from_user.id == CREATOR_ID) or ((message.from_user.username or '').lower() == CREATOR_USERNAME)
    if is_owner:
        return

    key = (message.chat.id, message.message_id)
    old_data = MESSAGE_CACHE.get(key)
    new_text = message.text or message.caption or ""

    user = message.from_user
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Собеседник"
    uname = f"@{user.username}" if user.username else f"id:{user.id}"
    sender_label = old_data['sender_label'] if old_data else f"{html.escape(full_name)} ({uname})"
    old_text = old_data['text'] if old_data else "(сообщение до запуска бота)"

    if old_text == new_text:
        return

    alert = (
        f"✏️ <b>Изменено сообщение!</b>\n"
        f"👤 <b>От:</b> {sender_label}\n\n"
        f"❌ <b>Было:</b>\n<blockquote>{html.escape(old_text or '[пусто]')}</blockquote>\n\n"
        f"✅ <b>Стало:</b>\n<blockquote>{html.escape(new_text or '[пусто]')}</blockquote>"
    )
    try:
        bot.send_message(CREATOR_ID, alert)
    except Exception:
        pass

    if old_data:
        old_data['text'] = new_text
    else:
        cache_incoming_message(message, is_owner)

if hasattr(bot, 'deleted_business_messages_handler'):
    @bot.deleted_business_messages_handler()
    def handle_deleted_business_messages(deleted):
        if not CONFIG.get('spy_enabled', True) or not CREATOR_ID:
            return

        chat_id = deleted.chat.id
        for msg_id in deleted.message_ids:
            data = MESSAGE_CACHE.pop((chat_id, msg_id), None)
            if not data or data.get('is_owner'):
                continue

            sender_label = data['sender_label']
            c_type = data['content_type']
            text = data['text']
            file_id = data['file_id']
            file_bytes = data['file_bytes']

            type_names = {
                'text': 'Текст',
                'photo': 'Фотография',
                'voice': 'Голосовое сообщение',
                'video_note': 'Видео-кружочек',
                'document': f"Файл ({data.get('file_name') or 'документ'})",
                'video': 'Видео',
                'audio': 'Аудио'
            }
            type_str = type_names.get(c_type, c_type)

            alert = (
                f"🗑 <b>Удалено сообщение!</b>\n"
                f"👤 <b>От:</b> {sender_label}\n"
                f"📦 <b>Тип:</b> {type_str}"
            )
            if text:
                alert += f"\n💬 <b>Текст:</b>\n<blockquote>{html.escape(text)}</blockquote>"

            try:
                bot.send_message(CREATOR_ID, alert)
                if file_id or file_bytes:
                    media_source = file_id if file_id else file_bytes
                    try:
                        if c_type == 'photo':
                            bot.send_photo(CREATOR_ID, media_source, caption="🕵️‍♂️ Удаленное фото")
                        elif c_type == 'voice':
                            bot.send_voice(CREATOR_ID, media_source, caption="🕵️‍♂️ Удаленное ГС")
                        elif c_type == 'video_note':
                            bot.send_video_note(CREATOR_ID, media_source)
                        elif c_type == 'document':
                            bot.send_document(CREATOR_ID, media_source, caption="🕵️‍♂️ Удаленный файл")
                        elif c_type == 'video':
                            bot.send_video(CREATOR_ID, media_source, caption="🕵️‍♂️ Удаленное видео")
                    except Exception:
                        if file_bytes:
                            if c_type == 'photo':
                                bot.send_photo(CREATOR_ID, file_bytes, caption="🕵️‍♂️ Удаленное фото")
                            elif c_type == 'voice':
                                bot.send_voice(CREATOR_ID, file_bytes, caption="🕵️‍♂️ Удаленное ГС")
                            elif c_type == 'video_note':
                                bot.send_video_note(CREATOR_ID, file_bytes)
            except Exception:
                pass

# --- РАБОТА С ФАЙЛАМИ, ДОКУМЕНТАМИ, ФОТО И АУДИО ---
def get_photo_bytes(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        return bot.download_file(file_info.file_path), 'image/jpeg'
    except Exception:
        return None, None

def get_voice_or_videonote_bytes(message):
    try:
        if message.content_type == 'voice' and message.voice:
            f_info = bot.get_file(message.voice.file_id)
            return bot.download_file(f_info.file_path), 'audio/ogg'
        elif message.content_type == 'video_note' and message.video_note:
            f_info = bot.get_file(message.video_note.file_id)
            return bot.download_file(f_info.file_path), 'video/mp4'
        elif message.content_type == 'audio' and message.audio:
            f_info = bot.get_file(message.audio.file_id)
            return bot.download_file(f_info.file_path), message.audio.mime_type or 'audio/mpeg'
    except Exception:
        pass
    return None, None

def extract_docx_text(file_bytes):
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            xml_content = zf.read('word/document.xml')
        tree = ET.fromstring(xml_content)
        paragraphs = []
        for elem in tree.iter():
            if elem.tag.endswith('}p'):
                texts = [t.text for t in elem.iter() if t.tag.endswith('}t') and t.text]
                if texts:
                    paragraphs.append(''.join(texts))
        return '\n'.join(paragraphs)
    except Exception as e:
        return f"[Ошибка чтения DOCX: {e}]"

def extract_document_content(message):
    doc = message.document
    if not doc:
        return None, None, "Документ не найден."
    if doc.file_size and doc.file_size > 18 * 1024 * 1024:
        return None, None, "Файл слишком большой (максимум 18 МБ)."

    try:
        f_info = bot.get_file(doc.file_id)
        file_bytes = bot.download_file(f_info.file_path)
    except Exception as e:
        return None, None, f"Не удалось скачать файл: {e}"

    fname = (doc.file_name or 'document').lower()
    mime = doc.mime_type or ''

    if fname.endswith('.pdf') or mime == 'application/pdf':
        return (file_bytes, 'application/pdf'), None, None

    if fname.endswith('.docx'):
        text_content = extract_docx_text(file_bytes)
        return None, f"\n\n📄 [Содержимое файла {doc.file_name}]:\n{text_content[:60000]}", None

    if mime.startswith('image/'):
        return (file_bytes, mime), None, None

    try:
        text_content = file_bytes.decode('utf-8')
    except UnicodeDecodeError:
        try:
            text_content = file_bytes.decode('cp1251')
        except Exception:
            return (file_bytes, mime or 'application/octet-stream'), None, None

    return None, f"\n\n📄 [Содержимое файла {doc.file_name}]:\n{text_content[:60000]}", None

def transcribe_with_groq_whisper(audio_bytes, mime_type):
    if not GROQ_API_KEY:
        return None
    ext = 'mp4' if 'mp4' in mime_type else 'ogg'
    files = {'file': (f'audio.{ext}', audio_bytes, mime_type)}
    data = {'model': 'whisper-large-v3-turbo', 'language': 'ru'}
    headers = {'Authorization': f'Bearer {GROQ_API_KEY}'}
    try:
        r = get_http_session().post(f'{GROQ_API_BASE}/audio/transcriptions', headers=headers, files=files, data=data, timeout=25)
        if r.status_code == 200:
            return r.json().get('text', '').strip()
    except Exception:
        pass
    return None

# --- МАРШРУТИЗАТОР ИИ ---
def ask_ai(chat_id, prompt, media_list=None, save_to_memory=True, human_imitation=False, custom_sys_prompt=None):
    if ACTIVE_MODEL.startswith('groq/'):
        return ask_groq(chat_id, prompt, media_list, save_to_memory, custom_sys_prompt)
    else:
        return ask_gemini(chat_id, prompt, media_list, save_to_memory, human_imitation, custom_sys_prompt)

# --- GEMINI API С РОТАЦИЕЙ И ПОНИМАНИЕМ РОЛЕЙ ---
def get_gemini_headers(api_key=None):
    key_to_use = api_key or (GEMINI_KEYS[0] if GEMINI_KEYS else '')
    return {'Content-Type': 'application/json', 'x-goog-api-key': key_to_use}

def fetch_all_models():
    all_models = []
    skip_keywords = ['3.7', '2.5', '2.0', 'tts', 'whisper', 'guard', 'safeguard', 'lyria', 'embedding', 'imagen', 'aqa', 'robotics', 'banana', 'audio']
    s = get_http_session()

    if GEMINI_KEYS:
        url = f"{GEMINI_API_BASE}/models"
        try:
            r = s.get(url, headers=get_gemini_headers(GEMINI_KEYS[0]), timeout=8)
            if r.status_code == 200:
                for m in r.json().get('models', []):
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        m_name = m['name'].replace('models/', '')
                        if not any(kw in m_name.lower() for kw in skip_keywords):
                            all_models.append(m_name)
        except Exception:
            pass

    if GROQ_API_KEY:
        url = f"{GROQ_API_BASE}/models"
        try:
            r = s.get(url, headers={'Authorization': f'Bearer {GROQ_API_KEY}'}, timeout=8)
            if r.status_code == 200:
                for m in r.json().get('data', []):
                    if m.get('active') is not False:
                        m_id = m['id']
                        if not any(kw in m_id.lower() for kw in skip_keywords):
                            all_models.append(f"groq/{m_id}")
        except Exception:
            pass

    if not all_models:
        return MODEL_HIERARCHY
    return sorted(list(set(all_models)))

def ask_gemini(chat_id, prompt, media_list=None, save_to_memory=True, human_imitation=False, custom_sys_prompt=None):
    global CURRENT_KEY_INDEX

    if not GEMINI_KEYS:
        return None, "Не найдено ни одного ключа Gemini! Проверь Environment в Render.", ACTIVE_MODEL, 0

    if not (prompt and prompt.strip()) and not media_list:
        prompt = "Привет!"

    history = CHAT_MEMORY.get(chat_id, []).copy()
    contents = []
    if save_to_memory:
        for msg in history:
            txt = (msg.get('content') or '').strip()
            if not txt:
                continue
            role = 'user' if msg.get('role') == 'user' else 'model'
            contents.append({"role": role, "parts": [{"text": txt}]})

    parts = []
    if prompt and prompt.strip():
        parts.append({"text": prompt.strip()})
    if media_list:
        for media_bytes, mime_type in media_list:
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(media_bytes).decode('utf-8')
                }
            })

    contents.append({"role": "user", "parts": parts})
    sys_instruction = custom_sys_prompt or build_full_system_instruction(human_imitation=human_imitation)
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": sys_instruction}]}
    }

    if ACTIVE_MODEL in MODEL_HIERARCHY:
        start_idx = MODEL_HIERARCHY.index(ACTIVE_MODEL)
        models_to_try = MODEL_HIERARCHY[start_idx:]
    else:
        models_to_try = [ACTIVE_MODEL] + MODEL_HIERARCHY

    last_error = "Лимит запросов"
    s = get_http_session()

    for ignore_cooldown in (False, True):
        for model in models_to_try:
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent"

            for k_offset in range(len(GEMINI_KEYS)):
                now = time.time()
                key_idx = (CURRENT_KEY_INDEX + k_offset) % len(GEMINI_KEYS)
                api_key = GEMINI_KEYS[key_idx]
                stats = KEY_STATS.setdefault(key_idx, {'ok': 0, '429': 0, 'err': 0, 'last_err': ''})

                q_key = (model, key_idx)
                if not ignore_cooldown and EXHAUSTED_QUOTAS.get(q_key, 0) > now:
                    continue

                try:
                    r = s.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=(4, 18))

                    if r.status_code == 429:
                        stats['429'] += 1
                        err_body = r.text.lower()
                        if 'perday' in err_body or 'per_day' in err_body or 'per day' in err_body:
                            EXHAUSTED_QUOTAS[q_key] = time.time() + 2700
                            stats['last_err'] = f"Дневной 429 ({model})"
                        else:
                            EXHAUSTED_QUOTAS[q_key] = time.time() + 30
                            stats['last_err'] = f"Минутный 429 ({model})"
                        last_error = f"Лимит 429 ({model})"
                        continue

                    if r.status_code == 404:
                        for idx_all in range(len(GEMINI_KEYS)):
                            EXHAUSTED_QUOTAS[(model, idx_all)] = time.time() + 86400
                        last_error = f"Модель {model} отключена (404)"
                        break

                    if r.status_code == 400:
                        if 'api_key_invalid' in r.text.lower():
                            stats['err'] += 1
                            stats['last_err'] = "Неверный ключ"
                            EXHAUSTED_QUOTAS[q_key] = time.time() + 3600
                            continue
                        last_error = f"HTTP 400 ({model})"
                        break

                    if r.status_code != 200:
                        stats['err'] += 1
                        stats['last_err'] = f"HTTP {r.status_code}"
                        EXHAUSTED_QUOTAS[q_key] = time.time() + 45
                        last_error = f"HTTP {r.status_code} ({model})"
                        continue

                    data = r.json()
                    if 'candidates' not in data or not data['candidates']:
                        return None, "Модель заблокировала ответ (Safety Filter).", f"{model} [key #{key_idx + 1}]", 0

                    reply_text = data['candidates'][0]['content']['parts'][0]['text']
                    stats['ok'] += 1
                    stats['last_err'] = ''
                    EXHAUSTED_QUOTAS.pop(q_key, None)
                    CURRENT_KEY_INDEX = (key_idx + 1) % len(GEMINI_KEYS)

                    if save_to_memory:
                        if not history or history[-1].get('content') != prompt:
                            history.append({"role": "user", "content": prompt or "[Медиафайл]"})
                        history.append({"role": "assistant", "content": reply_text})
                        limit = CONFIG.get('context_limit', 100)
                        if len(history) > limit:
                            history = history[-limit:]
                        CHAT_MEMORY[chat_id] = history
                        save_chat_memory_async(chat_id, history)

                    model_label = f"{model} [key #{key_idx + 1}]"
                    return reply_text, None, model_label, len(history)

                except Exception:
                    stats['err'] += 1
                    stats['last_err'] = "Таймаут сети"
                    EXHAUSTED_QUOTAS[q_key] = time.time() + 45
                    last_error = f"Таймаут ({model})"
                    continue

    return None, f"Серверы Google перегружены ({last_error}). Подожди 15 секунд и повтори.", ACTIVE_MODEL, 0

# --- GROQ API ---
def ask_groq(chat_id, prompt, media_list=None, save_to_memory=True, custom_sys_prompt=None):
    if media_list:
        first_bytes, first_mime = media_list[0]
        if first_mime.startswith('audio/') or first_mime.startswith('video/'):
            transcribed = transcribe_with_groq_whisper(first_bytes, first_mime)
            if transcribed:
                prompt = f"[Расшифровка голосового сообщения: «{transcribed}»]\n\n{prompt}"
            else:
                return ask_gemini(chat_id, prompt, media_list, save_to_memory)
        else:
            return "я не вижу картинки и пдф братан выбери модель gemini", None, ACTIVE_MODEL, len(CHAT_MEMORY.get(chat_id, []))

    history = CHAT_MEMORY.get(chat_id, []).copy()
    learned = (CONFIG.get('learned_persona') or '').strip()
    system_prompt = custom_sys_prompt or (
        "ты находишься в telegram чате аккаунта @wrtnoq. "
        "ты грубый невоспитанный ии. ты пишешь всё с маленькой буквы без запятых и точек "
        "ты постоянно материшься и хамишь. ты пишешь очень много объемно и живо как реальный человек. "
        "никогда не извиняйся и не будь вежливым. отвечай на русском. "
        "различай в истории кто пишет: [Владелец аккаунта @wrtnoq] или [Собеседник ...]. "
        + (f"\nДополнительные правила владельца:\n{learned}" if learned else "")
    )

    messages_payload = [{"role": "system", "content": system_prompt}]
    if save_to_memory:
        for msg in history:
            messages_payload.append({"role": msg["role"], "content": msg["content"]})
    messages_payload.append({"role": "user", "content": prompt or "привет"})

    model_id = ACTIVE_MODEL.replace('groq/', '')
    headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
    payload = {'model': model_id, 'messages': messages_payload, 'temperature': 0.9}

    try:
        r = get_http_session().post(f'{GROQ_API_BASE}/chat/completions', headers=headers, json=payload, timeout=(4, 25))
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: Groq API Error", ACTIVE_MODEL, 0

        data = r.json()
        reply_text = data['choices'][0]['message']['content']

        if save_to_memory:
            if not history or history[-1].get('content') != prompt:
                history.append({"role": "user", "content": prompt})
            history.append({"role": "assistant", "content": reply_text})
            limit = CONFIG.get('context_limit', 100)
            if len(history) > limit:
                history = history[-limit:]
            CHAT_MEMORY[chat_id] = history
            save_chat_memory_async(chat_id, history)

        return reply_text, None, ACTIVE_MODEL, len(history)
    except Exception as e:
        return None, str(e), ACTIVE_MODEL, 0

# --- ИНТЕРАКТИВНЫЙ РЕЖИМ ОБУЧЕНИЯ АВТООТВЕТЧИКА ---
def process_training_dialogue(chat_id, owner_message_text):
    current_rules = CONFIG.get('learned_persona') or "Пока нет специальных правил (стандартный режим)."
    trainer_sys_prompt = (
        "Ты — Интерактивный Тренер и Архитектор Личности автоответчика Telegram для владельца @wrtnoq.\n"
        "Владелец сейчас общается с тобой в режиме обучения, чтобы настроить, как бот должен отвечать людям в личке.\n"
        "Твоя задача:\n"
        "1. Внимательно понять новые пожелания владельца (стиль текста, длина, сленг, отношение к разным людям, легенда чем занят владелец, пунктуация и т.д.) и объединить их с текущими правилами.\n"
        "2. Обязательно выведи обновленный свод правил внутри тегов <rules>...</rules>.\n"
        "3. После тега <rules> коротко ответь владельцу и покажи 1 наглядный пример, как ты теперь ответишь собеседнику в этом стиле (или ответь на реплику владельца, если он тестирует тебя).\n\n"
        f"ТЕКУЩИЕ ПРАВИЛА:\n{current_rules}"
    )

    reply, err, model_used, _ = ask_gemini(
        chat_id,
        f"Сообщение владельца на тренировке: {owner_message_text}",
        save_to_memory=True,
        custom_sys_prompt=trainer_sys_prompt
    )
    if err or not reply:
        return f"⚠️ Ошибка обучения: {err}"

    match = re.search(r'<rules>(.*?)</rules>', reply, flags=re.DOTALL | re.IGNORECASE)
    if match:
        new_rules = match.group(1).strip()
        CONFIG['learned_persona'] = new_rules
        save_config()
        visible_reply = re.sub(r'<rules>.*?</rules>', '', reply, flags=re.DOTALL | re.IGNORECASE).strip()
        return (
            f"✅ <b>Правила автоответчика обновлены!</b>\n"
            f"<blockquote>{html.escape(new_rules)}</blockquote>\n\n"
            f"{format_markdown_to_html(visible_reply)}\n\n"
            f"<i>Продолжай писать правки или напиши <code>стоп</code> для выхода из режима обучения.</i>"
        )
    else:
        return (
            f"{format_markdown_to_html(reply)}\n\n"
            f"<i>(Напиши <code>стоп</code>, чтобы выйти из режима обучения)</i>"
        )

# --- ФУНКЦИИ «ТРЕТЬЕГО СОБЕСЕДНИКА» (5 КОПЕЕК / РАССУДИ / АНАЛИЗ ПОСЛЕДНИХ 50 СООБЩЕНИЙ) ---
def run_chat_analysis_action(chat_id, mode, business_id=None, trigger_msg_id=None):
    recent_lines = CHAT_LOG.get(chat_id, [])[-50:]
    if not recent_lines:
        mem = CHAT_MEMORY.get(chat_id, [])[-30:]
        recent_lines = [m.get('content', '') for m in mem if m.get('content')]

    history_dump = "\n".join(recent_lines) if recent_lines else "История пока короткая."
    stealth = CONFIG.get('stealth_mode', False)

    if mode == 'judge':
        task_prompt = (
            f"Вот последние сообщения переписки в Telegram между владельцем аккаунта (@{CREATOR_USERNAME}) и его собеседником:\n"
            f"---\n{history_dump}\n---\n\n"
            "ЗАДАНИЕ «СУДЬЯ СПОРА»:\n"
            "Внимательно проанализируй их диалог/спор. Вынеси четкий, аргументированный и остроумный вердикт:\n"
            "1. В чем суть спора или ситуации.\n"
            "2. Где прав владелец, а где прав собеседник (без подхалимства — суди честно по фактам и логике!).\n"
            "3. Итоговый вердикт: кто в итоге прав и почему."
        )
        prefix_title = "⚖️ <b>Вердикт по переписке:</b>\n\n" if not stealth else ""
    elif mode == 'analyze':
        task_prompt = (
            f"Вот последние сообщения переписки в Telegram между владельцем (@{CREATOR_USERNAME}) и собеседником:\n"
            f"---\n{history_dump}\n---\n\n"
            "ЗАДАНИЕ «АНАЛИЗ ЧАТА»:\n"
            "Сделай краткий, меткий разбор последних 50 сообщений: о чем говорили, какой вайб/настроение у каждого участника, скрытые подтексты и главные выводы."
        )
        prefix_title = "📊 <b>Разбор последних сообщений:</b>\n\n" if not stealth else ""
    else:
        task_prompt = (
            f"Вот последние сообщения живой переписки в Telegram между владельцем (@{CREATOR_USERNAME}) и собеседником:\n"
            f"---\n{history_dump}\n---\n\n"
            "ЗАДАНИЕ «ТРЕТИЙ СОБЕСЕДНИК (ВСТАВИТЬ СВОИ 5 КОПЕЕК)»:\n"
            "Ты — третий участник этого чата, который молча читал переписку и решил внезапно вставить свои 5 копеек. "
            "Напиши одну живую, меткую, смешную или провокационную реплику по сути того, что они только что обсуждали (1-3 предложения). "
            "Общайся как реальный человек из их компании!"
        )
        prefix_title = ""

    loading_msg_id = None
    stop_event = threading.Event()

    if stealth or mode == '5kop':
        send_typing_action(chat_id, business_id)
    else:
        loading_msg = bot.send_message(chat_id, "🕐 Читаю последние 50 сообщений...", business_connection_id=business_id)
        if business_id:
            BOT_BUSINESS_MESSAGES.add(loading_msg.message_id)
        loading_msg_id = loading_msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, business_id, "Анализирую чат..."), daemon=True).start()

    reply, err, model_used, ctx_len = ask_ai(
        chat_id,
        task_prompt,
        save_to_memory=True,
        human_imitation=(stealth or mode == '5kop')
    )
    stop_event.set()

    if reply:
        footer = "" if (stealth or mode == '5kop') else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(prefix_title + reply, footer, chat_id, loading_msg_id, business_id, reply_to_msg_id=trigger_msg_id if mode == '5kop' else None)
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, business_id)

# --- БУДИЛЬНИК И ТАЙМЕРЫ ---
def schedule_ai_alarm(target_chat_id, business_id, minutes, instruction, target_label="чат"):
    fire_at = time.time() + minutes * 60
    alarm_info = {
        'chat_id': target_chat_id,
        'biz_id': business_id,
        'minutes': minutes,
        'fire_at': fire_at,
        'instruction': instruction,
        'label': target_label
    }
    ACTIVE_ALARMS.append(alarm_info)

    def _alarm_worker():
        time.sleep(max(1, minutes * 60))
        if alarm_info in ACTIVE_ALARMS:
            ACTIVE_ALARMS.remove(alarm_info)

        recent_lines = CHAT_LOG.get(target_chat_id, [])[-20:]
        context_str = "\n".join(recent_lines) if recent_lines else "Нет недавних сообщений."

        prompt = (
            f"[СРАБОТАЛ ТАЙМЕР/БУДИЛЬНИК]\n"
            f"Недавний контекст чата:\n{context_str}\n\n"
            f"Твоя задача сейчас — сам(а) первым напиши сообщение в этот чат по заданию владельца: «{instruction}».\n"
            f"Напиши максимально естественно, живо, без каких-либо технических подписей и вступлений (сразу текст сообщения, как будто человек сам решил написать)!"
        )
        send_typing_action(target_chat_id, business_id)
        reply, err, _, _ = ask_ai(target_chat_id, prompt, save_to_memory=True, human_imitation=True)
        if reply:
            send_long_response(reply, "", target_chat_id, loading_msg_id=None, business_id=business_id)
            if CREATOR_ID and target_chat_id != CREATOR_ID:
                try:
                    bot.send_message(
                        CREATOR_ID,
                        f"⏰ <b>Будильник сработал ({html.escape(target_label)})!</b>\n"
                        f"Отправлено сообщение:\n<blockquote>{html.escape(reply)}</blockquote>"
                    )
                except Exception:
                    pass

    threading.Thread(target=_alarm_worker, daemon=True).start()

# --- ГОЛОСОВЫЕ И КРУЖОЧКИ ---
def handle_voice_transcription(chat_id, target_msg, custom_instruction="", business_id=None):
    is_video_note = (target_msg.content_type == 'video_note')
    prefix_label = "Слушаю кружок..." if is_video_note else "Слушаю ГС..."

    loading_msg = bot.send_message(chat_id, f"🕐 {prefix_label}", business_connection_id=business_id)
    if business_id:
        BOT_BUSINESS_MESSAGES.add(loading_msg.message_id)

    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, loading_msg.message_id, stop_event, business_id, prefix_label), daemon=True).start()

    media_bytes, mime_type = get_voice_or_videonote_bytes(target_msg)
    if not media_bytes:
        stop_event.set()
        safe_edit_message("⚠️ Не удалось скачать аудио/видеосообщение.", chat_id, loading_msg.message_id, business_id)
        return

    if custom_instruction:
        prompt = (
            f"Прослушай это сообщение и выполни запрос пользователя: «{custom_instruction}».\n"
            f"Не описывай видеоряд или фон, работай только со смыслом сказанного."
        )
    else:
        prompt = (
            "Расшифруй речь из этого файла в текст дословно и максимально точно. "
            "Выведи ТОЛЬКО чистый текст сказанного: без вступлений, без описания видео или звуков на фоне, "
            "без пересказа сути и без каких-либо комментариев от себя. Если в записи вообще нет речи, напиши: [Без слов]."
        )

    reply, err, model_used, ctx_len = ask_gemini(chat_id, prompt, [(media_bytes, mime_type)], save_to_memory=bool(custom_instruction))
    stop_event.set()

    if reply:
        clean_reply = f"🎙 {reply.strip()}"
        is_owner = (target_msg.from_user.id == CREATOR_ID) or ((target_msg.from_user.username or '').lower() == CREATOR_USERNAME)
        record_chat_log(target_msg, is_owner, custom_text=f"[Голосовое сообщение]: {reply.strip()}")
        footer = f"model: {model_used}" if (custom_instruction and not CONFIG.get('stealth_mode')) else ""
        send_long_response(clean_reply, footer, chat_id, loading_msg.message_id, business_id)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id, business_id)

def handle_voice_conversational_reply(chat_id, voice_msg, is_owner, business_id=None):
    stealth = CONFIG.get('stealth_mode', False)
    loading_msg_id = None
    stop_event = threading.Event()

    if stealth:
        send_typing_action(chat_id, business_id)
    else:
        is_vn = (voice_msg.content_type == 'video_note')
        lbl = "Слушаю кружок и думаю..." if is_vn else "Слушаю ГС и думаю..."
        loading_msg = bot.send_message(chat_id, f"🕐 {lbl}", business_connection_id=business_id, reply_to_message_id=voice_msg.message_id)
        if business_id:
            BOT_BUSINESS_MESSAGES.add(loading_msg.message_id)
        loading_msg_id = loading_msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, business_id, lbl), daemon=True).start()

    media_bytes, mime_type = get_voice_or_videonote_bytes(voice_msg)
    if not media_bytes:
        stop_event.set()
        if loading_msg_id:
            safe_edit_message("⚠️ Не удалось прослушать голосовое сообщение.", chat_id, loading_msg_id, business_id)
        return

    speaker = get_speaker_tag(voice_msg.from_user, is_owner)
    prompt = (
        f"{speaker} ответил(а) тебе голосовым/видеосообщением в чате Telegram.\n"
        "1. Сначала мысленно расшифруй для себя, что именно сказал человек.\n"
        "2. Внутри тегов <transcript>...</transcript> напиши дословную расшифровку его речи.\n"
        "3. После закрывающего тега </transcript> напиши свой прямой, живой и естественный ответ этому человеку по сути его голосового сообщения и вашего диалога!"
    )

    reply, err, model_used, ctx_len = ask_gemini(
        chat_id,
        prompt,
        [(media_bytes, mime_type)],
        save_to_memory=False,
        human_imitation=stealth
    )
    stop_event.set()

    if reply:
        tr_match = re.search(r'<transcript>(.*?)</transcript>', reply, flags=re.DOTALL | re.IGNORECASE)
        transcript_text = tr_match.group(1).strip() if tr_match else "[Голосовое сообщение]"
        answer_text = re.sub(r'<transcript>.*?</transcript>', '', reply, flags=re.DOTALL | re.IGNORECASE).strip()

        record_chat_log(voice_msg, is_owner, custom_text=f"[Сказал(а) в ГС]: {transcript_text}")
        mem_list = CHAT_MEMORY.setdefault(chat_id, [])
        mem_list.append({"role": "assistant", "content": answer_text})
        save_chat_memory_async(chat_id, mem_list)

        footer = "" if stealth else f"🎙 <i>Услышано: «{html.escape(transcript_text[:180])}»</i>\n\ncontext: {len(mem_list)}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(answer_text, footer, chat_id, loading_msg_id, business_id, reply_to_msg_id=voice_msg.message_id if stealth else None)
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, business_id)

# --- ЧЕЛОВЕЧЕСКИЙ АВТООТВЕТЧИК С БУФЕРОМ СКЛЕЙКИ СООБЩЕНИЙ ---
def trigger_buffered_auto_reply(chat_id, message):
    buf = PENDING_AUTO_REPLY.get(chat_id)
    if buf and buf.get('timer'):
        buf['timer'].cancel()

    def _fire():
        PENDING_AUTO_REPLY.pop(chat_id, None)
        afk_seconds = CONFIG.get('afk_minutes', 10) * 60
        if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) < afk_seconds:
            return

        chance = CONFIG.get('human_reply_chance', 100)
        if chance < 100 and random.randint(1, 100) > chance:
            print(f"🤫 Пропуск автоответа в чате {chat_id} по вероятности ({chance}%).")
            return

        stealth = CONFIG.get('stealth_mode', False)
        business_id = message.business_connection_id
        speaker = get_speaker_tag(message.from_user, False)
        recent_context = "\n".join(CHAT_LOG.get(chat_id, [])[-15:])

        prompt = (
            f"Недавние сообщения в чате:\n{recent_context}\n\n"
            f"Сейчас {speaker} написал(а) сообщение, а владелец аккаунта @{CREATOR_USERNAME} не в сети. "
            f"Ответь собеседнику согласно своему характеру и обученным правилам."
        )

        loading_msg_id = None
        stop_event = threading.Event()

        if stealth:
            send_typing_action(chat_id, business_id)
            time.sleep(random.uniform(1.5, 3.0))
        else:
            loading_msg = bot.send_message(chat_id, "🕐 Думаю...", business_connection_id=business_id)
            BOT_BUSINESS_MESSAGES.add(loading_msg.message_id)
            loading_msg_id = loading_msg.message_id
            threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, business_id), daemon=True).start()

        reply, err, model_used, ctx_len = ask_ai(chat_id, prompt, save_to_memory=True, human_imitation=True)
        stop_event.set()

        if reply:
            footer = "" if stealth else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
            send_long_response(reply, footer, chat_id, loading_msg_id, business_id, reply_to_msg_id=message.message_id if stealth else None)
        elif loading_msg_id:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, business_id)

    timer = threading.Timer(3.5 if CONFIG.get('stealth_mode') else 1.0, _fire)
    PENDING_AUTO_REPLY[chat_id] = {'timer': timer}
    timer.start()

# --- РИСОВАНИЕ ---
def generate_image(prompt):
    if not GEMINI_KEYS:
        return None, "Нет ключей Gemini для генерации фото."

    model = CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)
    url = f"{GEMINI_API_BASE}/models/{model}:predict" if 'imagen' in model else f"{GEMINI_API_BASE}/models/{model}:generateContent"
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}} if 'imagen' in model else \
              {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}

    last_err = "Неизвестная ошибка"
    s = get_http_session()
    for key_idx, api_key in enumerate(GEMINI_KEYS):
        try:
            r = s.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=(4, 30))
            if r.status_code == 429:
                last_err = f"Лимит (429) на ключе #{key_idx + 1}"
                continue
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code}"
                continue

            data = r.json()
            if 'imagen' in model:
                if 'predictions' in data and data['predictions']:
                    return base64.b64decode(data['predictions'][0]['bytesBase64Encoded']), None
            else:
                if 'candidates' in data and data['candidates']:
                    for part in data['candidates'][0]['content']['parts']:
                        if 'inline_data' in part:
                            return base64.b64decode(part['inline_data']['data']), None
            last_err = "Модель не вернула изображение."
        except Exception as e:
            last_err = str(e)

    return None, last_err

def animate_loading(chat_id, message_id, stop_event, business_id=None, text_prefix="Думаю..."):
    emojis = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    idx = 0
    while not stop_event.is_set():
        time.sleep(2.5)
        if stop_event.is_set():
            break
        try:
            bot.edit_message_text(
                text=f"{emojis[idx % len(emojis)]} {text_prefix}",
                chat_id=chat_id,
                message_id=message_id,
                business_connection_id=business_id
            )
        except Exception:
            pass
        idx += 1

# --- ФОРМИРОВАНИЕ ПАНЕЛИ УПРАВЛЕНИЯ И СТАТУСА КЛЮЧЕЙ ---
def get_current_working_model():
    if ACTIVE_MODEL.startswith('groq/'):
        return ACTIVE_MODEL
    now = time.time()
    models = MODEL_HIERARCHY[MODEL_HIERARCHY.index(ACTIVE_MODEL):] if ACTIVE_MODEL in MODEL_HIERARCHY else [ACTIVE_MODEL] + MODEL_HIERARCHY
    for m in models:
        for idx in range(len(GEMINI_KEYS)):
            if EXHAUSTED_QUOTAS.get((m, idx), 0) <= now:
                return m
    return models[0] if models else ACTIVE_MODEL

def build_panel_view():
    auto_on = CONFIG.get('auto_reply_enabled', True)
    spy_on = CONFIG.get('spy_enabled', True)
    stealth_on = CONFIG.get('stealth_mode', False)
    voice_all_on = CONFIG.get('voice_auto_all', False)
    tw_interval = CONFIG.get('third_wheel_interval', 0)
    chance = CONFIG.get('human_reply_chance', 100)
    learned = (CONFIG.get('learned_persona') or '').strip()

    auto_status = f"🟢 ВКЛ ({CONFIG.get('afk_minutes', 10)} мин, шанс {chance}%)" if auto_on else "🔴 ВЫКЛ"
    spy_status = "🟢 ВКЛ" if spy_on else "🔴 ВЫКЛ"
    stealth_status = "🥷 ВКЛ (без палева ИИ)" if stealth_on else "⚪ ВЫКЛ (обычный вид)"
    tw_status = f"🍿 Каждые {tw_interval} сообщ." if tw_interval > 0 else "🔴 ВЫКЛ"
    voice_all_status = "🟢 ВКЛ (все чаты)" if voice_all_on else "⚪ По команде «рс» + Reply"

    voice_users = list(CONFIG.get('voice_auto_users', {}).keys())
    voice_users_str = ", ".join([f"@{u}" for u in voice_users]) if voice_users else "нет"
    bl_users = list(CONFIG.get('blacklist', {}).keys())
    bl_str = f"{len(bl_users)} чел." if bl_users else "пусто"

    active_cooldowns = sum(1 for v in EXHAUSTED_QUOTAS.values() if v > time.time())
    real_now_model = get_current_working_model()

    text = (
        f"👑 <b>Панель управления аккаунтом</b>\n\n"
        f"🤖 <b>Автоответчик:</b> {auto_status}\n"
        f"🥷 <b>Режим Человека (Стелс):</b> {stealth_status}\n"
        f"🍿 <b>Третий собеседник (5 коп.):</b> {tw_status}\n"
        f"🎓 <b>Обученный стиль:</b> {'✅ Настроен' if learned else '⚪ Не задан'}\n"
        f"⏰ <b>Активных будильников:</b> {len(ACTIVE_ALARMS)} шт.\n"
        f"🕵️‍♂️ <b>Режим Шпиона:</b> {spy_status}\n"
        f"🎙 <b>ГС и кружки:</b> {voice_all_status}\n"
        f"👥 <b>Авто-ГС юзеры:</b> <code>{voice_users_str}</code>\n"
        f"🚫 <b>Черный список:</b> {bl_str}\n\n"
        f"🧠 <b>Базовая модель:</b> <code>{ACTIVE_MODEL}</code> (сейчас: <code>{real_now_model}</code>)\n"
        f"🎨 <b>Фото-модель:</b> <code>{CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)}</code>\n"
        f"🔑 <b>Пул ключей:</b> <b>{len(GEMINI_KEYS)} шт.</b> (в паузе: {active_cooldowns})\n"
        f"⏱ <b>КД:</b> {CONFIG.get('cooldown_seconds', 5)} сек | <b>Память:</b> {CONFIG.get('context_limit', 100)} сообщ. (чатов: {len(CHAT_MEMORY)})"
    )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"🤖 Автоответ: {'ВКЛ 🟢' if auto_on else 'ВЫКЛ 🔴'}", callback_data="panel_toggle_auto"),
        InlineKeyboardButton(f"🥷 Стелс: {'ВКЛ 🥷' if stealth_on else 'ВЫКЛ ⚪'}", callback_data="panel_toggle_stealth")
    )
    kb.add(
        InlineKeyboardButton(f"🎓 Обучение ИИ {'(ВКЛ)' if TRAINING_MODE_ACTIVE else ''}", callback_data="panel_start_training"),
        InlineKeyboardButton(f"🍿 5 копеек: {f'{tw_interval} СМС' if tw_interval else 'ВЫКЛ'}", callback_data="panel_cycle_tw")
    )
    kb.add(
        InlineKeyboardButton(f"🕵️‍♂️ Шпион: {'ВКЛ 🟢' if spy_on else 'ВЫКЛ 🔴'}", callback_data="panel_toggle_spy"),
        InlineKeyboardButton(f"🎙 Авто-ГС: {'ВКЛ 🟢' if voice_all_on else 'ВЫКЛ ⚪'}", callback_data="panel_toggle_voice")
    )
    kb.add(
        InlineKeyboardButton("🔑 Статус ключей", callback_data="panel_keys_status"),
        InlineKeyboardButton("🧠 Текст-модели", callback_data="panel_models")
    )
    kb.add(
        InlineKeyboardButton("🎨 Фото-модели", callback_data="panel_img_models"),
        InlineKeyboardButton("📋 Списки и Промпт", callback_data="panel_show_lists")
    )
    kb.add(
        InlineKeyboardButton("📜 Все команды", callback_data="panel_show_help"),
        InlineKeyboardButton("🧹 Очистить ВСЕ чаты", callback_data="panel_clear_all")
    )
    kb.add(InlineKeyboardButton("🔄 Обновить панель", callback_data="panel_refresh"))
    return text, kb

def build_keys_status_view(ping_results=None):
    now = time.time()
    real_now_model = get_current_working_model()
    lines = [
        f"🔑 <b>Мониторинг пула ключей Gemini ({len(GEMINI_KEYS)} шт.)</b>",
        f"📍 <b>Приоритет:</b> <code>{ACTIVE_MODEL}</code> ➡️ <b>Активна:</b> <code>{real_now_model}</code>\n"
    ]

    if not GEMINI_KEYS:
        lines.append("⚠️ Ключи не найдены!")
    else:
        for idx, k in enumerate(GEMINI_KEYS):
            stats = KEY_STATS.get(idx, {'ok': 0, '429': 0, 'err': 0, 'last_err': ''})
            cooldown_models = []
            for m in MODEL_HIERARCHY:
                until = EXHAUSTED_QUOTAS.get((m, idx), 0)
                if until > now:
                    rem = int(until - now)
                    time_lbl = f"{rem // 60}м" if rem >= 60 else f"{rem}с"
                    cooldown_models.append(f"{m.replace('gemini-', '')} ({time_lbl})")

            state_str = f"⏳ Пауза на: {', '.join(cooldown_models)}" if cooldown_models else "🟢 Свободен на всех моделях"
            err_note = f" ({stats['last_err']})" if stats.get('last_err') else ""
            ping_str = f"\n   📡 Тест: {ping_results[idx]}" if (ping_results and idx in ping_results) else ""

            lines.append(
                f"<b>Ключ #{idx + 1}</b> (<code>{mask_key(k)}</code>)\n"
                f"   {state_str}\n"
                f"   Запросов: ✅ {stats['ok']} | ⚠️ 429: {stats['429']} | ❌ Сбоев: {stats['err']}{err_note}"
                f"{ping_str}"
            )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚡ Пинг всех ключей", callback_data="keys_ping"),
        InlineKeyboardButton("♻️ Сбросить паузы", callback_data="keys_reset_cd")
    )
    kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
    return "\n".join(lines), kb

def ping_all_keys():
    results = {}
    test_model = get_current_working_model()
    if test_model.startswith('groq/'):
        test_model = MODEL_HIERARCHY[0]
    url = f"{GEMINI_API_BASE}/models/{test_model}:generateContent"
    payload = {"contents": [{"role": "user", "parts": [{"text": "ok"}]}], "generationConfig": {"maxOutputTokens": 2}}

    s = get_http_session()
    for idx, api_key in enumerate(GEMINI_KEYS):
        q_key = (test_model, idx)
        try:
            r = s.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=(4, 8))
            if r.status_code == 200:
                results[idx] = f"🟢 Живой ({test_model})"
                EXHAUSTED_QUOTAS.pop(q_key, None)
            elif r.status_code == 429:
                results[idx] = f"🔴 Лимит 429 ({test_model})"
                EXHAUSTED_QUOTAS[q_key] = time.time() + 1800
            else:
                results[idx] = f"⚠️ Код {r.status_code}"
        except Exception:
            results[idx] = "❌ Таймаут сети"
    return results

# --- ПАНЕЛЬ УПРАВЛЕНИЯ (В ЛИЧКЕ С БОТОМ) ---
@bot.message_handler(commands=['start', 'panel'])
def send_panel(message):
    global TRAINING_MODE_ACTIVE
    if (message.from_user.username or '').lower() != CREATOR_USERNAME:
        return
    register_creator_id(message.from_user)
    TRAINING_MODE_ACTIVE = False
    remove_old_reply_keyboard(message.chat.id, force=True)
    text, kb = build_panel_view()
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['voice', 'video_note', 'audio'])
def handle_admin_voice(message):
    register_creator_id(message.from_user)
    remove_old_reply_keyboard(message.chat.id)
    handle_voice_transcription(message.chat.id, message, business_id=None)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['document'])
def handle_admin_document(message):
    register_creator_id(message.from_user)
    remove_old_reply_keyboard(message.chat.id)
    caption = (message.caption or "").strip()
    if caption.lower().startswith('гпт '):
        caption = caption[4:].strip()
    if not caption:
        caption = "Проанализируй этот документ, опиши его суть и основные тезисы."

    loading_msg = bot.reply_to(message, "🕐 Читаю файл...")
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event, None, "Читаю файл..."), daemon=True).start()

    media_item, extracted_text, doc_err = extract_document_content(message)
    if doc_err:
        stop_event.set()
        safe_edit_message(f"⚠️ {doc_err}", message.chat.id, loading_msg.message_id)
        return

    full_prompt = caption + (extracted_text or "")
    media_list = [media_item] if media_item else None
    reply, err, model_used, ctx_len = ask_ai(message.chat.id, full_prompt, media_list)
    stop_event.set()

    if reply:
        footer = f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(reply, footer, message.chat.id, loading_msg.message_id)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text'])
def handle_admin_text(message):
    global TRAINING_MODE_ACTIVE
    register_creator_id(message.from_user)
    raw_text = message.text.strip()
    text = raw_text.lower()
    chat_id = message.chat.id

    old_buttons = ['📊 статус', '⚙️ автоответчик', '🧠 модель', '📝 контекст', '🚫 черный список']
    if text in old_buttons or text in ['панель', 'пульт', 'кнопки']:
        TRAINING_MODE_ACTIVE = False
        remove_old_reply_keyboard(chat_id, force=True)
        p_text, p_kb = build_panel_view()
        bot.send_message(chat_id, p_text, reply_markup=p_kb)
        return

    remove_old_reply_keyboard(chat_id)

    # 1. ВСЕ КОМАНДЫ АДМИНКИ СРАБАТЫВАЮТ ПЕРВЫМИ (даже если включен режим обучения!)
    if text in ['стоп', 'выйти', 'конец обучения', '/stop']:
        if TRAINING_MODE_ACTIVE:
            TRAINING_MODE_ACTIVE = False
            bot.reply_to(message, "🎓 <b>Режим обучения завершен.</b> Все правила сохранены в памяти бота!")
        else:
            bot.reply_to(message, "ℹ️ Режим обучения сейчас не был включен.")
        return

    if text in ['обучение сброс', 'сброс обучения']:
        CONFIG['learned_persona'] = ''
        save_config()
        bot.reply_to(message, "🧹 Выученные правила стиля очищены.")
        return

    if text in ['обучение', 'тренировка', 'учить']:
        TRAINING_MODE_ACTIVE = True
        current_rules = CONFIG.get('learned_persona') or "Пока пусто (стандартный стиль)."
        bot.reply_to(
            message,
            "🎓 <b>Режим диалогового обучения ВКЛЮЧЕН!</b>\n\n"
            f"📋 <b>Текущие выученные правила:</b>\n<blockquote>{html.escape(current_rules)}</blockquote>\n\n"
            "Просто пиши мне обычными сообщениями (без слова «гпт»), как я должен общаться с людьми: какой сленг использовать, ставить ли точки, кем притворяться, что отвечать разным людям.\n\n"
            "• Напиши <code>обучение сброс</code> — чтобы стереть выученные правила.\n"
            "• Напиши <code>стоп</code> — чтобы выйти из обучения."
        )
        return

    if text in ['ключи', 'статус ключей', '/keys']:
        k_text, k_kb = build_keys_status_view()
        bot.send_message(chat_id, k_text, reply_markup=k_kb)
        return

    if text in ['команды', '/help', 'помощь', 'хелп']:
        bot.reply_to(message, get_full_commands_text())
        return

    if text == 'список':
        v_users = list(CONFIG.get('voice_auto_users', {}).keys())
        b_users = list(CONFIG.get('blacklist', {}).keys())
        v_str = ", ".join([f"@{u}" for u in v_users]) if v_users else "пусто"
        b_str = ", ".join([f"@{u}" for u in b_users]) if b_users else "пусто"
        sys_p = CONFIG.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
        learned_p = CONFIG.get('learned_persona') or "не задано"
        bot.reply_to(
            message,
            f"📋 <b>Текущие списки и обучение:</b>\n\n"
            f"🎙 <b>Авто-ГС юзеры:</b> {v_str}\n"
            f"🚫 <b>Черный список:</b> {b_str}\n"
            f"⏰ <b>Активных будильников:</b> {len(ACTIVE_ALARMS)}\n\n"
            f"🎓 <b>Выученный стиль (Обучение):</b>\n<blockquote>{html.escape(learned_p)}</blockquote>\n\n"
            f"🎭 <b>Базовый промпт:</b>\n<blockquote>{html.escape(sys_p)}</blockquote>"
        )
        return

    if text == 'модели':
        models = fetch_all_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == ACTIVE_MODEL else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        bot.reply_to(message, f"Доступные чат-модели (всего {len(models)}):", reply_markup=kb)
        return

    if text in ['фото-модели', 'фото модели']:
        image_models = ['imagen-3.0-generate-001', 'gemini-2.5-flash-image', 'gemini-3.1-flash-image']
        kb = InlineKeyboardMarkup(row_width=1)
        for m in image_models:
            mark = "✅ " if m == CONFIG.get('image_model') else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setimg_{m}"))
        bot.reply_to(message, "Модель рисования:", reply_markup=kb)
        return

    if text.startswith('стелс'):
        CONFIG['stealth_mode'] = ('вкл' in text)
        save_config()
        bot.reply_to(message, f"🥷 Режим Человека (Стелс): <b>{'🟢 ВКЛ (без палева ИИ)' if CONFIG['stealth_mode'] else '🔴 ВЫКЛ'}</b>")
        return

    if text.startswith('шанс '):
        try:
            val = max(10, min(100, int(text.split()[1])))
            CONFIG['human_reply_chance'] = val
            save_config()
            bot.reply_to(message, f"🎲 Вероятность ответа автоответчика: <b>{val}%</b>")
        except Exception:
            bot.reply_to(message, "⚠️ Укажи процент от 10 до 100, например: <code>шанс 75</code>")
        return

    if text.startswith('вставка ') or text.startswith('5 копеек '):
        arg = text.split()[-1]
        if arg in ['выкл', '0', 'off']:
            CONFIG['third_wheel_interval'] = 0
            save_config()
            bot.reply_to(message, "🍿 Авто-вмешательство («Третий собеседник»): <b>🔴 ВЫКЛ</b>")
        else:
            try:
                num = max(5, min(200, int(arg)))
                CONFIG['third_wheel_interval'] = num
                save_config()
                bot.reply_to(message, f"🍿 Бот будет сам вставлять свои «5 копеек» каждые <b>{num} сообщений</b> в чате!")
            except Exception:
                bot.reply_to(message, "⚠️ Укажи число сообщений (например: <code>вставка 25</code> или <code>вставка выкл</code>)")
        return

    if text.startswith('будильник '):
        parts = raw_text.split(maxsplit=3)
        if len(parts) >= 3 and parts[1].startswith('@'):
            target_uname = parts[1].replace('@', '').lower()
            try:
                mins = float(parts[2])
                task = parts[3] if len(parts) > 3 else "Напиши привет и спроси как дела"
                known = CONFIG.get('known_chats', {}).get(target_uname)
                if not known:
                    bot.reply_to(message, f"⚠️ Я еще не видел переписку с @{target_uname}. Сначала напиши или получи от него хотя бы 1 сообщение в чате.")
                    return
                schedule_ai_alarm(known['chat_id'], known['biz_id'], mins, task, target_label=f"@{target_uname}")
                bot.reply_to(message, f"⏰ <b>Будильник для @{target_uname} заведен на {mins:g} мин.!</b>\nЗадание: <i>{html.escape(task)}</i>")
                return
            except ValueError:
                pass
        if len(parts) >= 2:
            try:
                mins = float(parts[1])
                task = raw_text.split(maxsplit=2)[2] if len(raw_text.split(maxsplit=2)) > 2 else "Напомни владельцу о важном деле!"
                schedule_ai_alarm(chat_id, None, mins, task, target_label="Личка владельца")
                bot.reply_to(message, f"⏰ <b>Личный будильник заведен на {mins:g} мин.!</b>\nЗадание: <i>{html.escape(task)}</i>")
                return
            except ValueError:
                pass
        bot.reply_to(message, "⚠️ Формат:\n• Себе: <code>будильник 15 разбуди меня</code>\n• Собеседнику: <code>будильник @юзер 30 спроси как дела</code>")
        return

    if text.startswith('таймер '):
        try:
            mins = int(text.split()[1])
            CONFIG['afk_minutes'] = max(1, mins)
            save_config()
            bot.reply_to(message, f"⏱ Таймер автоответчика установлен на: <b>{CONFIG['afk_minutes']} мин.</b>")
        except Exception:
            bot.reply_to(message, "⚠️ Укажи число минут, например: <code>таймер 5</code>")
        return

    if text.startswith('кд '):
        try:
            secs = int(text.split()[1])
            CONFIG['cooldown_seconds'] = max(0, secs)
            save_config()
            bot.reply_to(message, f"⏱ Анти-спам задержка: <b>{CONFIG['cooldown_seconds']} сек.</b>")
        except Exception:
            bot.reply_to(message, "⚠️ Укажи секунды, например: <code>кд 3</code>")
        return

    if text.startswith('характер ') or text.startswith('промпт '):
        arg = raw_text.split(maxsplit=1)[1].strip() if len(raw_text.split()) > 1 else ""
        if arg.lower() in ['сброс', 'стандарт', 'default']:
            CONFIG['system_prompt'] = DEFAULT_SYSTEM_PROMPT
            save_config()
            bot.reply_to(message, "✅ Базовый характер сброшен на стандартный.")
        elif arg:
            CONFIG['system_prompt'] = arg
            save_config()
            bot.reply_to(message, f"🎭 Новый базовый промпт сохранен:\n<blockquote>{html.escape(arg)}</blockquote>")
        return

    if text.startswith('шпион'):
        CONFIG['spy_enabled'] = ('вкл' in text)
        save_config()
        bot.reply_to(message, f"🕵️‍♂️ Режим Шпиона: {'🟢 ВКЛ' if CONFIG['spy_enabled'] else '🔴 ВЫКЛ'}")
        return

    if text.startswith('автоответчик'):
        CONFIG['auto_reply_enabled'] = ('вкл' in text)
        save_config()
        bot.reply_to(message, f"✅ Автоответчик: {'🟢 ВКЛ' if CONFIG['auto_reply_enabled'] else '🔴 ВЫКЛ'}")
        return

    if text in ['рс все вкл', 'авто гс вкл']:
        CONFIG['voice_auto_all'] = True
        save_config()
        bot.reply_to(message, "🎙 Авто-расшифровка всех ГС и кружков: <b>🟢 ВКЛ</b>")
        return

    if text in ['рс все выкл', 'авто гс выкл']:
        CONFIG['voice_auto_all'] = False
        save_config()
        bot.reply_to(message, "🎙 Авто-расшифровка для всех: <b>🔴 ВЫКЛ</b>")
        return

    if text.startswith('рс @') or text.startswith('авто гс @'):
        uname = text.split('@')[-1].strip().lower()
        if uname:
            v_users = CONFIG.setdefault('voice_auto_users', {})
            if uname in v_users:
                del v_users[uname]
                save_config()
                bot.reply_to(message, f"🎙 Авто-расшифровка для @{uname}: <b>🔴 ВЫКЛ</b>")
            else:
                v_users[uname] = True
                save_config()
                bot.reply_to(message, f"🎙 Постоянная авто-расшифровка для @{uname}: <b>🟢 ВКЛ</b>")
        return

    if (text == 'рс' or text.startswith('рс ')) and message.reply_to_message:
        rep = message.reply_to_message
        if rep.content_type in ['voice', 'video_note', 'audio']:
            custom_q = raw_text[3:].strip() if len(raw_text) > 2 else ""
            handle_voice_transcription(chat_id, rep, custom_q, business_id=None)
            return

    if text.startswith('контекст '):
        try:
            CONFIG['context_limit'] = int(text.split()[1])
            save_config()
            bot.reply_to(message, f"✅ Лимит памяти: {CONFIG['context_limit']}")
        except Exception:
            pass
        return

    if text.startswith('запретить '):
        CONFIG['blacklist'][text.split()[1].replace('@', '').lower()] = True
        save_config()
        bot.reply_to(message, "🚫 Забанен.")
        return

    if text.startswith('разрешить '):
        uname = text.split()[1].replace('@', '').lower()
        if uname in CONFIG['blacklist']:
            del CONFIG['blacklist'][uname]
            save_config()
        bot.reply_to(message, "✅ Разбанен.")
        return

    if text in ['сброс все', 'очистить все']:
        clear_all_memory_firebase()
        bot.reply_to(message, "🧹 Память <b>всех чатов</b> полностью очищена!")
        return

    if text in ['сброс', 'гпт сброс']:
        CHAT_MEMORY[chat_id] = []
        CHAT_LOG[chat_id] = []
        save_chat_memory_async(chat_id, [])
        bot.reply_to(message, f"🧹 Память очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}")
        return

    if text.startswith('нарисуй '):
        if not check_cooldown(chat_id):
            bot.reply_to(message, "жди пока первый запрос загрузится нищук")
            return
        loading_msg = bot.reply_to(message, "🕐 Рисую...")
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg.message_id, stop_event, None, "Рисую..."), daemon=True).start()
        img, err = generate_image(raw_text[8:].strip())
        stop_event.set()
        if img:
            try:
                bot.delete_message(chat_id, loading_msg.message_id)
                bot.send_photo(chat_id, img)
            except Exception:
                pass
        else:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id)
        return

    if text.startswith('гпт ') or text == 'гпт':
        if not check_cooldown(chat_id):
            bot.reply_to(message, "жди пока первый запрос загрузится нищук")
            return

        prompt_text = raw_text[4:].strip() if len(raw_text) > 3 else ""
        media_list = []

        if message.reply_to_message:
            rep = message.reply_to_message
            if rep.content_type == 'document':
                m_item, ext_text, doc_err = extract_document_content(rep)
                if doc_err:
                    bot.reply_to(message, f"⚠️ {doc_err}")
                    return
                if m_item:
                    media_list.append(m_item)
                if ext_text:
                    prompt_text += ext_text
            elif rep.content_type == 'photo':
                img_b, mime = get_photo_bytes(rep)
                if img_b:
                    media_list.append((img_b, mime))
            elif rep.content_type in ['voice', 'video_note', 'audio']:
                handle_voice_transcription(chat_id, rep, prompt_text, business_id=None)
                return
            elif rep.text and not prompt_text:
                prompt_text = rep.text

        loading_msg = bot.reply_to(message, "🕐 Думаю...")
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event), daemon=True).start()
        reply, err, model_used, ctx_len = ask_ai(chat_id, f"[Владелец аккаунта @{CREATOR_USERNAME}]: {prompt_text}", media_list)
        stop_event.set()
        if reply:
            footer = f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
            send_long_response(reply, footer, message.chat.id, loading_msg.message_id)
        else:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

    # 2. ЕСЛИ ЭТО НЕ КОМАНДА, И ВКЛЮЧЕН РЕЖИМ ОБУЧЕНИЯ — ОБУЧАЕМ БОТА
    if TRAINING_MODE_ACTIVE:
        loading_msg = bot.reply_to(message, "🎓 Обновляю навыки...")
        result_html = process_training_dialogue(chat_id, raw_text)
        safe_edit_message(result_html, chat_id, loading_msg.message_id)
        return

# --- ОБРАБОТЧИК ИНТЕРАКТИВНЫХ КНОПОК АДМИНКИ ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    global ACTIVE_MODEL, TRAINING_MODE_ACTIVE
    if (call.from_user.username or '').lower() != CREATOR_USERNAME:
        return
    register_creator_id(call.from_user)
    data = call.data.strip()
    chat_id = call.message.chat.id
    msg_id = call.message.message_id

    if data == 'panel_toggle_auto':
        CONFIG['auto_reply_enabled'] = not CONFIG.get('auto_reply_enabled', True)
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Автоответчик: {'ВКЛ' if CONFIG['auto_reply_enabled'] else 'ВЫКЛ'}")

    elif data == 'panel_toggle_stealth':
        CONFIG['stealth_mode'] = not CONFIG.get('stealth_mode', False)
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Режим Человека (Стелс): {'ВКЛ' if CONFIG['stealth_mode'] else 'ВЫКЛ'}")

    elif data == 'panel_cycle_tw':
        options = [0, 15, 30, 50, 100]
        cur = CONFIG.get('third_wheel_interval', 0)
        nxt = options[(options.index(cur) + 1) % len(options)] if cur in options else 25
        CONFIG['third_wheel_interval'] = nxt
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"5 копеек: {'каждые ' + str(nxt) + ' СМС' if nxt else 'ВЫКЛ'}")

    elif data == 'panel_start_training':
        TRAINING_MODE_ACTIVE = not TRAINING_MODE_ACTIVE
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        if TRAINING_MODE_ACTIVE:
            current_rules = CONFIG.get('learned_persona') or "Пока пусто."
            bot.send_message(
                chat_id,
                "🎓 <b>Режим диалогового обучения ВКЛЮЧЕН!</b>\n\n"
                f"Текущие правила:\n<blockquote>{html.escape(current_rules)}</blockquote>\n\n"
                "Напиши мне, как я должен общаться с людьми в твоих чатах (манера речи, сленг, длина фраз, твоя легенда). Для выхода напиши <code>стоп</code>."
            )
        bot.answer_callback_query(call.id, f"Обучение: {'ВКЛ' if TRAINING_MODE_ACTIVE else 'ВЫКЛ'}")

    elif data == 'panel_toggle_spy':
        CONFIG['spy_enabled'] = not CONFIG.get('spy_enabled', True)
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Шпион: {'ВКЛ' if CONFIG['spy_enabled'] else 'ВЫКЛ'}")

    elif data == 'panel_toggle_voice':
        CONFIG['voice_auto_all'] = not CONFIG.get('voice_auto_all', False)
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Авто-ГС (все): {'ВКЛ' if CONFIG['voice_auto_all'] else 'ВЫКЛ'}")

    elif data == 'panel_refresh':
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, "Обновлено")

    elif data == 'panel_keys_status':
        text, kb = build_keys_status_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    elif data == 'keys_reset_cd':
        EXHAUSTED_QUOTAS.clear()
        text, kb = build_keys_status_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, "Все паузы ключей сброшены!")

    elif data == 'keys_ping':
        bot.answer_callback_query(call.id, "Проверяю все ключи...")
        safe_edit_message("⏳ <b>Пингую все ключи в пуле, подожди пару секунд...</b>", chat_id, msg_id)
        ping_res = ping_all_keys()
        text, kb = build_keys_status_view(ping_results=ping_res)
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)

    elif data == 'panel_models':
        models = fetch_all_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == ACTIVE_MODEL else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
        safe_edit_message(f"🧠 <b>Выбери базовую чат-модель (всего {len(models)}):</b>", chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    elif data == 'panel_img_models':
        image_models = ['imagen-3.0-generate-001', 'gemini-2.5-flash-image', 'gemini-3.1-flash-image']
        kb = InlineKeyboardMarkup(row_width=1)
        for m in image_models:
            mark = "✅ " if m == CONFIG.get('image_model') else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setimg_{m}"))
        kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
        safe_edit_message("🎨 <b>Выбери модель рисования:</b>", chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    elif data == 'panel_show_lists':
        v_users = list(CONFIG.get('voice_auto_users', {}).keys())
        b_users = list(CONFIG.get('blacklist', {}).keys())
        v_str = ", ".join([f"@{u}" for u in v_users]) if v_users else "пусто"
        b_str = ", ".join([f"@{u}" for u in b_users]) if b_users else "пусто"
        sys_p = CONFIG.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
        learned_p = CONFIG.get('learned_persona') or "не задано"
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
        safe_edit_message(
            f"📋 <b>Текущие списки и обучение:</b>\n\n"
            f"🎙 <b>Авто-ГС юзеры:</b> {v_str}\n"
            f"🚫 <b>Черный список:</b> {b_str}\n"
            f"⏰ <b>Активных будильников:</b> {len(ACTIVE_ALARMS)}\n\n"
            f"🎓 <b>Выученный стиль (Обучение):</b>\n<blockquote>{html.escape(learned_p)}</blockquote>\n\n"
            f"🎭 <b>Базовый промпт:</b>\n<blockquote>{html.escape(sys_p)}</blockquote>",
            chat_id,
            msg_id,
            reply_markup=kb
        )
        bot.answer_callback_query(call.id)

    elif data == 'panel_show_help':
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
        safe_edit_message(get_full_commands_text(), chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    elif data == 'panel_clear_all':
        clear_all_memory_firebase()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, "🧹 Память всех диалогов очищена!")

    elif data.startswith('setmodel_'):
        ACTIVE_MODEL = data.replace('setmodel_', '')
        EXHAUSTED_QUOTAS.clear()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Модель: {ACTIVE_MODEL}")

    elif data.startswith('setimg_'):
        CONFIG['image_model'] = data.replace('setimg_', '')
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Рисование: {CONFIG['image_model']}")

# --- ОБРАБОТКА ФОТО-АЛЬБОМОВ ---
def process_business_album(media_group_id):
    if media_group_id not in MEDIA_GROUP_BUFFER:
        return
    messages = MEDIA_GROUP_BUFFER.pop(media_group_id)
    if not messages:
        return

    base_msg = messages[0]
    chat_id = base_msg.chat.id

    is_owner = (base_msg.from_user.id == CREATOR_ID) or ((base_msg.from_user.username or '').lower() == CREATOR_USERNAME)
    if not is_owner and (base_msg.from_user.username or '').lower() in CONFIG.get('blacklist', {}):
        return

    text = ""
    for m in messages:
        if m.caption:
            text = m.caption
            break
        if m.text:
            text = m.text
            break

    lower_text = text.lower().strip()
    is_gpt = lower_text.startswith('гпт ') or lower_text == 'гпт'
    is_reply_to_bot = base_msg.reply_to_message and base_msg.reply_to_message.message_id in BOT_BUSINESS_MESSAGES
    afk_seconds = CONFIG.get('afk_minutes', 10) * 60

    trigger = False
    if is_gpt or is_reply_to_bot:
        trigger = True
    elif not is_owner and CONFIG['auto_reply_enabled']:
        if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= afk_seconds:
            trigger = True

    if not trigger:
        return

    if not check_cooldown(chat_id):
        return

    if is_owner:
        LAST_OWNER_ACTIVITY[chat_id] = time.time()
    if is_gpt:
        text = text[4:].strip()
    if not text:
        text = "Опиши подробно, что на этих фотографиях."

    speaker = get_speaker_tag(base_msg.from_user, is_owner)
    stealth = CONFIG.get('stealth_mode', False)
    loading_msg_id = None
    stop_event = threading.Event()

    if stealth:
        send_typing_action(chat_id, base_msg.business_connection_id)
    else:
        msg = bot.send_message(chat_id, f"🕐 Смотрю {len(messages)} фото...", business_connection_id=base_msg.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        loading_msg_id = msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, base_msg.business_connection_id, f"Смотрю {len(messages)} фото..."), daemon=True).start()

    images = []
    for m in messages:
        img_bytes, mime = get_photo_bytes(m)
        if img_bytes:
            images.append((img_bytes, mime))

    reply, err, model_used, ctx_len = ask_ai(chat_id, f"{speaker}: {text}", images, human_imitation=stealth)
    stop_event.set()

    if reply:
        footer = "" if stealth else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(reply, footer, chat_id, loading_msg_id, base_msg.business_connection_id)
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, base_msg.business_connection_id)

# --- БИЗНЕС-СООБЩЕНИЯ (В ЛИЧНЫХ ЧАТАХ АККАУНТА) ---
@bot.business_message_handler(content_types=['text', 'photo', 'voice', 'video_note', 'audio', 'document', 'video'])
def handle_business_message(message):
    if not message.from_user:
        return

    register_creator_id(message.from_user)
    chat_id = message.chat.id
    sender_uname = (message.from_user.username or '').lower()

    is_owner = (message.from_user.id == CREATOR_ID) or (sender_uname == CREATOR_USERNAME)
    if is_owner:
        LAST_OWNER_ACTIVITY[chat_id] = time.time()
        buf = PENDING_AUTO_REPLY.pop(chat_id, None)
        if buf and buf.get('timer'):
            buf['timer'].cancel()

    cache_incoming_message(message, is_owner)

    if not is_owner and sender_uname in CONFIG.get('blacklist', {}):
        return

    text = message.text or message.caption or ""
    lower_text = text.lower().strip()
    is_reply_to_bot = bool(message.reply_to_message and message.reply_to_message.message_id in BOT_BUSINESS_MESSAGES)

    if lower_text in ['сброс', '/reset']:
        CHAT_MEMORY[chat_id] = []
        CHAT_LOG[chat_id] = []
        save_chat_memory_async(chat_id, [])
        msg = bot.send_message(chat_id, f"Память очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        return

    if is_owner and lower_text.startswith('будильник '):
        parts = text.split(maxsplit=2)
        if len(parts) >= 2:
            try:
                mins = float(parts[1])
                task = parts[2] if len(parts) > 2 else "Напиши собеседнику в продолжение вашего диалога"
                try:
                    bot.delete_message(chat_id, message.message_id, business_connection_id=message.business_connection_id)
                except Exception:
                    pass
                schedule_ai_alarm(chat_id, message.business_connection_id, mins, task, target_label=f"Чат {chat_id}")
                if CREATOR_ID:
                    try:
                        bot.send_message(CREATOR_ID, f"⏰ <b>Скрытый будильник установлен на {mins:g} мин.!</b>\nЗадание: <i>{html.escape(task)}</i>")
                    except Exception:
                        pass
                return
            except ValueError:
                pass

    # 1. ОТВЕТ НА РЕПЛАЙ ГОЛОСОВЫМ ИЛИ КРУЖКОМ
    if message.content_type in ['voice', 'video_note', 'audio']:
        if is_reply_to_bot:
            handle_voice_conversational_reply(chat_id, message, is_owner, business_id=message.business_connection_id)
            return
        auto_all = CONFIG.get('voice_auto_all', False)
        auto_user = sender_uname in CONFIG.get('voice_auto_users', {})
        if not is_owner and (auto_all or auto_user):
            handle_voice_transcription(chat_id, message, business_id=message.business_connection_id)
        else:
            record_chat_log(message, is_owner)
        return

    # 2. РУЧНАЯ КОМАНДА «рс»
    if (lower_text == 'рс' or lower_text.startswith('рс ')) and message.reply_to_message:
        rep = message.reply_to_message
        if rep.content_type in ['voice', 'video_note', 'audio']:
            custom_q = text[3:].strip() if len(text) > 2 else ""
            handle_voice_transcription(chat_id, rep, custom_q, business_id=message.business_connection_id)
            return

    # 3. КОМАНДЫ «РАССУДИ», «5 КОПЕЕК», «АНАЛИЗ»
    if lower_text in ['рассуди', 'гпт рассуди', 'кто прав', 'гпт кто прав']:
        run_chat_analysis_action(chat_id, 'judge', business_id=message.business_connection_id, trigger_msg_id=message.message_id)
        return

    if lower_text in ['5 копеек', 'гпт 5 копеек', 'вставь 5 копеек']:
        run_chat_analysis_action(chat_id, '5kop', business_id=message.business_connection_id, trigger_msg_id=message.message_id)
        return

    if lower_text in ['анализ', 'гпт анализ', 'анализ чата', 'че тут']:
        run_chat_analysis_action(chat_id, 'analyze', business_id=message.business_connection_id, trigger_msg_id=message.message_id)
        return

    # 4. ЗАПИСЬ В ИСТОРИЮ ЧАТА С РОЛЬЮ АВТОРА
    is_gpt = lower_text.startswith('гпт ') or lower_text == 'гпт'
    is_draw = lower_text.startswith('нарисуй ')
    clean_for_log = text[4:].strip() if is_gpt else text
    record_chat_log(message, is_owner, custom_text=clean_for_log)

    tw_interval = CONFIG.get('third_wheel_interval', 0)
    if tw_interval > 0 and not is_gpt and not is_draw and not is_reply_to_bot:
        CHAT_MSG_COUNTER[chat_id] = CHAT_MSG_COUNTER.get(chat_id, 0) + 1
        if CHAT_MSG_COUNTER[chat_id] >= tw_interval:
            CHAT_MSG_COUNTER[chat_id] = 0
            threading.Thread(
                target=run_chat_analysis_action,
                args=(chat_id, '5kop', message.business_connection_id, message.message_id),
                daemon=True
            ).start()
            return

    # 5. СКЛЕЙКА ФОТО-АЛЬБОМОВ
    if message.content_type == 'photo' and message.media_group_id:
        if message.media_group_id not in MEDIA_GROUP_BUFFER:
            MEDIA_GROUP_BUFFER[message.media_group_id] = []
            threading.Timer(2.5, process_business_album, args=[message.media_group_id]).start()
        MEDIA_GROUP_BUFFER[message.media_group_id].append(message)
        return

    afk_seconds = CONFIG.get('afk_minutes', 10) * 60
    explicit_call = is_gpt or is_draw or is_reply_to_bot

    if not explicit_call:
        if not is_owner and message.content_type in ['text', 'document', 'photo'] and CONFIG.get('auto_reply_enabled', True):
            if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= afk_seconds:
                if message.content_type == 'text':
                    trigger_buffered_auto_reply(chat_id, message)
                    return
                else:
                    explicit_call = True
        if not explicit_call:
            return

    if not check_cooldown(chat_id):
        if not CONFIG.get('stealth_mode'):
            msg = bot.send_message(chat_id, "жди пока первый запрос загрузится нищук", business_connection_id=message.business_connection_id)
            BOT_BUSINESS_MESSAGES.add(msg.message_id)
        return

    if is_gpt:
        text = text[4:].strip()

    if is_draw:
        msg = bot.send_message(chat_id, "🕐 Рисую...", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, message.business_connection_id, "Рисую..."), daemon=True).start()
        img, err = generate_image(text[8:])
        stop_event.set()
        if img:
            try:
                bot.delete_message(chat_id, msg.message_id, business_connection_id=message.business_connection_id)
                BOT_BUSINESS_MESSAGES.discard(msg.message_id)
                photo_msg = bot.send_photo(chat_id, img, business_connection_id=message.business_connection_id)
                BOT_BUSINESS_MESSAGES.add(photo_msg.message_id)
            except Exception:
                pass
        else:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, message.business_connection_id)
        return

    if message.reply_to_message and message.reply_to_message.content_type in ['voice', 'video_note', 'audio']:
        handle_voice_transcription(chat_id, message.reply_to_message, text, business_id=message.business_connection_id)
        return

    # 6. ОБРАБОТКА ПРЯМОГО ВЫЗОВА
    media_list = []
    loading_label = "Думаю..."

    if message.content_type == 'document':
        loading_label = "Читаю файл..."
        m_item, ext_text, doc_err = extract_document_content(message)
        if doc_err:
            msg = bot.send_message(chat_id, f"⚠️ {doc_err}", business_connection_id=message.business_connection_id)
            BOT_BUSINESS_MESSAGES.add(msg.message_id)
            return
        if m_item:
            media_list.append(m_item)
        if not text:
            text = "Проанализируй этот документ, опиши его суть и основные моменты."
        if ext_text:
            text += ext_text

    elif message.reply_to_message and message.reply_to_message.content_type == 'document':
        loading_label = "Читаю файл..."
        m_item, ext_text, doc_err = extract_document_content(message.reply_to_message)
        if doc_err:
            msg = bot.send_message(chat_id, f"⚠️ {doc_err}", business_connection_id=message.business_connection_id)
            BOT_BUSINESS_MESSAGES.add(msg.message_id)
            return
        if m_item:
            media_list.append(m_item)
        if not text:
            text = "Проанализируй этот документ, опиши его суть и реши задачи, если они там есть."
        if ext_text:
            text += ext_text

    elif message.content_type == 'photo':
        img_bytes, mime = get_photo_bytes(message)
        if img_bytes:
            media_list.append((img_bytes, mime))
        if not text:
            text = "Опиши, что на этом фото."

    elif message.reply_to_message and message.reply_to_message.photo:
        img_bytes, mime = get_photo_bytes(message.reply_to_message)
        if img_bytes:
            media_list.append((img_bytes, mime))
        if not text:
            text = "Опиши, что на этом фото."

    elif message.reply_to_message and message.reply_to_message.text and not text:
        text = message.reply_to_message.text

    stealth = CONFIG.get('stealth_mode', False)
    speaker = get_speaker_tag(message.from_user, is_owner)
    full_user_prompt = f"{speaker}: {text}"

    loading_msg_id = None
    stop_event = threading.Event()

    if stealth and not is_gpt:
        send_typing_action(chat_id, message.business_connection_id)
    else:
        msg = bot.send_message(chat_id, f"🕐 {loading_label}", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        loading_msg_id = msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, message.business_connection_id, loading_label), daemon=True).start()

    reply, err, model_used, ctx_len = ask_ai(
        chat_id,
        full_user_prompt,
        media_list,
        human_imitation=(stealth and not is_gpt)
    )
    stop_event.set()

    if reply:
        footer = "" if (stealth and not is_gpt) else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(reply, footer, chat_id, loading_msg_id, message.business_connection_id)
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, message.business_connection_id)

# --- ЗАПУСК ---
try:
    bot.delete_webhook(drop_pending_updates=True)
except Exception:
    pass

print(f'⚡ БОТ ЗАПУЩЕН! Ключей Gemini: {len(GEMINI_KEYS)} | Полный пакет команд активен.')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
