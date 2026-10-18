import html
import io
import os
import re
import base64
import time
import zipfile
import xml.etree.ElementTree as ET
import requests
import threading

try:
    import telebot
except ImportError:
    os.system('pip install -U pyTelegramBotAPI requests')
    import telebot

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- НАСТРОЙКИ И ЗАГРУЗКА ПУЛА КЛЮЧЕЙ (ДО 10 ШТ.) ---
TOKEN = os.environ.get('TG_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')  # Ключ Groq
FIREBASE_URL = os.environ.get('FIREBASE_URL')

def load_gemini_keys():
    """Собирает все ключи Gemini из переменных окружения."""
    keys = []
    env_names = [
        'GROQ_KEY',       # Ключ №1 (старое название)
        'GEMINI_KEYS',    # Запись через запятую
        'GEMINI_KEY',
        'GEMINI_KEY_1',
        'GEMINI_KEY_2',   # Ключ №2
        'GEMINI_KEY_3',   # Ключ №3
        'GEMINI_KEY_4',   # Ключ №4
        'GEMINI_KEY_5',   # Ключ №5
        'GEMINI_KEY_6',
        'GEMINI_KEY_7',
        'GEMINI_KEY_8',
        'GEMINI_KEY_9',
        'GEMINI_KEY_10'
    ]
    for name in env_names:
        val = os.environ.get(name, '')
        if val:
            for part in re.split(r'[,\s]+', val.strip()):
                if part and part not in keys:
                    keys.append(part)
    return keys

GEMINI_KEYS = load_gemini_keys()
CURRENT_KEY_INDEX = 0
EXHAUSTED_QUOTAS = {}  # {(model, key_idx): timestamp_until_free}
KEY_STATS = {i: {'ok': 0, '429': 0, 'err': 0} for i in range(len(GEMINI_KEYS))}

CREATOR_USERNAME = 'wrtnoq'
CREATOR_ID = None

MODEL_HIERARCHY = [
    'gemini-3.8-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-3-flash'
]
DEFAULT_IMAGE_MODEL = 'imagen-3.0-generate-001'
DEFAULT_CONTEXT_LIMIT = 100
DEFAULT_SYSTEM_PROMPT = "Ты — умный и полезный ИИ-ассистент. Отвечай четко, по делу и на русском языке."

GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GROQ_API_BASE = 'https://api.groq.com/openai/v1'

ACTIVE_MODEL = MODEL_HIERARCHY[0]

CONFIG = {
    'auto_reply_enabled': True,
    'spy_enabled': True,
    'voice_auto_all': False,
    'voice_auto_users': {},
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
BOT_BUSINESS_MESSAGES = set()
MEDIA_GROUP_BUFFER = {}

MESSAGE_CACHE = {}
MAX_CACHE_SIZE = 500

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)

retry_strategy = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504], allowed_methods=["POST"])
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- БАЗА ДАННЫХ (FIREBASE) ---
def init_firebase():
    global CREATOR_ID
    if not FIREBASE_URL:
        return
    try:
        r = session.get(f'{FIREBASE_URL}/config.json', timeout=10)
        if r.status_code == 200 and isinstance(r.json(), dict):
            CONFIG.update(r.json())
            if CONFIG.get('creator_id'):
                CREATOR_ID = int(CONFIG['creator_id'])
            if not isinstance(CONFIG.get('voice_auto_users'), dict):
                CONFIG['voice_auto_users'] = {}
            if not isinstance(CONFIG.get('blacklist'), dict):
                CONFIG['blacklist'] = {}
            print("🔥 Настройки загружены из Firebase.")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки настроек Firebase: {e}")

    try:
        r_mem = session.get(f'{FIREBASE_URL}/chat_memory.json', timeout=10)
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
            session.patch(f'{FIREBASE_URL}/config.json', json=CONFIG, timeout=5)
        except Exception as e:
            print(f"⚠️ Ошибка сохранения в Firebase: {e}")
    threading.Thread(target=_worker, daemon=True).start()

def save_chat_memory_async(chat_id, history):
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            session.put(f'{FIREBASE_URL}/chat_memory/{chat_id}.json', json=history, timeout=5)
        except Exception as e:
            print(f"⚠️ Ошибка сохранения памяти чата {chat_id}: {e}")
    threading.Thread(target=_worker, daemon=True).start()

