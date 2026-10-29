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
# Каскад лучших бесплатных моделей Groq для быстрого автоответчика
GROQ_AUTOREPLY_FALLBACKS = [
    'groq/openai/gpt-oss-120b',
    'groq/qwen/qwen3.8-27b',
    'groq/openai/gpt-oss-20b'
]

DEFAULT_IMAGE_MODEL = 'imagen-3.0-generate-001'
DEFAULT_CONTEXT_LIMIT = 100
DEFAULT_SYSTEM_PROMPT = "Ты — умный и полезный ИИ-ассистент. Отвечай четко, по делу и на русском языке."

GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GROQ_API_BASE = 'https://api.groq.com/openai/v1'

ACTIVE_MODEL = MODEL_HIERARCHY[0]
OLD_KEYBOARD_CLEARED = False
TRAINING_MODE_ACTIVE = False  # Режим диалогового обучения в админке

CONFIG = {
    'auto_reply_enabled': False,                     # Общий автоответчик (для всех)
    'auto_reply_users': {},                          # Индивидуальный автоответчик по @username
    'auto_reply_chats': {},                          # Индивидуальный автоответчик по chat_id
    'auto_reply_model': 'groq/openai/gpt-oss-120b',  # Отдельная быстрая модель для Автоответчика!
    'spy_enabled': True,
    'stealth_mode': True,                            # Работает ТОЛЬКО для автоответчика!
    'human_reply_chance': 100,                       # Шанс ответа автоответчика в % (10..100)
    'third_wheel_interval': 0,                       # Через сколько сообщений вставлять «5 копеек» (0 = выкл)
    'learned_persona': '',                           # Правила стиля ТОЛЬКО для автоответчика
    'voice_auto_all': False,
    'voice_auto_users': {},
    'known_chats': {},
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

# Раздельные множества сообщений бота: обычные ответы ГПТ vs сообщения Автоответчика
BOT_GPT_MESSAGES = set()
BOT_AUTOREPLY_MESSAGES = set()
MEDIA_GROUP_BUFFER = {}

MESSAGE_CACHE = {}
MAX_CACHE_SIZE = 500

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)

thread_local = threading.local()

def get_http_session():
    if not hasattr(thread_local, "session"):
        thread_local.session = requests.Session()
    return thread_local.session

def is_auto_reply_active_for(chat_id, sender_uname):
    """Проверяет, включен ли автоответчик глобально ИЛИ персонально для этого юзера/чата."""
    if CONFIG.get('auto_reply_enabled', False):
        return True
    if sender_uname and sender_uname.lower() in CONFIG.get('auto_reply_users', {}):
        return True
    if str(chat_id) in CONFIG.get('auto_reply_chats', {}):
        return True
    return False

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
                    print(f"✅ Проверенная очередь моделей ГПТ: {MODEL_HIERARCHY}")
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
            if not CONFIG.get('auto_reply_model'):
                CONFIG['auto_reply_model'] = 'groq/openai/gpt-oss-120b'
            for dict_key in ('voice_auto_users', 'blacklist', 'known_chats', 'auto_reply_users', 'auto_reply_chats'):
                if not isinstance(CONFIG.get(dict_key), dict):
                    CONFIG[dict_key] = {}
            print("🔥 Настройки загружены из Firebase.")
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
    chat_id = int(chat_id)
    def _worker():
        try:
            if not history:
                get_http_session().delete(f'{FIREBASE_URL}/chat_memory/{chat_id}.json', timeout=5)
            else:
                get_http_session().put(f'{FIREBASE_URL}/chat_memory/{chat_id}.json', json=history, timeout=5)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

def reset_single_chat_memory(chat_id):
    """Полностью очищает память и лог конкретного чата (общую для владельца и собеседника)."""
    cid = int(chat_id)
    CHAT_MEMORY[cid] = []
    CHAT_LOG[cid] = []
    CHAT_MSG_COUNTER[cid] = 0
    save_chat_memory_async(cid, [])

def clear_all_memory_firebase():
    """Полностью очищает память всех чатов в ОЗУ и в Firebase."""
    CHAT_MEMORY.clear()
    CHAT_LOG.clear()
    CHAT_MSG_COUNTER.clear()
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

# --- УЧЕТ РОЛЕЙ И ОБЩЕГО КОНТЕКСТА ЧАТА ---
def get_speaker_tag(user, is_owner):
    if is_owner:
        return f"[Владелец аккаунта @{CREATOR_USERNAME}]"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Собеседник"
    uname = f"@{user.username}" if user.username else f"id:{user.id}"
    return f"[Собеседник {full_name} ({uname})]"

def record_chat_log(message, is_owner, custom_text=None, add_to_memory=True):
    chat_id = int(message.chat.id)
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

    if add_to_memory:
        mem_list = CHAT_MEMORY.setdefault(chat_id, [])
        mem_list.append({"role": "user", "content": f"{speaker}: {text}"})
        limit = CONFIG.get('context_limit', 100)
        if len(mem_list) > limit:
            CHAT_MEMORY[chat_id] = mem_list[-limit:]

def build_gpt_system_instruction():
    """
    Чистая системная инструкция ТОЛЬКО для обычного вызова 'гпт' (работает одинаково и на Gemini, и на Groq).
    Никаких принудительных матов и никакого обучения автоответчика!
    """
    base = CONFIG.get('system_prompt') or DEFAULT_SYSTEM_PROMPT
    tg_context = (
        "КОНТЕКСТ СРЕДЫ:\n"
        f"1. Ты находишься в мессенджере Telegram (аккаунт владельца @{CREATOR_USERNAME}).\n"
        f"2. В истории чата перед сообщениями указано, кто пишет: `[Владелец аккаунта @{CREATOR_USERNAME}]` или `[Собеседник Имя (@юзернейм)]`.\n"
        "3. Четко различай, кто именно задает тебе вопрос, и отвечай на русском языке. Пиши сразу чистый ответ без технических меток в начале."
    )
    return f"{base}\n\n{tg_context}"

