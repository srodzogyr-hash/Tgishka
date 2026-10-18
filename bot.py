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

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get('TG_TOKEN')
GEMINI_KEY = os.environ.get('GROQ_KEY') # Сюда мы ранее положили ключ Gemini
GROQ_API_KEY = os.environ.get('GROQ_API_KEY') # НОВАЯ переменная для Groq
FIREBASE_URL = os.environ.get('FIREBASE_URL')

CREATOR_USERNAME = 'wrtnoq'
CREATOR_ID = None

# Иерархия Gemini (для автопереключения при лимитах)
MODEL_HIERARCHY = [
    'gemini-3.8-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-3.1-flash-tts',
    'gemini-3-flash'
]
# Модель Groq
GROQ_MODEL = 'llama-3.3-70b-versatile'

DEFAULT_IMAGE_MODEL = 'imagen-3.0-generate-001'
DEFAULT_CONTEXT_LIMIT = 100
GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'
GROQ_API_BASE = 'https://api.groq.com/openai/v1'

ACTIVE_MODEL = MODEL_HIERARCHY[0]
MODEL_COOLDOWN_UNTIL = 0

CONFIG = {
    'auto_reply_enabled': True,
    'context_limit': DEFAULT_CONTEXT_LIMIT,
    'image_model': DEFAULT_IMAGE_MODEL,
    'blacklist': {}
}
LAST_OWNER_ACTIVITY = {}
CHAT_MEMORY = {} # Теперь храним в универсальном формате {"role": "...", "content": "..."}
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
    if not FIREBASE_URL: return
    try:
        r = session.get(f'{FIREBASE_URL}/config.json', timeout=10)
        if r.status_code == 200 and r.json():
            CONFIG.update(r.json())
            print("🔥 Настройки загружены из Firebase.")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки Firebase: {e}")

def save_config():
    if not FIREBASE_URL: return
    try:
        session.patch(f'{FIREBASE_URL}/config.json', json=CONFIG, timeout=5)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения в Firebase: {e}")

init_firebase()

# --- МАРШРУТИЗАТОР ИИ ---
def ask_ai(chat_id, prompt, image_list=None):
    if ACTIVE_MODEL.startswith('groq/'):
        return ask_groq(chat_id, prompt, image_list)
    else:
        return ask_gemini(chat_id, prompt, image_list)

# --- GEMINI API ---
def get_gemini_headers():
    return {'Content-Type': 'application/json', 'x-goog-api-key': GEMINI_KEY}

def fetch_gemini_models():
    url = f"{GEMINI_API_BASE}/models"
    try:
        r = session.get(url, headers=get_gemini_headers(), timeout=10)
        if r.status_code == 200:
            models = []
            for m in r.json().get('models', []):
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    models.append(m['name'].replace('models/', ''))
            # Добавляем Groq в начало списка
            models.insert(0, f"groq/{GROQ_MODEL}")
            return models
    except Exception as e:
        print(f"Ошибка получения моделей: {e}")
    return MODEL_HIERARCHY

