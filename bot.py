import html
import os
import re
import threading
import time

try:
  import telebot
  import requests
except ImportError:
  os.system('pip install pyTelegramBotAPI requests')
  import telebot
  import requests

from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- НАСТРОЙКИ И КЛЮЧИ (БЕРУТСЯ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ RENDER) ---
TOKEN = os.environ.get('TG_TOKEN')
GROQ_KEY = os.environ.get('GROQ_KEY')

DEFAULT_MODEL = 'llama-3.3-70b-versatile'
GROQ_BASE = 'https://api.groq.com/openai/v1'

# Твой юзернейм, чтобы бот знал, кто тут главный
CREATOR_USERNAMES = ('wrtnoq', 'wtnoq')

MAX_CONTEXT_MESSAGES = 20
CHAT_MEMORY = {}
CACHED_FREE_MODELS = []

# Здесь храним выбранную модель (в оперативке)
ACTIVE_MODEL = DEFAULT_MODEL

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)
ai_session = requests.Session()


def is_creator_user(user):
  if not user:
    return False
  uname = (user.username or '').lower().lstrip('@')
  return uname in CREATOR_USERNAMES


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
      data = r.json().get('data', [])
      for item in data:
        mid = item.get('id', '')
        if not mid or mid in seen_ids:
          continue
        if item.get('active') is False:
          continue
        lower = mid.lower()
        if any(x in lower for x in ['whisper', 'tts', 'embed', 'guard', 'moderation', 'playai']):
          continue
        ctx_len = int(item.get('context_window') or 0)
        seen_ids.add(mid)
        free_list.append({'id': mid, 'name': mid, 'ctx': ctx_len})
  except Exception as e:
    print('Ошибка получения моделей Groq:', e)

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
  end = start + per_page
  page_items = models[start:end]

  lines = [
      '🤖 <b>Выбор модели ИИ для Секретаря (Groq)</b>\n',
      f'✅ <b>Активная модель:</b>\n<code>{html.escape(active)}</code>\n',
      f'📋 <b>Доступные модели (Стр. {page + 1}/{total_pages}):</b>',
  ]

  kb = InlineKeyboardMarkup(row_width=1)
  for idx, m in enumerate(page_items, start=start + 1):
    mark = '✅ ' if m['id'] == active else ''
    ctx_k = f"{m.get('ctx', 0) // 1024}K" if m.get('ctx') else '?K'
    lines.append(f"<b>{idx}.</b> {mark}{html.escape(m['name'])} (🧠 {ctx_k})\n└ <code>{html.escape(m['id'])}</code>")
    btn_text = f"{mark}{idx}. {m['name'][:32]} ({ctx_k})"
    kb.add(InlineKeyboardButton(btn_text, callback_data=f'mset_{idx - 1}'))

  nav_btns = []
  if page > 0:
    nav_btns.append(InlineKeyboardButton('⬅️ Назад', callback_data=f'mpage_{page - 1}'))
  nav_btns.append(InlineKeyboardButton('🔄 Обновить', callback_data='mrefresh'))
  if page < total_pages - 1:
    nav_btns.append(InlineKeyboardButton('Вперёд ➡️', callback_data=f'mpage_{page + 1}'))
  if nav_btns:
    kb.row(*nav_btns)

  lines.append('\n💡 <i>Нажми на кнопку или напиши</i> <code>модель [номер]</code>')
  return '\n'.join(lines), kb


@bot.callback_query_handler(func=lambda call: call.data.startswith(('mset_', 'mpage_', 'mrefresh')))
def handle_callbacks(call):
  user = call.from_user
  if not is_creator_user(user):
    bot.answer_callback_query(call.id, '🤬 Менять модели может только Создатель!', show_alert=True)
    return

  if call.data == 'mrefresh':
    fetch_free_models(force=True)
    text, kb = build_models_message_and_kb(page=0)
    try:
      bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
      pass
    bot.answer_callback_query(call.id, '🔄 Список моделей обновлен!')
    return

  if call.data.startswith('mpage_'):
    page = int(call.data.split('_')[1])
    text, kb = build_models_message_and_kb(page=page)
    try:
      bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
    except Exception:
      pass
    bot.answer_callback_query(call.id)
    return

  if call.data.startswith('mset_'):
    idx = int(call.data.split('_')[1])
    models = fetch_free_models()
    if 0 <= idx < len(models):
      chosen = models[idx]
      set_active_model(chosen['id'])
      page = idx // 8
      text, kb = build_models_message_and_kb(page=page)
      try:
        bot.edit_message_text(text, call.message.chat.id, call.message.message_id, reply_markup=kb)
      except Exception:
        pass
      bot.answer_callback_query(call.id, f"✅ Выбрана модель: {chosen['name'][:25]}!")


