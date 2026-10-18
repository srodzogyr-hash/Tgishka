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
DEFAULT_IMAGE_MODEL = 'imagen-3.0-generate-001'
DEFAULT_CONTEXT_LIMIT = 100
GEMINI_API_BASE = 'https://generativelanguage.googleapis.com/v1beta'

CONFIG = {
    'auto_reply_enabled': True,
    'context_limit': DEFAULT_CONTEXT_LIMIT,
    'active_model': DEFAULT_MODEL,
    'image_model': DEFAULT_IMAGE_MODEL,
    'blacklist': {}
}
LAST_OWNER_ACTIVITY = {}
CHAT_MEMORY = {}

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)
session = requests.Session()

# --- АНИМАЦИЯ ЗАГРУЗКИ ---
def animate_loading(chat_id, message_id, stop_event, business_id=None, text_prefix="Думаю..."):
    emojis = ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"]
    idx = 0
    while not stop_event.is_set():
        time.sleep(1.2)
        if stop_event.is_set(): break
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
        "system_instruction": {"parts": [{"text": "Ты — умный и полезный ИИ-ассистент. Отвечай на вопросы пользователя. Если на фото есть текст или объекты, опиши их."}]}
    }
    
    try:
        r = session.post(url, json=payload, timeout=45)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}: {r.text[:300]}", 0
        
        data = r.json()
        if 'candidates' not in data or not data['candidates']:
            return None, "Модель заблокировала ответ (Safety Filter).", 0
            
        reply_text = data['candidates'][0]['content']['parts'][0]['text']
        
        contents.append({"role": "model", "parts": [{"text": reply_text}]})
        
        limit = CONFIG.get('context_limit', 100)
        if len(contents) > limit:
            contents = contents[-limit:] 
            
        CHAT_MEMORY[chat_id] = contents
        return reply_text, None, len(contents)
    except Exception as e:
        return None, str(e), 0

def generate_image(prompt):
    model = CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)
    
    if 'imagen' in model:
        url = f"{GEMINI_API_BASE}/models/{model}:predict?key={GEMINI_KEY}"
        payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
    else:
        url = f"{GEMINI_API_BASE}/models/{model}:generateContent?key={GEMINI_KEY}"
        payload = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"responseModalities": ["IMAGE"]}
        }

    try:
        r = session.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            return None, f"HTTP {r.status_code}\n{r.text[:300]}"
            
        data = r.json()
        if 'imagen' in model:
            if 'predictions' in data and data['predictions']:
                return base64.b64decode(data['predictions'][0]['bytesBase64Encoded']), None
        else:
            if 'candidates' in data and data['candidates']:
                for part in data['candidates'][0]['content']['parts']:
                    if 'inline_data' in part:
                        return base64.b64decode(part['inline_data']['data']), None
                        
        return None, f"Модель не вернула изображение.\nОтвет API: {str(data)[:300]}"
    except Exception as e:
        return None, str(e)

