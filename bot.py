import html
import os
import re
import base64
import time
import requests
import threading

try:
    import telebot
except ImportError:
    os.system('pip install pyTelegramBotAPI requests')
    import telebot

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# --- НАСТРОЙКИ И ЗАГРУЗКА ПУЛА КЛЮЧЕЙ ---
TOKEN = os.environ.get('TG_TOKEN')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')  # Ключ Groq
FIREBASE_URL = os.environ.get('FIREBASE_URL')

def load_gemini_keys():
    """Собирает все ключи Gemini из переменных окружения (поддерживает отдельные поля и запись через запятую)."""
    keys = []
    env_names = [
        'GROQ_KEY',       # Твой старый ключ №1 (оставлен для совместимости)
        'GEMINI_KEYS',    # На случай если захочешь писать через запятую
        'GEMINI_KEY',
        'GEMINI_KEY_1',
        'GEMINI_KEY_2',   # Новый ключ №2
        'GEMINI_KEY_3',   # Новый ключ №3
        'GEMINI_KEY_4',
        'GEMINI_KEY_5'
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
# Хранит временные блокировки для конкретной пары (модель, индекс_ключа)
EXHAUSTED_QUOTAS = {}  # {(model, key_idx): timestamp_until_free}

CREATOR_USERNAME = 'wrtnoq'
CREATOR_ID = None

# Приоритет моделей: сначала выжимаются все ключи на 3.8, затем все ключи на 3.7 и т.д.
MODEL_HIERARCHY = [
    'gemini-3.8-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-3-flash'
]
DEFAULT_IMAGE_MODEL = 'imagen-3.0-generate-001'
DEFAULT_CONTEXT_LIMIT = 100
GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GROQ_API_BASE = 'https://api.groq.com/openai/v1'

ACTIVE_MODEL = MODEL_HIERARCHY[0]

CONFIG = {
    'auto_reply_enabled': True,
    'context_limit': DEFAULT_CONTEXT_LIMIT,
    'image_model': DEFAULT_IMAGE_MODEL,
    'blacklist': {}
}
LAST_OWNER_ACTIVITY = {}
LAST_REQUEST_TIME = {}  # Кулдаун на 5 секунд
CHAT_MEMORY = {}
BOT_BUSINESS_MESSAGES = set()
MEDIA_GROUP_BUFFER = {}

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)

retry_strategy = Retry(total=2, backoff_factor=1, status_forcelist=[500, 502, 503, 504], allowed_methods=["POST"])
session = requests.Session()
adapter = HTTPAdapter(max_retries=retry_strategy)
session.mount("https://", adapter)
session.mount("http://", adapter)

# --- БАЗА ДАННЫХ (FIREBASE) ---
def init_firebase():
    if not FIREBASE_URL:
        return
    try:
        r = session.get(f'{FIREBASE_URL}/config.json', timeout=10)
        if r.status_code == 200 and r.json():
            CONFIG.update(r.json())
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
    try:
        session.patch(f'{FIREBASE_URL}/config.json', json=CONFIG, timeout=5)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения в Firebase: {e}")

def save_chat_memory_async(chat_id, history):
    """Сохраняет историю конкретного чата в Firebase в фоновом потоке без задержки ответа."""
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            session.put(f'{FIREBASE_URL}/chat_memory/{chat_id}.json', json=history, timeout=5)
        except Exception as e:
            print(f"⚠️ Ошибка сохранения памяти чата {chat_id}: {e}")
    threading.Thread(target=_worker, daemon=True).start()

init_firebase()

def check_cooldown(chat_id):
    now = time.time()
    if now - LAST_REQUEST_TIME.get(chat_id, 0) < 5:
        return False
    LAST_REQUEST_TIME[chat_id] = now
    return True

# --- МАРШРУТИЗАТОР ИИ ---
def ask_ai(chat_id, prompt, image_list=None):
    if ACTIVE_MODEL.startswith('groq/'):
        return ask_groq(chat_id, prompt, image_list)
    else:
        return ask_gemini(chat_id, prompt, image_list)

# --- GEMINI API С РОТАЦИЕЙ КЛЮЧЕЙ И МОДЕЛЕЙ ---
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
        except Exception as e:
            print(f"Ошибка получения моделей Gemini: {e}")

    if GROQ_API_KEY:
        url = f"{GROQ_API_BASE}/models"
        try:
            r = session.get(url, headers={'Authorization': f'Bearer {GROQ_API_KEY}'}, timeout=10)
            if r.status_code == 200:
                for m in r.json().get('data', []):
                    if m.get('active') is not False:
                        all_models.append(f"groq/{m['id']}")
        except Exception as e:
            print(f"Ошибка получения моделей Groq: {e}")

    if not all_models:
        return MODEL_HIERARCHY
    return sorted(list(set(all_models)))