def build_autoreply_system_instruction():
    """
    Системная инструкция ТОЛЬКО для Автоответчика (включает выученный стиль learned_persona и режим человека).
    """
    learned = (CONFIG.get('learned_persona') or '').strip()
    stealth = CONFIG.get('stealth_mode', True)

    parts = [
        f"Ты ведешь переписку в личных сообщениях Telegram на аккаунте владельца @{CREATOR_USERNAME}.",
        "В истории чата видно, где писал `[Владелец аккаунта]`, а где `[Собеседник ...]`. Сейчас ты отвечаешь собеседнику.",
        "Никогда не пиши в начале ответа технические метки вроде `[Ответ]:` или `[Владелец]:` — выводи только сам текст сообщения на русском языке."
    ]

    if learned:
        parts.append(
            f"ПРАВИЛА СТИЛЯ И ПОВЕДЕНИЯ:\n{learned}"
        )
    else:
        parts.append(
            "Общайся живо, естественно, как обычный человек в Телеграме."
        )

    if stealth:
        parts.append(
            "Стиль общения: пиши кратко и естественно, как живой собеседник в мессенджере, без длинных списков и шаблонных фраз."
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

def send_long_response(reply_text, footer_text, chat_id, loading_msg_id=None, business_id=None, reply_to_msg_id=None, is_autoreply=False):
    reply_text = re.sub(r'^\[(Бот|Ассистент|ИИ|Владелец[^\]]*|Собеседник[^\]]*)\]:\s*', '', reply_text.strip(), flags=re.IGNORECASE)
    reply_text = re.sub(r'<think>.*?</think>', '', reply_text, flags=re.DOTALL | re.IGNORECASE).strip()

    raw_chunks = split_text_into_chunks(reply_text, max_len=3600)
    total = len(raw_chunks)
    target_set = BOT_AUTOREPLY_MESSAGES if is_autoreply else BOT_GPT_MESSAGES

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
            if business_id:
                target_set.add(loading_msg_id)
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
                    target_set.add(sent.message_id)
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
                        target_set.add(sent.message_id)
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

# --- ПОЛНЫЙ ТЕКСТ ВСЕХ КОМАНД ---
def get_full_commands_text():
    return (
        "📜 <b>Полный список всех команд бота:</b>\n\n"
        "🔹 <b>1. Обычный ГПТ и Модели (ВСЕГДА показывает context и model):</b>\n"
        "• <code>/panel</code> (или <code>панель</code>) — главный пульт с кнопками\n"
        "• <code>модели</code> — выбрать модель для обычного <code>гпт</code>\n"
        "• <code>авто-модели</code> — выбрать модель отдельно для Автоответчика (Groq / Gemini)\n"
        "• <code>фото-модели</code> — выбрать модель для рисования\n"
        "• <code>гпт [текст]</code> — задать вопрос ИИ (или Reply на фото/файл/ГС)\n"
        "• <code>нарисуй [описание]</code> — сгенерировать картинку\n"
        "• <code>контекст 100</code> — изменить глубину памяти диалога\n"
        "• <code>сброс</code> — очистить общий контекст чата (у тебя и у собеседника!)\n"
        "• <code>сброс все</code> — очистить память вообще всех чатов\n"
        "• <code>характер [текст]</code> — задать системный промпт для обычного <code>гпт</code>\n"
        "• <code>характер сброс</code> — вернуть стандартный промпт для <code>гпт</code>\n"
        "• <code>ключи</code> — статус всех ключей Gemini и пинг-тест\n"
        "• <code>список</code> — посмотреть все списки и настройки\n\n"
        "🔹 <b>2. Автоответчик (Работает отдельно на Groq + Обучение + Стелс):</b>\n"
        "• <code>автоответчик @юзер</code> (или <code>ао @юзер</code>) — вкл/выкл автоответчик для конкретного человека\n"
        "• <code>ао вкл</code> / <code>ао выкл</code> — (прямо в чате с человеком) скрытно вкл/выкл автоответчик для этого чата\n"
        "• <code>автоответчик вкл</code> / <code>выкл</code> — глобальный автоответчик для всех\n"
        "• <code>обучение</code> — диалоговое обучение ТОЛЬКО для автоответчика (выход: <code>стоп</code>)\n"
        "• <code>обучение сброс</code> — стереть выученные правила автоответчика\n"
        "• <code>стелс вкл</code> / <code>выкл</code> — скрывать model/context и часики в режиме автоответчика\n"
        "• <code>таймер 5</code> — через сколько минут молчания отвечает автоответчик\n"
        "• <code>шанс 75</code> — вероятность ответа автоответчика в % (от 10 до 100)\n\n"
        "🔹 <b>3. Голосовые и Кружки (рс + живой ответ):</b>\n"
        "• Ответь <code>рс</code> на ГС/кружок — чистая текстовая расшифровка без воды\n"
        "• Ответь <code>рс [задача]</code> — например: <code>рс перескажи кратко</code>\n"
        "• <code>рс @юзер</code> / <code>рс все вкл</code> — постоянная авто-расшифровка\n"
        "• <i>Если ответить на сообщение бота голосовым — он сам расшифрует его и ответит!</i>\n\n"
        "🔹 <b>4. Третий собеседник, Будильник и Шпион:</b>\n"
        "• <code>вставка 25</code> (или <code>вставка выкл</code>) — раз в N сообщений бот вставляет «5 копеек»\n"
        "• <code>рассуди</code> / <code>5 копеек</code> / <code>анализ</code> — разбор последних 50 сообщений чата\n"
        "• <code>будильник 30 [задача]</code> — через 30 мин бот сам напишет в чат\n"
        "• <code>будильник @юзер 60 [задача]</code> — будильник для юзера из админки\n"
        "• <code>шпион вкл</code> / <code>выкл</code> — перехват удаленных и измененных СМС\n"
        "• <code>запретить @юзер</code> / <code>разрешить @юзер</code> — ЧС для бота"
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

# --- РАЗДЕЛЬНЫЙ МАРШРУТИЗАТОР ИИ (ОБЫЧНЫЙ ГПТ vs АВТООТВЕТЧИК) ---
def ask_ai(chat_id, prompt, media_list=None, save_to_memory=True, is_autoreply=False, custom_sys_prompt=None):
    chat_id = int(chat_id)
    target_model = CONFIG.get('auto_reply_model', 'groq/openai/gpt-oss-120b') if is_autoreply else ACTIVE_MODEL
    sys_prompt = custom_sys_prompt or (build_autoreply_system_instruction() if is_autoreply else build_gpt_system_instruction())

    if target_model.startswith('groq/'):
        return ask_groq(
            chat_id,
            prompt,
            media_list,
            save_to_memory=save_to_memory,
            is_autoreply=is_autoreply,
            custom_sys_prompt=sys_prompt,
            chosen_groq_model=target_model
        )
    else:
        return ask_gemini(
            chat_id,
            prompt,
            media_list,
            save_to_memory=save_to_memory,
            custom_sys_prompt=sys_prompt,
            preferred_model=target_model
        )

# --- GEMINI API ---
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
        return MODEL_HIERARCHY + GROQ_AUTOREPLY_FALLBACKS
    return sorted(list(set(all_models)))

def ask_gemini(chat_id, prompt, media_list=None, save_to_memory=True, custom_sys_prompt=None, preferred_model=None):
    global CURRENT_KEY_INDEX
    chat_id = int(chat_id)

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
    sys_instruction = custom_sys_prompt or build_gpt_system_instruction()
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": sys_instruction}]}
    }

    base_m = preferred_model or ACTIVE_MODEL
    if base_m in MODEL_HIERARCHY:
        start_idx = MODEL_HIERARCHY.index(base_m)
        models_to_try = MODEL_HIERARCHY[start_idx:]
    else:
        models_to_try = [base_m] + MODEL_HIERARCHY

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

    return None, f"Серверы Google перегружены ({last_error}). Подожди 15 секунд и повтори.", base_m, 0

# --- GROQ API (ЧИСТЫЙ ПРОМПТ ДЛЯ ГПТ + ОБУЧЕНИЕ ДЛЯ АВТООТВЕТЧИКА) ---
def ask_groq(chat_id, prompt, media_list=None, save_to_memory=True, is_autoreply=False, custom_sys_prompt=None, chosen_groq_model=None):
    chat_id = int(chat_id)
    primary_model = chosen_groq_model or (CONFIG.get('auto_reply_model', 'groq/openai/gpt-oss-120b') if is_autoreply else ACTIVE_MODEL)

    if media_list:
        first_bytes, first_mime = media_list[0]
        if first_mime.startswith('audio/') or first_mime.startswith('video/'):
            transcribed = transcribe_with_groq_whisper(first_bytes, first_mime)
            if transcribed:
                prompt = f"[Расшифровка голосового сообщения: «{transcribed}»]\n\n{prompt}"
            else:
                return ask_gemini(chat_id, prompt, media_list, save_to_memory, custom_sys_prompt=custom_sys_prompt)
        else:
            if is_autoreply:
                return ask_gemini(chat_id, prompt, media_list, save_to_memory, custom_sys_prompt=custom_sys_prompt)
            return "Эта модель Groq не читает картинки/PDF — переключи модель ГПТ на Gemini.", None, primary_model, len(CHAT_MEMORY.get(chat_id, []))

    history = CHAT_MEMORY.get(chat_id, []).copy()

    # Никаких принудительных матов!
    # Для автоответчика — правила из Обучения, для обычного ГПТ — чистый системный промпт!
    if is_autoreply:
        system_prompt = custom_sys_prompt or build_autoreply_system_instruction()
    else:
        system_prompt = custom_sys_prompt or build_gpt_system_instruction()

    messages_payload = [{"role": "system", "content": system_prompt}]
    if save_to_memory:
        for msg in history:
            messages_payload.append({"role": msg["role"], "content": msg["content"]})
    messages_payload.append({"role": "user", "content": prompt or "Привет!"})

    groq_candidates = [primary_model]
    for fb in GROQ_AUTOREPLY_FALLBACKS:
        if fb not in groq_candidates:
            groq_candidates.append(fb)

    headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
    last_err = "Groq API Error"
    s = get_http_session()

    for g_model in groq_candidates:
        model_id = g_model.replace('groq/', '')
        payload = {'model': model_id, 'messages': messages_payload, 'temperature': 0.7}
        try:
            r = s.post(f'{GROQ_API_BASE}/chat/completions', headers=headers, json=payload, timeout=(4, 20))
            if r.status_code != 200:
                last_err = f"HTTP {r.status_code} ({g_model})"
                continue

            data = r.json()
            reply_text = data['choices'][0]['message']['content']
            reply_text = re.sub(r'<think>.*?</think>', '', reply_text, flags=re.DOTALL | re.IGNORECASE).strip()

            # Если цензор OpenAI (gpt-oss) вдруг отказал на слишком жесткую тему — автоматически пробуем Qwen 3.8 без цензуры!
            if "can't comply with that" in reply_text.lower() or "cannot comply with that" in reply_text.lower():
                if g_model != 'groq/qwen/qwen3.8-27b' and 'groq/qwen/qwen3.8-27b' in groq_candidates:
                    continue

            if save_to_memory:
                if not history or history[-1].get('content') != prompt:
                    history.append({"role": "user", "content": prompt})
                history.append({"role": "assistant", "content": reply_text})
                limit = CONFIG.get('context_limit', 100)
                if len(history) > limit:
                    history = history[-limit:]
                CHAT_MEMORY[chat_id] = history
                save_chat_memory_async(chat_id, history)

            return reply_text, None, g_model, len(history)
        except Exception as e:
            last_err = str(e)
            continue

    if is_autoreply and GEMINI_KEYS:
        return ask_gemini(chat_id, prompt, media_list, save_to_memory, custom_sys_prompt=system_prompt)

    return None, last_err, primary_model, 0

# --- ИНТЕРАКТИВНЫЙ РЕЖИМ ОБУЧЕНИЯ АВТООТВЕТЧИКА ---
def process_training_dialogue(chat_id, owner_message_text):
    current_rules = CONFIG.get('learned_persona') or "Пока нет специальных правил (стандартный режим)."
    trainer_sys_prompt = (
        "Ты — Интерактивный Тренер и Архитектор Личности АВТООТВЕТЧИКА Telegram для владельца @wrtnoq.\n"
        "Владелец сейчас общается с тобой в режиме обучения, чтобы настроить, как автоответчик должен общаться с людьми в личке (на обычный режим ГПТ это не влияет).\n"
        "Твоя задача:\n"
        "1. Внимательно понять новые пожелания владельца (стиль текста, длина, сленг, отношение к разным людям, легенда чем занят владелец, пунктуация и т.д.) и объединить их с текущими правилами.\n"
        "2. Обязательно выведи обновленный свод правил внутри тегов <rules>...</rules>.\n"
        "3. После тега <rules> коротко ответь владельцу и покажи 1 наглядный пример, как автоответчик теперь ответит собеседнику в этом стиле.\n\n"
        f"ТЕКУЩИЕ ПРАВИЛА АВТООТВЕТЧИКА:\n{current_rules}"
    )

    reply, err, _, _ = ask_gemini(
        chat_id,
        f"Сообщение владельца на тренировке автоответчика: {owner_message_text}",
        save_to_memory=False,
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
            f"✅ <b>Правила ТОЛЬКО для автоответчика обновлены!</b>\n"
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
def run_chat_analysis_action(chat_id, mode, business_id=None, trigger_msg_id=None, is_auto_5kop=False):
    chat_id = int(chat_id)
    recent_lines = CHAT_LOG.get(chat_id, [])[-50:]
    if not recent_lines:
        mem = CHAT_MEMORY.get(chat_id, [])[-30:]
        recent_lines = [m.get('content', '') for m in mem if m.get('content')]

    history_dump = "\n".join(recent_lines) if recent_lines else "История пока короткая."
    use_stealth = is_auto_5kop and CONFIG.get('stealth_mode', True)

    if mode == 'judge':
        task_prompt = (
            f"Вот последние сообщения переписки в Telegram между владельцем аккаунта (@{CREATOR_USERNAME}) и его собеседником:\n"
            f"---\n{history_dump}\n---\n\n"
            "ЗАДАНИЕ «СУДЬЯ СПОРА»:\n"
            "Внимательно проанализируй их диалог/спор. Вынеси четкий, аргументированный и остроумный вердикт:\n"
            "1. В чем суть спора или ситуации.\n"
            "2. Где прав владелец, а где прав собеседник (суди честно по фактам и логике!).\n"
            "3. Итоговый вердикт: кто в итоге прав и почему."
        )
        prefix_title = "⚖️ <b>Вердикт по переписке:</b>\n\n"
    elif mode == 'analyze':
        task_prompt = (
            f"Вот последние сообщения переписки в Telegram между владельцем (@{CREATOR_USERNAME}) и собеседником:\n"
            f"---\n{history_dump}\n---\n\n"
            "ЗАДАНИЕ «АНАЛИЗ ЧАТА»:\n"
            "Сделай краткий, меткий разбор последних 50 сообщений: о чем говорили, какой вайб/настроение у каждого участника, скрытые подтексты и главные выводы."
        )
        prefix_title = "📊 <b>Разбор последних сообщений:</b>\n\n"
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

    if use_stealth:
        send_typing_action(chat_id, business_id)
    else:
        loading_msg = bot.send_message(chat_id, "🕐 Читаю последние 50 сообщений...", business_connection_id=business_id)
        if business_id:
            BOT_GPT_MESSAGES.add(loading_msg.message_id)
        loading_msg_id = loading_msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, business_id, "Анализирую чат..."), daemon=True).start()

    reply, err, model_used, ctx_len = ask_ai(
        chat_id,
        task_prompt,
        save_to_memory=True,
        is_autoreply=is_auto_5kop
    )
    stop_event.set()

    if reply:
        footer = "" if use_stealth else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(
            prefix_title + reply,
            footer,
            chat_id,
            loading_msg_id,
            business_id,
            reply_to_msg_id=trigger_msg_id if use_stealth else None,
            is_autoreply=is_auto_5kop
        )
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, business_id)

