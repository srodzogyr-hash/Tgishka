import html
import os
import re
import threading
import time
import requests

try:
  import telebot
except ImportError:
  os.system('pip install pyTelegramBotAPI requests')
  import telebot

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- НАСТРОЙКИ ---
TOKEN = os.environ.get('TG_TOKEN')
GROQ_KEY = os.environ.get('GROQ_KEY')

DEFAULT_MODEL = 'llama-3.3-70b-versatile'
GROQ_BASE = 'https://api.groq.com/openai/v1'
CREATOR_USERNAMES = ('wrtnoq', 'wtnoq')

MAX_CONTEXT_MESSAGES = 20
CHAT_MEMORY = {}
CACHED_FREE_MODELS = []
ACTIVE_MODEL = DEFAULT_MODEL

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)
ai_session = requests.Session()


# --- ПРИНУДИТЕЛЬНАЯ ОЧИСТКА СТАРЫХ СЕССИЙ (ЛЕЧИТ ОШИБКУ 409) ---
def clear_old_sessions():
    """Удаляет вебхук и сбрасывает старые getUpdates, чтобы Telegram отпустил токен."""
    url = f"https://api.telegram.org/bot{TOKEN}/deleteWebhook?drop_pending_updates=true"
    try:
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            print("--> Старые сессии очищены, можно запускаться!")
        else:
            print(f"--> Очистка вернула код {r.status_code}")
    except Exception as e:
        print(f"--> Ошибка очистки: {e}")
    time.sleep(2)  # Даем Telegram пару секунд, чтобы точно закрыть старый сокет


def is_creator_user(user):
    if not user: return False
    return (user.username or '').lower().lstrip('@') in CREATOR_USERNAMES


def get_active_model():
    return ACTIVE_MODEL


def set_active_model(model_id):
    global ACTIVE_MODEL
    ACTIVE_MODEL = model_id


def fetch_free_models(force=False):
    global CACHED_FREE_MODELS
    if CACHED_FREE_MODELS and not force:
        return CACHED_FREE_MODELS

    free_list = []
    seen_ids = set()
    try:
        headers = {'Authorization': f'Bearer {GROQ_KEY}'}
        r = ai_session.get(f'{GROQ_BASE}/models', headers=headers, timeout=10)
        if r.status_code == 200:
            for item in r.json().get('data', []):
                mid = item.get('id', '')
                if not mid or mid in seen_ids or item.get('active') is False:
                    continue
                if any(x in mid.lower() for x in ['whisper', 'tts', 'embed', 'guard', 'moderation', 'playai']):
                    continue
                seen_ids.add(mid)
                free_list.append({'id': mid, 'name': mid, 'ctx': int(item.get('context_window') or 0)})
    except Exception as e:
        print('Ошибка моделей Groq:', e)

    free_list.sort(key=lambda m: (m['id'] != DEFAULT_MODEL, m['id']))
    if DEFAULT_MODEL not in seen_ids:
        free_list.insert(0, {'id': DEFAULT_MODEL, 'name': 'Llama 3.3 70B Versatile', 'ctx': 131072})

    CACHED_FREE_MODELS = free_list
    return CACHED_FREE_MODELS