def clear_all_memory_firebase():
    CHAT_MEMORY.clear()
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            session.delete(f'{FIREBASE_URL}/chat_memory.json', timeout=5)
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

init_firebase()

def check_cooldown(chat_id):
    cd = CONFIG.get('cooldown_seconds', 5)
    if cd <= 0:
        return True
    now = time.time()
    if now - LAST_REQUEST_TIME.get(chat_id, 0) < cd:
        return False
    LAST_REQUEST_TIME[chat_id] = now
    return True

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

def mask_key(key_str):
    if len(key_str) <= 10:
        return "***"
    return f"{key_str[:6]}...{key_str[-4:]}"

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
        r = session.post(f'{GROQ_API_BASE}/audio/transcriptions', headers=headers, files=files, data=data, timeout=45)
        if r.status_code == 200:
            return r.json().get('text', '').strip()
    except Exception:
        pass
    return None

# --- МАРШРУТИЗАТОР ИИ ---
def ask_ai(chat_id, prompt, media_list=None, save_to_memory=True):
    if ACTIVE_MODEL.startswith('groq/'):
        return ask_groq(chat_id, prompt, media_list, save_to_memory)
    else:
        return ask_gemini(chat_id, prompt, media_list, save_to_memory)

# --- GEMINI API С РОТАЦИЕЙ И СТАТИСТИКОЙ КЛЮЧЕЙ ---
def get_gemini_headers(api_key=None):
    key_to_use = api_key or (GEMINI_KEYS[0] if GEMINI_KEYS else '')
    return {'Content-Type': 'application/json', 'x-goog-api-key': key_to_use}

def fetch_all_models():
    all_models = []
    if GEMINI_KEYS:
        url = f"{GEMINI_API_BASE}/models"
        try:
            r = session.get(url, headers=get_gemini_headers(GEMINI_KEYS[0]), timeout=10)
            if r.status_code == 200:
                for m in r.json().get('models', []):
                    if 'generateContent' in m.get('supportedGenerationMethods', []):
                        all_models.append(m['name'].replace('models/', ''))
        except Exception:
            pass

    if GROQ_API_KEY:
        url = f"{GROQ_API_BASE}/models"
        try:
            r = session.get(url, headers={'Authorization': f'Bearer {GROQ_API_KEY}'}, timeout=10)
            if r.status_code == 200:
                for m in r.json().get('data', []):
                    if m.get('active') is not False:
                        all_models.append(f"groq/{m['id']}")
        except Exception:
            pass

    if not all_models:
        return MODEL_HIERARCHY
    return sorted(list(set(all_models)))