# --- БУДИЛЬНИК И ТАЙМЕРЫ ---
def schedule_ai_alarm(target_chat_id, business_id, minutes, instruction, target_label="чат"):
    target_chat_id = int(target_chat_id)
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
        reply, err, _, _ = ask_ai(target_chat_id, prompt, save_to_memory=True, is_autoreply=True)
        if reply:
            send_long_response(reply, "", target_chat_id, loading_msg_id=None, business_id=business_id, is_autoreply=True)
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
    chat_id = int(chat_id)
    is_video_note = (target_msg.content_type == 'video_note')
    prefix_label = "Слушаю кружок..." if is_video_note else "Слушаю ГС..."

    loading_msg = bot.send_message(chat_id, f"🕐 {prefix_label}", business_connection_id=business_id)
    if business_id:
        BOT_GPT_MESSAGES.add(loading_msg.message_id)

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
        record_chat_log(target_msg, is_owner, custom_text=f"[Голосовое сообщение]: {reply.strip()}", add_to_memory=True)
        footer = f"model: {model_used}" if custom_instruction else ""
        send_long_response(clean_reply, footer, chat_id, loading_msg.message_id, business_id, is_autoreply=False)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id, business_id)

def handle_voice_conversational_reply(chat_id, voice_msg, is_owner, business_id=None, is_autoreply_context=False):
    chat_id = int(chat_id)
    use_stealth = is_autoreply_context and CONFIG.get('stealth_mode', True)
    loading_msg_id = None
    stop_event = threading.Event()

    if use_stealth:
        send_typing_action(chat_id, business_id)
    else:
        is_vn = (voice_msg.content_type == 'video_note')
        lbl = "Слушаю кружок и думаю..." if is_vn else "Слушаю ГС и думаю..."
        loading_msg = bot.send_message(chat_id, f"🕐 {lbl}", business_connection_id=business_id, reply_to_message_id=voice_msg.message_id)
        if business_id:
            BOT_GPT_MESSAGES.add(loading_msg.message_id)
        loading_msg_id = loading_msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, business_id, lbl), daemon=True).start()

    media_bytes, mime_type = get_voice_or_videonote_bytes(voice_msg)
    if not media_bytes:
        stop_event.set()
        if loading_msg_id:
            safe_edit_message("⚠️ Не удалось прослушать голосовое сообщение.", chat_id, loading_msg_id, business_id)
        return

    speaker = get_speaker_tag(voice_msg.from_user, is_owner)

    if is_autoreply_context and GROQ_API_KEY:
        transcript_text = transcribe_with_groq_whisper(media_bytes, mime_type)
        if transcript_text:
            record_chat_log(voice_msg, is_owner, custom_text=f"[Сказал(а) в ГС]: {transcript_text}", add_to_memory=True)
            reply, err, model_used, ctx_len = ask_ai(
                chat_id,
                f"{speaker} (голосовым сообщением): {transcript_text}",
                save_to_memory=True,
                is_autoreply=True
            )
            stop_event.set()
            if reply:
                footer = "" if use_stealth else f"🎙 <i>Услышано: «{html.escape(transcript_text[:180])}»</i>\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
                send_long_response(reply, footer, chat_id, loading_msg_id, business_id, reply_to_msg_id=voice_msg.message_id if use_stealth else None, is_autoreply=True)
                return

    prompt = (
        f"{speaker} ответил(а) тебе голосовым/видеосообщением в чате Telegram.\n"
        "1. Внутри тегов <transcript>...</transcript> напиши дословную расшифровку его речи.\n"
        "2. После закрывающего тега </transcript> напиши свой прямой ответ этому человеку по сути его сообщения!"
    )
    sys_p = build_autoreply_system_instruction() if is_autoreply_context else build_gpt_system_instruction()
    reply, err, model_used, _ = ask_gemini(
        chat_id,
        prompt,
        [(media_bytes, mime_type)],
        save_to_memory=False,
        custom_sys_prompt=sys_p
    )
    stop_event.set()

    if reply:
        tr_match = re.search(r'<transcript>(.*?)</transcript>', reply, flags=re.DOTALL | re.IGNORECASE)
        transcript_text = tr_match.group(1).strip() if tr_match else "[Голосовое сообщение]"
        answer_text = re.sub(r'<transcript>.*?</transcript>', '', reply, flags=re.DOTALL | re.IGNORECASE).strip()

        record_chat_log(voice_msg, is_owner, custom_text=f"[Сказал(а) в ГС]: {transcript_text}", add_to_memory=True)
        mem_list = CHAT_MEMORY.setdefault(chat_id, [])
        mem_list.append({"role": "assistant", "content": answer_text})
        save_chat_memory_async(chat_id, mem_list)

        footer = "" if use_stealth else f"🎙 <i>Услышано: «{html.escape(transcript_text[:180])}»</i>\n\ncontext: {len(mem_list)}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(answer_text, footer, chat_id, loading_msg_id, business_id, reply_to_msg_id=voice_msg.message_id if use_stealth else None, is_autoreply=is_autoreply_context)
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, business_id)

# --- АВТООТВЕТЧИК НА БАЗЕ GROQ С БУФЕРОМ СКЛЕЙКИ И СТЕЛСОМ ---
def trigger_buffered_auto_reply(chat_id, message):
    chat_id = int(chat_id)
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

        stealth = CONFIG.get('stealth_mode', True)
        business_id = message.business_connection_id
        speaker = get_speaker_tag(message.from_user, False)
        last_msg_text = message.text or message.caption or "Привет"

        prompt = f"{speaker}: {last_msg_text}"

        loading_msg_id = None
        stop_event = threading.Event()

        if stealth:
            send_typing_action(chat_id, business_id)
            time.sleep(random.uniform(1.2, 2.5))
        else:
            loading_msg = bot.send_message(chat_id, "🕐 Думаю...", business_connection_id=business_id)
            BOT_AUTOREPLY_MESSAGES.add(loading_msg.message_id)
            loading_msg_id = loading_msg.message_id
            threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, business_id), daemon=True).start()

        reply, err, model_used, ctx_len = ask_ai(chat_id, prompt, save_to_memory=True, is_autoreply=True)
        stop_event.set()

        if reply:
            footer = "" if stealth else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used} [auto]"
            send_long_response(
                reply,
                footer,
                chat_id,
                loading_msg_id,
                business_id,
                reply_to_msg_id=message.message_id if stealth else None,
                is_autoreply=True
            )
        elif loading_msg_id:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, business_id)

    timer = threading.Timer(3.0 if CONFIG.get('stealth_mode', True) else 1.0, _fire)
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
    auto_on = CONFIG.get('auto_reply_enabled', False)
    spy_on = CONFIG.get('spy_enabled', True)
    stealth_on = CONFIG.get('stealth_mode', True)
    voice_all_on = CONFIG.get('voice_auto_all', False)
    tw_interval = CONFIG.get('third_wheel_interval', 0)
    chance = CONFIG.get('human_reply_chance', 100)
    learned = (CONFIG.get('learned_persona') or '').strip()
    ar_model = CONFIG.get('auto_reply_model', 'groq/openai/gpt-oss-120b')

    ar_users = list(CONFIG.get('auto_reply_users', {}).keys())
    ar_chats = list(CONFIG.get('auto_reply_chats', {}).values())
    ar_targets = [f"@{u}" for u in ar_users] + [str(c) for c in ar_chats if str(c) not in ar_users]
    ar_users_str = ", ".join(ar_targets) if ar_targets else "нет"

    auto_status = f"🟢 ВКЛ для всех ({CONFIG.get('afk_minutes', 10)} мин, {chance}%)" if auto_on else f"⚪ Точечно ({CONFIG.get('afk_minutes', 10)} мин, {chance}%)"
    spy_status = "🟢 ВКЛ" if spy_on else "🔴 ВЫКЛ"
    stealth_status = "🥷 ВКЛ (только в автоответе)" if stealth_on else "⚪ ВЫКЛ (с подписью model)"
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
        f"🧠 <b>Модель ГПТ (ручная):</b> <code>{ACTIVE_MODEL}</code> (сейчас: <code>{real_now_model}</code>)\n"
        f"🤖 <b>Модель Автоответа:</b> <code>{ar_model}</code> ⚡\n"
        f"🎨 <b>Фото-модель:</b> <code>{CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)}</code>\n\n"
        f"🤖 <b>Автоответчик (общий):</b> {auto_status}\n"
        f"🎯 <b>Автоответчик (юзеры):</b> <code>{ar_users_str}</code>\n"
        f"🥷 <b>Стелс (для автоответа):</b> {stealth_status}\n"
        f"🎓 <b>Обучение автоответа:</b> {'✅ Настроено' if learned else '⚪ Стандарт'}\n"
        f"🍿 <b>Третий собеседник:</b> {tw_status}\n"
        f"🕵️‍♂️ <b>Режим Шпиона:</b> {spy_status}\n"
        f"🎙 <b>ГС и кружки:</b> {voice_all_status} (юзеры: <code>{voice_users_str}</code>)\n"
        f"🚫 <b>ЧС:</b> {bl_str} | ⏰ <b>Будильников:</b> {len(ACTIVE_ALARMS)}\n"
        f"🔑 <b>Ключей Gemini:</b> <b>{len(GEMINI_KEYS)} шт.</b> (в паузе: {active_cooldowns}) | <b>Память:</b> {CONFIG.get('context_limit', 100)}"
    )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"🤖 Автоответ (все): {'ВКЛ 🟢' if auto_on else 'ВЫКЛ ⚪'}", callback_data="panel_toggle_auto"),
        InlineKeyboardButton(f"🥷 Стелс авто: {'ВКЛ 🥷' if stealth_on else 'ВЫКЛ ⚪'}", callback_data="panel_toggle_stealth")
    )
    kb.add(
        InlineKeyboardButton("🧠 Модель ГПТ", callback_data="panel_models"),
        InlineKeyboardButton("⚡ Модель Автоответа", callback_data="panel_auto_models")
    )
    kb.add(
        InlineKeyboardButton(f"🎓 Обучение Авто {'(ВКЛ)' if TRAINING_MODE_ACTIVE else ''}", callback_data="panel_start_training"),
        InlineKeyboardButton(f"🍿 5 копеек: {f'{tw_interval} СМС' if tw_interval else 'ВЫКЛ'}", callback_data="panel_cycle_tw")
    )
    kb.add(
        InlineKeyboardButton(f"🕵️‍♂️ Шпион: {'ВКЛ 🟢' if spy_on else 'ВЫКЛ 🔴'}", callback_data="panel_toggle_spy"),
        InlineKeyboardButton(f"🎙 Авто-ГС: {'ВКЛ 🟢' if voice_all_on else 'ВЫКЛ ⚪'}", callback_data="panel_toggle_voice")
    )
    kb.add(
        InlineKeyboardButton("🔑 Статус ключей", callback_data="panel_keys_status"),
        InlineKeyboardButton("🎨 Фото-модели", callback_data="panel_img_models")
    )
    kb.add(
        InlineKeyboardButton("📋 Списки и Промпт", callback_data="panel_show_lists"),
        InlineKeyboardButton("📜 Все команды", callback_data="panel_show_help")
    )
    kb.add(
        InlineKeyboardButton("🧹 Очистить ВСЕ чаты", callback_data="panel_clear_all"),
        InlineKeyboardButton("🔄 Обновить панель", callback_data="panel_refresh")
    )
    return text, kb