def ask_gemini(chat_id, prompt, image_list=None):
    global ACTIVE_MODEL, MODEL_COOLDOWN_UNTIL
    
    if ACTIVE_MODEL != MODEL_HIERARCHY[0] and time.time() > MODEL_COOLDOWN_UNTIL and not ACTIVE_MODEL.startswith('groq/'):
        ACTIVE_MODEL = MODEL_HIERARCHY[0]
        print(f"♻️ Квота восстановлена. Возврат к {ACTIVE_MODEL}")

    history = CHAT_MEMORY.get(chat_id, []).copy()
    
    # Конвертация универсальной памяти в формат Gemini
    contents = []
    for msg in history:
        role = 'user' if msg['role'] == 'user' else 'model'
        contents.append({"role": role, "parts": [{"text": msg['content']}]})
    
    parts = []
    if prompt: parts.append({"text": prompt})
    if image_list:
        for img_bytes, mime_type in image_list:
            parts.append({"inline_data": {"mime_type": mime_type, "data": base64.b64encode(img_bytes).decode('utf-8')}})
            
    contents.append({"role": "user", "parts": parts})
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": "Ты — умный и полезный ИИ-ассистент. Отвечай на вопросы пользователя."}]}
    }
    
    models_to_try = [ACTIVE_MODEL]
    if ACTIVE_MODEL in MODEL_HIERARCHY:
        idx = MODEL_HIERARCHY.index(ACTIVE_MODEL)
        models_to_try.extend(MODEL_HIERARCHY[idx+1:])
    else:
        models_to_try.extend(MODEL_HIERARCHY)

    for model in models_to_try:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
        try:
            r = session.post(url, headers=get_gemini_headers(), json=payload, timeout=60)
            
            if r.status_code == 429:
                print(f"⚠️ Лимит {model} исчерпан. Переключение...")
                if model in MODEL_HIERARCHY:
                    next_idx = min(MODEL_HIERARCHY.index(model) + 1, len(MODEL_HIERARCHY)-1)
                    ACTIVE_MODEL = MODEL_HIERARCHY[next_idx]
                    MODEL_COOLDOWN_UNTIL = time.time() + 86400
                continue 
                
            if r.status_code != 200:
                return None, f"HTTP {r.status_code}: Gemini API Error", model, 0
            
            data = r.json()
            if 'candidates' not in data or not data['candidates']:
                return None, "Модель заблокировала ответ (Safety Filter).", model, 0
                
            reply_text = data['candidates'][0]['content']['parts'][0]['text']
            
            if model in MODEL_HIERARCHY:
                ACTIVE_MODEL = model
                
            # Сохраняем в память
            history.append({"role": "user", "content": prompt})
            history.append({"role": "assistant", "content": reply_text})
            limit = CONFIG.get('context_limit', 100)
            if len(history) > limit: history = history[-limit:] 
                
            CHAT_MEMORY[chat_id] = history
            return reply_text, None, model, len(history)
            
        except Exception as e:
            return None, f"Сетевая ошибка: {str(e)}", model, 0
            
    return None, "Все доступные модели исчерпали лимиты. Попробуйте позже.", ACTIVE_MODEL, 0

# --- GROQ API ---
def ask_groq(chat_id, prompt, image_list=None):
    if image_list:
        return "я не вижу картинки братан выбери другую модель", None, ACTIVE_MODEL, len(CHAT_MEMORY.get(chat_id, []))
        
    history = CHAT_MEMORY.get(chat_id, []).copy()
    
    # ЖЕСТКИЙ СИСТЕМНЫЙ ПРОМПТ ДЛЯ GROQ
    system_prompt = (
        "ты грубый невоспитанный ии. ты пишешь всё с маленькой буквы без запятых и точек "
        "ты постоянно материшься и хамишь. ты пишешь очень много объемно и живо как реальный человек. "
        "никогда не извиняйся и не будь вежливым. отвечай на русском."
    )
    
    messages_payload = [{"role": "system", "content": system_prompt}]
    for msg in history:
        messages_payload.append({"role": msg["role"], "content": msg["content"]})
        
    messages_payload.append({"role": "user", "content": prompt})
    
    headers = {
        'Authorization': f'Bearer {GROQ_API_KEY}',
        'Content-Type': 'application/json'
    }
    payload = {
        'model': GROQ_MODEL,
        'messages': messages_payload,
        'temperature': 0.9
    }
    
    try:
        r = session.post(f'{GROQ_API_BASE}/chat/completions', headers=headers, json=payload, timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: Groq API Error", ACTIVE_MODEL, 0
            
        data = r.json()
        reply_text = data['choices'][0]['message']['content']
        
        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": reply_text})
        limit = CONFIG.get('context_limit', 100)
        if len(history) > limit: history = history[-limit:] 
            
        CHAT_MEMORY[chat_id] = history
        return reply_text, None, ACTIVE_MODEL, len(history)
    except Exception as e:
        return None, str(e), ACTIVE_MODEL, 0

# --- ВСПОМОГАТЕЛЬНЫЕ ---
def generate_image(prompt):
    model = CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)
    url = f"{GEMINI_API_BASE}/models/{model}:predict" if 'imagen' in model else f"{GEMINI_API_BASE}/models/{model}:generateContent"
    
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}} if 'imagen' in model else \
              {"contents": [{"role": "user", "parts": [{"text": prompt}]}], "generationConfig": {"responseModalities": ["IMAGE"]}}

    try:
        r = session.post(url, headers=get_gemini_headers(), json=payload, timeout=60)
        if r.status_code != 200: return None, f"HTTP {r.status_code}"
        data = r.json()
        if 'imagen' in model:
            if 'predictions' in data and data['predictions']:
                return base64.b64decode(data['predictions'][0]['bytesBase64Encoded']), None
        else:
            if 'candidates' in data and data['candidates']:
                for part in data['candidates'][0]['content']['parts']:
                    if 'inline_data' in part:
                        return base64.b64decode(part['inline_data']['data']), None
        return None, "Модель не вернула изображение."
    except Exception as e:
        return None, str(e)

