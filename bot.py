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

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get('TG_TOKEN')
GEMINI_KEY = os.environ.get('GROQ_KEY')
FIREBASE_URL = os.environ.get('FIREBASE_URL')

CREATOR_USERNAME = 'wrtnoq'
CREATOR_ID = None

DEFAULT_MODEL = 'gemini-3.8-flash' 
DEFAULT_CONTEXT_LIMIT = 100
GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'

CONFIG = {
    'auto_reply_enabled': True,
    'context_limit': DEFAULT_CONTEXT_LIMIT,
    'active_model': DEFAULT_MODEL,
    'blacklist': {}
}
LAST_OWNER_ACTIVITY = {}
CHAT_MEMORY = {}

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)
session = requests.Session()

# --- АНИМАЦИЯ ЗАГРУЗКИ ---
def animate_loading(chat_id, message_id, stop_event, business_id=None):
    emojis = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    idx = 0
    while not stop_event.is_set():
        time.sleep(1.2)
        if stop_event.is_set(): break
        try:
            bot.edit_message_text(
                text=f"{emojis[idx % len(emojis)]} Думаю...",
                chat_id=chat_id,
                message_id=message_id,
                business_connection_id=business_id
            )
        except Exception:
            pass
        idx += 1

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

# --- GEMINI API ---
def fetch_gemini_models():
    url = f"{GEMINI_API_BASE}/models?key={GEMINI_KEY}"
    try:
        r = session.get(url, timeout=10)
        if r.status_code == 200:
            models = []
            for m in r.json().get('models', []):
                if 'generateContent' in m.get('supportedGenerationMethods', []):
                    models.append(m['name'].replace('models/', ''))
            return models
        else:
            print(f"ListModels error: {r.text[:200]}")
    except Exception as e:
        print(f"Ошибка получения моделей: {e}")
    return [DEFAULT_MODEL]

def ask_gemini(chat_id, prompt, image_bytes=None, mime_type=None):
    model = CONFIG.get('active_model', DEFAULT_MODEL)
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={GEMINI_KEY}"
    
    contents = CHAT_MEMORY.get(chat_id, []).copy()
    
    parts = []
    if prompt: parts.append({"text": prompt})
    if image_bytes:
        parts.append({"inline_data": {"mime_type": mime_type, "data": base64.b64encode(image_bytes).decode('utf-8')}})
        
    contents.append({"role": "user", "parts": parts})
    
    payload = {
        "contents": contents,
        "system_instruction": {"parts": [{"text": "Ты — умный секретарь Славы. Отвечай кратко, по делу. Не говори, что ты ИИ, если не спросят напрямую."}]}
    }
    
    try:
        r = session.post(url, json=payload, timeout=40)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
        
        data = r.json()
        if 'candidates' not in data or not data['candidates']:
            return None, "Модель заблокировала ответ (Safety Filter)."
            
        reply_text = data['candidates'][0]['content']['parts'][0]['text']
        
        contents.append({"role": "model", "parts": [{"text": reply_text}]})
        limit = CONFIG.get('context_limit', 100)
        CHAT_MEMORY[chat_id] = contents[-limit:] if len(contents) > limit else contents
        
        return reply_text, None
    except Exception as e:
        return None, str(e)

def generate_image(prompt):
    model = 'gemini-2.5-flash-image'
    url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"responseModalities": ["IMAGE"]}
    }
    try:
        r = session.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:200]}"
        data = r.json()
        for part in data['candidates'][0]['content']['parts']:
            if 'inline_data' in part:
                return base64.b64decode(part['inline_data']['data']), None
        return None, "Модель не вернула изображение."
    except Exception as e:
        return None, str(e)

# --- ПАНЕЛЬ УПРАВЛЕНИЯ ---
@bot.message_handler(commands=['start', 'panel'])
def send_panel(message):
    if not (message.from_user.username or '').lower() == CREATOR_USERNAME: return
    
    status = "🟢 ВКЛ" if CONFIG['auto_reply_enabled'] else "🔴 ВЫКЛ"
    text = (f"👑 <b>Панель управления</b>\n\n"
            f"Автоответчик: {status}\n"
            f"Модель: <code>{CONFIG['active_model']}</code>\n"
            f"Контекст: {CONFIG['context_limit']} соо\n\n"
            f"Для теста напиши в этот чат: <code>гпт привет</code>")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text'])