def build_keys_status_view(ping_results=None):
    now = time.time()
    real_now_model = get_current_working_model()
    lines = [
        f"🔑 <b>Мониторинг пула ключей Gemini ({len(GEMINI_KEYS)} шт.)</b>",
        f"📍 <b>Модель ГПТ:</b> <code>{ACTIVE_MODEL}</code> ➡️ <b>Активна:</b> <code>{real_now_model}</code>",
        f"⚡ <b>Модель Автоответа:</b> <code>{CONFIG.get('auto_reply_model', 'groq/openai/gpt-oss-120b')}</code>\n"
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
    reply, err, model_used, ctx_len = ask_ai(message.chat.id, full_prompt, media_list, is_autoreply=False)
    stop_event.set()

    if reply:
        footer = f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(reply, footer, message.chat.id, loading_msg.message_id, is_autoreply=False)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text'])
def handle_admin_text(message):
    global TRAINING_MODE_ACTIVE
    register_creator_id(message.from_user)
    raw_text = message.text.strip()
    text = raw_text.lower()
    chat_id = int(message.chat.id)

    old_buttons = ['📊 статус', '⚙️ автоответчик', '🧠 модель', '📝 контекст', '🚫 черный список']
    if text in old_buttons or text in ['панель', 'пульт', 'кнопки']:
        TRAINING_MODE_ACTIVE = False
        remove_old_reply_keyboard(chat_id, force=True)
        p_text, p_kb = build_panel_view()
        bot.send_message(chat_id, p_text, reply_markup=p_kb)
        return

    remove_old_reply_keyboard(chat_id)

    if text in ['стоп', 'выйти', 'конец обучения', '/stop']:
        if TRAINING_MODE_ACTIVE:
            TRAINING_MODE_ACTIVE = False
            bot.reply_to(message, "🎓 <b>Режим обучения автоответчика завершен.</b> На обычный <code>гпт</code> эти правила не влияют!")
        else:
            bot.reply_to(message, "ℹ️ Режим обучения сейчас не был включен.")
        return

    if text in ['обучение сброс', 'сброс обучения']:
        CONFIG['learned_persona'] = ''
        save_config()
        bot.reply_to(message, "🧹 Выученные правила стиля автоответчика очищены.")
        return

    if text in ['обучение', 'тренировка', 'учить']:
        TRAINING_MODE_ACTIVE = True
        current_rules = CONFIG.get('learned_persona') or "Пока пусто (стандартный живой стиль)."
        bot.reply_to(
            message,
            "🎓 <b>Обучение АВТООТВЕТЧИКА включено!</b>\n"
            "<i>(Эти правила применяются ТОЛЬКО когда бот отвечает сам в режиме автоответчика. Обычный <code>гпт</code> остается чистым!)</i>\n\n"
            f"📋 <b>Текущие правила автоответчика:</b>\n<blockquote>{html.escape(current_rules)}</blockquote>\n\n"
            "Пиши обычными сообщениями, как автоответчик должен общаться с людьми.\n"
            "• Напиши <code>обучение сброс</code> — стереть правила.\n"
            "• Напиши <code>стоп</code> — выйти из обучения."
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
        ar_users = list(CONFIG.get('auto_reply_users', {}).keys())
        ar_chats = list(CONFIG.get('auto_reply_chats', {}).values())
        ar_list = [f"@{u}" for u in ar_users] + [str(c) for c in ar_chats if str(c) not in ar_users]
        ar_str = ", ".join(ar_list) if ar_list else "пусто"

        v_users = list(CONFIG.get('voice_auto_users', {}).keys())
        b_users = list(CONFIG.get('blacklist', {}).keys())
        v_str = ", ".join([f"@{u}" for u in v_users]) if v_users else "пусто"
        b_str = ", ".join([f"@{u}" for u in b_users]) if b_users else "пусто"
        sys_p = CONFIG.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
        learned_p = CONFIG.get('learned_persona') or "не задано"
        bot.reply_to(
            message,
            f"📋 <b>Текущие списки и модели:</b>\n\n"
            f"🧠 <b>Модель ГПТ:</b> <code>{ACTIVE_MODEL}</code>\n"
            f"⚡ <b>Модель Автоответа:</b> <code>{CONFIG.get('auto_reply_model')}</code>\n"
            f"🎯 <b>Автоответчик (юзеры):</b> {ar_str}\n"
            f"🎙 <b>Авто-ГС юзеры:</b> {v_str}\n"
            f"🚫 <b>Черный список:</b> {b_str}\n"
            f"⏰ <b>Активных будильников:</b> {len(ACTIVE_ALARMS)}\n\n"
            f"🎓 <b>Выученный стиль (ТОЛЬКО Автоответчик):</b>\n<blockquote>{html.escape(learned_p)}</blockquote>\n\n"
            f"🎭 <b>Базовый промпт (Обычный ГПТ):</b>\n<blockquote>{html.escape(sys_p)}</blockquote>"
        )
        return

    if text == 'модели':
        models = fetch_all_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == ACTIVE_MODEL else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        bot.reply_to(message, f"🧠 <b>Выбери модель для обычного ГПТ</b> (всего {len(models)}):", reply_markup=kb)
        return

    if text in ['авто-модели', 'авто модели', 'модели авто']:
        models = fetch_all_models()
        groq_first = sorted([m for m in models if m.startswith('groq/')]) + sorted([m for m in models if not m.startswith('groq/')])
        ar_cur = CONFIG.get('auto_reply_model', 'groq/openai/gpt-oss-120b')
        kb = InlineKeyboardMarkup(row_width=1)
        for m in groq_first:
            mark = "✅ " if m == ar_cur else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setautomodel_{m}"))
        bot.reply_to(
            message,
            "⚡ <b>Выбери модель отдельно для Автоответчика:</b>\n"
            "<i>Рекомендуется: <code>groq/openai/gpt-oss-120b</code> (самая умная) или <code>groq/qwen/qwen3.8-27b</code></i>",
            reply_markup=kb
        )
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
        bot.reply_to(
            message,
            f"🥷 Стелс-режим (ТОЛЬКО для автоответчика): <b>{'🟢 ВКЛ (без палева ИИ)' if CONFIG['stealth_mode'] else '🔴 ВЫКЛ'}</b>\n"
            f"<i>Обычная команда <code>гпт</code> всегда показывает context и model!</i>"
        )
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
            bot.reply_to(message, "✅ Базовый характер для обычного <code>гпт</code> сброшен на стандартный.")
        elif arg:
            CONFIG['system_prompt'] = arg
            save_config()
            bot.reply_to(message, f"🎭 Новый базовый промпт для <code>гпт</code> сохранен:\n<blockquote>{html.escape(arg)}</blockquote>")
        return

    if text.startswith('шпион'):
        CONFIG['spy_enabled'] = ('вкл' in text)
        save_config()
        bot.reply_to(message, f"🕵️‍♂️ Режим Шпиона: {'🟢 ВКЛ' if CONFIG['spy_enabled'] else '🔴 ВЫКЛ'}")
        return

    if text.startswith('автоответчик') or text.startswith('ао '):
        if '@' in text:
            uname = text.split('@')[-1].split()[0].strip().lower()
            if uname:
                ar_users = CONFIG.setdefault('auto_reply_users', {})
                if 'выкл' in text:
                    ar_users.pop(uname, None)
                    state_lbl = "🔴 ВЫКЛ"
                elif 'вкл' in text:
                    ar_users[uname] = True
                    state_lbl = "🟢 ВКЛ"
                else:
                    if uname in ar_users:
                        del ar_users[uname]
                        state_lbl = "🔴 ВЫКЛ"
                    else:
                        ar_users[uname] = True
                        state_lbl = "🟢 ВКЛ"
                save_config()
                bot.reply_to(message, f"🎯 Персональный автоответчик для <b>@{uname}</b>: <b>{state_lbl}</b>")
                return

        if text in ['автоответчик сброс', 'ао сброс', 'автоответчик очистить']:
            CONFIG['auto_reply_users'] = {}
            CONFIG['auto_reply_chats'] = {}
            save_config()
            bot.reply_to(message, "🧹 Список отдельных пользователей автоответчика очищен.")
            return

        if 'выкл' in text:
            CONFIG['auto_reply_enabled'] = False
            save_config()
            ar_cnt = len(CONFIG.get('auto_reply_users', {})) + len(CONFIG.get('auto_reply_chats', {}))
            extra_note = f" (но продолжает работать для {ar_cnt} выбранных чатов/юзеров)" if ar_cnt else ""
            bot.reply_to(message, f"✅ Общий автоответчик для всех: <b>🔴 ВЫКЛ</b>{extra_note}")
            return
        elif 'вкл' in text:
            CONFIG['auto_reply_enabled'] = True
            save_config()
            bot.reply_to(message, "✅ Общий автоответчик для всех чатов: <b>🟢 ВКЛ</b>")
            return

        parts = text.split()
        if len(parts) == 2 and parts[1] not in ['вкл', 'выкл', 'все', 'сброс']:
            uname = parts[1].replace('@', '').strip().lower()
            ar_users = CONFIG.setdefault('auto_reply_users', {})
            if uname in ar_users:
                del ar_users[uname]
                state_lbl = "🔴 ВЫКЛ"
            else:
                ar_users[uname] = True
                state_lbl = "🟢 ВКЛ"
            save_config()
            bot.reply_to(message, f"🎯 Персональный автоответчик для <b>@{uname}</b>: <b>{state_lbl}</b>")
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

    if text.startswith('сброс @'):
        uname = text.split('@')[-1].strip().lower()
        known = CONFIG.get('known_chats', {}).get(uname)
        if known and known.get('chat_id'):
            reset_single_chat_memory(known['chat_id'])
            bot.reply_to(message, f"🧹 Общая память диалога с <b>@{uname}</b> полностью сброшена (0/{CONFIG.get('context_limit', 100)})!")
        else:
            bot.reply_to(message, f"⚠️ Чат с @{uname} не найден. Используй <code>сброс все</code>.")
        return

    if text in ['сброс', 'гпт сброс', 'сброс все', 'очистить все']:
        clear_all_memory_firebase()
        bot.reply_to(message, f"🧹 Память во всех чатах (у тебя и у собеседников) полностью очищена!\ncontext: 0/{CONFIG.get('context_limit', 100)}")
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
        reply, err, model_used, ctx_len = ask_ai(
            chat_id,
            f"[Владелец аккаунта @{CREATOR_USERNAME}]: {prompt_text}",
            media_list,
            is_autoreply=False
        )
        stop_event.set()
        if reply:
            footer = f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
            send_long_response(reply, footer, message.chat.id, loading_msg.message_id, is_autoreply=False)
        else:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

    if TRAINING_MODE_ACTIVE:
        loading_msg = bot.reply_to(message, "🎓 Обновляю навыки автоответчика...")
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
        CONFIG['auto_reply_enabled'] = not CONFIG.get('auto_reply_enabled', False)
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Автоответчик (для всех): {'ВКЛ' if CONFIG['auto_reply_enabled'] else 'ВЫКЛ'}")

    elif data == 'panel_toggle_stealth':
        CONFIG['stealth_mode'] = not CONFIG.get('stealth_mode', True)
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Стелс (только для автоответа): {'ВКЛ' if CONFIG['stealth_mode'] else 'ВЫКЛ'}")

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
                "🎓 <b>Режим диалогового обучения АВТООТВЕТЧИКА ВКЛЮЧЕН!</b>\n"
                "<i>(Влияет только на автоответчик, обычный <code>гпт</code> не трогает)</i>\n\n"
                f"Текущие правила:\n<blockquote>{html.escape(current_rules)}</blockquote>\n\n"
                "Напиши мне, как автоответчик должен общаться с людьми. Для выхода напиши <code>стоп</code>."
            )
        bot.answer_callback_query(call.id, f"Обучение автоответа: {'ВКЛ' if TRAINING_MODE_ACTIVE else 'ВЫКЛ'}")

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
        safe_edit_message(f"🧠 <b>Выбери базовую модель для обычного ГПТ (всего {len(models)}):</b>", chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id)

    elif data == 'panel_auto_models':
        models = fetch_all_models()
        groq_first = sorted([m for m in models if m.startswith('groq/')]) + sorted([m for m in models if not m.startswith('groq/')])
        ar_cur = CONFIG.get('auto_reply_model', 'groq/openai/gpt-oss-120b')
        kb = InlineKeyboardMarkup(row_width=1)
        for m in groq_first:
            mark = "✅ " if m == ar_cur else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setautomodel_{m}"))
        kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
        safe_edit_message(
            "⚡ <b>Выбери модель отдельно для Автоответчика:</b>\n"
            "• <code>groq/openai/gpt-oss-120b</code> — самая умная и быстрая на Groq\n"
            "• <code>groq/qwen/qwen3.8-27b</code> — топ для русского сленга\n"
            "• <code>groq/openai/gpt-oss-20b</code> — ультра-быстрая",
            chat_id,
            msg_id,
            reply_markup=kb
        )
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
        ar_users = list(CONFIG.get('auto_reply_users', {}).keys())
        ar_chats = list(CONFIG.get('auto_reply_chats', {}).values())
        ar_list = [f"@{u}" for u in ar_users] + [str(c) for c in ar_chats if str(c) not in ar_users]
        ar_str = ", ".join(ar_list) if ar_list else "пусто"

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
            f"🧠 <b>Модель ГПТ:</b> <code>{ACTIVE_MODEL}</code>\n"
            f"⚡ <b>Модель Автоответа:</b> <code>{CONFIG.get('auto_reply_model')}</code>\n"
            f"🎯 <b>Автоответчик (юзеры):</b> {ar_str}\n"
            f"🎙 <b>Авто-ГС юзеры:</b> {v_str}\n"
            f"🚫 <b>Черный список:</b> {b_str}\n"
            f"⏰ <b>Активных будильников:</b> {len(ACTIVE_ALARMS)}\n\n"
            f"🎓 <b>Выученный стиль (ТОЛЬКО Автоответчик):</b>\n<blockquote>{html.escape(learned_p)}</blockquote>\n\n"
            f"🎭 <b>Базовый промпт (Обычный ГПТ):</b>\n<blockquote>{html.escape(sys_p)}</blockquote>",
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
        bot.answer_callback_query(call.id, f"Модель ГПТ: {ACTIVE_MODEL}")

    elif data.startswith('setautomodel_'):
        CONFIG['auto_reply_model'] = data.replace('setautomodel_', '')
        save_config()
        text, kb = build_panel_view()
        safe_edit_message(text, chat_id, msg_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"Модель Автоответа: {CONFIG['auto_reply_model']}")

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
    chat_id = int(base_msg.chat.id)
    sender_uname = (base_msg.from_user.username or '').lower()

    is_owner = (base_msg.from_user.id == CREATOR_ID) or (sender_uname == CREATOR_USERNAME)
    if not is_owner and sender_uname in CONFIG.get('blacklist', {}):
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
    rep_id = base_msg.reply_to_message.message_id if base_msg.reply_to_message else None
    is_reply_to_gpt = bool(rep_id and rep_id in BOT_GPT_MESSAGES)
    is_reply_to_auto = bool(rep_id and rep_id in BOT_AUTOREPLY_MESSAGES)
    afk_seconds = CONFIG.get('afk_minutes', 10) * 60

    trigger_mode = None
    if is_gpt or is_reply_to_gpt:
        trigger_mode = 'gpt'
    elif is_reply_to_auto:
        trigger_mode = 'auto'
    elif not is_owner and is_auto_reply_active_for(chat_id, sender_uname):
        if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= afk_seconds:
            trigger_mode = 'auto'

    if not trigger_mode:
        return

    if not check_cooldown(chat_id):
        return

    if is_owner:
        LAST_OWNER_ACTIVITY[chat_id] = time.time()
    if is_gpt:
        text = text[4:].strip()
    if not text:
        text = "Опиши подробно, что на этих фотографиях."

    is_auto = (trigger_mode == 'auto')
    use_stealth = is_auto and CONFIG.get('stealth_mode', True)
    speaker = get_speaker_tag(base_msg.from_user, is_owner)
    loading_msg_id = None
    stop_event = threading.Event()

    if use_stealth:
        send_typing_action(chat_id, base_msg.business_connection_id)
    else:
        msg = bot.send_message(chat_id, f"🕐 Смотрю {len(messages)} фото...", business_connection_id=base_msg.business_connection_id)
        (BOT_AUTOREPLY_MESSAGES if is_auto else BOT_GPT_MESSAGES).add(msg.message_id)
        loading_msg_id = msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, base_msg.business_connection_id, f"Смотрю {len(messages)} фото..."), daemon=True).start()

    images = []
    for m in messages:
        img_bytes, mime = get_photo_bytes(m)
        if img_bytes:
            images.append((img_bytes, mime))

    reply, err, model_used, ctx_len = ask_ai(chat_id, f"{speaker}: {text}", images, is_autoreply=is_auto)
    stop_event.set()

    if reply:
        footer = "" if use_stealth else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(reply, footer, chat_id, loading_msg_id, base_msg.business_connection_id, is_autoreply=is_auto)
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, base_msg.business_connection_id)

