import html
import os
import random
import re
import signal
import threading
import time

try:
  import telebot
  import requests
except ImportError:
  os.system('pip install pyTelegramBotAPI requests')
  import telebot
  import requests

from telebot.types import ChatPermissions, InlineKeyboardMarkup, InlineKeyboardButton


# --- ОСТАНОВКА СТАРЫХ КОПИЙ БОТА ---
def kill_other_instances():
  my_pid = os.getpid()
  try:
    for pid_str in os.listdir('/proc'):
      if pid_str.isdigit():
        pid = int(pid_str)
        if pid != my_pid:
          try:
            with open(f'/proc/{pid}/cmdline', 'rb') as f:
              cmd = f.read().decode('utf-8', errors='ignore')
            if 'bot.py' in cmd and 'python' in cmd:
              os.kill(pid, signal.SIGKILL)
          except Exception:
            pass
  except Exception:
    pass


#kill_other_instances()

# --- НАСТРОЙКИ И КЛЮЧИ ---
TOKEN = '8458915232:AAHyS1jj_Oq6vy1_ILK73Rzxllfd0UiRVTA'
FIREBASE_URL = 'https://tgbotik-d479c-default-rtdb.firebaseio.com/secret_845891'

GROQ_KEY = 
DEFAULT_MODEL = 'llama-3.3-70b-versatile'
GROQ_BASE = 'https://api.groq.com/openai/v1'

CREATOR_USERNAMES = ('wrtnoq', 'wtnoq')
CREATOR_DISPLAY_USER = '@wrtnoq'
CREATOR_TITLE = 'Слава Огурцов'

MAX_CONTEXT_MESSAGES = 20

RANK_NAMES = {
    0: '👤 Участник',
    1: '🧹 Нищук',
    2: '⚔️ Заглотус',
    3: '💎 Баронесса',
    4: '🥒 Слава Огурцов (Создатель)',
}

bot = telebot.TeleBot(TOKEN, parse_mode='HTML', threaded=True)
FIREBASE_URL = FIREBASE_URL.rstrip('/')
BOT_ID = None

# Постоянная сессия с пулом соединений (экономит 90% CPU на SSL-рукопожатиях)
fb_session = requests.Session()
ai_session = requests.Session()

# --- ОПЕРАТИВНЫЙ КЭШ БАЗЫ FIREBASE (МГНОВЕННЫЙ ДОСТУП + ФОНОВАЯ СИНХРОНИЗАЦИЯ) ---
RAM_DB = {
    'config': {'ai_model': DEFAULT_MODEL, 'creator_id': None},
    'chats': {},
}
DB_LOCK = threading.Lock()
DIRTY_PATHS = {}  # Пути в Firebase, которые нужно отправить в облако

CHAT_MEMORY = {}
CHAT_LIVE_LOG = {}
LAST_TARGET_ID = {}
PENDING_ACTION = {}
CACHED_FREE_MODELS = []
SCANNED_ADMINS_CHATS = set()


def init_firebase_sync():
  """При старте загружает всю базу из Firebase в оперативку, чтобы не дергать сеть на каждое слово."""
  global RAM_DB
  for attempt in range(3):
    try:
      r = fb_session.get(f'{FIREBASE_URL}/.json', timeout=8)
      if r.status_code == 200:
        data = r.json()
        if isinstance(data, dict):
          with DB_LOCK:
            if isinstance(data.get('config'), dict):
              RAM_DB['config'].update(data['config'])
            if isinstance(data.get('chats'), dict):
              # Приводим все ключи к строкам и чистим структуру
              for cid_s, cval in data['chats'].items():
                if isinstance(cval, dict):
                  cval.setdefault('meta', {})
                  cval.setdefault('users', {})
                  RAM_DB['chats'][str(cid_s)] = cval
          print(
              '🔥 База Firebase успешно загружена в память! Чатов в базе:'
              f" {len(RAM_DB['chats'])}"
          )
          return
      else:
        print(f'⚠️ Firebase ответил кодом {r.status_code}: {r.text[:150]}')
    except Exception as e:
      print(f'⚠️ Попытка {attempt + 1}/3 подключения к Firebase: {e}')
      time.sleep(1)


def mark_firebase_dirty(path, value, immediate=False):
  """Отмечает узел для отправки в Firebase. Если immediate=True — отправляет сразу в фоне."""
  with DB_LOCK:
    DIRTY_PATHS[path] = value
  if immediate:
    threading.Thread(target=flush_firebase_changes, daemon=True).start()


def flush_firebase_changes():
  """Отправляет накопившиеся изменения в Firebase без нагрузки на основной поток бота."""
  with DB_LOCK:
    if not DIRTY_PATHS:
      return
    batch = dict(DIRTY_PATHS)
    DIRTY_PATHS.clear()

  for path, val in batch.items():
    url = f"{FIREBASE_URL}/{path.lstrip('/')}.json"
    for _ in range(2):
      try:
        if isinstance(val, dict):
          r = fb_session.patch(url, json=val, timeout=6)
        else:
          r = fb_session.put(url, json=val, timeout=6)
        if r.status_code == 200:
          break
      except Exception:
        time.sleep(0.5)


def firebase_background_worker():
  """Фоновый поток: раз в 5 секунд тихо сохраняет счетчики сообщений в Firebase."""
  while True:
    time.sleep(5)
    try:
      flush_firebase_changes()
    except Exception:
      pass


init_firebase_sync()
threading.Thread(target=firebase_background_worker, daemon=True).start()


def get_bot_id():
  global BOT_ID
  if not BOT_ID:
    try:
      BOT_ID = bot.get_me().id
    except Exception:
      pass
  return BOT_ID


# --- УПРАВЛЕНИЕ МОДЕЛЯМИ В FIREBASE (ТОЛЬКО ВЫБОР СОЗДАТЕЛЯ) ---
def get_active_model():
  return RAM_DB.get('config', {}).get('ai_model') or DEFAULT_MODEL


def set_active_model(model_id):
  with DB_LOCK:
    RAM_DB.setdefault('config', {})['ai_model'] = model_id
  mark_firebase_dirty('config/ai_model', model_id, immediate=True)


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
        # Пропускаем не-чат модели (голос, эмбеддинги, модерация)
        if any(
            x in lower
            for x in [
                'whisper',
                'tts',
                'embed',
                'guard',
                'moderation',
                'playai',
                'distil-whisper',
            ]
        ):
          continue
        ctx_len = int(item.get('context_window') or 0)
        seen_ids.add(mid)
        free_list.append({'id': mid, 'name': mid, 'ctx': ctx_len})
    else:
      print(f'⚠️ Groq /models вернул код {r.status_code}: {r.text[:150]}')
  except Exception as e:
    print('Ошибка получения моделей Groq:', e)

  # Дефолтная модель — первой в списке
  free_list.sort(key=lambda m: (m['id'] != DEFAULT_MODEL, m['id']))

  if DEFAULT_MODEL not in seen_ids:
    free_list.insert(
        0,
        {
            'id': DEFAULT_MODEL,
            'name': 'Llama 3.3 70B Versatile',
            'ctx': 131072,
        },
    )

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
      '🤖 <b>Выбор модели ИИ (Groq)</b>\n',
      f'✅ <b>Активная модель (в Firebase):</b>\n<code>{html.escape(active)}</code>\n',
      f'📋 <b>Доступные модели (Стр. {page + 1}/{total_pages}):</b>',
  ]

  kb = InlineKeyboardMarkup(row_width=1)
  for idx, m in enumerate(page_items, start=start + 1):
    mark = '✅ ' if m['id'] == active else ''
    ctx_k = f"{m.get('ctx', 0) // 1024}K" if m.get('ctx') else '?K'
    lines.append(
        f"<b>{idx}.</b> {mark}{html.escape(m['name'])} (🧠 {ctx_k})\n"
        f"└ <code>{html.escape(m['id'])}</code>"
    )
    btn_text = f"{mark}{idx}. {m['name'][:32]} ({ctx_k})"
    kb.add(InlineKeyboardButton(btn_text, callback_data=f'mset_{idx - 1}'))

  nav_btns = []
  if page > 0:
    nav_btns.append(
        InlineKeyboardButton('⬅️ Назад', callback_data=f'mpage_{page - 1}')
    )
  nav_btns.append(
      InlineKeyboardButton('🔄 Обновить', callback_data='mrefresh')
  )
  if page < total_pages - 1:
    nav_btns.append(
        InlineKeyboardButton('Вперёд ➡️', callback_data=f'mpage_{page + 1}')
    )
  if nav_btns:
    kb.row(*nav_btns)

  lines.append(
      '\n💡 <i>Нажми на кнопку ниже или напиши</i> <code>модель [номер]</code>'
  )
  return '\n'.join(lines), kb


# --- ИДЕНТИФИКАЦИЯ СОЗДАТЕЛЯ И СИНХРОНИЗАЦИЯ С FIREBASE ---
def is_creator_user(user):
  if not user:
    return False
  uname = (user.username or '').lower().lstrip('@')
  if uname in CREATOR_USERNAMES:
    return True
  cid = RAM_DB.get('config', {}).get('creator_id')
  return bool(cid and int(user.id) == int(cid))


def get_creator_id():
  cid = RAM_DB.get('config', {}).get('creator_id')
  return int(cid) if cid else None


