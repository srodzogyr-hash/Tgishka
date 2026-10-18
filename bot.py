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

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get('TG_TOKEN')
GEMINI_KEY = os.environ.get('GROQ_KEY') # Сюда вставляем ключ от Google AI Studio
FIREBASE_URL = os.environ.get('FIREBASE_URL')

CREATOR_USERNAME = 'wrtnoq'
CREATOR_ID = None # Определится автоматически при первом сообщении от тебя

# По умолчанию
DEFAULT_MODEL = 'gemini-3.8-flash'
DEFAULT_CONTEXT_LIMIT = 100
GEMINI_API_URL = 'https://generativelanguage.googleapis.com/v1beta/models'

# Настройки в оперативке (синхронизируются с Firebase)
CONFIG = {
    'auto_reply_enabled': True,
    'context_limit': DEFAULT_CONTEXT_LIMIT,
    'active_model': DEFAULT_MODEL,
    'blacklist': {} # user_id -> True
}
LAST_OWNER_ACTIVITY = {} # chat_id -> timestamp
CHAT_MEMORY = {} # chat_id -> list of messages

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)
session = requests.Session()

# --- БАЗА ДАННЫХ (FIREBASE) ---
def init_firebase():
    if not FIREBASE_URL:
        print("⚠️ Firebase не настроен, работаем в памяти.")
        return
    try:
        r = session.get(f'{FIREBASE_URL}/config.json', timeout=10)
        if r.status_code == 200 and r.json():
            CONFIG.update(r.json())
            print("🔥 Настройки загружены из Firebase.")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки Firebase: {e}")

def save_config():
    if not FIREBASE_URL:
        return
    try:
        session.patch(f'{FIREBASE_URL}/config.json', json=CONFIG, timeout=5)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения в Firebase: {e}")

init_firebase()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def is_creator(user):
    if not user:
        return False
    uname = (user.username or '').lower().lstrip('@')
    return uname == CREATOR_USERNAME or user.id == CREATOR_ID

def clean_name(user):
    if is_creator(user): return "Создатель"
    return html.escape(user.first_name or "Пользователь")

# --- ИНТЕГРАЦИЯ С GEMINI ---
def ask_gemini_text(chat_id, prompt, system_prompt=None):
    model = CONFIG.get('active_model', DEFAULT_MODEL)
    url = f"{GEMINI_API_URL}/{model}:generateContent?key={GEMINI_KEY}"
    
    contents = []
    # Загружаем историю
    history = CHAT_MEMORY.get(chat_id, [])
    for msg in history:
        contents.append(msg)
    
    # Добавляем текущий запрос
    contents.append({"role": "user", "parts": [{"text": prompt}]})
    
    payload = {"contents": contents}
    if system_prompt:
        payload["system_instruction"] = {"parts": [{"text": system_prompt}]}
        
    try:
        r = session.post(url, json=payload, timeout=30)
        if r.status_code != 200:
            return None, f"Ошибка API: {r.status_code} - {r.text[:100]}"
        
        data = r.json()
        if not data.get('candidates'):
            return None, "Модель вернула пустой ответ (возможно, сработал фильтр безопасности)."
            
        reply_text = data['candidates'][0]['content']['parts'][0]['text']
        
        # Сохраняем в память
        history.append({"role": "user", "parts": [{"text": prompt}]})
        history.append({"role": "model", "parts": [{"text": reply_text}]})
        
        # Обрезаем контекст
        limit = CONFIG.get('context_limit', DEFAULT_CONTEXT_LIMIT)
        if len(history) > limit:
            history = history[-limit:]
        CHAT_MEMORY[chat_id] = history
        
        return reply_text, None
    except Exception as e:
        return None, f"Сетевая ошибка: {e}"