# --- БИЗНЕС-СООБЩЕНИЯ (В ЛИЧНЫХ ЧАТАХ АККАУНТА) ---
@bot.business_message_handler(content_types=['text', 'photo', 'voice', 'video_note', 'audio', 'document', 'video'])
def handle_business_message(message):
    if not message.from_user:
        return

    register_creator_id(message.from_user)
    chat_id = int(message.chat.id)
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

    rep_id = message.reply_to_message.message_id if message.reply_to_message else None
    is_reply_to_gpt = bool(rep_id and rep_id in BOT_GPT_MESSAGES)
    is_reply_to_auto = bool(rep_id and rep_id in BOT_AUTOREPLY_MESSAGES)
    is_reply_to_any_bot = is_reply_to_gpt or is_reply_to_auto

    if lower_text in ['сброс', 'гпт сброс', '/reset']:
        reset_single_chat_memory(chat_id)
        msg = bot.send_message(
            chat_id,
            f"🧹 Общая память этого чата очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}",
            business_connection_id=message.business_connection_id
        )
        BOT_GPT_MESSAGES.add(msg.message_id)
        return

    if is_owner and lower_text in ['ао вкл', 'ао выкл', 'автоответчик вкл', 'автоответчик выкл']:
        try:
            bot.delete_message(chat_id, message.message_id, business_connection_id=message.business_connection_id)
        except Exception:
            pass
        ar_chats = CONFIG.setdefault('auto_reply_chats', {})
        chat_label = f"@{message.chat.username}" if getattr(message.chat, 'username', None) else (getattr(message.chat, 'first_name', None) or f"id:{chat_id}")
        if 'вкл' in lower_text:
            ar_chats[str(chat_id)] = chat_label
            if getattr(message.chat, 'username', None):
                CONFIG.setdefault('auto_reply_users', {})[message.chat.username.lower()] = True
            state_lbl = "🟢 ВКЛ"
        else:
            ar_chats.pop(str(chat_id), None)
            if getattr(message.chat, 'username', None):
                CONFIG.get('auto_reply_users', {}).pop(message.chat.username.lower(), None)
            state_lbl = "🔴 ВЫКЛ"
        save_config()
        if CREATOR_ID:
            try:
                bot.send_message(CREATOR_ID, f"🎯 Автоответчик для чата <b>{html.escape(chat_label)}</b>: <b>{state_lbl}</b>")
            except Exception:
                pass
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
        if is_reply_to_any_bot:
            handle_voice_conversational_reply(
                chat_id,
                message,
                is_owner,
                business_id=message.business_connection_id,
                is_autoreply_context=is_reply_to_auto
            )
            return
        auto_all = CONFIG.get('voice_auto_all', False)
        auto_user = sender_uname in CONFIG.get('voice_auto_users', {})
        if not is_owner and (auto_all or auto_user):
            handle_voice_transcription(chat_id, message, business_id=message.business_connection_id)
        else:
            record_chat_log(message, is_owner, add_to_memory=True)
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
        run_chat_analysis_action(chat_id, 'judge', business_id=message.business_connection_id, trigger_msg_id=message.message_id, is_auto_5kop=False)
        return

    if lower_text in ['5 копеек', 'гпт 5 копеек', 'вставь 5 копеек']:
        run_chat_analysis_action(chat_id, '5kop', business_id=message.business_connection_id, trigger_msg_id=message.message_id, is_auto_5kop=False)
        return

    if lower_text in ['анализ', 'гпт анализ', 'анализ чата', 'че тут']:
        run_chat_analysis_action(chat_id, 'analyze', business_id=message.business_connection_id, trigger_msg_id=message.message_id, is_auto_5kop=False)
        return

    # 4. ЗАПИСЬ В ИСТОРИЮ ЧАТА С РОЛЬЮ АВТОРА
    is_gpt = lower_text.startswith('гпт ') or lower_text == 'гпт'
    is_draw = lower_text.startswith('нарисуй ')
    clean_for_log = text[4:].strip() if is_gpt else text
    record_chat_log(message, is_owner, custom_text=clean_for_log, add_to_memory=not (is_gpt or is_draw or is_reply_to_any_bot))

    tw_interval = CONFIG.get('third_wheel_interval', 0)
    if tw_interval > 0 and not is_gpt and not is_draw and not is_reply_to_any_bot:
        CHAT_MSG_COUNTER[chat_id] = CHAT_MSG_COUNTER.get(chat_id, 0) + 1
        if CHAT_MSG_COUNTER[chat_id] >= tw_interval:
            CHAT_MSG_COUNTER[chat_id] = 0
            threading.Thread(
                target=run_chat_analysis_action,
                args=(chat_id, '5kop', message.business_connection_id, message.message_id, True),
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

    trigger_mode = None
    if is_gpt or is_draw or is_reply_to_gpt:
        trigger_mode = 'gpt'
    elif is_reply_to_auto and not is_owner:
        trigger_mode = 'auto'
    elif is_reply_to_auto and is_owner:
        trigger_mode = 'gpt'
    elif not is_owner and message.content_type in ['text', 'document', 'photo'] and is_auto_reply_active_for(chat_id, sender_uname):
        if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= afk_seconds:
            if message.content_type == 'text':
                trigger_buffered_auto_reply(chat_id, message)
                return
            else:
                trigger_mode = 'auto'

    if not trigger_mode:
        return

    is_auto = (trigger_mode == 'auto')
    use_stealth = is_auto and CONFIG.get('stealth_mode', True)

    if not check_cooldown(chat_id):
        if not use_stealth:
            msg = bot.send_message(chat_id, "жди пока первый запрос загрузится нищук", business_connection_id=message.business_connection_id)
            BOT_GPT_MESSAGES.add(msg.message_id)
        return

    if is_gpt:
        text = text[4:].strip()

    if is_draw:
        msg = bot.send_message(chat_id, "🕐 Рисую...", business_connection_id=message.business_connection_id)
        BOT_GPT_MESSAGES.add(msg.message_id)
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, message.business_connection_id, "Рисую..."), daemon=True).start()
        img, err = generate_image(text[8:])
        stop_event.set()
        if img:
            try:
                bot.delete_message(chat_id, msg.message_id, business_connection_id=message.business_connection_id)
                BOT_GPT_MESSAGES.discard(msg.message_id)
                photo_msg = bot.send_photo(chat_id, img, business_connection_id=message.business_connection_id)
                BOT_GPT_MESSAGES.add(photo_msg.message_id)
            except Exception:
                pass
        else:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, message.business_connection_id)
        return

    if message.reply_to_message and message.reply_to_message.content_type in ['voice', 'video_note', 'audio']:
        handle_voice_transcription(chat_id, message.reply_to_message, text, business_id=message.business_connection_id)
        return

    # 6. ОБРАБОТКА ВЫЗОВА
    media_list = []
    loading_label = "Думаю..."

    if message.content_type == 'document':
        loading_label = "Читаю файл..."
        m_item, ext_text, doc_err = extract_document_content(message)
        if doc_err:
            msg = bot.send_message(chat_id, f"⚠️ {doc_err}", business_connection_id=message.business_connection_id)
            BOT_GPT_MESSAGES.add(msg.message_id)
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
            BOT_GPT_MESSAGES.add(msg.message_id)
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

    speaker = get_speaker_tag(message.from_user, is_owner)
    full_user_prompt = f"{speaker}: {text}"

    loading_msg_id = None
    stop_event = threading.Event()

    if use_stealth:
        send_typing_action(chat_id, message.business_connection_id)
    else:
        msg = bot.send_message(chat_id, f"🕐 {loading_label}", business_connection_id=message.business_connection_id)
        (BOT_AUTOREPLY_MESSAGES if is_auto else BOT_GPT_MESSAGES).add(msg.message_id)
        loading_msg_id = msg.message_id
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg_id, stop_event, message.business_connection_id, loading_label), daemon=True).start()

    reply, err, model_used, ctx_len = ask_ai(
        chat_id,
        full_user_prompt,
        media_list,
        is_autoreply=is_auto
    )
    stop_event.set()

    if reply:
        footer = "" if use_stealth else f"context: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}"
        send_long_response(
            reply,
            footer,
            chat_id,
            loading_msg_id,
            message.business_connection_id,
            reply_to_msg_id=message.message_id if use_stealth else None,
            is_autoreply=is_auto
        )
    elif loading_msg_id:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg_id, message.business_connection_id)