def save_creator_id(user_id):
  if RAM_DB.get('config', {}).get('creator_id') != int(user_id):
    with DB_LOCK:
      RAM_DB.setdefault('config', {})['creator_id'] = int(user_id)
    mark_firebase_dirty('config/creator_id', int(user_id), immediate=True)


def clean_user_name(user):
  if is_creator_user(user):
    return CREATOR_TITLE
  raw = (user.first_name or '').strip()
  if getattr(user, 'last_name', None):
    raw = f'{raw} {user.last_name}'.strip()
  if not raw or raw in ['.', ',', '-', '_']:
    return user.username or f'Участник_{user.id}'
  return html.escape(raw)


def register_chat_in_firebase(chat):
  if not chat or chat.type == 'private':
    return
  cid_str = str(chat.id)
  title = chat.title or f'Чат {chat.id}'
  need_push = False

  with DB_LOCK:
    chat_entry = RAM_DB.setdefault('chats', {}).setdefault(
        cid_str, {'meta': {}, 'users': {}}
    )
    old_title = chat_entry.get('meta', {}).get('title')
    if old_title != title:
      chat_entry['meta'] = {
          'title': title,
          'id': chat.id,
          'type': chat.type,
          'updated': int(time.time()),
      }
      need_push = True

  if need_push:
    mark_firebase_dirty(
        f'chats/{cid_str}/meta', RAM_DB['chats'][cid_str]['meta'], immediate=True
    )

  if chat.id not in SCANNED_ADMINS_CHATS:
    SCANNED_ADMINS_CHATS.add(chat.id)
    try:
      admins = bot.get_chat_administrators(chat.id)
      for adm in admins:
        u = adm.user
        if u.id != get_bot_id():
          get_user_data(chat.id, u)
    except Exception:
      pass


def get_user_data(chat_id, user, chat_obj=None):
  if not user:
    return {}
  if chat_obj and chat_obj.type != 'private':
    register_chat_in_firebase(chat_obj)

  cid_str = str(chat_id)
  uid_str = str(user.id)
  username = (user.username or '').lower().lstrip('@')
  is_boss = (username in CREATOR_USERNAMES) or (get_creator_id() == user.id)
  first_name = CREATOR_TITLE if is_boss else clean_user_name(user)

  if is_boss:
    save_creator_id(user.id)

  immediate_sync = False
  with DB_LOCK:
    chat_entry = RAM_DB.setdefault('chats', {}).setdefault(
        cid_str, {'meta': {}, 'users': {}}
    )
    users_dict = chat_entry.setdefault('users', {})

    if uid_str not in users_dict or not isinstance(users_dict[uid_str], dict):
      users_dict[uid_str] = {
          'id': user.id,
          'warns': 0,
          'reputation': 0,
          'messages': 0,
          'rank': 4 if is_boss else 0,
          'nick': '',
          'partner': 0,
          'partner_name': '',
          'name': first_name,
          'username': username,
          'is_bot': bool(getattr(user, 'is_bot', False)),
      }
      immediate_sync = True
    else:
      u_rec = users_dict[uid_str]
      changed = False
      if u_rec.get('name') != first_name:
        u_rec['name'] = first_name
        changed = True
      if username and u_rec.get('username') != username:
        u_rec['username'] = username
        changed = True
      if is_boss and int(u_rec.get('rank', 0)) != 4:
        u_rec['rank'] = 4
        changed = True
      if changed:
        immediate_sync = True

    res_copy = dict(users_dict[uid_str])

  if immediate_sync:
    mark_firebase_dirty(
        f'chats/{cid_str}/users/{uid_str}', res_copy, immediate=True
    )

  return res_copy


def update_user_fields(chat_id, user_id, fields_dict, immediate=False):
  cid_str = str(chat_id)
  uid_str = str(user_id)
  with DB_LOCK:
    chat_entry = RAM_DB.setdefault('chats', {}).setdefault(
        cid_str, {'meta': {}, 'users': {}}
    )
    u_rec = chat_entry.setdefault('users', {}).setdefault(
        uid_str,
        {
            'id': int(user_id),
            'warns': 0,
            'reputation': 0,
            'messages': 0,
            'rank': 0,
            'nick': '',
            'partner': 0,
            'partner_name': '',
            'name': f'Участник_{user_id}',
            'username': '',
        },
    )
    u_rec.update(fields_dict)
    updated_copy = dict(u_rec)

  mark_firebase_dirty(
      f'chats/{cid_str}/users/{uid_str}', updated_copy, immediate=immediate
  )


def get_all_chat_users(chat_id):
  cid_str = str(chat_id)
  with DB_LOCK:
    users = RAM_DB.get('chats', {}).get(cid_str, {}).get('users', {})
    return {k: dict(v) for k, v in users.items() if isinstance(v, dict)}


def record_live_chat_event(chat_id, speaker_name, text):
  log = CHAT_LIVE_LOG.get(chat_id, [])
  log.append(f'{speaker_name}: {text[:180]}')
  CHAT_LIVE_LOG[chat_id] = log[-20:]


def get_global_chats_snapshot(current_chat=None):
  with DB_LOCK:
    chats_dict = dict(RAM_DB.get('chats', {}))

  summary_lines = []
  for cid_str, cdata in chats_dict.items():
    if not str(cid_str).startswith('-'):
      continue
    meta = cdata.get('meta') if isinstance(cdata.get('meta'), dict) else {}
    title = meta.get('title')
    if not title:
      try:
        c_info = bot.get_chat(int(cid_str))
        title = c_info.title or f'Группа {cid_str}'
        register_chat_in_firebase(c_info)
      except Exception:
        title = f'Группа {cid_str}'

    users_dict = (
        cdata.get('users') if isinstance(cdata.get('users'), dict) else {}
    )
    members_desc = []
    for uid_s, uinfo in users_dict.items():
      if not isinstance(uinfo, dict):
        continue
      uname = str(uinfo.get('username') or '')
      r = (
          4
          if uname.lower() in CREATOR_USERNAMES
          else int(uinfo.get('rank', 0))
      )
      nm = (
          CREATOR_TITLE
          if r == 4
          else (uinfo.get('nick') or uinfo.get('name') or 'Участник')
      )
      if nm == '.':
        nm = CREATOR_TITLE if r == 4 else f'Участник_{uid_s}'
      un_str = f'@{uname}' if uname else f'ID:{uid_s}'
      bot_tag = ' [БОТ]' if uinfo.get('is_bot') else ''
      members_desc.append(
          f"{nm}{bot_tag} ({un_str}, {RANK_NAMES.get(r, 'Участник')}, сообщ:"
          f" {uinfo.get('messages', 0)})"
      )

    here_mark = (
        ' 👈 [ТЕКУЩИЙ ЧАТ]'
        if current_chat and str(current_chat.id) == str(cid_str)
        else ''
    )
    members_str = (
        '; '.join(members_desc)
        if members_desc
        else 'пока нет записанных участников'
    )
    summary_lines.append(
        f'• Чат «{title}» (ID: {cid_str}){here_mark} — участников в Firebase:'
        f' {len(members_desc)} [{members_str}]'
    )

  if not summary_lines:
    return 'В Firebase пока не зарегистрировано групповых чатов.'
  return '\n'.join(summary_lines)


def get_effective_rank(chat_id, user, user_data=None):
  if is_creator_user(user):
    save_creator_id(user.id)
    return 4
  if chat_id == user.id:
    return 0
  if user_data is None:
    user_data = get_user_data(chat_id, user)
  return int(user_data.get('rank', 0))


def display_name(chat_id, user, data=None):
  if is_creator_user(user):
    return f"<a href='tg://user?id={user.id}'>{CREATOR_TITLE}</a>"
  if not data:
    data = get_user_data(chat_id, user)
  nick = data.get('nick')
  name = html.escape(str(nick if nick else clean_user_name(user)))
  return f"<a href='tg://user?id={user.id}'>{name}</a>"


# --- КНОПКИ МОДЕЛЕЙ ---
@bot.callback_query_handler(
    func=lambda call: call.data.startswith(('mset_', 'mpage_', 'mrefresh'))
)
def handle_callbacks(call):
  user = call.from_user
  if not is_creator_user(user):
    bot.answer_callback_query(
        call.id,
        '🤬 Менять модели может только создатель Слава Огурцов!',
        show_alert=True,
    )
    return

  if call.data == 'mrefresh':
    fetch_free_models(force=True)
    text, kb = build_models_message_and_kb(page=0)
    try:
      bot.edit_message_text(
          text, call.message.chat.id, call.message.message_id, reply_markup=kb
      )
    except Exception:
      pass
    bot.answer_callback_query(call.id, '🔄 Список моделей обновлен!')
    return

  if call.data.startswith('mpage_'):
    page = int(call.data.split('_')[1])
    text, kb = build_models_message_and_kb(page=page)
    try:
      bot.edit_message_text(
          text, call.message.chat.id, call.message.message_id, reply_markup=kb
      )
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
        bot.edit_message_text(
            text, call.message.chat.id, call.message.message_id, reply_markup=kb
        )
      except Exception:
        pass
      bot.answer_callback_query(
          call.id, f"✅ Выбрана в Firebase: {chosen['name'][:25]}!"
      )