def ask_gemini(chat_id, prompt, media_list=None, save_to_memory=True):
    global CURRENT_KEY_INDEX

    if not GEMINI_KEYS:
        return None, "Не найдено ни одного ключа Gemini! Проверь Environment в Render.", ACTIVE_MODEL, 0

    history = CHAT_MEMORY.get(chat_id, []).copy()
    contents = []
    if save_to_memory:
        for msg in history:
            role = 'user' if msg['role'] == 'user' else 'model'
            contents.append({"role": role, "parts": [{"text": msg['content']}]})

    parts = []
    if prompt:
        parts.append({"text": prompt})
    if media_list:
        for media_bytes, mime_type in media_list:
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(media_bytes).decode('utf-8')
                }
            })

    contents.append({"role": "user", "parts": parts})
    sys_instruction = CONFIG.get('system_prompt') or DEFAULT_SYSTEM_PROMPT
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": sys_instruction}]}
    }

    if ACTIVE_MODEL in MODEL_HIERARCHY:
        start_idx = MODEL_HIERARCHY.index(ACTIVE_MODEL)
        models_to_try = MODEL_HIERARCHY[start_idx:]
    else:
        models_to_try = [ACTIVE_MODEL] + MODEL_HIERARCHY

    last_error = "Все доступные модели и ключи исчерпали лимиты."
    now = time.time()

    for model in models_to_try:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"

        for k_offset in range(len(GEMINI_KEYS)):
            key_idx = (CURRENT_KEY_INDEX + k_offset) % len(GEMINI_KEYS)
            api_key = GEMINI_KEYS[key_idx]
            stats = KEY_STATS.setdefault(key_idx, {'ok': 0, '429': 0, 'err': 0})

            if EXHAUSTED_QUOTAS.get((model, key_idx), 0) > now:
                continue

            try:
                r = session.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=75)

                if r.status_code == 429:
                    stats['429'] += 1
                    EXHAUSTED_QUOTAS[(model, key_idx)] = time.time() + 900
                    last_error = f"Лимит (429) на {model}"
                    continue

                if r.status_code in [400, 404]:
                    last_error = f"HTTP {r.status_code} ({model})"
                    break

                if r.status_code != 200:
                    stats['err'] += 1
                    last_error = f"HTTP {r.status_code}: Gemini API Error"
                    continue

                data = r.json()
                if 'candidates' not in data or not data['candidates']:
                    return None, "Модель заблокировала ответ (Safety Filter).", f"{model} [key #{key_idx + 1}]", 0

                reply_text = data['candidates'][0]['content']['parts'][0]['text']
                stats['ok'] += 1
                CURRENT_KEY_INDEX = (key_idx + 1) % len(GEMINI_KEYS)

                if save_to_memory:
                    history.append({"role": "user", "content": prompt or "[Медиафайл]"})
                    history.append({"role": "assistant", "content": reply_text})
                    limit = CONFIG.get('context_limit', 100)
                    if len(history) > limit:
                        history = history[-limit:]
                    CHAT_MEMORY[chat_id] = history
                    save_chat_memory_async(chat_id, history)

                model_label = f"{model} [key #{key_idx + 1}]"
                return reply_text, None, model_label, len(history)

            except Exception as e:
                stats['err'] += 1
                last_error = f"Сетевая ошибка: {str(e)}"
                continue

    EXHAUSTED_QUOTAS.clear()
    return None, f"Все ключи ({len(GEMINI_KEYS)} шт.) и модели исчерпали лимит ({last_error}).", ACTIVE_MODEL, 0

# --- GROQ API ---
def ask_groq(chat_id, prompt, media_list=None, save_to_memory=True):
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
    system_prompt = (
        "ты грубый невоспитанный ии. ты пишешь всё с маленькой буквы без запятых и точек "
        "ты постоянно материшься и хамишь. ты пишешь очень много объемно и живо как реальный человек. "
        "никогда не извиняйся и не будь вежливым. отвечай на русском."
    )

    messages_payload = [{"role": "system", "content": system_prompt}]
    if save_to_memory:
        for msg in history:
            messages_payload.append({"role": msg["role"], "content": msg["content"]})
    messages_payload.append({"role": "user", "content": prompt})

    model_id = ACTIVE_MODEL.replace('groq/', '')
    headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
    payload = {'model': model_id, 'messages': messages_payload, 'temperature': 0.9}

    try:
        r = session.post(f'{GROQ_API_BASE}/chat/completions', headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: Groq API Error", ACTIVE_MODEL, 0

        data = r.json()
        reply_text = data['choices'][0]['message']['content']

        if save_to_memory:
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

# --- ЧИСТАЯ РАСШИФРОВКА ГОЛОСОВЫХ И КРУЖОЧКОВ ---
def handle_voice_transcription(chat_id, target_msg, custom_instruction="", business_id=None):
    """Расшифровывает ГС/кружок строго в чистый текст (без описаний и воды), если нет доп. поручений."""
    is_video_note = (target_msg.content_type == 'video_note')
    prefix_label = "Слушаю кружок..." if is_video_note else "Слушаю ГС..."

    loading_msg = bot.send_message(chat_id, f"🕐 {prefix_label}", business_connection_id=business_id)
    if business_id:
        BOT_BUSINESS_MESSAGES.add(loading_msg.message_id)

    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, loading_msg.message_id, stop_event, business_id, prefix_label)).start()

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
        clean_reply = reply.strip()
        if custom_instruction:
            out_text = f"🎙 {clean_reply}\n\nmodel: {model_used}"
        else:
            out_text = f"🎙 {clean_reply}"
        safe_edit_message(out_text, chat_id, loading_msg.message_id, business_id)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id, business_id)