# =====================================================================
# 🃏 ИГРА «ПОДКИДНОЙ ДУРАК» НА КЛИКАБЕЛЬНЫХ СМАЙЛИКАХ (PvE + PvP)
# =====================================================================
DURAK_GAMES = {}
DURAK_SUITS = ['♠️', '♣️', '♥️', '♦️']
DURAK_RANKS = [
    ('6', 6), ('7', 7), ('8', 8), ('9', 9), ('10', 10),
    ('В', 11), ('Д', 12), ('К', 13), ('Т', 14)
]

def durak_card_label(card, trump_suit):
    r_name, _, suit = card
    if suit == trump_suit:
        return f"{r_name}{suit}🔥"
    return f"{r_name}{suit}"

def durak_can_beat(atk_card, def_card, trump_suit):
    _, a_val, a_suit = atk_card
    _, d_val, d_suit = def_card
    if a_suit == d_suit:
        return d_val > a_val
    if d_suit == trump_suit and a_suit != trump_suit:
        return True
    return False

def durak_sort_hand(hand, trump_suit):
    hand.sort(key=lambda c: (1 if c[2] == trump_suit else 0, c[1], c[2]))

def durak_table_ranks(table):
    ranks = set()
    for atk, dfn in table:
        ranks.add(atk[0])
        if dfn:
            ranks.add(dfn[0])
    return ranks

def durak_refill_hands(game):
    deck = game['deck']
    order = ['p1', 'p2'] if game['attacker'] == 'p1' else ['p2', 'p1']
    for who in order:
        hand_key = who + '_hand'
        hand = game[hand_key]
        while len(hand) < 6 and deck:
            hand.append(deck.pop(0))
        durak_sort_hand(hand, game['trump_suit'])

def durak_check_winner(game):
    if game['deck']:
        return None
    p1_empty = (len(game['p1_hand']) == 0)
    p2_empty = (len(game['p2_hand']) == 0)
    p1_name = html.escape(game['p1_name'])
    p2_name = html.escape(game['p2_name'])
    if p1_empty and p2_empty:
        return "🤝 <b>Ничья!</b> Оба игрока вышли одновременно!"
    if p1_empty:
        return f"🏆 <b>Победил {p1_name}!</b>\n🤡 {p2_name} остался в дураках!"
    if p2_empty:
        return f"🏆 <b>Победил {p2_name}!</b>\n🤡 {p1_name} остался в дураках!"
    return None

def durak_bot_ai_turn(game):
    trump = game['trump_suit']
    table = game['table']
    bot_hand = game['p2_hand']

    if game['attacker'] == 'p1' and table and table[-1][1] is None:
        atk_card = table[-1][0]
        valid = [idx for idx, c in enumerate(bot_hand) if durak_can_beat(atk_card, c, trump)]
        if valid:
            best_idx = valid[0]
            chosen = bot_hand.pop(best_idx)
            table[-1][1] = chosen
            card_str = durak_card_label(chosen, trump)
            game['status'] = f"🤖 Бот отбился картой <b>{card_str}</b>! Подкидывай или жми «✅ Бито»."
        else:
            for a_c, d_c in table:
                bot_hand.append(a_c)
                if d_c:
                    bot_hand.append(d_c)
            table.clear()
            durak_refill_hands(game)
            game['status'] = "📥 <b>Бот не смог отбиться и забрал карты!</b> Твой ход!"
        return

    if game['attacker'] == 'p2':
        if not table:
            if not bot_hand:
                return
            chosen = bot_hand.pop(0)
            table.append([chosen, None])
            card_str = durak_card_label(chosen, trump)
            game['status'] = f"⚔️ Бот походил: <b>{card_str}</b>. Чем будешь крыть?"
        else:
            t_ranks = durak_table_ranks(table)
            can_throw = [
                idx for idx, c in enumerate(bot_hand)
                if c[0] in t_ranks and (c[2] != trump or len(game['deck']) == 0)
            ]
            if can_throw and len(game['p1_hand']) > 0 and len(table) < 6:
                chosen = bot_hand.pop(can_throw[0])
                table.append([chosen, None])
                card_str = durak_card_label(chosen, trump)
                game['status'] = f"😈 Бот подкинул: <b>{card_str}</b>! Отбивайся!"
            else:
                table.clear()
                game['attacker'] = 'p1'
                durak_refill_hands(game)
                game['status'] = "✅ <b>Бот сказал «Бито»!</b> Теперь твоя очередь ходить!"