def handle_admin_text(message):
    text = message.text.lower().strip()
    
    if text == 'модели':
        models = fetch_gemini_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == CONFIG['active_model'] else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        bot.reply_to(message, f"Доступные модели (всего {len(models)}):", reply_markup=kb)
        return

    if text.startswith('автоответчик'):
        if 'выкл' in text: CONFIG['auto_reply_enabled'] = False
        elif 'вкл' in text: CONFIG['auto_reply_enabled'] = True
        save_config()
        bot.reply_to(message, f"✅ Автоответчик: {'ВКЛ' if CONFIG['auto_reply_enabled'] else 'ВЫКЛ'}")
        return
        
    if text.startswith('контекст '):
        try:
            val = int(text.split()[1])
            CONFIG['context_limit'] = val
            save_config()
            bot.reply_to(message, f"✅ Лимит контекста: {val}")
        except:
            bot.reply_to(message, "⚠️ Напиши число: <code>контекст 50</code>")
        return
        
    if text.startswith('запретить '):
        uname = text.split()[1].replace('@', '')
        CONFIG['blacklist'][uname] = True
        save_config()
        bot.reply_to(message, f"🚫 @{uname} заблокирован.")
        return
        
    if text.startswith('разрешить '):
        uname = text.split()[1].replace('@', '')
        if uname in CONFIG['blacklist']:
            del CONFIG['blacklist'][uname]
            save_config()
            bot.reply_to(message, f"✅ @{uname} разблокирован.")
        return

    # --- ТЕСТОВЫЙ РЕЖИМ В ЛИЧКЕ БОТА ---
    if text.startswith('гпт '):
        prompt = message.text[4:].strip()
        loading_msg = bot.reply_to(message, "🕐 Думаю...")
        stop_event = threading.Event()
        anim = threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event))
        anim.start()
        
        reply, err = ask_gemini(message.chat.id, prompt)
        
        stop_event.set()
        anim.join()
        
        if reply:
            bot.edit_message_text(reply, message.chat.id, loading_msg.message_id)
        else:
            bot.edit_message_text(f"⚠️ Ошибка API:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_'))
def callback_set_model(call):
    if not (call.from_user.username or '').lower() == CREATOR_USERNAME: return
    model = call.data.replace('setmodel_', '')
    CONFIG['active_model'] = model
    save_config()
    bot.edit_message_text(f"✅ Активная модель: <b>{model}</b>", call.message.chat.id, call.message.message_id)

# --- СЕКРЕТАРЬ (ДЛЯ РЕАЛЬНЫХ КЛИЕНТОВ) ---
@bot.business_message_handler(content_types=['text', 'photo'])
def handle_business_message(message):
    global CREATOR_ID
    if not message.from_user: return
    
    if (message.from_user.username or '').lower() == CREATOR_USERNAME:
        CREATOR_ID = message.from_user.id
        LAST_OWNER_ACTIVITY[message.chat.id] = time.time()
        return
        
    if (message.from_user.username or '').lower() in CONFIG['blacklist']:
        return
        
    text = message.text or ""
    is_gpt = text.lower().startswith('гпт ')
    
    if not CONFIG['auto_reply_enabled'] and not is_gpt:
        return
        
    if CONFIG['auto_reply_enabled'] and not is_gpt:
        if time.time() - LAST_OWNER_ACTIVITY.get(message.chat.id, 0) < 600:
            return
            
    if is_gpt: text = text[4:].strip()
    
    if text.lower().startswith('нарисуй '):
        loading_msg = bot.send_message(message.chat.id, "🕐 Рисую...", business_connection_id=message.business_connection_id)
        stop_event = threading.Event()
        anim = threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event, message.business_connection_id))
        anim.start()
        
        img, err = generate_image(text[8:])
        stop_event.set()
        anim.join()
        
        if img:
            try:
                bot.delete_message(message.chat.id, loading_msg.message_id, business_connection_id=message.business_connection_id)
                bot.send_photo(message.chat.id, img, business_connection_id=message.business_connection_id)
            except Exception: pass
        else:
            bot.edit_message_text(f"⚠️ Ошибка: {err}", message.chat.id, loading_msg.message_id, business_connection_id=message.business_connection_id, parse_mode=None)
        return
        
    loading_msg = bot.send_message(message.chat.id, "🕐 Думаю...", business_connection_id=message.business_connection_id)
    stop_event = threading.Event()
    anim = threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event, message.business_connection_id))
    anim.start()
    
    img_bytes, mime = None, None
    if message.content_type == 'photo':
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            img_bytes = bot.download_file(file_info.file_path)
            mime = 'image/jpeg'
        except Exception: pass
        
    reply, err = ask_gemini(message.chat.id, text, img_bytes, mime)
    
    stop_event.set()
    anim.join()
    
    if reply:
        try:
            bot.edit_message_text(reply, message.chat.id, loading_msg.message_id, business_connection_id=message.business_connection_id)
        except Exception:
            bot.edit_message_text(reply, message.chat.id, loading_msg.message_id, business_connection_id=message.business_connection_id, parse_mode=None)
    else:
        bot.edit_message_text(f"⚠️ Ошибка API:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id, business_connection_id=message.business_connection_id)

# --- ЗАПУСК ---
print("--> Очистка старых сессий...")
try:
    # Лечит ошибку 409 при деплое на Render
    bot.delete_webhook(drop_pending_updates=True) 
except Exception:
    pass

print('⚡ БОТ (GEMINI) ЗАПУЩЕН!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)