# --- РИСОВАНИЕ ---
def generate_image(prompt):
    if not GEMINI_KEYS:
        return None, "Нет ключей Gemini для генерации фото."

    model = CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)
    url = f"{GEMINI_API_BASE}/models/{model}:predict" if 'imagen' in model else f"{GEMINI_API_BASE}/models/{model}:generateContent"
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}} if 'imagen' in model else \
              {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}

    last_err = "Неизвестная ошибка"
    for key_idx, api_key in enumerate(GEMINI_KEYS):
        try:
            r = session.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=60)
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
                            return base64.b64encode(part['inline_data']['data']), None
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
def build_panel_view():
    auto_on = CONFIG.get('auto_reply_enabled', True)
    spy_on = CONFIG.get('spy_enabled', True)
    voice_all_on = CONFIG.get('voice_auto_all', False)

    auto_status = f"🟢 ВКЛ ({CONFIG.get('afk_minutes', 10)} мин)" if auto_on else "🔴 ВЫКЛ"
    spy_status = "🟢 ВКЛ" if spy_on else "🔴 ВЫКЛ"
    voice_all_status = "🟢 ВКЛ (все чаты)" if voice_all_on else "⚪ По команде «рс»"

    voice_users = list(CONFIG.get('voice_auto_users', {}).keys())
    voice_users_str = ", ".join([f"@{u}" for u in voice_users]) if voice_users else "нет"
    bl_users = list(CONFIG.get('blacklist', {}).keys())
    bl_str = f"{len(bl_users)} чел." if bl_users else "пусто"

    active_cooldowns = sum(1 for v in EXHAUSTED_QUOTAS.values() if v > time.time())
    sys_prompt = CONFIG.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
    is_custom_prompt = (sys_prompt != DEFAULT_SYSTEM_PROMPT)

    text = (
        f"👑 <b>Панель управления аккаунтом</b>\n\n"
        f"🤖 <b>Автоответчик:</b> {auto_status}\n"
        f"🕵️‍♂️ <b>Режим Шпиона:</b> {spy_status}\n"
        f"🎙 <b>Авто-ГС/кружки:</b> {voice_all_status}\n"
        f"👥 <b>Авто-ГС юзеры:</b> <code>{voice_users_str}</code>\n"
        f"🚫 <b>Черный список:</b> {bl_str}\n\n"
        f"🧠 <b>Модель:</b> <code>{ACTIVE_MODEL}</code>\n"
        f"🎭 <b>Характер ИИ:</b> {'Кастомный ✏️' if is_custom_prompt else 'Стандартный'}\n"
        f"🔑 <b>Пул ключей:</b> <b>{len(GEMINI_KEYS)} шт.</b> (следующий: #{CURRENT_KEY_INDEX + 1 if GEMINI_KEYS else 0}, в кулдауне пар: {active_cooldowns})\n"
        f"⏱ <b>Анти-спам КД:</b> {CONFIG.get('cooldown_seconds', 5)} сек | <b>Память:</b> {CONFIG.get('context_limit', 100)} сообщ. (активных чатов: {len(CHAT_MEMORY)})"
    )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"🤖 Автоответ: {'ВКЛ 🟢' if auto_on else 'ВЫКЛ 🔴'}", callback_data=" panel_toggle_auto"),
        InlineKeyboardButton(f"🕵️‍♂️ Шпион: {'ВКЛ 🟢' if spy_on else 'ВЫКЛ 🔴'}", callback_data="panel_toggle_spy")
    )
    kb.add(
        InlineKeyboardButton(f"🎙 Авто-ГС (все): {'ВКЛ 🟢' if voice_all_on else 'ВЫКЛ ⚪'}", callback_data="panel_toggle_voice"),
        InlineKeyboardButton("🔑 Статус ключей", callback_data="panel_keys_status")
    )
    kb.add(
        InlineKeyboardButton("🧠 Сменить модель", callback_data="panel_models"),
        InlineKeyboardButton("🧹 Очистить ВСЕ чаты", callback_data="panel_clear_all")
    )
    kb.add(InlineKeyboardButton("🔄 Обновить панель", callback_data="panel_refresh"))
    return text, kb