def durak_build_text(game, viewer_role):
    trump = game['trump_suit']
    trump_lbl = durak_card_label(game['trump_card'], trump)
    deck_cnt = len(game['deck'])

    opp_role = 'p2' if viewer_role == 'p1' else 'p1'
    opp_name = html.escape(game[opp_role + '_name'])
    opp_cnt = len(game[opp_role + '_hand'])
    opp_cards_visual = "🂠 " * min(opp_cnt, 10) if opp_cnt > 0 else "<i>(пусто)</i>"

    table_lines = []
    if not game['table']:
        table_lines.append("<i>🟢 Стол чист — ожидание хода...</i>")
    else:
        for idx, (atk, dfn) in enumerate(game['table'], 1):
            a_str = durak_card_label(atk, trump)
            d_str = durak_card_label(dfn, trump) if dfn else "⏳ <i>ждет защиты...</i>"
            table_lines.append(f"  {idx}. <b>{a_str}</b>  ➔  <b>{d_str}</b>")

    p1_turn_cond = (
        (game['attacker'] == 'p1' and (not game['table'] or game['table'][-1][1] is not None)) or
        (game['attacker'] == 'p2' and game['table'] and game['table'][-1][1] is None)
    )
    turn_who = html.escape(game['p1_name'] if p1_turn_cond else game['p2_name'])
    table_block = "\n".join(table_lines)

    return (
        f"🃏 <b>ПОДКИДНОЙ ДУРАК</b>\n"
        f"📦 Колода: <b>{deck_cnt}</b> шт. | Козырь: <b>{trump_lbl}</b>\n"
        f"👤 Соперник (<b>{opp_name}</b>): <b>{opp_cnt}</b> карт {opp_cards_visual}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🍽 <b>На столе:</b>\n{table_block}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💬 {game['status']}\n"
        f"👉 Сейчас очередь: <b>{turn_who}</b>"
    )

def durak_build_keyboard(game, screen_type):
    cid = game['chat_id']
    trump = game['trump_suit']
    kb = InlineKeyboardMarkup(row_width=4)

    if game.get('finished'):
        kb.add(InlineKeyboardButton("🔄 Сыграть еще раз", callback_data=f"durak_menu_{cid}"))
        return kb

    table = game['table']
    uncovered = (len(table) > 0 and table[-1][1] is None)

    action_btns = []
    if table and not uncovered:
        action_btns.append(InlineKeyboardButton("✅ Бито! (Отбой)", callback_data=f"durak_bito_{cid}"))
    if uncovered:
        action_btns.append(InlineKeyboardButton("📥 Беру! (Забрать стол)", callback_data=f"durak_take_{cid}"))
    if action_btns:
        kb.row(*action_btns)

    if screen_type in ('bot_game', 'pvp_dm'):
        hand = game['p1_hand']
        btns = [
            InlineKeyboardButton(durak_card_label(c, trump), callback_data=f"durak_play_{cid}_p1_{i}")
            for i, c in enumerate(hand)
        ]
        for i in range(0, len(btns), 4):
            kb.row(*btns[i:i + 4])
    elif screen_type == 'pvp_chat':
        p2_hand = game['p2_hand']
        p2_btns = [
            InlineKeyboardButton(durak_card_label(c, trump), callback_data=f"durak_play_{cid}_p2_{i}")
            for i, c in enumerate(p2_hand)
        ]
        for i in range(0, len(p2_btns), 4):
            kb.row(*p2_btns[i:i + 4])

        kb.row(InlineKeyboardButton("👁 Мои карты (Секретно без палева)", callback_data=f"durak_peek_{cid}"))
        p1_hand = game['p1_hand']
        if p1_hand:
            secret_btns = [
                InlineKeyboardButton(f"🎴#{i + 1}", callback_data=f"durak_play_{cid}_p1_{i}")
                for i in range(len(p1_hand))
            ]
            for i in range(0, len(secret_btns), 6):
                kb.row(*secret_btns[i:i + 6])

    kb.row(InlineKeyboardButton("🏳️ Завершить игру", callback_data=f"durak_stop_{cid}"))
    return kb

def durak_sync_screens(game):
    cid = game['chat_id']
    biz_id = game.get('biz_id')

    if game['mode'] == 'bot':
        text = durak_build_text(game, 'p1')
        kb = durak_build_keyboard(game, 'bot_game')
        if game.get('chat_msg_id'):
            safe_edit_message(text, cid, game['chat_msg_id'], business_id=biz_id, reply_markup=kb)
    else:
        chat_text = durak_build_text(game, 'p2')
        chat_kb = durak_build_keyboard(game, 'pvp_chat')
        if game.get('chat_msg_id'):
            safe_edit_message(chat_text, cid, game['chat_msg_id'], business_id=biz_id, reply_markup=chat_kb)

        if CREATOR_ID and game.get('dm_msg_id'):
            dm_text = durak_build_text(game, 'p1')
            dm_kb = durak_build_keyboard(game, 'pvp_dm')
            safe_edit_message(dm_text, CREATOR_ID, game['dm_msg_id'], business_id=None, reply_markup=dm_kb)

def start_new_durak_game(chat_id, biz_id, mode, starter_user, msg_id_to_edit=None):
    deck = [(r_name, r_val, s) for s in DURAK_SUITS for (r_name, r_val) in DURAK_RANKS]
    random.shuffle(deck)

    p1_hand = [deck.pop(0) for _ in range(6)]
    p2_hand = [deck.pop(0) for _ in range(6)]
    trump_card = deck[-1]
    trump_suit = trump_card[2]

    durak_sort_hand(p1_hand, trump_suit)
    durak_sort_hand(p2_hand, trump_suit)

    if mode == 'bot':
        p1_id = starter_user.id
        p1_name = starter_user.first_name or "Игрок"
        p2_id = 0
        p2_name = "🤖 Бот"
    else:
        p1_id = CREATOR_ID
        p1_name = f"@{CREATOR_USERNAME}"
        p2_id = None if starter_user.id == CREATOR_ID else starter_user.id
        p2_name = "Собеседник" if starter_user.id == CREATOR_ID else (starter_user.first_name or "Собеседник")

    safe_p1_name = html.escape(p1_name)
    game = {
        'chat_id': int(chat_id),
        'biz_id': biz_id,
        'mode': mode,
        'deck': deck,
        'trump_card': trump_card,
        'trump_suit': trump_suit,
        'p1_id': p1_id,
        'p1_name': p1_name,
        'p1_hand': p1_hand,
        'p2_id': p2_id,
        'p2_name': p2_name,
        'p2_hand': p2_hand,
        'attacker': 'p1',
        'table': [],
        'status': f"🔥 Игра началась! Первым ходит <b>{safe_p1_name}</b>.",
        'chat_msg_id': msg_id_to_edit,
        'dm_msg_id': None,
        'finished': False
    }
    DURAK_GAMES[int(chat_id)] = game

    if mode == 'bot':
        text = durak_build_text(game, 'p1')
        kb = durak_build_keyboard(game, 'bot_game')
        if msg_id_to_edit:
            safe_edit_message(text, chat_id, msg_id_to_edit, business_id=biz_id, reply_markup=kb)
        else:
            sent = bot.send_message(chat_id, text, business_connection_id=biz_id, reply_markup=kb)
            game['chat_msg_id'] = sent.message_id
    else:
        if not msg_id_to_edit:
            chat_text = durak_build_text(game, 'p2')
            chat_kb = durak_build_keyboard(game, 'pvp_chat')
            sent = bot.send_message(chat_id, chat_text, business_connection_id=biz_id, reply_markup=chat_kb)
            game['chat_msg_id'] = sent.message_id
        if CREATOR_ID:
            try:
                dm_text = durak_build_text(game, 'p1')
                dm_kb = durak_build_keyboard(game, 'pvp_dm')
                dm_sent = bot.send_message(
                    CREATOR_ID,
                    f"🕵️‍♂️ <b>Твой секретный пульт для игры 1 на 1:</b>\n\n{dm_text}",
                    reply_markup=dm_kb
                )
                game['dm_msg_id'] = dm_sent.message_id
            except Exception:
                pass
        durak_sync_screens(game)

def send_durak_mode_menu(chat_id, biz_id=None):
    kb = InlineKeyboardMarkup(row_width=1)
    kb.add(
        InlineKeyboardButton("🤖 Играть против Бота (Смайлики)", callback_data=f"durak_start_{chat_id}_bot"),
        InlineKeyboardButton("⚔️ Играть 1 на 1 (Ты vs Собеседник)", callback_data=f"durak_start_{chat_id}_pvp")
    )
    text = (
        "🃏 <b>Подкидной Дурак на смайликах!</b>\n\n"
        "• Все карты — кликабельные кнопки со смайликами (козыри помечены 🔥).\n"
        "• В режиме <b>1 на 1</b> твои карты скрыты от соперника (приходят тебе в ЛС бота + доступны по секретной кнопке 👁 прямо в чате).\n\n"
        "👇 <b>Выбери режим игры:</b>"
    )
    sent = bot.send_message(chat_id, text, business_connection_id=biz_id, reply_markup=kb)
    if biz_id and sent:
        BOT_GPT_MESSAGES.add(sent.message_id)