def ask_gemini(chat_id, prompt, image_list=None):
    global CURRENT_KEY_INDEX

    if not GEMINI_KEYS:
        return None, "Не найдено ни одного ключа Gemini! Проверь настройки Environment в Render.", ACTIVE_MODEL, 0

    history = CHAT_MEMORY.get(chat_id, []).copy()
    contents = []
    for msg in history:
        role = 'user' if msg['role'] == 'user' else 'model'
        contents.append({"role": role, "parts": [{"text": msg['content']}]})

    parts = []
    if prompt:
        parts.append({"text": prompt})
    if image_list:
        for img_bytes, mime_type in image_list:
            parts.append({
                "inline_data": {
                    "mime_type": mime_type,
                    "data": base64.b64encode(img_bytes).decode('utf-8')
                }
            })

    contents.append({"role": "user", "parts": parts})
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": "Ты — умный и полезный ИИ-ассистент. Отвечай на вопросы пользователя."}]}
    }

    # Строим очередь моделей: если выбрана модель из иерархии (по умолчанию 3.8), идем строго по списку вниз
    if ACTIVE_MODEL in MODEL_HIERARCHY:
        start_idx = MODEL_HIERARCHY.index(ACTIVE_MODEL)
        models_to_try = MODEL_HIERARCHY[start_idx:]
    else:
        models_to_try = [ACTIVE_MODEL] + MODEL_HIERARCHY

    last_error = "Все доступные модели и ключи исчерпали лимиты."
    now = time.time()

    # Внешний цикл: по моделям (сначала 3.8 на всех ключах, потом 3.7 на всех ключах и т.д.)
    for model in models_to_try:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"

        # Внутренний цикл: проверяем все ключи для текущей модели
        for k_offset in range(len(GEMINI_KEYS)):
            key_idx = (CURRENT_KEY_INDEX + k_offset) % len(GEMINI_KEYS)
            api_key = GEMINI_KEYS[key_idx]

            # Если эта пара (модель + ключ) недавно словила 429, пропускаем её, пока не выйдет кулдаун
            if EXHAUSTED_QUOTAS.get((model, key_idx), 0) > now:
                continue

            try:
                r = session.post(url, headers=get_gemini_headers(api_key), json=payload, timeout=60)

                # Если лимит исчерпан (429) — ставим этот ключ для этой модели на паузу (15 минут) и пробуем следующий ключ
                if r.status_code == 429:
                    print(f"⚠️ Лимит {model} на ключе #{key_idx + 1} исчерпан. Переключаю ключ...")
                    EXHAUSTED_QUOTAS[(model, key_idx)] = time.time() + 900
                    last_error = f"Лимит (429) на {model}"
                    continue

                # Если модель не найдена (404) или не подходит для текста (400) — сразу переходим к следующей модели
                if r.status_code in [400, 404]:
                    print(f"⚠️ Модель {model} вернула {r.status_code}. Переход к следующей модели...")
                    last_error = f"HTTP {r.status_code} ({model})"
                    break

                # Другие ошибки сервера или ключа — пробуем следующий ключ
                if r.status_code != 200:
                    last_error = f"HTTP {r.status_code}: Gemini API Error"
                    continue

                data = r.json()
                if 'candidates' not in data or not data['candidates']:
                    return None, "Модель заблокировала ответ (Safety Filter).", f"{model} [key #{key_idx + 1}]", 0

                reply_text = data['candidates'][0]['content']['parts'][0]['text']

                # Сдвигаем указатель ключа для следующего запроса (равномерная нагрузка Round-Robin)
                CURRENT_KEY_INDEX = (key_idx + 1) % len(GEMINI_KEYS)

                # Сохраняем контекст (память общая для всех ключей и моделей!)
                history.append({"role": "user", "content": prompt})
                history.append({"role": "assistant", "content": reply_text})
                limit = CONFIG.get('context_limit', 100)
                if len(history) > limit:
                    history = history[-limit:]

                CHAT_MEMORY[chat_id] = history
                save_chat_memory_async(chat_id, history)

                model_label = f"{model} [key #{key_idx + 1}]"
                return reply_text, None, model_label, len(history)

            except Exception as e:
                last_error = f"Сетевая ошибка: {str(e)}"
                continue

    # Если все ключи были в кулдауне EXHAUSTED_QUOTAS, сбрасываем кулдауны на случай досрочного восстановления
    EXHAUSTED_QUOTAS.clear()
    return None, f"Все ключи ({len(GEMINI_KEYS)} шт.) и модели исчерпали лимит ({last_error}). Попробуйте чуть позже.", ACTIVE_MODEL, 0