def ask_ai(mem_key, system_prompt, user_text):
  selected_model = get_active_model()
  history = CHAT_MEMORY.get(mem_key, [])

  messages_payload = [{'role': 'system', 'content': system_prompt}]
  for item in history[-(MAX_CONTEXT_MESSAGES - 1):]:
    messages_payload.append(item)
  messages_payload.append({'role': 'user', 'content': user_text})

  headers = {'Authorization': f'Bearer {GROQ_KEY}', 'Content-Type': 'application/json'}
  payload = {'model': selected_model, 'messages': messages_payload, 'temperature': 0.6}

  try:
    r = ai_session.post(f'{GROQ_BASE}/chat/completions', headers=headers, json=payload, timeout=30)
  except Exception as e:
    return None, f'Ошибка сети: {e}'

  if r.status_code != 200:
    try:
      err_data = r.json()
      msg = err_data.get('error', {}).get('message') or str(err_data)
      return None, f'[Код {r.status_code}] {msg}'
    except Exception:
      return None, f'[HTTP {r.status_code}] Ошибка Groq'

  try:
    data = r.json()
  except Exception as e:
    return None, f'Ошибка чтения JSON: {e}'

  choices = data.get('choices') or []
  if not choices:
    return None, f'Модель вернула пустой ответ'

  msg_obj = choices[0].get('message') or {}
  content = msg_obj.get('content') or ''
  content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

  if not content:
    return None, f'Модель не сгенерировала текст'

  history.append({'role': 'user', 'content': user_text})
  history.append({'role': 'assistant', 'content': content})
  
  if len(history) > MAX_CONTEXT_MESSAGES:
    history = history[-MAX_CONTEXT_MESSAGES:]
    
  CHAT_MEMORY[mem_key] = history
  return content, None


# --- ОБРАБОТКА ЛИЧНЫХ СООБЩЕНИЙ (КОМАНДЫ ВЛАДЕЛЬЦА) ---
@bot.message_handler(commands=['start', 'help', 'помощь'])
def handle_owner_commands(message):
  if not is_creator_user(message.from_user):
    bot.reply_to(message, "Я — личный ИИ-секретарь. Я не отвечаю на команды посторонних.")
    return
  
  text = (
      "👑 <b>Панель управления секретарем</b>\n\n"
      "Твой бот подключен к режиму <b>«Автоматизация чатов»</b>.\n"
      "Я буду автоматически отвечать на сообщения, которые приходят тебе в личку, от твоего имени.\n\n"
      "Доступные команды:\n"
      "• <code>модели</code> — выбрать модель Groq\n"
      "• <code>сброс</code> — очистить память диалога\n\n"
      "<i>Чтобы отключить меня, зайди в Настройки -> Автоматизация чатов и убери галочку.</i>"
  )
  bot.reply_to(message, text)


@bot.message_handler(func=lambda msg: msg.chat.type == 'private' and is_creator_user(msg.from_user) and msg.text.lower() in ['модели', '/models'])
def cmd_models(message):
  msg_text, kb = build_models_message_and_kb(page=0)
  bot.reply_to(message, msg_text, reply_markup=kb)


@bot.message_handler(func=lambda msg: msg.chat.type == 'private' and is_creator_user(msg.from_user) and msg.text.lower().startswith('модель '))
def cmd_set_model(message):
  arg = message.text[7:].strip()
  models = fetch_free_models()
  if arg.isdigit():
    idx = int(arg) - 1
    if 0 <= idx < len(models):
      chosen = models[idx]
      set_active_model(chosen['id'])
      bot.reply_to(message, f'✅ Модель переключена на:\n<b>{html.escape(chosen["name"])}</b>')
    else:
      bot.reply_to(message, f'⚠️ Номер от 1 до {len(models)}.')
  else:
    set_active_model(arg)
    bot.reply_to(message, f'✅ Установлена модель:\n<code>{html.escape(arg)}</code>')


# --- ОСНОВНАЯ МАГИЯ: СЕКРЕТАРЬ (BUSINESS MESSAGES) ---
@bot.business_message_handler(content_types=['text'])
def handle_business_message(message):
  # Игнорируем сообщения от самого себя
  if message.from_user.id == bot.get_me().id:
      return

  chat_id = message.chat.id
  user_name = message.from_user.first_name or "Пользователь"
  user_text = message.text
  
  # Ключ памяти для каждого конкретного собеседника
  mem_key = f"biz_{chat_id}"
  
  if user_text.lower().strip() in ['сброс', '/reset']:
      CHAT_MEMORY[mem_key] = []
      bot.send_message(chat_id, "🧹 Память диалога очищена.", business_connection_id=message.business_connection_id)
      return

  # Системный промпт для секретаря
  system_prompt = (
      f"Ты — умный, вежливый и лаконичный секретарь-ассистент Славы Огурцова.\n"
      f"Твоя задача — отвечать на входящие сообщения от лица Славы, пока он занят.\n"
      f"Собеседника зовут: {user_name}.\n\n"
      f"ПРАВИЛА ОБЩЕНИЯ:\n"
      f"1. Отвечай кратко, по делу, в деловом или дружелюбном тоне (в зависимости от контекста).\n"
      f"2. Если тебя спрашивают о чем-то важном, о чем ты не знаешь — вежливо скажи, что передашь информацию Славе, как только он освободится.\n"
      f"3. Никогда не признавайся, что ты нейросеть или бот, если тебя прямо об этом не спросят. Веди себя как живой человек.\n"
      f"4. Не используй символы разметки (**, *, #). Только обычный текст."
  )

  # Отправляем сообщение "печатает..."
  try:
      bot.send_chat_action(chat_id, 'typing', business_connection_id=message.business_connection_id)
  except Exception:
      pass

  ai_reply, error_text = ask_ai(mem_key, system_prompt, user_text)

  if error_text:
      print(f"Ошибка ИИ в бизнес-чате: {error_text}")
      return

  # Отправляем ответ от имени владельца аккаунта
  try:
      bot.send_message(
          chat_id=chat_id, 
          text=ai_reply, 
          business_connection_id=message.business_connection_id
      )
  except Exception as e:
      print(f"Не удалось отправить сообщение: {e}")


print('⚡ БОТ-СЕКРЕТАРЬ ЗАПУЩЕН (GROQ)!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)