# --- РЕАЛЬНОЕ ВЫПОЛНЕНИЕ МОДЕРАЦИИ В TELEGRAM ---
def exec_mod_action(
    chat_id,
    caller,
    target_id,
    target_name_html,
    target_rank,
    action,
    minutes=60,
    reason='По приказу администрации',
    reply_msg_id=None,
    target_msg_id=None,
):
  caller_rank = get_effective_rank(chat_id, caller)

  if caller_rank == 0:
    return False, '🤬 Ты даже не нищук, у тебя нет прав!'

  required_rank = {
      'delete': 1,
      'mute': 1,
      'unmute': 1,
      'mute_all': 3,
      'unmute_all': 3,
      'warn': 2,
      'unwarn': 2,
      'kick': 2,
      'ban': 3,
      'unban': 3,
  }.get(action, 1)

  if caller_rank < required_rank:
    needed_name = RANK_NAMES[required_rank]
    return (
        False,
        f'❌ Твой ранг ({RANK_NAMES[caller_rank]}) слишком мал! Нужен минимум:'
        f' <b>{needed_name}</b>.',
    )

  if action == 'mute_all':
    all_u = get_all_chat_users(chat_id)
    muted_names = []
    until = int(time.time()) + max(1, minutes) * 60
    for uid_str, uinfo in all_u.items():
      uid = int(uid_str)
      uname = str(uinfo.get('username') or '').lower()
      urank = (
          4 if uname in CREATOR_USERNAMES else int(uinfo.get('rank', 0))
      )
      if uid == caller.id or uid == get_bot_id() or urank >= caller_rank:
        continue
      try:
        bot.restrict_chat_member(
            chat_id,
            uid,
            until_date=until,
            permissions=ChatPermissions(can_send_messages=False),
        )
        muted_names.append(
            html.escape(str(uinfo.get('nick') or uinfo.get('name') or uid))
        )
      except Exception:
        pass
    try:
      bot.set_chat_permissions(
          chat_id, ChatPermissions(can_send_messages=False)
      )
    except Exception:
      pass
    who_str = f" (Заглушены: {', '.join(muted_names)})" if muted_names else ''
    return (
        True,
        '✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> Тотальный мут чата активирован на'
        f' <b>{minutes} мин.</b>!{who_str}',
    )

  if action == 'unmute_all':
    all_u = get_all_chat_users(chat_id)
    for uid_str in all_u.keys():
      uid = int(uid_str)
      if uid == get_bot_id():
        continue
      try:
        bot.restrict_chat_member(
            chat_id,
            uid,
            permissions=ChatPermissions(
                can_send_messages=True,
                can_send_media_messages=True,
                can_send_other_messages=True,
                can_add_web_page_previews=True,
            ),
        )
      except Exception:
        pass
    try:
      bot.set_chat_permissions(
          chat_id,
          ChatPermissions(
              can_send_messages=True,
              can_send_media_messages=True,
              can_send_other_messages=True,
              can_add_web_page_previews=True,
          ),
      )
    except Exception:
      pass
    return (
        True,
        '✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> Все ограничения сняты, чат открыт!',
    )

  if target_id == get_bot_id():
    return False, '🛡 Себя я наказывать не буду!'

  if target_rank >= caller_rank and caller_rank < 4:
    return (
        False,
        f'🛡 Нельзя применить это к {target_name_html} — его ранг не ниже твоего'
        f' ({RANK_NAMES[target_rank]})!',
    )

  try:
    if action == 'delete':
      if target_msg_id:
        bot.delete_message(chat_id, target_msg_id)
      if reply_msg_id:
        bot.delete_message(chat_id, reply_msg_id)
      return True, '✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> Сообщение удалено.'

    if action == 'mute':
      until = int(time.time()) + max(1, minutes) * 60
      bot.restrict_chat_member(
          chat_id,
          target_id,
          until_date=until,
          permissions=ChatPermissions(can_send_messages=False),
      )
      return (
          True,
          f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> 🔇 {target_name_html} отправлен в'
          f' мут на <b>{minutes} мин.</b> (Причина: {html.escape(reason)})',
      )

    if action == 'unmute':
      bot.restrict_chat_member(
          chat_id,
          target_id,
          permissions=ChatPermissions(
              can_send_messages=True,
              can_send_media_messages=True,
              can_send_other_messages=True,
              can_add_web_page_previews=True,
          ),
      )
      return (
          True,
          f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> 🔊 С {target_name_html} снят мут!',
      )

    if action == 'warn':
      all_u = get_all_chat_users(chat_id)
      uinfo = all_u.get(str(target_id), {})
      warns = int(uinfo.get('warns', 0)) + 1
      if warns >= 3:
        bot.ban_chat_member(chat_id, target_id)
        update_user_fields(chat_id, target_id, {'warns': 0}, immediate=True)
        return (
            True,
            f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> 🚫 У {target_name_html}'
            ' <b>3/3</b> предупреждений — <b>БАН!</b>',
        )
      else:
        update_user_fields(chat_id, target_id, {'warns': warns}, immediate=True)
        return (
            True,
            f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> ⚠️ {target_name_html} получил'
            f' пред (<b>{warns}/3</b>).',
        )

    if action == 'unwarn':
      update_user_fields(chat_id, target_id, {'warns': 0}, immediate=True)
      return (
          True,
          '✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> Предупреждения с'
          f' {target_name_html} сняты!',
      )

    if action == 'kick':
      bot.ban_chat_member(chat_id, target_id)
      bot.unban_chat_member(chat_id, target_id)
      return (
          True,
          f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> 👢 {target_name_html} кикнут из'
          ' чата!',
      )

    if action == 'ban':
      bot.ban_chat_member(chat_id, target_id)
      return (
          True,
          f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> 🔨 {target_name_html} забанен'
          ' навсегда!',
      )

    if action == 'unban':
      bot.unban_chat_member(chat_id, target_id, only_if_banned=True)
      return (
          True,
          f'✅ <b>[ВЫПОЛНЕНО В TELEGRAM]:</b> 🕊 {target_name_html} разбанен!',
      )
  except Exception as e:
    err_s = str(e)
    if (
        'administrator' in err_s.lower()
        or 'owner' in err_s.lower()
        or "can't restrict" in err_s.lower()
    ):
      return (
          False,
          '⚠️ Ошибка Telegram: Нельзя замутить/кикнуть'
          f' {target_name_html}, так как он назначен Администратором в'
          ' настройках самой группы Telegram!',
      )
    return (
        False,
        f'⚠️ Ошибка Telegram API: <code>{html.escape(err_s[:120])}</code>',
    )

  return False, 'Неизвестное действие'


# --- ПОИСК ЦЕЛИ В БАЗЕ FIREBASE (В ТЕКУЩЕМ И СОСЕДНИХ ЧАТАХ) ---
def resolve_target_smart(chat_id, message, search_text):
  all_u = get_all_chat_users(chat_id)
  text_lower = search_text.lower()

  # 1. Если ответили свайпом на сообщение цели
  if message.reply_to_message and message.reply_to_message.from_user:
    r_user = message.reply_to_message.from_user
    if r_user.id != get_bot_id():
      u_data = get_user_data(chat_id, r_user, message.chat)
      LAST_TARGET_ID[chat_id] = r_user.id
      t_rank = get_effective_rank(chat_id, r_user, u_data)
      return (
          r_user.id,
          display_name(chat_id, r_user, u_data),
          f"{clean_user_name(r_user)} (@{r_user.username or 'без_юза'})",
          t_rank,
      )

  # 2. Text mention
  if message.entities:
    for ent in message.entities:
      if (
          ent.type == 'text_mention'
          and ent.user
          and ent.user.id != get_bot_id()
      ):
        u_data = get_user_data(chat_id, ent.user, message.chat)
        LAST_TARGET_ID[chat_id] = ent.user.id
        t_rank = get_effective_rank(chat_id, ent.user, u_data)
        return (
            ent.user.id,
            display_name(chat_id, ent.user, u_data),
            f'{clean_user_name(ent.user)}',
            t_rank,
        )

  # Собираем пул пользователей из текущего чата + из всех остальных чатов в Firebase
  with DB_LOCK:
    other_pools = [
        c.get('users', {}) for c in RAM_DB.get('chats', {}).values()
    ]

  # 3. Поиск по @username
  m = re.search(r'@([a-z0-9_]+)', text_lower)
  if m:
    uname = m.group(1).lower()
    for pool in [all_u] + other_pools:
      for uid_str, uinfo in pool.items():
        if str(uinfo.get('username') or '').lower() == uname:
          uid = int(uid_str)
          LAST_TARGET_ID[chat_id] = uid
          if str(uid) not in all_u:
            update_user_fields(chat_id, uid, uinfo, immediate=True)
          nm = html.escape(
              str(uinfo.get('nick') or uinfo.get('name') or 'Участник')
          )
          rk = (
              4
              if uname in CREATOR_USERNAMES
              else int(uinfo.get('rank', 0))
          )
          return (
              uid,
              f"<a href='tg://user?id={uid}'>{nm}</a>",
              f'{nm} (@{uname})',
              rk,
          )

  # 4. Поиск по имени/фамилии/нику (по первым 4 буквам: "жири" -> "Владимир Жириновский")
  for pool in [all_u] + other_pools:
    for uid_str, uinfo in pool.items():
      uid = int(uid_str)
      if uid == message.from_user.id or uid == get_bot_id():
        continue
      uname = str(uinfo.get('username') or '').lower()
      for field in [
          str(uinfo.get('name') or ''),
          str(uinfo.get('nick') or ''),
          uname,
      ]:
        for word in field.lower().split():
          clean_w = re.sub(r'[^a-zа-яё0-9]', '', word)
          if len(clean_w) >= 4:
            prefix = clean_w[:4]
            if prefix in text_lower:
              LAST_TARGET_ID[chat_id] = uid
              if str(uid) not in all_u:
                update_user_fields(chat_id, uid, uinfo, immediate=True)
              nm = html.escape(
                  str(uinfo.get('nick') or uinfo.get('name') or 'Участник')
              )
              rk = (
                  4
                  if uname in CREATOR_USERNAMES
                  else int(uinfo.get('rank', 0))
              )
              return (
                  uid,
                  f"<a href='tg://user?id={uid}'>{nm}</a>",
                  f"{nm} (@{uname or 'без_юза'})",
                  rk,
              )

  # 5. По местоимению ("его", "ему", "этого", "бота")
  if any(
      p in text_lower.split()
      for p in ['его', 'ему', 'ее', 'ей', 'этого', 'бота', 'клоуна', 'типа']
  ):
    last_id = LAST_TARGET_ID.get(chat_id)
    if last_id and str(last_id) in all_u:
      uinfo = all_u[str(last_id)]
      uname = str(uinfo.get('username') or '').lower()
      nm = html.escape(
          str(uinfo.get('nick') or uinfo.get('name') or 'Участник')
      )
      rk = 4 if uname in CREATOR_USERNAMES else int(uinfo.get('rank', 0))
      return (
          last_id,
          f"<a href='tg://user?id={last_id}'>{nm}</a>",
          f"{nm} (@{uname or 'без_юза'})",
          rk,
      )

  return None, None, 'Не выбран', 0