# --- GROQ API ---
def ask_groq(chat_id, prompt, image_list=None):
    if image_list:
        return "я не вижу картинки братан выбери другую модель", None, ACTIVE_MODEL, len(CHAT_MEMORY.get(chat_id, []))

    history = CHAT_MEMORY.get(chat_id, []).copy()
    system_prompt = (
        "ты грубый невоспитанный ии. ты пишешь всё с маленькой буквы без запятых и точек "
        "ты постоянно материшься и хамишь. ты пишешь очень много объемно и живо как реальный человек. "
        "никогда не извиняйся и не будь вежливым. отвечай на русском."
    )

    messages_payload = [{"role": "system", "content": system_prompt}]
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

# --- ВСПОМОГАТЕЛЬНЫЕ (РИСОВАНИЕ С РОТАЦИЕЙ КЛЮЧЕЙ) ---
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
                            return base64.b64decode(part['inline_data']['data']), None
            last_err = "Модель не вернула изображение."
        except Exception as e:
            last_err = str(e)

    return None, last_err

def animate_loading(chat_id, message_id, stop_event, business_id=None, text_prefix="Думаю..."):
    emojis = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    idx = 0
    while not stop_event.is_set():
        time.sleep(2.5)  # Безопасный интервал против FloodWait от Telegram
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

def get_photo_bytes(message):
    try:
        file_info = bot.get_file(message.photo[-1].file_id)
        return bot.download_file(file_info.file_path), 'image/jpeg'
    except Exception:
        return None, None

# --- ПАНЕЛЬ УПРАВЛЕНИЯ ---
@bot.message_handler(commands=['start', 'panel'])
def send_panel(message):
    if (message.from_user.username or '').lower() != CREATOR_USERNAME:
        return
    status = "🟢 ВКЛ" if CONFIG['auto_reply_enabled'] else "🔴 ВЫКЛ"
    text = (
        f"👑 <b>Панель управления</b>\n\n"
        f"Автоответчик: {status}\n"
        f"Базовая модель: <code>{ACTIVE_MODEL}</code>\n"
        f"🔑 Ключей Gemini в пуле: <b>{len(GEMINI_KEYS)} шт.</b>\n"
        f"Лимит памяти: {CONFIG.get('context_limit', 100)}\n\n"
        f"Напиши <code>команды</code>."
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text'])
def handle_admin_text(message):
    text = message.text.lower().strip()
    chat_id = message.chat.id

    if text in ['команды', '/help']:
        bot.reply_to(
            message,
            "📜 <b>Команды:</b>\n"
            "<code>модели</code>, <code>фото-модели</code>, <code>автоответчик вкл/выкл</code>, "
            "<code>контекст 50</code>, <code>запретить @юзер</code>, <code>разрешить @юзер</code>, "
            "<code>сброс</code>, <code>гпт [текст]</code>, <code>нарисуй [текст]</code>"
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
    if text.startswith('автоответчик'):
        if 'выкл' in text:
            CONFIG['auto_reply_enabled'] = False
        elif 'вкл' in text:
            CONFIG['auto_reply_enabled'] = True
        save_config()
        bot.reply_to(message, f"✅ Автоответчик: {'ВКЛ' if CONFIG['auto_reply_enabled'] else 'ВЫКЛ'}")
        return
    if text.startswith('контекст '):
        try:
            CONFIG['context_limit'] = int(text.split()[1])
            save_config()
            bot.reply_to(message, f"✅ Лимит: {CONFIG['context_limit']}")
        except Exception:
            pass
        return
    if text.startswith('запретить '):
        CONFIG['blacklist'][text.split()[1].replace('@', '')] = True
        save_config()
        bot.reply_to(message, "🚫 Забанен.")
        return
    if text.startswith('разрешить '):
        uname = text.split()[1].replace('@', '')
        if uname in CONFIG['blacklist']:
            del CONFIG['blacklist'][uname]
            save_config()
        bot.reply_to(message, "✅ Разбанен.")
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
        img, err = generate_image(message.text[8:].strip())
        stop_event.set()
        if img:
            try:
                bot.delete_message(chat_id, loading_msg.message_id)
                bot.send_photo(chat_id, img)
            except Exception:
                pass
        else:
            bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id)
        return
    if text.startswith('гпт '):
        if not check_cooldown(chat_id):
            bot.reply_to(message, "жди пока первый запрос загрузится нищук")
            return

        loading_msg = bot.reply_to(message, "🕐 Думаю...")
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event)).start()
        reply, err, model_used, ctx_len = ask_ai(chat_id, message.text[4:].strip())
        stop_event.set()
        if reply:
            try:
                bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", message.chat.id, loading_msg.message_id)
            except Exception:
                bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", message.chat.id, loading_msg.message_id, parse_mode=None)
        else:
            bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_') or call.data.startswith('setimg_'))