def ask_gemini_vision(chat_id, prompt, image_bytes, mime_type='image/jpeg'):
    model = CONFIG.get('active_model', DEFAULT_MODEL)
    url = f"{GEMINI_API_URL}/{model}:generateContent?key={GEMINI_KEY}"
    
    b64_img = base64.b64encode(image_bytes).decode('utf-8')
    
    contents = [
        {"role": "user", "parts": [
            {"text": prompt or "Опиши, что на этом изображении."},
            {"inline_data": {"mime_type": mime_type, "data": b64_img}}
        ]}
    ]
    
    payload = {"contents": contents}
    try:
        r = session.post(url, json=payload, timeout=45)
        if r.status_code != 200:
            return None, f"Ошибка Vision: {r.status_code}"
        data = r.json()
        return data['candidates'][0]['content']['parts'][0]['text'], None
    except Exception as e:
        return None, str(e)

def generate_image(prompt):
    # Используем Imagen 4 для генерации картинок
    model = 'imagen-4.0-generate-001'
    url = f"{GEMINI_API_URL}/{model}:predict?key={GEMINI_KEY}"
    payload = {"instances": [{"prompt": prompt}], "parameters": {"sampleCount": 1}}
    try:
        r = session.post(url, json=payload, timeout=60)
        if r.status_code != 200:
            return None, f"Ошибка Imagen: {r.status_code} - {r.text[:100]}"
        data = r.json()
        img_b64 = data['predictions'][0]['bytesBase64Encoded']
        return base64.b64decode(img_b64), None
    except Exception as e:
        return None, str(e)

# --- ПАНЕЛЬ УПРАВЛЕНИЯ (В ЛС) ---
@bot.message_handler(commands=['start', 'panel'])
def send_panel(message):
    if not is_creator(message.from_user): return
    
    kb = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    kb.add('📊 Статус', '⚙️ Автоответчик')
    kb.add('🧠 Модель', '📝 Контекст')
    kb.add('🚫 Черный список')
    
    status = "ВКЛ" if CONFIG['auto_reply_enabled'] else "ВЫКЛ"
    text = (f"👑 **Панель управления секретарем**\n\n"
            f"🤖 **Автоответчик:** {status}\n"
            f"⏱ **Таймер:** отвечает только если тебя нет > 10 мин.\n"
            f"🧠 **Модель:** `{CONFIG['active_model']}`\n"
            f"📝 **Лимит контекста:** {CONFIG['context_limit']} сообщений\n\n"
            f"Отправляй команды или используй кнопки ниже.")
    bot.send_message(message.chat.id, text, reply_markup=kb, parse_mode='Markdown')

@bot.message_handler(func=lambda m: m.chat.type == 'private' and is_creator(m.from_user), content_types=['text'])
def handle_admin_commands(message):
    text = message.text.lower()
    
    if text == '📊 статус' or text == 'статус':
        status = "ВКЛ" if CONFIG['auto_reply_enabled'] else "ВЫКЛ"
        bot.reply_to(message, f"Автоответчик: **{status}**\nМодель: `{CONFIG['active_model']}`\nКонтекст: {CONFIG['context_limit']}", parse_mode='Markdown')
        
    elif text == '⚙️ автоответчик' or 'автоответчик' in text:
        if 'вкл' in text or 'on' in text or text == '⚙️ автоответчик':
            CONFIG['auto_reply_enabled'] = not CONFIG['auto_reply_enabled']
        elif 'выкл' in text or 'off' in text:
            CONFIG['auto_reply_enabled'] = False
            
        save_config()
        status = "включен" if CONFIG['auto_reply_enabled'] else "выключен"
        bot.reply_to(message, f"✅ Автоответчик теперь **{status}**.")
        
    elif text.startswith('контекст '):
        try:
            val = int(text.split()[1])
            CONFIG['context_limit'] = val
            save_config()
            bot.reply_to(message, f"✅ Лимит контекста изменен на **{val}** сообщений.")
        except:
            bot.reply_to(message, "⚠️ Напиши число, например: `контекст 50`")
            
    elif text.startswith('запретить '):
        uname = text.split()[1].replace('@', '')
        CONFIG['blacklist'][uname] = True
        save_config()
        bot.reply_to(message, f"🚫 Пользователь @{uname} заблокирован.")
        
    elif text.startswith('разрешить '):
        uname = text.split()[1].replace('@', '')
        if uname in CONFIG['blacklist']:
            del CONFIG['blacklist'][uname]
            save_config()
            bot.reply_to(message, f"✅ Пользователь @{uname} разблокирован.")
            
    elif text == '🚫 черный список':
        if not CONFIG['blacklist']:
            bot.reply_to(message, "Список пуст.")
        else:
            lst = "\n".join([f"@{u}" for u in CONFIG['blacklist']])
            bot.reply_to(message, f"Заблокированы:\n{lst}")
            
    elif text.startswith('модель '):
        new_model = text.split(' ', 1)[1]
        CONFIG['active_model'] = new_model
        save_config()
        bot.reply_to(message, f"✅ Модель изменена на `{new_model}`", parse_mode='Markdown')