def detect_informal_mod_intent(prompt_lower):
  if any(w in prompt_lower for w in ['всем', 'всём', 'всех']) and any(
      w in prompt_lower for w in ['мут', 'замуть', 'заткни']
  ):
    return 'mute_all'
  if any(w in prompt_lower for w in ['всем', 'всём', 'всех']) and any(
      w in prompt_lower for w in ['размут', 'сними мут', 'открой']
  ):
    return 'unmute_all'
  if any(
      w in prompt_lower for w in ['разбань', 'разбанить', 'верни из бана']
  ):
    return 'unban'
  if any(
      w in prompt_lower
      for w in ['забань', 'забанить', 'в бан', 'пермач', 'заблокируй']
  ):
    return 'ban'
  if any(
      w in prompt_lower
      for w in ['размуть', 'размутить', 'сними мут', 'пусть говорит', 'дай слово']
  ):
    return 'unmute'
  if any(
      w in prompt_lower
      for w in [
          'замуть',
          'замутить',
          'в мут',
          'выдай мут',
          'дай мут',
          'мут ',
          'заткни',
          'рот закрой',
      ]
  ):
    return 'mute'
  if any(
      w in prompt_lower
      for w in [
          'сними варн',
          'снять варн',
          'разварн',
          'обнули варн',
          'сними пред',
      ]
  ):
    return 'unwarn'
  if any(
      w in prompt_lower
      for w in [
          'выдай варн',
          'дай варн',
          'варн ему',
          'выдай пред',
          'дай пред',
          'предупреждение',
      ]
  ):
    return 'warn'
  if any(
      w in prompt_lower
      for w in ['кикни', 'кикнуть', 'кик ', 'выкинь', 'выгони', 'выпни']
  ):
    return 'kick'
  if any(
      w in prompt_lower
      for w in [
          'удали сообщ',
          'снеси сообщ',
          'сотри сообщ',
          'удали это',
          'снеси это',
          '-смс',
      ]
  ):
    return 'delete'
  return None


# --- ЗАПРОС К GROQ ---
def ask_ai(chat_id, system_prompt, formatted_user_turn, raw_user_text):
  selected_model = get_active_model()
  history = CHAT_MEMORY.get(chat_id, [])

  messages_payload = [{'role': 'system', 'content': system_prompt}]
  for item in history[-(MAX_CONTEXT_MESSAGES - 1) :]:
    messages_payload.append(item)
  messages_payload.append({'role': 'user', 'content': formatted_user_turn})

  headers = {
      'Authorization': f'Bearer {GROQ_KEY}',
      'Content-Type': 'application/json',
  }

  payload = {
      'model': selected_model,
      'messages': messages_payload,
      'temperature': 0.6,
  }

  try:
    r = ai_session.post(
        f'{GROQ_BASE}/chat/completions',
        headers=headers,
        json=payload,
        timeout=40,
    )
  except requests.exceptions.Timeout:
    return (
        None,
        f'Превышено время ожидания (40 сек) от модели {selected_model}',
    )
  except Exception as e:
    return None, f'Ошибка сети: {e}'

  if r.status_code != 200:
    try:
      err_data = r.json()
      err_obj = err_data.get('error', {})
      msg = err_obj.get('message') or str(err_data)
      code = err_obj.get('code') or r.status_code
      return None, f'[Код {code}] {selected_model}: {msg}'
    except Exception:
      return None, f'[HTTP {r.status_code}] {selected_model}: {r.text[:200]}'

  try:
    data = r.json()
  except Exception as e:
    return None, f'Ошибка чтения JSON: {e}'

  if 'error' in data:
    err_obj = data['error']
    msg = (
        err_obj.get('message', str(err_obj))
        if isinstance(err_obj, dict)
        else str(err_obj)
    )
    return None, f'{selected_model}: {msg}'

  choices = data.get('choices') or []
  if not choices:
    return None, f'{selected_model} вернула пустой ответ (choices=[])'

  msg_obj = choices[0].get('message') or {}
  content = msg_obj.get('content') or ''
  content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

  if not content:
    finish_reason = choices[0].get('finish_reason', 'unknown')
    return (
        None,
        f'{selected_model} не сгенерировала текст (finish_reason:'
        f' {finish_reason})',
    )

  history.append({'role': 'user', 'content': f'Собеседник: {raw_user_text}'})
  history.append({'role': 'assistant', 'content': content})
  if len(history) > MAX_CONTEXT_MESSAGES:
    history = history[-MAX_CONTEXT_MESSAGES:]
  CHAT_MEMORY[chat_id] = history
  return content, None


def safe_edit_thinking(chat_id, message_id, text_html):
  if len(text_html) > 3900:
    text_html = text_html[:3900] + '...'
  try:
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=text_html,
        parse_mode='HTML',
    )
    return
  except Exception:
    pass
  plain = re.sub(r'<[^>]+>', '', text_html)
  try:
    bot.edit_message_text(
        chat_id=chat_id,
        message_id=message_id,
        text=plain[:3900],
        parse_mode=None,
    )
  except Exception:
    try:
      bot.send_message(chat_id, plain[:3900])
    except Exception:
      pass