def build_models_message_and_kb(page=0):
    models = fetch_free_models()
    active = get_active_model()
    per_page = 8
    total_pages = max(1, (len(models) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    page_items = models[start:start + per_page]

    lines = [
        '🤖 <b>Выбор модели ИИ для Секретаря</b>\n',
        f'✅ <b>Активная:</b> <code>{html.escape(active)}</code>\n',
        f'📋 <b>Модели (Стр. {page + 1}/{total_pages}):</b>',
    ]

    kb = InlineKeyboardMarkup(row_width=1)
    for idx, m in enumerate(page_items, start=start + 1):
        mark = '✅ ' if m['id'] == active else ''
        ctx_k = f"{m.get('ctx', 0) // 1024}K" if m.get('ctx') else '?K'
        lines.append(f"<b>{idx}.</b> {mark}{html.escape(m['name'])} (🧠 {ctx_k})\n└ <code>{html.escape(m['id'])}</code>")
        kb.add(InlineKeyboardButton(f"{mark}{idx}. {m['name'][:32]} ({ctx_k})", callback_data=f'mset_{idx - 1}'))

    nav_btns = []
    if page > 0: nav_btns.append(InlineKeyboardButton('⬅️ Назад', callback_data=f'mpage_{page - 1}'))
    nav_btns.append(InlineKeyboardButton('🔄 Обновить', callback_data='mrefresh'))
    if page < total_pages - 1: nav_btns.append(InlineKeyboardButton('Вперёд ➡️', callback_data=f'mpage_{page + 1}'))
    if nav_btns: kb.row(*nav_btns)

    lines.append('\n💡 <i>Нажми на кнопку или напиши</i> <code>модель [номер]</code>')
    return '\n'.join(lines), kb


@bot.callback_query_handler(func=lambda call: call.data.startswith(('mset_', 'mpage_', 'mrefresh')))
def handle_callbacks(call):
    user = call.from_user
    if not is_creator_user(user):
        bot.answer_callback_query(call.id, '🤬 Только Создатель!', show_alert=True)
        return

    if call.data == 'mrefresh':
        fetch_free_models(force=True)
        text, kb = build_models_message_and_kb(0)
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception: pass
        bot.answer_callback_query(call.id, '🔄 Обновлено!')
        return

    if call.data.startswith('mpage_'):
        page = int(call.data.split('_')[1])
        text, kb = build_models_message_and_kb(page)
        try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
        except Exception: pass
        bot.answer_callback_query(call.id)
        return

    if call.data.startswith('mset_'):
        idx = int(call.data.split('_')[1])
        models = fetch_free_models()
        if 0 <= idx < len(models):
            chosen = models[idx]
            set_active_model(chosen['id'])
            text, kb = build_models_message_and_kb(idx // 8)
            try: bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
            except Exception: pass
            bot.answer_callback_query(call.id, f"✅ {chosen['name'][:25]}!")


def ask_ai(mem_key, system_prompt, user_text):
    selected_model = get_active_model()
    history = CHAT_MEMORY.get(mem_key, [])

    messages_payload = [{'role': 'system', 'content': system_prompt}]
    for item in history[-(MAX_CONTEXT_MESSAGES - 1):]: messages_payload.append(item)
    messages_payload.append({'role': 'user', 'content': user_text})

    headers = {'Authorization': f'Bearer {GROQ_KEY}', 'Content-Type': 'application/json'}
    payload = {'model': selected_model, 'messages': messages_payload, 'temperature': 0.6}

    try:
        r = ai_session.post(f'{GROQ_BASE}/chat/completions', headers=headers, json=payload, timeout=30)
    except Exception as e:
        return None, f'Сеть: {e}'

    if r.status_code != 200:
        try: msg = r.json().get('error', {}).get('message', str(r.status_code))
        except Exception: msg = f'HTTP {r.status_code}'
        return None, msg

    try: content = r.json()['choices'][0]['message']['content'] or ''
    except Exception: return None, 'Ошибка чтения ответа'

    content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
    if not content: return None, 'Пустой ответ модели'

    history.append({'role': 'user', 'content': user_text})
    history.append({'role': 'assistant', 'content': content})
    CHAT_MEMORY[mem_key] = history[-MAX_CONTEXT_MESSAGES:]
    return content, None


# --- КОМАНДЫ ВЛАДЕЛЬЦА ---
@bot.message_handler(commands=['start', 'help'])
def handle_owner_commands(message):
    if not is_creator_user(message.from_user):
        bot.reply_to(message, "Я — личный ИИ-секретарь.")
        return
    bot.reply_to(message, "👑 <b>Панель секретаря</b>\n\n"
                          "Я подключен к твоему аккаунту через «Автоматизацию чатов».\n"
                          "• <code>модели</code> — выбрать модель\n"
                          "• <code>сброс</code> — очистить память диалога")


@bot.message_handler(func=lambda msg: msg.chat.type == 'private' and is_creator_user(msg.from_user) and msg.text.lower() in ['модели', '/models'])
def cmd_models(message):
    text, kb = build_models_message_and_kb(0)
    bot.reply_to(message, text, reply_markup=kb)


@bot.message_handler(func=lambda msg: msg.chat.type == 'private' and is_creator_user(msg.from_user) and msg.text.lower().startswith('модель '))
def cmd_set_model(message):
    arg = message.text[7:].strip()
    models = fetch_free_models()
    if arg.isdigit() and 0 < int(arg) <= len(models):
        set_active_model(models[int(arg) - 1]['id'])
        bot.reply_to(message, f'✅ Модель: <b>{html.escape(models[int(arg)-1]["name"])}</b>')
    elif arg:
        set_active_model(arg)
        bot.reply_to(message, f'✅ Модель: <code>{html.escape(arg)}</code>')


# --- СЕКРЕТАРЬ ---
@bot.business_message_handler(content_types=['text'])
def handle_business_message(message):
    if message.from_user.id == bot.get_me().id: return

    chat_id = message.chat.id
    mem_key = f"biz_{chat_id}"
    user_text = message.text.strip()

    if user_text.lower() in ['сброс', '/reset']:
        CHAT_MEMORY[mem_key] = []
        bot.send_message(chat_id, "🧹 Память очищена.", business_connection_id=message.business_connection_id)
        return

    system_prompt = (
        f"Ты — умный, вежливый и лаконичный секретарь-ассистент. Твоя задача — отвечать на сообщения от лица владельца, пока он занят.\n"
        f"ПРАВИЛА:\n"
        f"1. Отвечай кратко, по делу, в деловом или дружелюбном тоне.\n"
        f"2. Если не знаешь ответа — вежливо скажи, что передашь информацию владельцу.\n"
        f"3. Не признавайся, что ты бот, если не спросят прямо.\n"
        f"4. Без символов ** и #."
    )

    try: bot.send_chat_action(chat_id, 'typing', business_connection_id=message.business_connection_id)
    except Exception: pass

    ai_reply, error_text = ask_ai(mem_key, system_prompt, user_text)
    if error_text:
        print(f"Ошибка ИИ: {error_text}")
        return

    try:
        bot.send_message(chat_id, ai_reply, business_connection_id=message.business_connection_id)
    except Exception as e:
        print(f"Не отправлено: {e}")


# --- ЗАПУСК ---
print('--> Подготовка к запуску...')
clear_old_sessions()
print('⚡ БОТ-СЕКРЕТАРЬ ЗАПУЩЕН!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)