def build_keys_status_view(ping_results=None):
    now = time.time()
    lines = [
        f"🔑 <b>Мониторинг пула ключей Gemini ({len(GEMINI_KEYS)} шт.)</b>",
        f"📍 <b>Приоритетная модель:</b> <code>{ACTIVE_MODEL}</code>",
        f"➡️ <b>Очередь на запрос:</b> Ключ #{CURRENT_KEY_INDEX + 1 if GEMINI_KEYS else 0}\n"
    ]

    if not GEMINI_KEYS:
        lines.append("⚠️ Ключи не найдены! Добавь GEMINI_KEY_1..5 в Render.")
    else:
        for idx, k in enumerate(GEMINI_KEYS):
            stats = KEY_STATS.get(idx, {'ok': 0, '429': 0, 'err': 0})
            cooldown_models = []
            for m in MODEL_HIERARCHY:
                until = EXHAUSTED_QUOTAS.get((m, idx), 0)
                if until > now:
                    mins_left = int((until - now) // 60) + 1
                    cooldown_models.append(f"{m} ({mins_left}м)")

            if cooldown_models:
                state_str = f"⏳ КД на: {', '.join(cooldown_models)}"
            else:
                state_str = "🟢 Свободен на всех моделях"

            ping_str = ""
            if ping_results and idx in ping_results:
                ping_str = f"\n   📡 Тест: {ping_results[idx]}"

            lines.append(
                f"<b>Ключ #{idx + 1}</b> (<code>{mask_key(k)}</code>)\n"
                f"   Статус: {state_str}\n"
                f"   Запросов: ✅ {stats['ok']} | ⚠️ 429: {stats['429']} | ❌ Сбоев: {stats['err']}"
                f"{ping_str}"
            )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("⚡ Пинг всех ключей", callback_data="keys_ping"),
        InlineKeyboardButton("♻️ Сбросить кулдауны", callback_data="keys_reset_cd")
    )
    kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
    return "\n".join(lines), kb

def ping_all_keys():
    """Быстрая проверка работоспособности каждого ключа на текущей модели."""
    results = {}
    test_model = ACTIVE_MODEL if not ACTIVE_MODEL.startswith('groq/') else MODEL_HIERARCHY[0]
    url = f"{GEMINI_API_BASE}/models/{test_model}:generateContent"
    payload = {"contents": [{"role": "user", "parts": [{"text": "ok"}]}], "generationConfig": {"maxOutputTokens": 2}}

    for idx, api_key in enumerate(GEMINI_KEYS):
        try:
            r = session.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=10)
            if r.status_code == 200:
                results[idx] = f"🟢 Работает ({test_model})"
            elif r.status_code == 429:
                results[idx] = f"🔴 Лимит 429 ({test_model})"
            else:
                results[idx] = f"⚠️ Код {r.status_code}"
        except Exception as e:
            results[idx] = f"❌ Ошибка сети"
    return results

# --- ПАНЕЛЬ УПРАВЛЕНИЯ (В ЛИЧКЕ С БОТОМ) ---
@bot.message_handler(commands=['start', 'panel'])
def send_panel(message):
    if (message.from_user.username or '').lower() != CREATOR_USERNAME:
        return
    register_creator_id(message.from_user)
    text, kb = build_panel_view()
    bot.send_message(message.chat.id, text, reply_markup=kb)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['voice', 'video_note', 'audio'])
def handle_admin_voice(message):
    register_creator_id(message.from_user)
    handle_voice_transcription(message.chat.id, message, business_id=None)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['document'])