def callback_set_model(call):
    global ACTIVE_MODEL
    if (call.from_user.username or '').lower() != CREATOR_USERNAME:
        return
    if call.data.startswith('setmodel_'):
        ACTIVE_MODEL = call.data.replace('setmodel_', '')
        EXHAUSTED_QUOTAS.clear()
        bot.edit_message_text(f"✅ Текст: <b>{ACTIVE_MODEL}</b>", call.message.chat.id, call.message.message_id)
    else:
        CONFIG['image_model'] = call.data.replace('setimg_', '')
        save_config()
        bot.edit_message_text(f"✅ Рисование: <b>{CONFIG['image_model']}</b>", call.message.chat.id, call.message.message_id)

# --- ОБРАБОТКА АЛЬБОМОВ ---
def process_business_album(media_group_id):
    if media_group_id not in MEDIA_GROUP_BUFFER:
        return
    messages = MEDIA_GROUP_BUFFER.pop(media_group_id)
    if not messages:
        return

    base_msg = messages[0]
    chat_id = base_msg.chat.id

    global CREATOR_ID
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

    trigger = False
    if is_gpt or is_reply_to_bot:
        trigger = True
    elif not is_owner and CONFIG['auto_reply_enabled']:
        if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= 600:
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
        try:
            bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=base_msg.business_connection_id)
        except Exception:
            bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=base_msg.business_connection_id, parse_mode=None)
    else:
        bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, business_connection_id=base_msg.business_connection_id)

# --- БИЗНЕС-СООБЩЕНИЯ ---
@bot.business_message_handler(content_types=['text', 'photo'])
def handle_business_message(message):
    global CREATOR_ID
    if not message.from_user:
        return

    chat_id = message.chat.id
    if (message.from_user.username or '').lower() == CREATOR_USERNAME:
        CREATOR_ID = message.from_user.id
        LAST_OWNER_ACTIVITY[chat_id] = time.time()

    is_owner = (message.from_user.id == CREATOR_ID) or ((message.from_user.username or '').lower() == CREATOR_USERNAME)
    if not is_owner and (message.from_user.username or '').lower() in CONFIG.get('blacklist', {}):
        return

    text = message.text or message.caption or ""
    lower_text = text.lower().strip()

    if lower_text in ['сброс', '/reset']:
        CHAT_MEMORY[chat_id] = []
        save_chat_memory_async(chat_id, [])
        msg = bot.send_message(chat_id, f"Память очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}", business_connection_id=message.business_connection_id)
        BOT_BUSINESS_MESSAGES.add(msg.message_id)
        return

    if message.content_type == 'photo' and message.media_group_id:
        if message.media_group_id not in MEDIA_GROUP_BUFFER:
            MEDIA_GROUP_BUFFER[message.media_group_id] = []
            threading.Timer(2.5, process_business_album, args=[message.media_group_id]).start()
        MEDIA_GROUP_BUFFER[message.media_group_id].append(message)
        return

    is_gpt = lower_text.startswith('гпт ')
    is_draw = lower_text.startswith('нарисуй ')
    is_reply_to_bot = message.reply_to_message and message.reply_to_message.message_id in BOT_BUSINESS_MESSAGES

    trigger = False
    if is_owner:
        if is_gpt or is_draw or is_reply_to_bot:
            trigger = True
    else:
        if is_gpt or is_draw or is_reply_to_bot:
            trigger = True
        elif message.content_type == 'text' and CONFIG['auto_reply_enabled']:
            if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= 600:
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
            bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, business_connection_id=message.business_connection_id)
        return

    msg = bot.send_message(chat_id, "🕐 Думаю...", business_connection_id=message.business_connection_id)
    BOT_BUSINESS_MESSAGES.add(msg.message_id)
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, message.business_connection_id)).start()

    images = []
    if message.content_type == 'photo':
        img_bytes, mime = get_photo_bytes(message)
        if img_bytes:
            images.append((img_bytes, mime))
        if not text:
            text = "Опиши, что на этом фото."
    elif message.reply_to_message and message.reply_to_message.photo:
        img_bytes, mime = get_photo_bytes(message.reply_to_message)
        if img_bytes:
            images.append((img_bytes, mime))

    reply, err, model_used, ctx_len = ask_ai(chat_id, text, images)
    stop_event.set()

    if reply:
        try:
            bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=message.business_connection_id)
        except Exception:
            bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=message.business_connection_id, parse_mode=None)
    else:
        bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, business_connection_id=message.business_connection_id)

# --- ЗАПУСК ---
try:
    bot.delete_webhook(drop_pending_updates=True)
except Exception:
    pass

print(f'⚡ БОТ ЗАПУЩЕН! Ключей Gemini в пуле: {len(GEMINI_KEYS)}')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