# --- ПАНЕЛЬ УПРАВЛЕНИЯ (В ЛС) ---
@bot.message_handler(commands=['start', 'panel'])
def send_panel(message):
    if (message.from_user.username or '').lower() != CREATOR_USERNAME: return
    
    status = "🟢 ВКЛ" if CONFIG['auto_reply_enabled'] else "🔴 ВЫКЛ"
    limit = CONFIG.get('context_limit', 100)
    text = (f"👑 <b>Панель управления</b>\n\n"
            f"Автоответчик: {status}\n"
            f"Текстовая модель: <code>{CONFIG['active_model']}</code>\n"
            f"Модель рисования: <code>{CONFIG.get('image_model', DEFAULT_IMAGE_MODEL)}</code>\n"
            f"Лимит контекста: {limit} соо\n\n"
            f"Напиши <code>команды</code> для списка всех функций.")
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (m.from_user.username or '').lower() == CREATOR_USERNAME, content_types=['text', 'photo'])
def handle_admin_text(message):
    text = message.text or message.caption or ""
    lower_text = text.lower().strip()
    chat_id = message.chat.id
    
    if lower_text in ['команды', '/help']:
        limit = CONFIG.get('context_limit', 100)
        ctx_now = len(CHAT_MEMORY.get(chat_id, []))
        status = "ВКЛ" if CONFIG['auto_reply_enabled'] else "ВЫКЛ"
        help_text = (
            f"📜 <b>Список команд:</b>\n\n"
            f"<code>модели</code> — список текстовых моделей\n"
            f"<code>фото-модели</code> — список моделей для рисования\n"
            f"<code>автоответчик вкл/выкл</code> — автоответ (сейчас {status})\n"
            f"<code>контекст [число]</code> — лимит (сейчас {limit})\n"
            f"<code>запретить @юзер</code> — заблокировать вызов ИИ\n"
            f"<code>разрешить @юзер</code> — разблокировать\n"
            f"<code>сброс</code> — очистить память (сейчас {ctx_now}/{limit})\n"
            f"<code>гпт [текст]</code> — вызов ИИ\n"
            f"<code>нарисуй [текст]</code> — сгенерировать фото\n"
            f"<i>Для анализа фото: отправь его с подписью</i> <code>гпт опиши</code>"
        )
        bot.reply_to(message, help_text)
        return

    if lower_text == 'модели':
        models = fetch_gemini_models()
        kb = InlineKeyboardMarkup(row_width=1)
        for m in models:
            mark = "✅ " if m == CONFIG['active_model'] else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setmodel_{m}"))
        bot.reply_to(message, f"Текстовые модели (всего {len(models)}):", reply_markup=kb)
        return

    if lower_text in ['фото-модели', 'фото модели']:
        image_models = ['imagen-3.0-generate-001', 'gemini-2.5-flash-image', 'gemini-3.1-flash-image']
        kb = InlineKeyboardMarkup(row_width=1)
        for m in image_models:
            mark = "✅ " if m == CONFIG.get('image_model') else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setimg_{m}"))
        bot.reply_to(message, "Выбери модель для генерации картинок:", reply_markup=kb)
        return

    if lower_text.startswith('автоответчик'):
        if 'выкл' in lower_text: CONFIG['auto_reply_enabled'] = False
        elif 'вкл' in lower_text: CONFIG['auto_reply_enabled'] = True
        save_config()
        bot.reply_to(message, f"✅ Автоответчик: {'ВКЛ' if CONFIG['auto_reply_enabled'] else 'ВЫКЛ'}")
        return
        
    if lower_text.startswith('контекст '):
        try:
            val = int(lower_text.split()[1])
            CONFIG['context_limit'] = val
            save_config()
            bot.reply_to(message, f"✅ Лимит контекста: {val}")
        except:
            bot.reply_to(message, "⚠️ Напиши число: <code>контекст 50</code>")
        return

    if lower_text.startswith('запретить '):
        uname = lower_text.split()[1].replace('@', '')
        CONFIG['blacklist'][uname] = True
        save_config()
        bot.reply_to(message, f"🚫 @{uname} больше не может вызывать ИИ.")
        return
        
    if lower_text.startswith('разрешить '):
        uname = lower_text.split()[1].replace('@', '')
        if uname in CONFIG['blacklist']:
            del CONFIG['blacklist'][uname]
            save_config()
            bot.reply_to(message, f"✅ @{uname} разблокирован.")
        return

    if lower_text in ['сброс', 'гпт сброс']:
        CHAT_MEMORY[chat_id] = []
        limit = CONFIG.get('context_limit', 100)
        bot.reply_to(message, f"🧹 Память очищена.\ncontext: 0/{limit}")
        return

    is_reply_to_bot = message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id == bot.get_me().id
    is_gpt = lower_text.startswith('гпт ') or lower_text.startswith('алл ')
    is_draw = lower_text.startswith('нарисуй ')
    
    # Защита от реагирования на всё подряд в ЛС
    if message.content_type == 'photo' and not is_gpt and not is_reply_to_bot:
        bot.reply_to(message, "⚠️ Чтобы ИИ посмотрел фото, отправь его с подписью <code>гпт опиши</code> или свайпни на сообщение бота.")
        return
        
    if message.content_type == 'text' and not (is_gpt or is_draw or is_reply_to_bot):
        return

    # --- ОБРАБОТКА ФОТО В ЛС ---
    if message.content_type == 'photo':
        loading_msg = bot.reply_to(message, "🕐 Смотрю фото...")
        stop_event = threading.Event()
        anim = threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event, None, "Смотрю фото..."))
        anim.start()
        
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            img_bytes = bot.download_file(file_info.file_path)
            prompt = text.replace('гпт ', '').replace('алл ', '').strip() if is_gpt else (message.caption or "Опиши, что на этом фото.")
            reply, err, ctx_len = ask_gemini(chat_id, prompt, img_bytes, 'image/jpeg')
        except Exception as e:
            reply, err, ctx_len = None, str(e), 0
            
        stop_event.set()
        anim.join()
        
        limit = CONFIG.get('context_limit', 100)
        if reply:
            bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{limit}", message.chat.id, loading_msg.message_id)
        else:
            bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

    # --- ГЕНЕРАЦИЯ КАРТИНКИ В ЛС ---
    if is_draw:
        prompt = text[8:]
        loading_msg = bot.reply_to(message, "🕐 Рисую...")
        stop_event = threading.Event()
        anim = threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event, None, "Рисую..."))
        anim.start()
        
        img, err = generate_image(prompt)
        stop_event.set()
        anim.join()
        
        if img:
            try:
                bot.delete_message(message.chat.id, loading_msg.message_id)
                bot.send_photo(message.chat.id, img)
            except Exception as e:
                bot.edit_message_text(f"⚠️ Ошибка отправки: {e}", message.chat.id, loading_msg.message_id)
        else:
            bot.edit_message_text(f"⚠️ Ошибка генерации:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

    # --- ТЕКСТОВЫЙ ЗАПРОС В ЛС ---
    if is_gpt or is_reply_to_bot:
        prompt = text[4:].strip() if is_gpt else text
        loading_msg = bot.reply_to(message, "🕐 Думаю...")
        stop_event = threading.Event()
        anim = threading.Thread(target=animate_loading, args=(message.chat.id, loading_msg.message_id, stop_event))
        anim.start()
        
        reply, err, ctx_len = ask_gemini(chat_id, prompt)
        
        stop_event.set()
        anim.join()
        
        limit = CONFIG.get('context_limit', 100)
        if reply:
            bot.edit_message_text(f"{reply}\n\ncontext: {ctx_len}/{limit}", message.chat.id, loading_msg.message_id)
        else:
            bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", message.chat.id, loading_msg.message_id)
        return

@bot.callback_query_handler(func=lambda call: call.data.startswith('setmodel_') or call.data.startswith('setimg_'))
def callback_set_model(call):
    if (call.from_user.username or '').lower() != CREATOR_USERNAME: return
    
    if call.data.startswith('setmodel_'):
        model = call.data.replace('setmodel_', '')
        CONFIG['active_model'] = model
        save_config()
        bot.edit_message_text(f"✅ Текстовая модель: <b>{model}</b>", call.message.chat.id, call.message.message_id)
    else:
        model = call.data.replace('setimg_', '')
        CONFIG['image_model'] = model
        save_config()
        bot.edit_message_text(f"✅ Модель рисования: <b>{model}</b>", call.message.chat.id, call.message.message_id)

# --- БИЗНЕС-СООБЩЕНИЯ ---
@bot.business_message_handler(content_types=['text', 'photo'])
def handle_business_message(message):
    global CREATOR_ID
    if not message.from_user: return
    if message.from_user.id == bot.get_me().id: return 
    
    chat_id = message.chat.id
    text = message.text or message.caption or ""
    lower_text = text.lower().strip()
    
    is_owner = (message.from_user.username or '').lower() == CREATOR_USERNAME
    is_gpt = lower_text.startswith('гпт ')
    is_draw = lower_text.startswith('нарисуй ')
    
    # ИСПРАВЛЕНИЕ: Проверяем, что реплай именно на сообщение БОТА
    is_reply_to_bot = False
    if message.reply_to_message and message.reply_to_message.from_user:
        is_reply_to_bot = (message.reply_to_message.from_user.id == bot.get_me().id)
    
    user_uname = (message.from_user.username or '').lower()
    if not is_owner and user_uname in CONFIG.get('blacklist', {}):
        return

    trigger = False
    if is_gpt or is_draw or is_reply_to_bot:
        trigger = True
    elif is_owner:
        pass # Владелец должен использовать триггеры
    else:
        # Автоответ для собеседников (только для текста!)
        if message.content_type == 'text' and CONFIG['auto_reply_enabled']:
            if time.time() - LAST_OWNER_ACTIVITY.get(chat_id, 0) >= 600:
                trigger = True
                
    # Если ни одно условие не сработало — просто игнорируем
    if not trigger:
        return

    if is_owner:
        CREATOR_ID = message.from_user.id
        LAST_OWNER_ACTIVITY[chat_id] = time.time()

    if is_gpt:
        text = text[4:].strip() 

    if text.lower() in ['сброс', '/reset']:
        CHAT_MEMORY[chat_id] = []
        limit = CONFIG.get('context_limit', 100)
        bot.send_message(chat_id, f"Память очищена.\ncontext: 0/{limit}", business_connection_id=message.business_connection_id)
        return

    # --- ГЕНЕРАЦИЯ В БИЗНЕС-ЧАТЕ ---
    if is_draw:
        loading_msg = bot.send_message(chat_id, "🕐 Рисую...", business_connection_id=message.business_connection_id)
        stop_event = threading.Event()
        anim = threading.Thread(target=animate_loading, args=(chat_id, loading_msg.message_id, stop_event, message.business_connection_id, "Рисую..."))
        anim.start()
        
        img, err = generate_image(text[8:])
        stop_event.set()
        anim.join()
        
        if img:
            try:
                bot.delete_message(chat_id, loading_msg.message_id, business_connection_id=message.business_connection_id)
                bot.send_photo(chat_id, img, business_connection_id=message.business_connection_id)
            except Exception: pass
        else:
            bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id, business_connection_id=message.business_connection_id)
        return
        
    # --- ТЕКСТ ИЛИ ПРОСМОТР ФОТО В БИЗНЕС-ЧАТЕ ---
    loading_msg = bot.send_message(chat_id, "🕐 Думаю...", business_connection_id=message.business_connection_id)
    stop_event = threading.Event()
    anim = threading.Thread(target=animate_loading, args=(chat_id, loading_msg.message_id, stop_event, message.business_connection_id))
    anim.start()
    
    img_bytes, mime = None, None
    if message.content_type == 'photo':
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            img_bytes = bot.download_file(file_info.file_path)
            mime = 'image/jpeg'
            if not text: text = "Опиши, что на этом фото."
        except Exception: pass
        
    reply, err, ctx_len = ask_gemini(chat_id, text, img_bytes, mime)
    
    stop_event.set()
    anim.join()
    
    limit = CONFIG.get('context_limit', 100)
    if reply:
        final_text = f"{reply}\n\ncontext: {ctx_len}/{limit}"
        try:
            bot.edit_message_text(final_text, chat_id, loading_msg.message_id, business_connection_id=message.business_connection_id)
        except Exception:
            bot.edit_message_text(final_text, chat_id, loading_msg.message_id, business_connection_id=message.business_connection_id, parse_mode=None)
    else:
        bot.edit_message_text(f"⚠️ Ошибка:\n<code>{html.escape(err)}</code>", chat_id, loading_msg.message_id, business_connection_id=message.business_connection_id)

# --- ЗАПУСК ---
print("--> Очистка старых сессий...")
try:
    bot.delete_webhook(drop_pending_updates=True) 
except Exception:
    pass

print('⚡ БОТ (GEMINI) ЗАПУЩЕН!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)