def handle_admin_document(message):
    register_creator_id(message.from_user)
    caption = (message.caption or "").strip()
    if caption.lower().startswith('гпт '):
        caption = caption[4:].strip()
    if not caption:
        caption = "Проанализируй этот документ, опиши его суть и основные тезисы."

    loading_msg = bot.reply_to(message, "🕐 Читаю файл...")
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event, None, "Читаю файл...")).start()

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
        safe_edit_message(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", message.chat.id, loading_msg.message_id)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text'])
def handle_admin_text(message):
    register_creator_id(message.from_user)
    raw_text = message.text.strip()
    text = raw_text.lower()
    chat_id = message.chat.id

    if text in ['панель', 'пульт']:
        p_text, p_kb = build_panel_view()
        bot.send_message(chat_id, p_text, reply_markup=p_kb)
        return

    if text in ['ключи', 'статус ключей', '/keys']:
        k_text, k_kb = build_keys_status_view()
        bot.send_message(chat_id, k_text, reply_markup=k_kb)
        return

    if text in ['команды', '/help']:
        bot.reply_to(
            message,
            "📜 <b>Все команды управления:</b>\n\n"
            "🎛 <b>Пульт и Ключи:</b>\n"
            "• <code>/panel</code> (или <code>панель</code>) — интерактивный пульт с кнопками\n"
            "• <code>ключи</code> — статистика всех ключей, кулдауны и пинг-тест\n"
            "• <code>список</code> — кто в ЧС и у кого включено авто-ГС\n\n"
            "🧠 <b>Нейросеть и Настройки:</b>\n"
            "• <code>модели</code> / <code>фото-модели</code> — выбор моделей\n"
            "• <code>гпт [запрос]</code> — вопрос ИИ (или Reply на файл/фото)\n"
            "• <code>нарисуй [описание]</code> — генерация картинки\n"
            "• <code>характер [текст]</code> — задать свой промпт/статус для Gemini\n"
            "• <code>характер сброс</code> — вернуть стандартный промпт\n"
            "• <code>таймер 5</code> — через сколько минут молчания отвечает бот\n"
            "• <code>кд 3</code> — задержка анти-спама в секундах (0 — без задержки)\n"
            "• <code>контекст 100</code> — лимит памяти сообщений\n"
            "• <code>сброс</code> / <code>сброс все</code> — очистить память чата или всех чатов\n\n"
            "🎙 <b>Голосовые и Кружки (чистый текст):</b>\n"
            "• Ответь <code>рс</code> на ГС/кружок — чистая расшифровка без воды\n"
            "• Ответь <code>рс перескажи кратко</code> — расшифровка с заданием\n"
            "• <code>рс @юзер</code> — вкл/выкл авто-расшифровку для конкретного человека\n"
            "• <code>рс все вкл</code> / <code>выкл</code> — авто-расшифровка для всех\n\n"
            "🕵️‍♂️ <b>Шпион и Доступ:</b>\n"
            "• <code>шпион вкл</code> / <code>выкл</code> — перехват удаленных и измененных СМС\n"
            "• <code>автоответчик вкл</code> / <code>выкл</code> — режим секретаря\n"
            "• <code>запретить @юзер</code> / <code>разрешить @юзер</code> — ЧС для бота"
        )
        return

    if text == 'список':
        v_users = list(CONFIG.get('voice_auto_users', {}).keys())
        b_users = list(CONFIG.get('blacklist', {}).keys())
        v_str = ", ".join([f"@{u}" for u in v_users]) if v_users else "пусто"
        b_str = ", ".join([f"@{u}" for u in b_users]) if b_users else "пусто"
        sys_p = CONFIG.get('system_prompt', DEFAULT_SYSTEM_PROMPT)
        bot.reply_to(
            message,
            f"📋 <b>Текущие списки и промпт:</b>\n\n"
            f"🎙 <b>Авто-ГС юзеры:</b> {v_str}\n"
            f"🚫 <b>Черный список:</b> {b_str}\n\n"
            f"🎭 <b>Системный промпт Gemini:</b>\n<blockquote>{html.escape(sys_p)}</blockquote>"
        )
        return

    if text == 'модели':
        models = fetch_all_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == ACTIVE_MODEL else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        bot.reply_to(message, f"Все доступные модели (всего {len(models)}):", reply_markup=kb)
        return

    if text in ['фото-модели', 'фото модели']:
        image_models = ['imagen-3.0-generate-001', 'gemini-2.5-flash-image', 'gemini-3.1-flash-image']
        kb = InlineKeyboardMarkup(row_width=1)
        for m in image_models:
            mark = "✅ " if m == CONFIG.get('image_model') else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setimg_{m}"))
        bot.reply_to(message, "Модель рисования:", reply_markup=kb)
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
            bot.reply_to(message, "⚠️ Укажи секунды, например: <code>кд 3</code> (или <code>кд 0</code>)")
        return

    if text.startswith('характер ') or text.startswith('промпт '):
        arg = raw_text.split(maxsplit=1)[1].strip() if len(raw_text.split()) > 1 else ""
        if arg.lower() in ['сброс', 'стандарт', 'default']:
            CONFIG['system_prompt'] = DEFAULT_SYSTEM_PROMPT
            save_config()
            bot.reply_to(message, "✅ Характер Gemini сброшен на стандартный.")
        elif arg:
            CONFIG['system_prompt'] = arg
            save_config()
            bot.reply_to(message, f"🎭 Новый характер/статус для Gemini сохранен:\n<blockquote>{html.escape(arg)}</blockquote>")
        return

    if text.startswith('шпион'):
        if 'выкл' in text:
            CONFIG['spy_enabled'] = False
        elif 'вкл' in text:
            CONFIG['spy_enabled'] = True
        save_config()
        bot.reply_to(message, f"🕵️‍♂️ Режим Шпиона: {'🟢 ВКЛ' if CONFIG['spy_enabled'] else '🔴 ВЫКЛ'}")
        return

    if text.startswith('автоответчик'):
        if 'выкл' in text:
            CONFIG['auto_reply_enabled'] = False
        elif 'вкл' in text:
            CONFIG['auto_reply_enabled'] = True
        save_config()
        bot.reply_to(message, f"✅ Автоответчик: {'🟢 ВКЛ' if CONFIG['auto_reply_enabled'] else '🔴 ВЫКЛ'}")
        return

    if text in ['рс все вкл', 'авто гс вкл']:
        CONFIG['voice_auto_all'] = True
        save_config()
        bot.reply_to(message, "🎙 Авто-расшифровка всех ГС и кружков: <b>🟢 ВКЛ (для всех чатов)</b>")
        return

    if text in ['рс все выкл', 'авто гс выкл']:
        CONFIG['voice_auto_all'] = False
        save_config()
        bot.reply_to(message, "🎙 Авто-расшифровка для всех: <b>🔴 ВЫКЛ</b> (работает по команде «рс» и для выбранных юзеров)")
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
                bot.reply_to(message, f"🎙 Постоянная авто-расшифровка ГС/кружков для @{uname}: <b>🟢 ВКЛ</b>")
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
        save_chat_memory_async(chat_id, [])
        bot.reply_to(message, f"🧹 Память очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}")
        return

    if text.startswith('нарисуй '):
        if not check_cooldown(chat_id):
            bot.reply_to(message, "жди пока первый запрос загрузится нищук")
            return
        loading_msg = bot.reply_to(message, "🕐 Рисую...")
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(chat_id, loading_msg.message_id, stop_event, None, "Рисую...")).start()
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

    if text.startswith('гпт '):
        if not check_cooldown(chat_id):
            bot.reply_to(message, "жди пока первый запрос загрузится нищук")
            return

        prompt_text = raw_text[4:].strip()
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

        loading_msg = bot.reply_to(message, "🕐 Думаю...")
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event)).start()
        reply, err, model_used, ctx_len = ask_ai(chat_id, prompt_text, media_list)
        stop_event.set()
        if reply:
            safe_edit_message(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", message.chat.id, loading_msg.message_id)
        else:
            safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

# --- ОБРАБОТЧИК ИНТЕРАКТИВНЫХ КНОПОК АДМИНКИ ---
@bot.callback_query_handler(func=lambda call: True)
def handle_callbacks(call):
    global ACTIVE_MODEL
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
        bot.answer_callback_query(call.id, "Все кулдауны ключей сброшены!")

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
        safe_edit_message(f"🧠 <b>Выбери базовую модель (всего {len(models)}):</b>", chat_id, msg_id, reply_markup=kb)
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
        bot.edit_message_text(f"✅ Рисование: <b>{CONFIG['image_model']}</b>", chat_id, msg_id)
        bot.answer_callback_query(call.id, "Модель рисования изменена")

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
    is_gpt = lower_text.startswith('гпт ')
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
        msg = bot.send_message(chat_id, "жди пока первый запрос загрузится нищук", business_connection_id=base_msg.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        return

    if is_owner:
        LAST_OWNER_ACTIVITY[chat_id] = time.time()
    if is_gpt:
        text = text[4:].strip()
    if not text:
        text = "Опиши подробно, что на этих фотографиях."

    msg = bot.send_message(chat_id, f"🕐 Смотрю {len(messages)} фото...", business_connection_id=base_msg.business_connection_id)
    BOT_BUSINESS_MESSAGES.add(msg.message_id)
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, base_msg.business_connection_id, f"Смотрю {len(messages)} фото...")).start()

    images = []
    for m in messages:
        img_bytes, mime = get_photo_bytes(m)
        if img_bytes:
            images.append((img_bytes, mime))

    reply, err, model_used, ctx_len = ask_ai(chat_id, text, images)
    stop_event.set()

    if reply:
        safe_edit_message(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, base_msg.business_connection_id)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, base_msg.business_connection_id)

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

    cache_incoming_message(message, is_owner)

    if not is_owner and sender_uname in CONFIG.get('blacklist', {}):
        return

    text = message.text or message.caption or ""
    lower_text = text.lower().strip()

    if lower_text in ['сброс', '/reset']:
        CHAT_MEMORY[chat_id] = []
        save_chat_memory_async(chat_id, [])
        msg = bot.send_message(chat_id, f"Память очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        return

    # 1. АВТО-РАСШИФРОВКА ВХОДЯЩИХ ГС И КРУЖКОВ
    if message.content_type in ['voice', 'video_note', 'audio']:
        auto_all = CONFIG.get('voice_auto_all', False)
        auto_user = sender_uname in CONFIG.get('voice_auto_users', {})
        if not is_owner and (auto_all or auto_user):
            handle_voice_transcription(chat_id, message, business_id=message.business_connection_id)
        return

    # 2. РУЧНАЯ КОМАНДА «рс» (ИЛИ «рс [задание]»)
    if (lower_text == 'рс' or lower_text.startswith('рс ')) and message.reply_to_message:
        rep = message.reply_to_message
        if rep.content_type in ['voice', 'video_note', 'audio']:
            custom_q = text[3:].strip() if len(text) > 2 else ""
            handle_voice_transcription(chat_id, rep, custom_q, business_id=message.business_connection_id)
            return

    # 3. СКЛЕЙКА ФОТО-АЛЬБОМОВ
    if message.content_type == 'photo' and message.media_group_id:
        if message.media_group_id not in MEDIA_GROUP_BUFFER:
            MEDIA_GROUP_BUFFER[message.media_group_id] = []
            threading.Timer(2.5, process_business_album, args=[message.media_group_id]).start()
        MEDIA_GROUP_BUFFER[message.media_group_id].append(message)
        return

    is_gpt = lower_text.startswith('гпт ') or lower_text == 'гпт'
    is_draw = lower_text.startswith('нарисуй ')
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.message_id in BOT_BUSINESS_MESSAGES
    afk_seconds = CONFIG.get('afk_minutes', 10) * 60

    trigger = False
    if is_owner:
        if is_gpt or is_draw or is_reply_to_bot:
            trigger = True
    else:
        if is_gpt or is_draw or is_reply_to_bot:
            trigger = True
        elif message.content_type in ['text', 'document', 'photo'] and CONFIG['auto_reply_enabled']:
            if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= afk_seconds:
                trigger = True

    if not trigger:
        return

    if not check_cooldown(chat_id):
        msg = bot.send_message(chat_id, "жди пока первый запрос загрузится нищук", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        return

    if is_gpt:
        text = text[4:].strip()

    if is_draw:
        msg = bot.send_message(chat_id, "🕐 Рисую...", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, message.business_connection_id, "Рисую...")).start()
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

    # 4. ОБРАБОТКА ТЕКСТА, ФОТО И ДОКУМЕНТОВ
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

    msg = bot.send_message(chat_id, f"🕐 {loading_label}", business_connection_id=message.business_connection_id)
    BOT_BUSINESS_MESSAGES.add(msg.message_id)
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, message.business_connection_id, loading_label)).start()

    reply, err, model_used, ctx_len = ask_ai(chat_id, text, media_list)
    stop_event.set()

    if reply:
        safe_edit_message(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, message.business_connection_id)
    else:
        safe_edit_message(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, message.business_connection_id)

# --- ЗАПУСК ---
try:
    bot.delete_webhook(drop_pending_updates=True)
except Exception:
    pass

print(f'⚡ БОТ ЗАПУЩЕН! Ключей Gemini в пуле: {len(GEMINI_KEYS)} | Админка 2.0 активна.')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