@bot.callback_query_handler(func=lambda call: call.data and call.data.startswith('durak_'))
def handle_durak_callbacks(call):
    register_creator_id(call.from_user)
    data = call.data
    user = call.from_user
    uid = user.id
    is_owner = (uid == CREATOR_ID) or ((user.username or '').lower() == CREATOR_USERNAME)

    if data.startswith('durak_menu_'):
        cid = int(data.replace('durak_menu_', ''))
        old_g = DURAK_GAMES.get(cid)
        biz_id = old_g.get('biz_id') if old_g else getattr(call.message, 'business_connection_id', None)
        kb = InlineKeyboardMarkup(row_width=1)
        kb.add(
            InlineKeyboardButton("🤖 Играть против Бота (Смайлики)", callback_data=f"durak_start_{cid}_bot"),
            InlineKeyboardButton("⚔️ Играть 1 на 1 (Ты vs Собеседник)", callback_data=f"durak_start_{cid}_pvp")
        )
        safe_edit_message("🃏 <b>Выбери режим игры в Дурака:</b>", call.message.chat.id, call.message.message_id, business_id=biz_id, reply_markup=kb)
        bot.answer_callback_query(call.id)
        return

    if data.startswith('durak_start_'):
        parts = data.split('_')
        cid = int(parts[2])
        mode = parts[3]
        biz_id = getattr(call.message, 'business_connection_id', None)
        start_new_durak_game(cid, biz_id, mode, user, msg_id_to_edit=call.message.message_id)
        bot.answer_callback_query(call.id, "🃏 Карты розданы!")
        return

    parts = data.split('_')
    action = parts[1]
    cid = int(parts[2])
    game = DURAK_GAMES.get(cid)

    if not game:
        bot.answer_callback_query(call.id, "⚠️ Партия уже завершена. Напиши «дурак» для новой игры!", show_alert=True)
        return

    if game['mode'] == 'pvp' and not is_owner and game['p2_id'] is None:
        game['p2_id'] = uid
        game['p2_name'] = user.first_name or "Собеседник"

    if action == 'peek':
        role = 'p1' if is_owner else 'p2'
        hand = game[role + '_hand']
        trump = game['trump_suit']
        if not hand:
            bot.answer_callback_query(call.id, "У тебя пусто в руке!", show_alert=True)
            return
        lines = [f"🎴#{i + 1} ➔ {durak_card_label(c, trump)}" for i, c in enumerate(hand)]
        trump_str = durak_card_label(game['trump_card'], trump)
        joined_cards = "  |  ".join(lines)
        secret_text = (
            f"🃏 ТВОИ КАРТЫ (видишь только ты):\n"
            f"Козырь стола: {trump_str}\n\n"
            f"{joined_cards}\n\n"
            f"💡 Чтобы сходить прямо тут, нажми кнопку с нужным номером 🎴#1, 🎴#2..."
        )
        bot.answer_callback_query(call.id, secret_text, show_alert=True)
        return

    if action == 'stop':
        DURAK_GAMES.pop(cid, None)
        stopper_name = html.escape(user.first_name or "Игрок")
        safe_edit_message(f"🏳️ Игра в Дурака остановлена игроком <b>{stopper_name}</b>.", cid, game['chat_msg_id'], business_id=game['biz_id'])
        if CREATOR_ID and game.get('dm_msg_id'):
            safe_edit_message("🏳️ Игра в Дурака завершена.", CREATOR_ID, game['dm_msg_id'])
        bot.answer_callback_query(call.id, "Игра завершена")
        return

    if game['mode'] == 'bot':
        if uid != game['p1_id'] and not is_owner:
            bot.answer_callback_query(call.id, "⛔ Сейчас играет другой человек!", show_alert=True)
            return
        caller_role = 'p1'
    else:
        caller_role = 'p1' if is_owner else 'p2'

    trump = game['trump_suit']
    table = game['table']
    uncovered = (len(table) > 0 and table[-1][1] is None)

    if action == 'bito':
        if caller_role != game['attacker']:
            bot.answer_callback_query(call.id, "⛔ Только атакующий может сказать «Бито!»", show_alert=True)
            return
        if not table or uncovered:
            bot.answer_callback_query(call.id, "⛔ На столе еще есть неотбитая карта!", show_alert=True)
            return

        table.clear()
        game['attacker'] = 'p2' if game['attacker'] == 'p1' else 'p1'
        durak_refill_hands(game)
        new_atk_name = html.escape(game[game['attacker'] + '_name'])
        game['status'] = f"✅ <b>Бито!</b> Карты ушли в отбой. Теперь ходит <b>{new_atk_name}</b>!"

        if game['mode'] == 'bot' and game['attacker'] == 'p2':
            durak_bot_ai_turn(game)

        win_text = durak_check_winner(game)
        if win_text:
            game['finished'] = True
            game['status'] = win_text

        durak_sync_screens(game)
        bot.answer_callback_query(call.id, "✅ Бито!")
        return

    if action == 'take':
        def_role = 'p2' if game['attacker'] == 'p1' else 'p1'
        if caller_role != def_role or not uncovered:
            bot.answer_callback_query(call.id, "⛔ Сейчас не твоя очередь забирать карты!", show_alert=True)
            return

        def_hand = game[def_role + '_hand']
        for a_c, d_c in table:
            def_hand.append(a_c)
            if d_c:
                def_hand.append(d_c)
        table.clear()
        durak_refill_hands(game)
        def_name = html.escape(game[def_role + '_name'])
        atk_name = html.escape(game[game['attacker'] + '_name'])
        game['status'] = f"📥 <b>{def_name} забрал(а) все карты со стола!</b> Снова ходит {atk_name}."

        if game['mode'] == 'bot' and game['attacker'] == 'p2':
            durak_bot_ai_turn(game)

        win_text = durak_check_winner(game)
        if win_text:
            game['finished'] = True
            game['status'] = win_text

        durak_sync_screens(game)
        bot.answer_callback_query(call.id, "📥 Ты забрал(а) карты со стола")
        return

    if action == 'play':
        btn_owner_role = parts[3]
        card_idx = int(parts[4])

        if caller_role != btn_owner_role:
            bot.answer_callback_query(
                call.id,
                "⛔ Это карты соперника! Нажми «👁 Мои карты» ниже или ходи со своего пульта в ЛС бота.",
                show_alert=True
            )
            return

        hand = game[caller_role + '_hand']
        if card_idx < 0 or card_idx >= len(hand):
            bot.answer_callback_query(call.id, "⚠️ Карта уже сыграна")
            return

        chosen_card = hand[card_idx]
        def_role = 'p2' if game['attacker'] == 'p1' else 'p1'
        caller_name = html.escape(game[caller_role + '_name'])
        chosen_lbl = durak_card_label(chosen_card, trump)

        if caller_role == game['attacker']:
            if uncovered:
                bot.answer_callback_query(call.id, "⏳ Подожди, пока соперник отобьет карту или возьмет!", show_alert=True)
                return
            if len(table) >= 6 or len(game[def_role + '_hand']) == 0:
                bot.answer_callback_query(call.id, "⛔ Больше подкидывать нельзя! Жми «✅ Бито!»", show_alert=True)
                return
            if table:
                t_ranks = durak_table_ranks(table)
                if chosen_card[0] not in t_ranks:
                    allowed_str = ", ".join(t_ranks)
                    bot.answer_callback_query(
                        call.id,
                        f"⛔ Подкидывать можно только карты достоинством: {allowed_str}",
                        show_alert=True
                    )
                    return

            hand.pop(card_idx)
            table.append([chosen_card, None])
            game['status'] = f"⚔️ <b>{caller_name}</b> ходит: <b>{chosen_lbl}</b>"

            if game['mode'] == 'bot':
                durak_bot_ai_turn(game)

        elif caller_role == def_role:
            if not uncovered:
                bot.answer_callback_query(call.id, "⏳ Сейчас ход атакующего!", show_alert=True)
                return

            atk_card = table[-1][0]
            atk_lbl = durak_card_label(atk_card, trump)
            if not durak_can_beat(atk_card, chosen_card, trump):
                bot.answer_callback_query(
                    call.id,
                    f"⛔ Карта {chosen_lbl} не бьет {atk_lbl}!",
                    show_alert=True
                )
                return

            hand.pop(card_idx)
            table[-1][1] = chosen_card
            game['status'] = f"🛡 <b>{caller_name}</b> отбил(а) картой <b>{chosen_lbl}</b>!"

            if game['mode'] == 'bot' and game['attacker'] == 'p2':
                durak_bot_ai_turn(game)

        win_text = durak_check_winner(game)
        if win_text:
            game['finished'] = True
            game['status'] = win_text

        durak_sync_screens(game)
        bot.answer_callback_query(call.id, f"Сыграно: {chosen_lbl}")

@bot.business_message_handler(func=lambda m: (m.text or '').lower().strip() in ['дурак', 'игра дурак', 'дурак бот', 'дурак пвп', 'дурак стоп', '/durak'])
def handle_business_durak_command(message):
    register_creator_id(message.from_user)
    cid = int(message.chat.id)
    biz_id = message.business_connection_id
    cmd = (message.text or '').lower().strip()

    if cmd == 'дурак стоп':
        DURAK_GAMES.pop(cid, None)
        bot.send_message(cid, "🏳️ Игра в Дурака остановлена.", business_connection_id=biz_id)
        return

    if cmd == 'дурак бот':
        start_new_durak_game(cid, biz_id, 'bot', message.from_user)
    elif cmd == 'дурак пвп':
        start_new_durak_game(cid, biz_id, 'pvp', message.from_user)
    else:
        send_durak_mode_menu(cid, biz_id=biz_id)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.text or '').lower().strip() in ['дурак', 'игра дурак', 'дурак бот', 'дурак стоп', '/durak'])
def handle_private_durak_command(message):
    register_creator_id(message.from_user)
    cid = int(message.chat.id)
    cmd = (message.text or '').lower().strip()
    if cmd == 'дурак стоп':
        DURAK_GAMES.pop(cid, None)
        bot.send_message(cid, "🏳️ Игра в Дурака остановлена.")
        return
    start_new_durak_game(cid, None, 'bot', message.from_user)

bot.callback_query_handlers.insert(0, bot.callback_query_handlers.pop())
bot.business_message_handlers.insert(0, bot.business_message_handlers.pop())
bot.message_handlers.insert(0, bot.message_handlers.pop())
# =====================================================================

# --- ЗАПУСК ---
try:
    bot.delete_webhook(drop_pending_updates=True)
except Exception:
    pass

print(f'⚡ БОТ ЗАПУЩЕН! ГПТ: {ACTIVE_MODEL} | Автоответчик: {CONFIG.get("auto_reply_model")} (Groq без матерного блока).')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