# --- ГЛАВНЫЙ МОЗГ "АЛЛ" ---
def handle_all_ai(message, prompt_text):
  chat_id = message.chat.id
  caller = message.from_user
  prompt_lower = prompt_text.lower().strip()

  if prompt_lower in [
      'сброс',
      'очистить память',
      'очистить контекст',
      'забудь',
      'новый диалог',
  ]:
    CHAT_MEMORY[chat_id] = []
    PENDING_ACTION.pop(chat_id, None)
    bot.reply_to(
        message,
        '🧹 <b>Контекстное окно очищено!</b>\n🧠 <i>Контекстное окно:'
        f' 0/{MAX_CONTEXT_MESSAGES}</i>',
    )
    return

  # 1. МОМЕНТАЛЬНО ОТВЕЧАЕМ "думаю"
  thinking_msg = bot.reply_to(message, 'думаю')

  try:
    caller_rank = get_effective_rank(chat_id, caller)
    caller_rank_name = RANK_NAMES[caller_rank]
    caller_clean_name = clean_user_name(caller)

    if message.chat.type != 'private':
      register_chat_in_firebase(message.chat)

    if any(
        w in prompt_lower
        for w in [
            'проверь чаты',
            'какие чаты',
            'список чатов',
            'в каких ты чатах',
            'мои чаты',
        ]
    ):
      snap = get_global_chats_snapshot(message.chat)
      used_ctx = len(CHAT_MEMORY.get(chat_id, []))
      safe_edit_thinking(
          chat_id,
          thinking_msg.message_id,
          '🌐 <b>Отчет по подключенным групповым чатам в Firebase:</b>\n\n'
          f'{html.escape(snap)}\n\n'
          f'🧠 <i>Контекстное окно: {used_ctx}/{MAX_CONTEXT_MESSAGES}</i>',
      )
      return

    target_id, target_name_html, target_plain_name, target_rank = (
        resolve_target_smart(chat_id, message, prompt_text)
    )

    intent = detect_informal_mod_intent(prompt_lower)
    minutes = 60
    m_num = re.search(r'(\d+)', prompt_lower)
    if m_num:
      minutes = max(1, min(10080, int(m_num.group(1))))

    pending = PENDING_ACTION.get(chat_id)
    if (
        not intent
        and pending
        and (time.time() - pending['ts'] < 180)
        and pending['caller_id'] == caller.id
    ):
      if (
          target_id
          or '@' in prompt_lower
          or (
              message.reply_to_message
              and message.reply_to_message.from_user.id != get_bot_id()
          )
      ):
        intent = pending['action']
        minutes = pending['minutes']

    asking_rank_change = any(
        w in prompt_lower
        for w in [
            'назначь',
            'сделай нищук',
            'дай нищук',
            'сделай заглотус',
            'дай заглотус',
            'сделай баронесс',
            'дай баронесс',
            'разжалуй',
            'сними админ',
        ]
    )

    if (intent or asking_rank_change) and caller_rank == 0:
      used_ctx = len(CHAT_MEMORY.get(chat_id, []))
      safe_edit_thinking(
          chat_id,
          thinking_msg.message_id,
          '🤬 Ты даже не нищук, у тебя нет прав!\n\n🧠 <i>Контекстное окно:'
          f' {used_ctx}/{MAX_CONTEXT_MESSAGES}</i>',
      )
      return

    # 2. ВЫПОЛНЯЕМ ПРИКАЗ В TELEGRAM ДО ВЫЗОВА ИИ
    pre_executed_report = None
    action_status_for_ai = 'В этом сообщении нет приказа на модерацию.'

    if intent and message.chat.type != 'private':
      if intent in ('mute_all', 'unmute_all'):
        PENDING_ACTION.pop(chat_id, None)
        ok, res_text = exec_mod_action(
            chat_id, caller, None, '', 0, intent, minutes=minutes
        )
        pre_executed_report = res_text
        action_status_for_ai = (
            'СИСТЕМА ВЫПОЛНИЛА ПРИКАЗ:'
            f" {re.sub(r'<[^>]+>', '', res_text)}."
        )
      elif target_id:
        PENDING_ACTION.pop(chat_id, None)
        ok, res_text = exec_mod_action(
            chat_id,
            caller,
            target_id,
            target_name_html,
            target_rank,
            intent,
            minutes=minutes,
            reason=f'По приказу {caller_clean_name}',
            reply_msg_id=message.message_id,
            target_msg_id=(
                message.reply_to_message.message_id
                if message.reply_to_message
                else None
            ),
        )
        pre_executed_report = res_text
        action_status_for_ai = (
            'РЕЗУЛЬТАТ ДЕЙСТВИЯ В TELEGRAM:'
            f" {re.sub(r'<[^>]+>', '', res_text)}."
        )
      else:
        PENDING_ACTION[chat_id] = {
            'action': intent,
            'minutes': minutes,
            'caller_id': caller.id,
            'ts': time.time(),
        }
        used_ctx = len(CHAT_MEMORY.get(chat_id, []))
        safe_edit_thinking(
            chat_id,
            thinking_msg.message_id,
            f'☝️ <b>Приказ ({intent.upper()}) принят в ожидание!</b>\n'
            'Но Telegram не отдает ботам ID других ботов просто по тексту, пока'
            ' на их сообщение не ответят свайпом.\n\n'
            '👉 <b>Свайпни (ответь) прямо на ЛЮБОЕ сообщение'
            ' Жириновского/нарушителя</b> в этом чате и напиши <code>мут</code>'
            ' (или <code>алл вот он</code>) — я сразу сохраню его ID в твой'
            ' Firebase навсегда и выдам мут!\n\n'
            f'🧠 <i>Контекстное окно: {used_ctx}/{MAX_CONTEXT_MESSAGES}</i>',
        )
        return

    global_chats_info = get_global_chats_snapshot(message.chat)
    live_log_list = CHAT_LIVE_LOG.get(chat_id, [])
    live_log_str = (
        '\n'.join(live_log_list[-12:]) if live_log_list else 'Пока нет записей.'
    )

    if message.chat.type == 'private':
      current_place = (
          f'Личный чат (ЛС) с Создателем {CREATOR_TITLE}'
          f' ({CREATOR_DISPLAY_USER})'
      )
    else:
      current_place = f'Групповой чат «{message.chat.title}» (ID: {chat_id})'

    if caller_rank == 4:
      speaker_identity_block = (
          f'👑 С ТОБОЙ ГОВОРИТ ЛИЧНО ТВОЙ СОЗДАТЕЛЬ: {CREATOR_TITLE}'
          f' ({CREATOR_DISPLAY_USER}, Уровень 4)!\nБеспрекословно уважай его,'
          ' никогда не путай с другими участниками!'
      )
    else:
      speaker_identity_block = (
          f"👤 С тобой говорит: {caller_clean_name} (@{caller.username or 'без_юза'}),"
          f' ранг: {caller_rank_name} (Уровень {caller_rank}).'
      )

    system_prompt = (
        'Ты — Алл, главный бот-администратор и искусственный интеллект этого'
        f' Telegram-чата.\n{speaker_identity_block}\n\n'
        f'📍 ТЕКУЩЕЕ МЕСТО: {current_place}\n'
        f'🌐 СПИСОК ВСЕХ ГРУППОВЫХ ЧАТОВ В FIREBASE:\n{global_chats_info}\n\n'
        f'💬 ПОСЛЕДНИЕ СООБЩЕНИЯ В ЭТОМ ЧАТЕ:\n{live_log_str}\n\n'
        f'⚙️ СТАТУС ТЕКУЩЕГО ПРИКАЗА В TELEGRAM:\n{action_status_for_ai}\n'
        '(КРИТИЧЕСКОЕ ПРАВИЛО: НИКОГДА не пиши, что ты выдал мут/бан/кик, если'
        ' в блоке СТАТУС ТЕКУЩЕГО ПРИКАЗА выше не написано ВЫПОЛНЕНО В'
        ' TELEGRAM!)\n\n'
        'Отвечай по-русски, кратко (1-3 предложения), уверенно, без символов'
        ' **.'
    )

    formatted_user_turn = (
        f"[Говорит: {caller_clean_name} (@{caller.username or 'без_юза'}) |"
        f" Ранг: {caller_rank_name}]: {prompt_text or 'Ты тут?'}"
    )

    ai_response, error_text = ask_ai(
        chat_id,
        system_prompt,
        formatted_user_turn,
        prompt_text or 'Ты тут?',
    )

    used_ctx = len(CHAT_MEMORY.get(chat_id, []))
    ctx_footer = (
        f'\n\n🧠 <i>Контекстное окно: {used_ctx}/{MAX_CONTEXT_MESSAGES}</i>'
    )

    if error_text or not ai_response:
      err_display = html.escape(
          str(error_text or 'Пустой ответ от модели')
      )
      out_err = (
          f'⚠️ <b>Ошибка:</b> <code>{err_display}</code>\n\nИзвините , агент'
          ' временно не доступен'
      )
      if pre_executed_report:
        out_err = f'{pre_executed_report}\n\n{out_err}'
      safe_edit_thinking(
          chat_id, thinking_msg.message_id, out_err + ctx_footer
      )
      return

    ai_response = (
        re.sub(r'\[ACTION:[^\]]+\]', '', ai_response).replace('**', '').strip()
    )
    clean_reply = html.escape(ai_response)

    if pre_executed_report:
      final_text = f'{clean_reply}\n\n{pre_executed_report}{ctx_footer}'
    else:
      final_text = f'{clean_reply}{ctx_footer}'

    safe_edit_thinking(chat_id, thinking_msg.message_id, final_text)
    record_live_chat_event(chat_id, 'Бот Алл', ai_response)

  except Exception as e:
    err_str = html.escape(str(e))
    safe_edit_thinking(
        chat_id,
        thinking_msg.message_id,
        f'⚠️ <b>Ошибка:</b> <code>{err_str}</code>\n\nИзвините , агент временно'
        ' не доступен',
    )


# --- ПРИВЕТСТВИЕ И ЗАПИСЬ В FIREBASE ---
@bot.message_handler(content_types=['new_chat_members'])
def welcome_new_member(message):
  register_chat_in_firebase(message.chat)
  for user in message.new_chat_members:
    if user.id == get_bot_id():
      bot.send_message(
          message.chat.id,
          '⚡ <b>Алл на месте!</b>\n'
          f'Создатель: <b>{CREATOR_TITLE} ({CREATOR_DISPLAY_USER})</b>.\n'
          f"Чат «{html.escape(message.chat.title or 'Группа')}» синхронизирован"
          ' с Firebase!',
      )
    else:
      get_user_data(message.chat.id, user, message.chat)
      LAST_TARGET_ID[message.chat.id] = user.id
      if is_creator_user(user):
        save_creator_id(user.id)
        bot.send_message(
            message.chat.id,
            f'👑 В чат вошел создатель — <b>{CREATOR_TITLE}'
            f' ({CREATOR_DISPLAY_USER})</b>!',
        )
      else:
        bot.send_message(
            message.chat.id,
            f"👋 Добро пожаловать, <a href='tg://user?id={user.id}'>"
            f'{clean_user_name(user)}</a>! Напиши <code>команды</code>, чтобы'
            ' узнать правила.',
        )