def animate_loading(chat_id, message_id, stop_event, business_id=None, text_prefix="Думаю..."):
    emojis = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    idx = 0
    while not stop_event.is_set():
        time.sleep(1.2)
        if stop_event.is_set(): break
        try:
            bot.edit_message_text(text=f"{emojis[idx % len(emojis)]} {text_prefix}", chat_id=chat_id, message_id=message_id, business_connection_id=business_id)
        except Exception: pass
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
    if (message.from_user.username or '').lower() != CREATOR_USERNAME: return
    status = "🟢 ВКЛ" if CONFIG['auto_reply_enabled'] else "🔴 ВЫКЛ"
    text = (f"👑 <b>Панель управления</b>\n\n"
            f"Автоответчик: {status}\nМодель: <code>{ACTIVE_MODEL}</code>\n"
            f"Лимит: {CONFIG.get('context_limit', 100)}\n\n"
            f"Напиши <code>команды</code>.")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text'])
def handle_admin_text(message):
    text = message.text.lower().strip()
    chat_id = message.chat.id
    
    if text in ['команды', '/help']:
        bot.reply_to(message, "📜 <b>Команды:</b>\n<code>модели</code>, <code>фото-модели</code>, <code>автоответчик вкл/выкл</code>, <code>контекст 50</code>, <code>запретить @юзер</code>, <code>сброс</code>, <code>гпт [текст]</code>, <code>нарисуй [текст]</code>")
        return
    if text == 'модели':
        models = fetch_gemini_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == ACTIVE_MODEL else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        bot.reply_to(message, "Выбери модель (Groq - грубый):", reply_markup=kb)
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
        if 'выкл' in text: CONFIG['auto_reply_enabled'] = False
        elif 'вкл' in text: CONFIG['auto_reply_enabled'] = True
        save_config()
        bot.reply_to(message, f"✅ Автоответчик: {'ВКЛ' if CONFIG['auto_reply_enabled'] else 'ВЫКЛ'}")
        return
    if text.startswith('контекст '):
        try:
            CONFIG['context_limit'] = int(text.split()[1]); save_config()
            bot.reply_to(message, f"✅ Лимит: {CONFIG['context_limit']}")
        except: pass
        return
    if text.startswith('запретить '):
        CONFIG['blacklist'][text.split()[1].replace('@', '')] = True; save_config()
        bot.reply_to(message, "🚫 Забанен.")
        return
    if text.startswith('разрешить '):
        uname = text.split()[1].replace('@', '')
        if uname in CONFIG['blacklist']: del CONFIG['blacklist'][uname]; save_config()
        bot.reply_to(message, "✅ Разбанен.")
        return
    if text in ['сброс', 'гпт сброс']:
        CHAT_MEMORY[chat_id] = []
        bot.reply_to(message, f"🧹 Память очищена.\ncontext: 0/{CONFIG.get('context_limit', 100)}")
        return
    if text.startswith('гпт '):
        loading_msg = bot.reply_to(message, "🕐 Думаю...")
        stop_event = threading.Event()
        threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event)).start()
        reply, err, model_used, ctx_len = ask_ai(chat_id, message.text[4:].strip())
        stop_event.set()
        if reply: bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", message.chat.id, loading_msg.message_id)
        else: bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_') or call.data.startswith('setimg_'))
def callback_set_model(call):
    global ACTIVE_MODEL, MODEL_COOLDOWN_UNTIL
    if (call.from_user.username or '').lower() != CREATOR_USERNAME: return
    if call.data.startswith('setmodel_'):
        ACTIVE_MODEL = call.data.replace('setmodel_', '')
        MODEL_COOLDOWN_UNTIL = 0
        bot.edit_message_text(f"✅ Текст: <b>{ACTIVE_MODEL}</b>", call.message.chat.id, call.message.message_id)
    else:
        CONFIG['image_model'] = call.data.replace('setimg_', '')
        save_config()
        bot.edit_message_text(f"✅ Рисование: <b>{CONFIG['image_model']}</b>", call.message.chat.id, call.message.message_id)