# --- ОСНОВНОЙ ОБРАБОТЧИК БИЗНЕС-СООБЩЕНИЙ ---
@bot.business_message_handler(content_types=['text', 'photo'])
def handle_business_message(message):
    global CREATOR_ID
    if not message.from_user: return
    
    # Запоминаем ID создателя
    if is_creator(message.from_user):
        CREATOR_ID = message.from_user.id
    
    chat_id = message.chat.id
    
    # 1. Если пишет сам владелец — обновляем таймер активности и выходим
    if message.from_user.id == CREATOR_ID:
        LAST_OWNER_ACTIVITY[chat_id] = time.time()
        return
        
    # 2. Проверка на бан
    user_uname = (message.from_user.username or '').lower()
    if user_uname in CONFIG['blacklist']:
        return # Игнорируем
        
    # 3. Логика вызова
    text = message.text or ""
    is_gpt_call = text.lower().startswith('гпт ')
    
    # Если автоответчик выключен и это не вызов "гпт", игнорируем
    if not CONFIG['auto_reply_enabled'] and not is_gpt_call:
        return
        
    # Если автоответчик включен, проверяем таймер (10 минут = 600 секунд)
    if CONFIG['auto_reply_enabled']:
        last_active = LAST_OWNER_ACTIVITY.get(chat_id, 0)
        if time.time() - last_active < 600:
            # Владелец недавно был в сети, не мешаем ему
            if not is_gpt_call:
                return
                
    # Убираем префикс "гпт " если есть
    if is_gpt_call:
        text = text[4:].strip()
        
    system_prompt = "Ты — умный и вежливый секретарь Славы Огурцова. Отвечай кратко, по делу, от первого лица. Никогда не говори, что ты ИИ."

    # 4. Обработка текста или фото
    if message.content_type == 'photo':
        try:
            file_info = bot.get_file(message.photo[-1].file_id)
            downloaded_file = bot.download_file(file_info.file_path)
            reply, err = ask_gemini_vision(chat_id, text, downloaded_file)
        except Exception as e:
            reply, err = None, str(e)
    else:
        # Проверка на генерацию картинки
        if text.lower().startswith('нарисуй '):
            prompt = text[8:]
            bot.send_chat_action(chat_id, 'upload_photo', business_connection_id=message.business_connection_id)
            img_bytes, err = generate_image(prompt)
            if not err:
                bot.send_photo(chat_id, img_bytes, business_connection_id=message.business_connection_id)
            else:
                bot.send_message(chat_id, f"Не смог нарисовать: {err}", business_connection_id=message.business_connection_id)
            return
            
        bot.send_chat_action(chat_id, 'typing', business_connection_id=message.business_connection_id)
        reply, err = ask_gemini_text(chat_id, text, system_prompt)
        
    if err:
        print(f"Ошибка ИИ: {err}")
        return
        
    # Отправляем ответ
    try:
        bot.send_message(chat_id, reply, business_connection_id=message.business_connection_id)
    except Exception as e:
        print(f"Ошибка отправки: {e}")

print('⚡ БОТ-СЕКРЕТАРЬ (GEMINI) ЗАПУЩЕН!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)