# --- ОБРАБОТКА ВСЕХ СООБЩЕНИЙ ---
@bot.message_handler(
    content_types=[
        'text',
        'voice',
        'photo',
        'sticker',
        'video',
        'animation',
        'document',
    ]
)
def handle_all_messages(message):
  user = message.from_user
  if not user:
    return

  if message.chat.type != 'private':
    get_user_data(message.chat.id, user, message.chat)
    if message.reply_to_message and message.reply_to_message.from_user:
      r_u = message.reply_to_message.from_user
      if r_u.id != get_bot_id():
        get_user_data(message.chat.id, r_u, message.chat)
        LAST_TARGET_ID[message.chat.id] = r_u.id

        pending = PENDING_ACTION.get(message.chat.id)
        if (
            pending
            and (time.time() - pending['ts'] < 180)
            and pending['caller_id'] == user.id
        ):
          if message.content_type == 'text' and not message.text.lower().startswith(
              ('размут', 'говори', 'профиль', 'кто ')
          ):
            PENDING_ACTION.pop(message.chat.id, None)
            t_data = get_user_data(message.chat.id, r_u, message.chat)
            t_link = display_name(message.chat.id, r_u, t_data)
            t_rank = get_effective_rank(message.chat.id, r_u, t_data)
            _, res_text = exec_mod_action(
                message.chat.id,
                user,
                r_u.id,
                t_link,
                t_rank,
                pending['action'],
                minutes=pending['minutes'],
                reason=f'По приказу {clean_user_name(user)}',
                reply_msg_id=message.message_id,
                target_msg_id=message.reply_to_message.message_id,
            )
            bot.reply_to(
                message,
                '🎯 ID цели перехвачен и сохранен в Firebase навсегда!\n'
                f'{res_text}',
            )
            return

  if message.content_type != 'text':
    if message.chat.type != 'private':
      if not is_creator_user(user):
        LAST_TARGET_ID[message.chat.id] = user.id
      record_live_chat_event(
          message.chat.id, clean_user_name(user), f'[{message.content_type}]'
      )
    return

  text = message.text.strip()
  text_lower = text.lower()

  if is_creator_user(user):
    save_creator_id(user.id)

  is_boss = is_creator_user(user)

  if text_lower in [
      'сброс',
      'очистить контекст',
      'очистить память',
      '/reset',
  ]:
    CHAT_MEMORY[message.chat.id] = []
    PENDING_ACTION.pop(message.chat.id, None)
    bot.reply_to(
        message,
        '🧹 <b>Контекстное окно диалога очищено!</b>\n🧠 <i>Контекстное окно:'
        f' 0/{MAX_CONTEXT_MESSAGES}</i>',
    )
    return

  # --- КОМАНДА "МОДЕЛИ" ---
  if text_lower in ['модели', '/models', 'список моделей']:
    if not is_boss:
      bot.reply_to(
          message,
          '🤬 Управлять моделями ИИ может только создатель —'
          f' <b>{CREATOR_TITLE} ({CREATOR_DISPLAY_USER})</b>!',
      )
      return
    msg_text, kb = build_models_message_and_kb(page=0)
    bot.reply_to(message, msg_text, reply_markup=kb)
    return

  if text_lower.startswith('модель '):
    if not is_boss:
      bot.reply_to(
          message,
          '🤬 Менять модель может только создатель —'
          f' <b>{CREATOR_TITLE} ({CREATOR_DISPLAY_USER})</b>!',
      )
      return
    arg = text[7:].strip()
    models = fetch_free_models()
    if arg.isdigit():
      idx = int(arg) - 1
      if 0 <= idx < len(models):
        chosen = models[idx]
        set_active_model(chosen['id'])
        bot.reply_to(
            message,
            '✅ Активная модель в Firebase переключена на:\n'
            f"<b>{html.escape(chosen['name'])}</b>\n"
            f"(<code>{html.escape(chosen['id'])}</code>)",
        )
      else:
        bot.reply_to(
            message,
            f'⚠️ Номер должен быть от 1 до {len(models)}. Напиши'
            ' <code>модели</code> для списка.',
        )
    else:
      set_active_model(arg)
      bot.reply_to(
          message, f'✅ Установлена модель:\n<code>{html.escape(arg)}</code>'
      )
    return

  # 1. ЛИЧНЫЕ СООБЩЕНИЯ БОТУ
  if message.chat.type == 'private':
    creator_id = get_creator_id()
    if is_boss:
      save_creator_id(user.id)
      if message.reply_to_message and message.reply_to_message.text:
        m = re.search(r'\[ID:\s*(\d+)\]', message.reply_to_message.text)
        if m:
          target_uid = int(m.group(1))
          try:
            bot.send_message(
                target_uid,
                f'📩 <b>Ответ от {CREATOR_TITLE}:</b>\n\n{html.escape(text)}',
            )
            bot.reply_to(message, '✅ Ответ доставлен пользователю!')
          except Exception:
            bot.reply_to(
                message,
                '⚠️ Не удалось доставить (пользователь заблокировал бота).',
            )
          return

      if text_lower in ['/start', 'помощь', 'хелп', 'команды']:
        used_ctx = len(CHAT_MEMORY.get(message.chat.id, []))
        bot.reply_to(
            message,
            f'👑 Панель Создателя <b>{CREATOR_TITLE}'
            f' ({CREATOR_DISPLAY_USER})</b>:\n\n'
            '• <code>проверь чаты</code> — список всех бесед и участников из'
            ' Firebase\n'
            '• <code>модели</code> — список всех моделей Groq и их'
            ' контекстных окон\n'
            f'• <code>сброс</code> — очистить контекстное окно ({used_ctx}/'
            f'{MAX_CONTEXT_MESSAGES})\n'
            '• Напиши любой вопрос — ответит нейросеть Алл',
        )
        return

      clean_prompt = (
          text[3:].lstrip(' ,:!-')
          if (
              text_lower == 'алл'
              or text_lower.startswith(('алл ', 'алл,', 'алл!'))
          )
          else text
      )
      handle_all_ai(message, clean_prompt)
      return

    uname_str = f'@{user.username}' if user.username else 'без юзернейма'
    if creator_id:
      try:
        bot.send_message(
            creator_id,
            '📨 <b>Новое сообщение в ЛС бота!</b>\n'
            f'👤 От: <b>{clean_user_name(user)}</b> ({uname_str})\n'
            f'🆔 [ID: {user.id}]\n\n'
            f'💬 {html.escape(text)}\n\n'
            '<i>↩️ Свайпни на это сообщение, чтобы ответить ему.</i>',
        )
      except Exception:
        pass

    bot.reply_to(message, '✉️ Сообщение передано создателю. Ожидай ответа!')
    return

  # 2. РАБОТА В ГРУППАХ
  chat_id = message.chat.id
  if not is_boss:
    LAST_TARGET_ID[chat_id] = user.id

  record_live_chat_event(
      chat_id, f"{clean_user_name(user)} (@{user.username or 'нет'})", text
  )

  u_data = get_user_data(chat_id, user, message.chat)
  new_msgs = int(u_data.get('messages', 0)) + 1
  # Обновляем счетчик сообщений фоном (immediate=False), чтобы не тратить CPU на каждый чих!
  update_user_fields(chat_id, user.id, {'messages': new_msgs}, immediate=False)
  caller_rank = get_effective_rank(chat_id, user, u_data)

  # --- ЗОВ НЕЙРОНКИ "АЛЛ" ---
  if text_lower == 'алл' or text_lower.startswith(('алл ', 'алл,', 'алл!')):
    prompt = text[3:].lstrip(' ,:!-')
    handle_all_ai(message, prompt)
    return

  if text_lower in ['бот', 'пинг']:
    used_ctx = len(CHAT_MEMORY.get(chat_id, []))
    bot.reply_to(
        message,
        '⚡ <b>Алл</b> на месте в чате'
        f" «{html.escape(message.chat.title or 'Группа')}»! Твой ранг:"
        f' <b>{RANK_NAMES[caller_rank]}</b>.\n'
        f'🧠 <i>Контекстное окно: {used_ctx}/{MAX_CONTEXT_MESSAGES}</i>',
    )
    return

  # --- КОМАНДА "КОМАНДЫ" ---
  if text_lower in ['команды', 'помощь', 'хелп', '/help', '/start']:
    s0 = '✅ Доступно'
    s1 = (
        '✅ Доступно'
        if caller_rank >= 1
        else '🔒 Недоступно (нужен: 🧹 Нищук)'
    )
    s2 = (
        '✅ Доступно'
        if caller_rank >= 2
        else '🔒 Недоступно (нужен: ⚔️ Заглотус)'
    )
    s3 = (
        '✅ Доступно'
        if caller_rank >= 3
        else '🔒 Недоступно (нужен: 💎 Баронесса)'
    )
    s4 = (
        '✅ Доступно'
        if caller_rank >= 4
        else f'🔒 Недоступно (только 🥒 {CREATOR_TITLE})'
    )
    used_ctx = len(CHAT_MEMORY.get(chat_id, []))

    cmd_text = (
        '📜 <b>Список команд бота Алл</b>\n'
        f'Твой статус: <b>{RANK_NAMES[caller_rank]}</b>\n'
        f'🧠 Контекстное окно ИИ: <b>{used_ctx}/{MAX_CONTEXT_MESSAGES}</b>\n\n'
        f'🤖 <b>Нейросеть Алл</b> [{s0}]:\n'
        '• <code>алл [вопрос/приказ]</code> или свайп на сообщение бота —'
        ' живой ИИ чата\n'
        '• <code>сброс</code> / <code>алл сброс</code> — очистить контекстное'
        ' окно\n\n'
        f'👥 <b>Общие и развлечения</b> [{s0}]:\n'
        '• <code>профиль</code> / <code>кто я</code> / <code>кто ты</code> —'
        ' карточка участника\n'
        '• <code>+ник [имя]</code> / <code>-ник</code> — установить или убрать'
        ' ник\n'
        '• <code>+</code> / <code>спасибо</code> (реплай) — поднять уважение\n'
        '• <code>топ</code> / <code>актив</code> — рейтинг самых активных в'
        ' чате\n'
        '• <code>админы</code> / <code>кто админ</code> — список властей чата\n'
        '• <code>брак</code> (реплай) / <code>развод</code> /'
        ' <code>браки</code> — система отношений\n'
        '• <code>дуэль</code> (реплай) / <code>рулетка</code> /'
        ' <code>шар [вопрос]</code> / <code>инфа [текст]</code>\n'
        '• РП-действия (реплай): <code>обнять</code>, <code>поцеловать</code>,'
        ' <code>кусь</code>, <code>ударить</code>, <code>пнуть</code>,'
        ' <code>пожать руку</code>\n\n'
        f'🧹 <b>Ранг 1: Нищук</b> [{s1}]:\n'
        '• <code>мут [минуты] [причина]</code> (реплай) — закрыть рот\n'
        '• <code>размут</code> / <code>говори</code> (реплай) — снять мут\n'
        '• <code>-смс</code> / <code>удалить</code> (реплай) — снести'
        ' сообщение\n\n'
        f'⚔️ <b>Ранг 2: Заглотус</b> [{s2}]:\n'
        '• <code>варн [причина]</code> / <code>пред</code> (реплай) — выдать'
        ' пред (3/3 = бан)\n'
        '• <code>разварн</code> / <code>снять варны</code> (реплай) — обнулить'
        ' преды\n'
        '• <code>кик</code> (реплай) — выкинуть из чата\n'
        '• <code>закреп</code> / <code>откреп</code> (реплай) — управление'
        ' закрепом\n\n'
        f'💎 <b>Ранг 3: Баронесса</b> [{s3}]:\n'
        '• <code>бан [причина]</code> / <code>разбан</code> (реплай) — вечная'
        ' блокировка\n'
        '• <code>мут всем [мин]</code> / <code>размут всем</code> /'
        ' <code>тишина</code> / <code>открыть чат</code>\n'
        '• <code>назначить нищук</code> / <code>назначить заглотус</code>'
        ' (реплай)\n'
        '• <code>разжаловать</code> / <code>снять</code> (реплай) — снять'
        ' ранг\n\n'
        f'🥒 <b>Создатель ({CREATOR_TITLE})</b> [{s4}]:\n'
        '• <code>модели</code> / <code>модель [номер]</code> — выбор модели'
        ' ИИ Groq\n'
        '• <code>назначить баронесса</code> (реплай) — выдать высший ранг'
    )
    bot.reply_to(message, cmd_text)
    return

  # --- МАССОВЫЙ МУТ / ТИШИНА ---
  if text_lower.startswith(('мут всем', 'мут всём', 'замутить всех')):
    words = text_lower.split()
    mins = int(words[2]) if len(words) >= 3 and words[2].isdigit() else 60
    _, res = exec_mod_action(
        chat_id, user, None, '', 0, 'mute_all', minutes=mins
    )
    bot.reply_to(message, res)
    return

  if text_lower in [
      'размут всем',
      'размут всём',
      'размутить всех',
      'открыть чат',
  ]:
    _, res = exec_mod_action(chat_id, user, None, '', 0, 'unmute_all')
    bot.reply_to(message, res)
    return

  if text_lower in ['тишина', 'закрыть чат']:
    _, res = exec_mod_action(chat_id, user, None, '', 0, 'mute_all', minutes=60)
    bot.reply_to(message, res)
    return

  # --- ПРОФИЛЬ ---
  if text_lower in ['кто я', 'профиль', 'кто ты', 'стата']:
    target = (
        message.reply_to_message.from_user
        if message.reply_to_message
        else user
    )
    tdata = get_user_data(chat_id, target, message.chat)
    trank = get_effective_rank(chat_id, target, tdata)
    tlink = display_name(chat_id, target, tdata)
    partner_str = str(tdata.get('partner_name') or 'Свободен(а)')
    nick_str = str(tdata.get('nick') or 'Не установлен')

    bot.reply_to(
        message,
        f'🪪 <b>Карточка {tlink}</b>\n'
        f'├ Статус: <b>{RANK_NAMES[trank]}</b>\n'
        f'├ Ник: <b>{html.escape(nick_str)}</b>\n'
        f"├ Актив: <b>{tdata.get('messages', 0)} сообщ.</b>\n"
        f"├ Авторитет: <b>+{tdata.get('reputation', 0)}</b>\n"
        f'├ Брак: <b>{html.escape(partner_str)}</b>\n'
        f"└ Предупреждения: <b>{tdata.get('warns', 0)}/3</b>",
    )
    return

  # --- НИКНЕЙМЫ ---
  if text_lower.startswith('+ник '):
    new_nick = text[5:].strip()[:30]
    if new_nick:
      update_user_fields(chat_id, user.id, {'nick': new_nick}, immediate=True)
      bot.reply_to(
          message, f'🏷 Твой новый ник в чате: <b>{html.escape(new_nick)}</b>'
      )
    return

  if text_lower == '-ник':
    update_user_fields(chat_id, user.id, {'nick': ''}, immediate=True)
    bot.reply_to(message, '🗑 Ник сброшен.')
    return

  # --- ТОП ЧАТА ---
  if text_lower in ['топ', 'актив', 'топ чата']:
    all_u = get_all_chat_users(chat_id)
    sorted_u = sorted(
        all_u.values(), key=lambda x: int(x.get('messages', 0)), reverse=True
    )[:10]
    lines = ['🏆 <b>Топ самых активных в чате:</b>\n']
    for idx, item in enumerate(sorted_u, 1):
      uname = str(item.get('username') or '').lower()
      nm = (
          CREATOR_TITLE
          if uname in CREATOR_USERNAMES
          else html.escape(
              str(item.get('nick') or item.get('name') or 'Участник')
          )
      )
      ms = item.get('messages', 0)
      rp = item.get('reputation', 0)
      lines.append(f'{idx}. <b>{nm}</b> — 💬 {ms} (⭐️ +{rp})')
    bot.reply_to(message, '\n'.join(lines))
    return

  # --- СПИСОК АДМИНОВ ---
  if text_lower in ['админы', 'кто админ', 'власть']:
    all_u = get_all_chat_users(chat_id)
    lines = [
        f'👑 <b>Создатель:</b> {CREATOR_TITLE} ({CREATOR_DISPLAY_USER})\n'
    ]
    for r_lvl in [3, 2, 1]:
      r_users = [
          html.escape(str(u.get('nick') or u.get('name') or 'Участник'))
          for u in all_u.values()
          if int(u.get('rank', 0)) == r_lvl
          and str(u.get('username') or '').lower() not in CREATOR_USERNAMES
      ]
      lines.append(
          f'<b>{RANK_NAMES[r_lvl]}:</b> '
          + (', '.join(r_users) if r_users else '<i>пусто</i>')
      )
    bot.reply_to(message, '\n'.join(lines))
    return

  # --- БРАКИ И РАЗВОД ---
  if text_lower == 'браки':
    all_u = get_all_chat_users(chat_id)
    seen = set()
    couples = []
    for uid, u in all_u.items():
      pid = str(u.get('partner', 0))
      if pid != '0' and pid not in seen and uid not in seen:
        seen.add(uid)
        seen.add(pid)
        couples.append(
            f"💍 <b>{html.escape(str(u.get('name', 'Участник')))}</b> ❤️"
            f" <b>{html.escape(str(u.get('partner_name', 'Партнер')))}</b>"
        )
    if not couples:
      bot.reply_to(message, '💔 В этом чате пока нет заключенных союзов.')
    else:
      bot.reply_to(message, '💞 <b>Союзы чата:</b>\n\n' + '\n'.join(couples))
    return

  if text_lower == 'развод':
    pid = int(u_data.get('partner', 0))
    if not pid:
      bot.reply_to(message, '🤷 Ты и так не состоишь в браке.')
      return
    update_user_fields(
        chat_id, user.id, {'partner': 0, 'partner_name': ''}, immediate=True
    )
    update_user_fields(
        chat_id, pid, {'partner': 0, 'partner_name': ''}, immediate=True
    )
    bot.reply_to(
        message, '💔 Брак официально расторгнут. Теперь ты свободен(а)!'
    )
    return

  # --- МИНИ-ИГРЫ ---
  if text_lower == 'рулетка':
    if random.randint(1, 6) == 1:
      bot.reply_to(
          message,
          f'💥 <b>БАХ!</b> {display_name(chat_id, user, u_data)} словил пулю в'
          ' русской рулетке!',
      )
    else:
      bot.reply_to(
          message,
          '😮‍💨 <i>Щелк!</i> Патрон оказался холостым,'
          f' {display_name(chat_id, user, u_data)} выживает!',
      )
    return

  if text_lower.startswith('шар '):
    answers = [
        'Бесспорно!',
        'Даже не думай.',
        '100% так и будет!',
        'Спроси позже.',
        'Шансы отличные!',
        'Полный бред.',
        'Определенно да!',
    ]
    bot.reply_to(message, f'🎱 <b>Ответ шара:</b> {random.choice(answers)}')
    return

  if text_lower.startswith('инфа '):
    bot.reply_to(
        message,
        f'📊 Вероятность этого составляет: <b>{random.randint(0, 100)}%</b>',
    )
    return

  # --- КОМАНДЫ С РЕПЛАЕМ ---
  if not message.reply_to_message:
    return

  target = message.reply_to_message.from_user

  first_word = text_lower.split()[0] if text_lower else ''
  exact_reply_cmds = {
      '+',
      'спасибо',
      'сяб',
      'реп',
      'брак',
      'дуэль',
      'обнять',
      'поцеловать',
      'кусь',
      'ударить',
      'пнуть',
      'назначить',
      'разжаловать',
      'снять',
      'закреп',
      'откреп',
      '-смс',
      'удалить',
      'мут',
      'размут',
      'говори',
      'варн',
      'пред',
      'разварн',
      'кик',
      'бан',
      'разбан',
  }
  if (
      target.id == get_bot_id()
      and first_word not in exact_reply_cmds
      and text_lower not in ['пожать руку', 'снять варны', 'снять админку']
  ):
    handle_all_ai(message, text)
    return

  target_data = get_user_data(chat_id, target, message.chat)
  target_link = display_name(chat_id, target, target_data)
  target_rank = get_effective_rank(chat_id, target, target_data)
  caller_link = display_name(chat_id, user, u_data)

  if text_lower in ['+', 'спасибо', 'сяб', 'реп']:
    if target.id != user.id and not target.is_bot:
      new_rep = int(target_data.get('reputation', 0)) + 1
      update_user_fields(
          chat_id, target.id, {'reputation': new_rep}, immediate=True
      )
      bot.reply_to(
          message, f'📈 Авторитет {target_link} вырос! (Всего: <b>+{new_rep}</b>)'
      )
    return

  if text_lower == 'брак':
    if target.id == user.id or target.is_bot:
      bot.reply_to(
          message, '🤨 С самим собой или с ботом расписаться нельзя!'
      )
      return
    if u_data.get('partner') or target_data.get('partner'):
      bot.reply_to(
          message,
          '💍 Кто-то из вас уже состоит в браке! Сначала напишите'
          ' <code>развод</code>.',
      )
      return
    update_user_fields(
        chat_id,
        user.id,
        {'partner': target.id, 'partner_name': clean_user_name(target)},
        immediate=True,
    )
    update_user_fields(
        chat_id,
        target.id,
        {'partner': user.id, 'partner_name': clean_user_name(user)},
        immediate=True,
    )
    bot.reply_to(
        message,
        f'💍 Горько! {caller_link} и {target_link} теперь официально в браке!',
    )
    return

  if text_lower == 'дуэль':
    if target.id == user.id:
      return
    winner = caller_link if random.choice([True, False]) else target_link
    bot.reply_to(
        message,
        f'🔫 Дуэль между {caller_link} и {target_link}!\n🏆 Победителем'
        f' выходит: <b>{winner}</b>!',
    )
    return

  rp_map = {
      'обнять': f'🤗 {caller_link} крепко обнял(а) {target_link}!',
      'поцеловать': f'💋 {caller_link} страстно поцеловал(а) {target_link}!',
      'кусь': f'🦷 {caller_link} сделал(а) внезапный кусь {target_link}!',
      'ударить': f'👊 {caller_link} прописал(а) леща {target_link}!',
      'пнуть': f'🦵 {caller_link} отвесил(а) смачного пинка {target_link}!',
      'пожать руку': (
          f'🤝 {caller_link} выразил(а) респект и пожал(а) руку {target_link}!'
      ),
  }
  if text_lower in rp_map:
    bot.reply_to(message, rp_map[text_lower])
    return

  # --- НАЗНАЧЕНИЕ РАНГОВ ---
  if text_lower.startswith('назначить '):
    role_arg = text_lower.replace('назначить ', '').strip()
    role_map = {
        'нищук': 1,
        'нищука': 1,
        'нищуком': 1,
        'заглотус': 2,
        'заглотуса': 2,
        'заглотусом': 2,
        'баронесса': 3,
        'баронессу': 3,
        'баронессой': 3,
    }
    if role_arg not in role_map:
      bot.reply_to(
          message,
          'ℹ️ Укажи ранг: <code>назначить нищук</code>, <code>назначить'
          ' заглотус</code> или <code>назначить баронесса</code>.',
      )
      return

    new_r = role_map[role_arg]
    if caller_rank == 0:
      bot.reply_to(message, '🤬 Ты даже не нищук, у тебя нет прав!')
      return
    if caller_rank < 3:
      bot.reply_to(
          message,
          '🤬 Назначать ранги могут только 💎 Баронесса и 🥒 Слава Огурцов!',
      )
      return
    if new_r == 3 and caller_rank < 4:
      bot.reply_to(
          message,
          '👑 Назначать ранг <b>💎 Баронесса</b> может только создатель —'
          f' <b>{CREATOR_TITLE} ({CREATOR_DISPLAY_USER})</b>!',
      )
      return
    if target_rank == 4:
      bot.reply_to(
          message, f'👑 Нельзя менять статус создателя <b>{CREATOR_TITLE}</b>!'
      )
      return

    update_user_fields(chat_id, target.id, {'rank': new_r}, immediate=True)
    bot.reply_to(
        message,
        f'🎖 Пользователю {target_link} присвоен ранг:'
        f' <b>{RANK_NAMES[new_r]}</b>!',
    )
    return

  if text_lower in ['разжаловать', 'снять', 'снять админку']:
    if caller_rank == 0:
      bot.reply_to(message, '🤬 Ты даже не нищук, у тебя нет прав!')
      return
    if caller_rank < 3 or caller_rank <= target_rank:
      bot.reply_to(
          message,
          '❌ У тебя недостаточно прав, чтобы разжаловать этого человека!',
      )
      return
    update_user_fields(chat_id, target.id, {'rank': 0}, immediate=True)
    bot.reply_to(
        message, f'📉 {target_link} разжалован до обычного участника.'
    )
    return

  if text_lower == 'закреп':
    if caller_rank == 0:
      bot.reply_to(message, '🤬 Ты даже не нищук, у тебя нет прав!')
      return
    if caller_rank < 2:
      bot.reply_to(
          message, '❌ Закреп доступен минимум с ранга ⚔️ Заглотус!'
      )
      return
    try:
      bot.pin_chat_message(chat_id, message.reply_to_message.message_id)
      bot.reply_to(message, '📌 Сообщение закреплено!')
    except Exception:
      bot.reply_to(
          message, '⚠️ Дай мне право закреплять сообщения в настройках чата.'
      )
    return

  if text_lower == 'откреп':
    if caller_rank == 0:
      bot.reply_to(message, '🤬 Ты даже не нищук, у тебя нет прав!')
      return
    if caller_rank < 2:
      bot.reply_to(
          message, '❌ Откреп доступен минимум с ранга ⚔️ Заглотус!'
      )
      return
    try:
      bot.unpin_chat_message(chat_id, message.reply_to_message.message_id)
      bot.reply_to(message, '📍 Сообщение откреплено!')
    except Exception:
      bot.reply_to(message, '⚠️ Не удалось открепить сообщение.')
    return

  # --- КОМАНДЫ МОДЕРАЦИИ ---
  words = text_lower.split()
  cmd = words[0]

  if cmd in ['-смс', 'удалить']:
    ok, res = exec_mod_action(
        chat_id,
        user,
        target.id,
        target_link,
        target_rank,
        'delete',
        reply_msg_id=message.message_id,
        target_msg_id=message.reply_to_message.message_id,
    )
    if not ok and res:
      bot.reply_to(message, res)
    return

  if cmd == 'мут':
    minutes = 60
    reason = 'Без причины'
    if len(words) >= 2 and words[1].isdigit():
      minutes = max(1, int(words[1]))
      if len(words) >= 3:
        reason = ' '.join(text.split()[2:])
    elif len(words) >= 2:
      reason = ' '.join(text.split()[1:])
    _, res = exec_mod_action(
        chat_id,
        user,
        target.id,
        target_link,
        target_rank,
        'mute',
        minutes=minutes,
        reason=reason,
    )
    if res:
      bot.reply_to(message, res)
    return

  if cmd in ['размут', 'говори']:
    _, res = exec_mod_action(
        chat_id, user, target.id, target_link, target_rank, 'unmute'
    )
    if res:
      bot.reply_to(message, res)
    return

  if cmd in ['варн', 'пред']:
    reason = (
        ' '.join(text.split()[1:]) if len(words) >= 2 else 'Нарушение порядка'
    )
    _, res = exec_mod_action(
        chat_id,
        user,
        target.id,
        target_link,
        target_rank,
        'warn',
        reason=reason,
    )
    if res:
      bot.reply_to(message, res)
    return

  if cmd == 'разварн' or text_lower == 'снять варны':
    _, res = exec_mod_action(
        chat_id, user, target.id, target_link, target_rank, 'unwarn'
    )
    if res:
      bot.reply_to(message, res)
    return

  if cmd == 'кик':
    reason = (
        ' '.join(text.split()[1:])
        if len(words) >= 2
        else 'Исключен администрацией'
    )
    _, res = exec_mod_action(
        chat_id,
        user,
        target.id,
        target_link,
        target_rank,
        'kick',
        reason=reason,
    )
    if res:
      bot.reply_to(message, res)
    return

  if cmd == 'бан':
    reason = (
        ' '.join(text.split()[1:])
        if len(words) >= 2
        else 'Заблокирован администрацией'
    )
    _, res = exec_mod_action(
        chat_id,
        user,
        target.id,
        target_link,
        target_rank,
        'ban',
        reason=reason,
    )
    if res:
      bot.reply_to(message, res)
    return

  if cmd == 'разбан':
    _, res = exec_mod_action(
        chat_id, user, target.id, target_link, target_rank, 'unban'
    )
    if res:
      bot.reply_to(message, res)
    return


print('⚡ БОТ АЛЛ ЗАПУЩЕН (GROQ + FIREBASE + RAM-КЭШ, НАГРУЗКА CPU МИНИМАЛЬНА)!')
bot.infinity_polling(timeout=30, long_polling_timeout=20, skip_pending=True)