# --- ОБРАБОТКА АЛЬБОМОВ ---
def process_business_album(media_group_id):
    if media_group_id not in MEDIA_GROUP_BUFFER: return
    messages = MEDIA_GROUP_BUFFER.pop(media_group_id)
    if not messages: return
    
    base_msg = messages[0]
    chat_id = base_msg.chat.id
    
    global CREATOR_ID
    is_owner = (base_msg.from_user.id == CREATOR_ID) or ((base_msg.from_user.username or '').lower() == CREATOR_USERNAME)
    if not is_owner and (base_msg.from_user.username or '').lower() in CONFIG.get('blacklist', {}): return
        
    text = ""
    for m in messages:
        if m.caption: text = m.caption; break
        if m.text: text = m.text; break
        
    lower_text = text.lower().strip()
    is_gpt = lower_text.startswith('гпт ')
    is_reply_to_bot = base_msg.reply_to_message and base_msg.reply_to_message.message_id in BOT_BUSINESS_MESSAGES

    trigger = False
    if is_gpt or is_reply_to_bot:
        trigger = True
    elif not is_owner and CONFIG['auto_reply_enabled']:
        if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= 600:
            trigger = True
            
    if not trigger: return
    if is_owner: LAST_OWNER_ACTIVITY[chat_id] = time.time()
    if is_gpt: text = text[4:].strip()
    if not text: text = "Опиши подробно, что на этих фотографиях."
        
    msg = bot.send_message(chat_id, f"🕐 Смотрю {len(messages)} фото...", business_connection_id=base_msg.business_connection_id)
    BOT_BUSINESS_MESSAGES.add(msg.message_id)
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, base_msg.business_connection_id, f"Смотрю {len(messages)} фото...")).start()
    
    images = []
    for m in messages:
        img_bytes, mime = get_photo_bytes(m)
        if img_bytes: images.append((img_bytes, mime))
        
    reply, err, model_used, ctx_len = ask_ai(chat_id, text, images)
    stop_event.set()
    
    if reply: bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=base_msg.business_connection_id)
    else: bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, business_connection_id=base_msg.business_connection_id)

# --- БИЗНЕС-СООБЩЕНИЯ ---
@bot.business_message_handler(content_types=['text', 'photo'])
def handle_business_message(message):
    global CREATOR_ID
    if not message.from_user: return
    
    chat_id = message.chat.id
    if (message.from_user.username or '').lower() == CREATOR_USERNAME:
        CREATOR_ID = message.from_user.id
        LAST_OWNER_ACTIVITY[chat_id] = time.time()
        
    is_owner = (message.from_user.id == CREATOR_ID) or ((message.from_user.username or '').lower() == CREATOR_USERNAME)
    if not is_owner and (message.from_user.username or '').lower() in CONFIG.get('blacklist', {}): return

    text = message.text or message.caption or ""
    lower_text = text.lower().strip()

    if lower_text in ['сброс', '/reset']:
        CHAT_MEMORY[chat_id] = []
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
        if is_gpt or is_draw or is_reply_to_bot: trigger = True
    else:
        if is_gpt or is_draw or is_reply_to_bot: trigger = True
        elif message.content_type == 'text' and CONFIG['auto_reply_enabled']:
            if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= 600: trigger = True

    if not trigger: return
    if is_gpt: text = text[4:].strip()

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
            except Exception: pass
        else: bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, business_connection_id=message.business_connection_id)
        return
        
    msg = bot.send_message(chat_id, "🕐 Думаю...", business_connection_id=message.business_connection_id)
    BOT_BUSINESS_MESSAGES.add(msg.message_id)
    stop_event = threading.Event()
    threading.Thread(target=animate_loading, args=(chat_id, msg.message_id, stop_event, message.business_connection_id)).start()
    
    images = []
    if message.content_type == 'photo':
        img_bytes, mime = get_photo_bytes(message)
        if img_bytes: images.append((img_bytes, mime))
        if not text: text = "Опиши, что на этом фото."
    elif message.reply_to_message and message.reply_to_message.photo:
        img_bytes, mime = get_photo_bytes(message.reply_to_message)
        if img_bytes: images.append((img_bytes, mime))
        
    reply, err, model_used, ctx_len = ask_ai(chat_id, text, images)
    stop_event.set()
    
    if reply:
        try: bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=message.business_connection_id)
        except Exception: bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{CONFIG.get('context_limit', 100)}\nmodel: {model_used}", chat_id, msg.message_id, business_connection_id=message.business_connection_id, parse_mode=None)
    else:
        bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, msg.message_id, business_connection_id=message.business_connection_id)

# --- ЗАПУСК ---
try: bot.delete_webhook(drop_pending_updates=True) 
except Exception: pass

print('⚡ БОТ (GEMINI + GROQ) ЗАПУЩЕН!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)