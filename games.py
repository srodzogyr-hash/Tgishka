import html
import re
import time
import random
import threading
import requests
import sys
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

main = sys.modules.get('bot') or sys.modules['__main__']

bot = main.bot
CONFIG = main.CONFIG
CHAT_MEMORY = main.CHAT_MEMORY
CHAT_LOG = main.CHAT_LOG
FIREBASE_URL = main.FIREBASE_URL
CREATOR_USERNAME = main.CREATOR_USERNAME
GROQ_API_KEY = main.GROQ_API_KEY
GROQ_API_BASE = main.GROQ_API_BASE
GEMINI_API_BASE = main.GEMINI_API_BASE
GEMINI_KEYS = main.GEMINI_KEYS
EXHAUSTED_QUOTAS = main.EXHAUSTED_QUOTAS
KEY_STATS = main.KEY_STATS
get_http_session = main.get_http_session
safe_edit_message = main.safe_edit_message
save_config = main.save_config
ask_gemini = main.ask_gemini
register_creator_id = main.register_creator_id

# --- НАСТРОЙКИ ПАРАМЕТРОВ И МОДЕРАЦИИ ПО УМОЛЧАНИЮ ---
DEFAULT_MOD_CONFIG = {
    'looting_cd_minutes': 5,
    'game_round_seconds': 60,
    'game_max_rounds': 3,
    'game_host_model': 'auto_gemini',
    'pm_spy_enabled': True,           # Пересылать ли владельцу действия юзеров в ЛС бота
    'reward_bunker': 1000,            # Приз за победу в Бункере / Мафии
    'reward_hangman': 200,            # Приз за победу в Виселице
    'reward_rps': 50,                 # Приз за победу в КНБ
    'vaccine_cost': 80,               # Цена вакцины
    'base_patogen_minutes': 15,       # Базовое время синтеза 1 патогена в минутах
    'base_infect_chance': 55          # Базовый шанс заражения в %
}

for k_def, v_def in DEFAULT_MOD_CONFIG.items():
    if k_def not in CONFIG:
        CONFIG[k_def] = v_def

if CONFIG.get('game_host_model') == 'groq/openai/gpt-oss-120b':
    CONFIG['game_host_model'] = 'auto_gemini'

for dict_k in ('allowed_users', 'bot_banned_users'):
    if not isinstance(CONFIG.get(dict_k), dict):
        CONFIG[dict_k] = {}

BIZ_CONN_OWNER_CACHE = {}

# Логи переписки пользователей с ботом в ЛС и карта реплаев для ответа от имени бота
BOT_PM_LOGS = {}          # uid_str -> {'name': str, 'username': str, 'log': [str]}
FORWARDED_PM_MAP = {}     # admin_msg_id -> target_uid_int
ADMIN_LIVE_CHAT_UID = None  # ID юзера, с которым владелец сейчас ведет прямой диалог от лица бота

# =====================================================================
# 🔒 0. ЗАЩИТА ТГ БИЗНЕС, ПРОВЕРКА ПРАВ И ПЕРЕСЫЛКА ЛС БОТА
# =====================================================================
def is_owner_user(user):
    if not user:
        return False
    uname = (user.username or '').lower()
    if uname == CREATOR_USERNAME.lower():
        return True
    if main.CREATOR_ID and user.id == int(main.CREATOR_ID):
        return True
    if CONFIG.get('creator_id') and user.id == int(CONFIG['creator_id']):
        return True
    return False

def is_user_banned_in_bot(user):
    if not user or is_owner_user(user):
        return False
    uname = (user.username or '').lower()
    uid_str = str(user.id)
    banned = CONFIG.get('bot_banned_users', {})
    return (uname and uname in banned) or (uid_str in banned)

def is_user_authorized_for_ai(user):
    if not user:
        return False
    if is_owner_user(user):
        return True
    uname = (user.username or '').lower()
    uid_str = str(user.id)
    allowed = CONFIG.get('allowed_users', {})
    return (uname and uname in allowed) or (uid_str in allowed)

def is_business_connection_authorized(biz_id):
    if not biz_id:
        return True
    if biz_id in BIZ_CONN_OWNER_CACHE:
        return BIZ_CONN_OWNER_CACHE[biz_id]
    try:
        conn = bot.get_business_connection(biz_id)
        if conn and getattr(conn, 'user', None):
            register_creator_id(conn.user)
            ok = is_user_authorized_for_ai(conn.user)
            BIZ_CONN_OWNER_CACHE[biz_id] = ok
            return ok
    except Exception:
        pass
    return True

def log_bot_pm_interaction(user, text, from_bot=False):
    """Записывает сообщения пользователя в ЛС бота и ответы бота для просмотра владельцем."""
    if not user or is_owner_user(user):
        return
    uid_str = str(user.id)
    fname = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Игрок"
    uname = (user.username or '').lower()

    entry = BOT_PM_LOGS.setdefault(uid_str, {'uid': uid_str, 'name': fname, 'username': uname, 'log': []})
    entry['name'] = fname
    if uname:
        entry['username'] = uname

    t_str = time.strftime('%H:%M')
    prefix = "🤖 [Бот]" if from_bot else f"👤 [{fname}]"
    clean_t = re.sub(r'<[^>]+>', '', text or '')[:300]
    entry['log'].append(f"[{t_str}] {prefix}: {clean_t}")
    if len(entry['log']) > 60:
        entry['log'] = entry['log'][-60:]

def notify_owner_about_bot_pm(user, text):
    """Пересылает владельцу сообщение, которое пользователь написал боту в ЛС."""
    if not user or is_owner_user(user):
        return
    log_bot_pm_interaction(user, text, from_bot=False)
    if not CONFIG.get('pm_spy_enabled', True) or not main.CREATOR_ID:
        return

    uid_str = str(user.id)
    fname = html.escape(f"{user.first_name or ''} {user.last_name or ''}".strip() or "Игрок")
    uname_lbl = f"@{user.username}" if user.username else f"id:{uid_str}"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🗣 Писать от бота", callback_data=f"mod_live_{uid_str}"),
        InlineKeyboardButton("📜 История ЛС", callback_data=f"mod_hist_{uid_str}")
    )
    kb.add(
        InlineKeyboardButton("🎁 +500 🧬", callback_data=f"mod_give500_{uid_str}"),
        InlineKeyboardButton("🚫 Бан в боте", callback_data=f"mod_ban_{uid_str}")
    )

    alert = (
        f"📩 <b>[ЛС Бота] от {fname} ({uname_lbl}):</b>\n"
        f"<blockquote>{html.escape(text or '[медиа]')}</blockquote>\n"
        f"<i>💡 Ответь (Reply) на это сообщение, чтобы написать ему от имени бота!</i>"
    )
    try:
        sent = bot.send_message(main.CREATOR_ID, alert, reply_markup=kb)
        if sent:
            FORWARDED_PM_MAP[sent.message_id] = int(user.id)
    except Exception:
        pass

def send_logged_pm(chat_id, text, business_connection_id=None, reply_markup=None, target_user=None):
    """Отправляет сообщение и, если это ЛС бота с пользователем, сохраняет ответ бота в лог."""
    sent = bot.send_message(chat_id, text, business_connection_id=business_connection_id, reply_markup=reply_markup)
    if not business_connection_id and target_user and not is_owner_user(target_user):
        log_bot_pm_interaction(target_user, text, from_bot=True)
    return sent

# =====================================================================
# 📜 0.5 ПОЛНЫЙ СПИСОК КОМАНД И ИНТЕГРАЦИЯ В /PANEL
# =====================================================================
def get_updated_full_commands_text():
    return (
        "📜 <b>Полный список всех команд бота:</b>\n\n"
        "🔹 <b>1. Модерация, ЛС Бота и Доступы (Бог-мод):</b>\n"
        "• <code>/panel</code> (или <code>панель</code>) — главный пульт управления\n"
        "• <code>лс бота</code> (или <code>чаты бота</code>) — читать переписки юзеров с ботом и отвечать от имени бота!\n"
        "• <code>написать @юзер [текст]</code> — отправить сообщение человеку в ЛС от лица бота\n"
        "• <code>диалог @юзер</code> — войти в режим живого чата от имени бота (выход: <code>стоп</code>)\n"
        "• <code>модерация</code> (или <code>/mod</code>) — панель управления игроками и параметрами\n"
        "• <code>профиль @юзер</code> — полная инспекция любого игрока\n"
        "• <code>выдать @юзер 1000</code> / <code>забрать @юзер 500</code> — выдать/списать вирусы 🧬\n"
        "• <code>опыт @юзер 500</code> | <code>патогены @юзер 10</code> — изменить XP или патогены\n"
        "• <code>вылечить @юзер</code> | <code>обнулить @юзер</code> — снять горячку / сбросить профиль\n"
        "• <code>бан бот @юзер</code> / <code>разбан бот @юзер</code> — заблокировать юзера в боте\n"
        "• <code>доступ @юзер</code> / <code>забрать доступ @юзер</code> / <code>доступы</code> — доступ к ИИ и ТГ Бизнес\n"
        "• <code>рассылка [текст]</code> — отправить объявление всем игрокам в ЛС бота\n\n"
        "🔹 <b>2. Настройка всех параметров на лету:</b>\n"
        "• <code>настройки</code> (или <code>параметры</code>) — меню всех числовых настроек\n"
        "• <code>кд лут 5</code> | <code>кд патоген 15</code> | <code>цена вакцины 80</code>\n"
        "• <code>приз бункер 1000</code> | <code>приз виселица 200</code> | <code>приз кнб 50</code>\n"
        "• <code>время игры 60</code> | <code>раунды игры 3</code> | <code>игра-модели</code>\n\n"
        "🔹 <b>3. Сюжетные ИИ-Игры, Лутинг, Виселица и КНБ:</b>\n"
        "• <code>бункер</code> | <code>мафия</code> | <code>стоп игра</code> — игры с Ведущим Gemini в отдельном окне\n"
        "• <code>/search</code> (или <code>лут</code> / <code>мусорка</code>) — «Хабаровский Лутинг» у Меда\n"
        "• <code>виселица</code> / <code>виселица ии</code> — Поле Чудес (кнопки + буквы/слова в чат)\n"
        "• <code>кнб</code> — Камень-Ножницы-Бумага\n\n"
        "🔹 <b>4. Био-войны (Классика Ириса + Залп без спама):</b>\n"
        "• <code>лаба</code> (или <code>био</code>) — твоя Био-Лаборатория (6 классических навыков)\n"
        "• <code>заразить</code> (или <code>заразить @юзер</code>) — пустить 1 патоген (с уведомлением жертве!)\n"
        "• <code>заразить все</code> (или <code>заразить все @юзер</code> / <code>заразить фулл</code>) — выпустить ВСЕ патогены разом в одном отчете без спама!\n"
        "• <code>вакцина</code> — вылечить горячку | <code>штамм [имя]</code> — назвать свой вирус\n"
        "• <code>игры</code> (или <code>профиль</code>) | <code>био топ</code> — звания, баффы и рейтинг\n\n"
        "🔹 <b>5. Обычный ГПТ, Автоответчик и Голосовые:</b>\n"
        "• <code>гпт [текст]</code> | <code>нарисуй [текст]</code> | <code>рс</code> | <code>рассуди</code> | <code>анализ</code> | <code>5 копеек</code>\n"
        "• <code>модели</code> | <code>авто-модели</code> | <code>фото-модели</code> | <code>обучение</code> | <code>ключи</code>"
    )

def get_chat_commands_text():
    return (
        "📜 <b>Команды, доступные в чате и в боте:</b>\n\n"
        "🎭 <b>Сюжетные ИИ-Игры (Приз: 1000 🧬 за победу!):</b>\n"
        "• <code>бункер</code> — «Бункер на выживание» (скрытые карты, катастрофа по чату, ведет Gemini)\n"
        "• <code>мафия</code> — «Мафия: Допрос» (секретные роли, улики, блеф)\n"
        "• <code>стоп игра</code> — завершить текущую партию\n\n"
        "🗑 <b>Хабаровский Лутинг и Мини-игры:</b>\n"
        "• <code>/search</code> (или <code>лут</code> / <code>мусорка</code>) — копаться в мусорке у Меда (до +800 🧬)\n"
        "• <code>виселица</code> — Поле Чудес (+200 🧬 за победу, пиши буквы или слова прямо в чат!)\n"
        "• <code>виселица ии</code> — ГПТ загадывает слово по контексту вашего чата (+250 🧬)!\n"
        "• <code>кнб</code> — Камень-Ножницы-Бумага\n\n"
        "🧬 <b>Био-войны (Коронаирис):</b>\n"
        "• <code>лаба</code> — открыть свою Био-Лабораторию и прокачку\n"
        "• <code>заразить</code> (или <code>заразить @юзер</code>) — атаковать 1 патогеном\n"
        "• <code>заразить все</code> (или <code>заразить все @юзер</code>) — атаковать сразу ВСЕМИ патогенами без спама!\n"
        "• <code>вакцина</code> — вылечить горячку\n"
        "• <code>штамм [название]</code> — дать имя своему вирусу\n"
        "• <code>+патоген</code> | <code>+разработка</code> | <code>+заразность</code> | <code>+иммунитет</code> | <code>+летальность</code> | <code>+безопасность</code>\n"
        "• <code>профиль</code> (или <code>игры</code>) | <code>био топ</code> — твой ранг, баффы и таблица лидеров\n\n"
        "🤖 <b>ИИ-Функции (в бизнес-чате и по доступу):</b>\n"
        "• <code>гпт [запрос]</code> | <code>нарисуй [арт]</code> | <code>рс</code> | <code>рассуди</code> | <code>анализ</code> | <code>5 копеек</code>"
    )

main.get_full_commands_text = get_updated_full_commands_text

# Добавляем кнопки «📩 ЛС Бота и Модерация» и «⚙️ Все параметры» прямо в твой /panel
_orig_build_panel_view = main.build_panel_view

def _enhanced_build_panel_view():
    text, kb = _orig_build_panel_view()
    pm_cnt = len(BOT_PM_LOGS)
    live_note = f"\n🗣 <b>Режим ответа от бота:</b> АКТИВЕН (id:{ADMIN_LIVE_CHAT_UID})" if ADMIN_LIVE_CHAT_UID else ""
    text += f"\n📩 <b>Диалогов в ЛС бота:</b> {pm_cnt} | <b>Игроков в РПГ:</b> {len(RPG_PLAYERS)}{live_note}"
    kb.add(
        InlineKeyboardButton(f"📩 ЛС Бота ({pm_cnt}) и Мод", callback_data="mod_open_hub"),
        InlineKeyboardButton("⚙️ Все параметры", callback_data="mod_open_settings")
    )
    return text, kb

main.build_panel_view = _enhanced_build_panel_view

# =====================================================================
# 🎨 1. БЕСПЛАТНАЯ РИСОВАЛКА FLUX
# =====================================================================
FREE_IMAGE_MODELS = ['flux', 'flux-realism', 'flux-anime', 'flux-3d', 'turbo']
if CONFIG.get('image_model') not in FREE_IMAGE_MODELS:
    CONFIG['image_model'] = 'flux'

def enhance_image_prompt(ru_prompt):
    sys_t = (
        "Translate the user's image description into English and make it a vivid, detailed prompt "
        "for the FLUX image generator. Output ONLY the final English prompt without quotes."
    )
    if GROQ_API_KEY:
        try:
            headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
            payload = {
                'model': 'openai/gpt-oss-20b',
                'messages': [{'role': 'system', 'content': sys_t}, {'role': 'user', 'content': ru_prompt}],
                'temperature': 0.5
            }
            r = get_http_session().post(f'{GROQ_API_BASE}/chat/completions', headers=headers, json=payload, timeout=7)
            if r.status_code == 200:
                ans = r.json()['choices'][0]['message']['content'].strip()
                ans = re.sub(r'<think>.*?</think>', '', ans, flags=re.DOTALL | re.IGNORECASE).strip()
                if ans:
                    return ans
        except Exception:
            pass
    return ru_prompt

def free_generate_image(prompt):
    raw_prompt = (prompt or "").strip()
    if not raw_prompt:
        return None, "Напиши описание картинки!"
    model = CONFIG.get('image_model', 'flux')
    if model not in FREE_IMAGE_MODELS:
        model = 'flux'
    eng_prompt = enhance_image_prompt(raw_prompt)
    seed = random.randint(1, 9999999)
    encoded = requests.utils.quote(eng_prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded}?model={model}&width=1024&height=1024&seed={seed}&nologo=true"
    s = get_http_session()
    for _ in range(2):
        try:
            r = s.get(url, timeout=40)
            if r.status_code == 200 and r.content and len(r.content) > 1000:
                return r.content, None
        except Exception:
            time.sleep(1)
    return None, "Сервер рисования перегружен, повтори через 5 секунд."

main.generate_image = free_generate_image

# =====================================================================
# 🏆 2. БАЗА РПГ, 14 РАНГОВ, БАФФОВ И КЛАССИЧЕСКАЯ ЛАБОРАТОРИЯ ИРИСА
# =====================================================================
RPG_PLAYERS = {}

RARE_BUFFS_INFO = {
    'oracle_eye': {
        'name': '🔮 Глаз Оракула',
        'desc': '25% шанс сразу открыть 1 букву на старте Виселицы'
    },
    'trickster_glove': {
        'name': '🧤 Ловкость Шулера',
        'desc': '25% шанс получить +40 вирусов даже при ничьей в КНБ'
    },
    'antigen_shield': {
        'name': '🧬 Антиген-Щит',
        'desc': 'Дает +10% к защите от заражения в Био-войнах'
    },
    'rna_catalyst': {
        'name': '⚡ Катализатор РНК',
        'desc': '20% шанс не потратить патоген при атаке «заразить»'
    },
    'stalker_flashlight': {
        'name': '🔦 Фонарь Сталкера',
        'desc': 'Дает +25% больше вирусов при «Хабаровском Лутинге»'
    }
}

def load_rpg_firebase():
    if not FIREBASE_URL:
        return
    try:
        r = get_http_session().get(f'{FIREBASE_URL}/rpg_data.json', timeout=8)
        if r.status_code == 200 and isinstance(r.json(), dict):
            RPG_PLAYERS.update(r.json())
            print(f"🧬 Данные Био-войн загружены ({len(RPG_PLAYERS)} игроков).")
    except Exception as e:
        print(f"⚠️ Ошибка загрузки РПГ из Firebase: {e}")

def save_rpg_firebase():
    if not FIREBASE_URL:
        return
    def _worker():
        try:
            get_http_session().put(f'{FIREBASE_URL}/rpg_data.json', json=RPG_PLAYERS, timeout=6)
        except Exception:
            pass
    threading.Thread(target=_worker, daemon=True).start()

load_rpg_firebase()

def get_player(user):
    uid = str(user.id)
    uname = (user.username or '').lower()
    fname = f"{user.first_name or ''} {user.last_name or ''}".strip() or "Игрок"
    now = time.time()

    if uid not in RPG_PLAYERS:
        RPG_PLAYERS[uid] = {
            'uid': uid,
            'username': uname,
            'name': fname,
            'strain_name': f"Штамм-{fname[:10]}",
            'xp': 0,
            'bio_res': 200,
            'bio_xp': 0,
            'wins_hangman': 0,
            'wins_rps': 0,
            'wins_bunker': 0,
            'wins_mafia': 0,
            'loot_count': 0,
            'infections_done': 0,
            'buffs': [],
            'lab_patogen_lvl': 1,
            'lab_dev_lvl': 1,
            'lab_infect_lvl': 1,
            'lab_immune_lvl': 1,
            'lab_lethal_lvl': 1,
            'lab_security_lvl': 1,
            'patogens': 3,
            'last_patogen_ts': now,
            'last_loot_ts': 0,
            'fever_until': 0,
            'infected_by': ''
        }
        save_rpg_firebase()
    else:
        p = RPG_PLAYERS[uid]
        p['name'] = fname
        if uname:
            p['username'] = uname
        if not isinstance(p.get('buffs'), list):
            p['buffs'] = []
        if not p.get('strain_name'):
            p['strain_name'] = f"Штамм-{fname[:10]}"

    sync_lab_patogens(RPG_PLAYERS[uid])
    return RPG_PLAYERS[uid]

def find_player_by_username_or_id(query_str):
    q = str(query_str).lower().replace('@', '').strip()
    if q in RPG_PLAYERS:
        sync_lab_patogens(RPG_PLAYERS[q])
        return RPG_PLAYERS[q]
    for uid, p in RPG_PLAYERS.items():
        if (p.get('username') or '').lower() == q:
            sync_lab_patogens(p)
            return p
    return None

def sync_lab_patogens(p):
    now = time.time()
    max_cells = 2 + p.get('lab_patogen_lvl', 1)
    dev_lvl = p.get('lab_dev_lvl', 1)
    base_sec = max(60, float(CONFIG.get('base_patogen_minutes', 15)) * 60)
    interval_sec = max(120, int(base_sec - (dev_lvl - 1) * 65))

    cur_p = p.get('patogens', 3)
    last_ts = p.get('last_patogen_ts', now)

    if cur_p < max_cells:
        elapsed = now - last_ts
        if elapsed >= interval_sec:
            added = int(elapsed // interval_sec)
            p['patogens'] = min(max_cells, cur_p + added)
            p['last_patogen_ts'] = now if p['patogens'] >= max_cells else last_ts + added * interval_sec
    else:
        p['last_patogen_ts'] = now

def get_level_and_rank(xp):
    lvl = int((xp // 100) + 1)
    ranks = [
        (1, "🧪 Санитар пробирки"),
        (3, "🗑 Искатель у Меда"),
        (5, "🔬 Лаборант штаммов"),
        (8, "🦠 Переносчик заразы"),
        (12, "🧬 Охотник за геномом"),
        (16, "☣️ Полевой Эпидемиолог"),
        (22, "🏥 Главврач Изолятора"),
        (28, "⚡ Инженер Мутаций"),
        (35, "🧪 Владыка Патогенов"),
        (45, "🌍 Архитектор Пандемии"),
        (55, "💀 Всадник Чумы"),
        (65, "👑 Крёстный Отец Вируса"),
        (80, "🌌 Божество Биохазарда"),
        (100, "🔱 Абсолютный Пациент Ноль")
    ]
    title = ranks[0][1]
    for r_lvl, r_name in ranks:
        if lvl >= r_lvl:
            title = r_name
    return lvl, title

def try_grant_rare_buff(p):
    available = [k for k in RARE_BUFFS_INFO.keys() if k not in p.get('buffs', [])]
    if not available:
        return None
    if random.randint(1, 100) <= 14:
        chosen = random.choice(available)
        p.setdefault('buffs', []).append(chosen)
        b_info = RARE_BUFFS_INFO[chosen]
        return f"\n🎁 <b>РЕДКИЙ БАФФ ПОЛУЧЕН:</b> {b_info['name']} (<i>{b_info['desc']}</i>)!"
    return None

# =====================================================================
# 🦠 3. БИО-ВОЙНЫ (КЛАССИЧЕСКИЕ 6 НАВЫКОВ ИРИСА + УВЕДОМЛЕНИЯ + ЗАЛП ВСЕХ ПАТОГЕНОВ)
# =====================================================================
UPGRADE_TYPES = {
    'patogen': ('lab_patogen_lvl', '🧪 Патоген (ячейки)', 60),
    'dev': ('lab_dev_lvl', '⏱ Разработка (скорость)', 75),
    'infect': ('lab_infect_lvl', '🦠 Заразность (атака)', 80),
    'immune': ('lab_immune_lvl', '🛡 Иммунитет (защита)', 80),
    'lethal': ('lab_lethal_lvl', '☠️ Летальность (доход/горячка)', 90),
    'security': ('lab_security_lvl', '🕵️ Служба безопасности', 70)
}

def get_upgrade_cost(p, upg_key):
    field, _, base_cost = UPGRADE_TYPES[upg_key]
    lvl = p.get(field, 1)
    return int(base_cost * (1.35 ** (lvl - 1)))

def build_lab_view(p):
    sync_lab_patogens(p)
    now = time.time()
    lvl, rank_title = get_level_and_rank(p.get('xp', 0))
    max_cells = 2 + p.get('lab_patogen_lvl', 1)
    dev_lvl = p.get('lab_dev_lvl', 1)
    base_sec = max(60, float(CONFIG.get('base_patogen_minutes', 15)) * 60)
    interval_min = max(2, round((base_sec - (dev_lvl - 1) * 65) / 60, 1))
    vac_cost = int(CONFIG.get('vaccine_cost', 80))

    fever_rem = int(p.get('fever_until', 0) - now)
    if fever_rem > 0:
        mins = (fever_rem // 60) + 1
        by_who = p.get('infected_by') or 'неизвестного вируса'
        status_line = f"🤒 <b>ГОРЯЧКА (Заражен от {html.escape(by_who)})!</b> Осталось: <b>{mins} мин.</b>"
    else:
        status_line = "🟢 <b>Здоров (Лаборатория работает штатно)</b>"

    buff_names = [RARE_BUFFS_INFO[b]['name'] for b in p.get('buffs', []) if b in RARE_BUFFS_INFO]
    buff_str = ", ".join(buff_names) if buff_names else "нет (выпадают в играх)"
    strain_safe = html.escape(p.get('strain_name', 'Штамм-Х'))
    name_safe = html.escape(p.get('name', 'Игрок'))

    text = (
        f"🧬 <b>Био-Лаборатория: {name_safe}</b>\n"
        f"🎖 <b>Звание:</b> {rank_title} (Ур. {lvl} | ✨ {p.get('xp', 0)} XP)\n"
        f"☣️ <b>Штамм:</b> «<b>{strain_safe}</b>» (сменить: <code>штамм Имя</code>)\n"
        f"🩺 <b>Статус:</b> {status_line}\n\n"
        f"🔋 <b>Вирусы:</b> <code>{p.get('bio_res', 0)}</code> 🧬 | <b>Био-опыт:</b> <code>{p.get('bio_xp', 0)}</code> ☣️\n"
        f"🧪 <b>Готовых патогенов:</b> <b>{p.get('patogens', 0)} / {max_cells}</b> (новый каждые ~{interval_min} мин)\n"
        f"☠️ <b>Успешных заражений:</b> {p.get('infections_done', 0)}\n\n"
        f"🔬 <b>Характеристики лаборатории:</b>\n"
        f"• 🧪 <b>Патоген (ячейки):</b> Ур. {p.get('lab_patogen_lvl', 1)} (след: {get_upgrade_cost(p, 'patogen')} 🧬)\n"
        f"• ⏱ <b>Разработка:</b> Ур. {p.get('lab_dev_lvl', 1)} (след: {get_upgrade_cost(p, 'dev')} 🧬)\n"
        f"• 🦠 <b>Заразность:</b> Ур. {p.get('lab_infect_lvl', 1)} (след: {get_upgrade_cost(p, 'infect')} 🧬)\n"
        f"• 🛡 <b>Иммунитет:</b> Ур. {p.get('lab_immune_lvl', 1)} (след: {get_upgrade_cost(p, 'immune')} 🧬)\n"
        f"• ☠️ <b>Летальность:</b> Ур. {p.get('lab_lethal_lvl', 1)} (след: {get_upgrade_cost(p, 'lethal')} 🧬)\n"
        f"• 🕵️ <b>Безопасность:</b> Ур. {p.get('lab_security_lvl', 1)} (след: {get_upgrade_cost(p, 'security')} 🧬)\n\n"
        f"🔮 <b>Редкие баффы:</b> {buff_str}\n"
        f"<i>💡 Атака: <code>заразить @юзер</code> или залп без спама: <code>заразить все @юзер</code></i>"
    )

    uid = p['uid']
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"⬆️ +Патоген ({get_upgrade_cost(p, 'patogen')}🧬)", callback_data=f"bioupg_{uid}_patogen"),
        InlineKeyboardButton(f"⬆️ +Разработка ({get_upgrade_cost(p, 'dev')}🧬)", callback_data=f"bioupg_{uid}_dev")
    )
    kb.add(
        InlineKeyboardButton(f"⬆️ +Заразность ({get_upgrade_cost(p, 'infect')}🧬)", callback_data=f"bioupg_{uid}_infect"),
        InlineKeyboardButton(f"⬆️ +Иммунитет ({get_upgrade_cost(p, 'immune')}🧬)", callback_data=f"bioupg_{uid}_immune")
    )
    kb.add(
        InlineKeyboardButton(f"⬆️ +Летальность ({get_upgrade_cost(p, 'lethal')}🧬)", callback_data=f"bioupg_{uid}_lethal"),
        InlineKeyboardButton(f"⬆️ +Безопасность ({get_upgrade_cost(p, 'security')}🧬)", callback_data=f"bioupg_{uid}_security")
    )
    if fever_rem > 0:
        kb.add(InlineKeyboardButton(f"💉 Купить вакцину ({vac_cost} 🧬)", callback_data=f"biocure_{uid}"))
    kb.add(
        InlineKeyboardButton("🗑 Лутать мусорку", callback_data="loot_again"),
        InlineKeyboardButton("🔄 Обновить лабу", callback_data=f"biorefresh_{uid}")
    )
    return text, kb

def send_victim_infection_notification(target_p, notify_html, source_chat_id):
    """
    Отправляет уведомление жертве о том, что её заразили!
    Сначала пробует отправить в ЛС бота, а если жертва не запускала бота в ЛС —
    ищет бизнес-чат с ней в known_chats и шлет туда!
    """
    try:
        target_uid = int(target_p['uid'])
    except Exception:
        return

    # Если заражение произошло прямо в личном чате с самой жертвой — она уже видит сообщение в этом чате
    if int(source_chat_id) == target_uid:
        return

    vac_cost = int(CONFIG.get('vaccine_cost', 80))
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton(f"💉 Принять вакцину ({vac_cost} 🧬)", callback_data=f"biocure_{target_uid}"))

    # 1. Пробуем отправить в ЛС самого бота
    try:
        bot.send_message(target_uid, notify_html, reply_markup=kb)
        return
    except Exception:
        pass

    # 2. Если ЛС бота закрыто, ищем бизнес-чат с этим человеком в known_chats
    uname = (target_p.get('username') or '').lower()
    known = CONFIG.get('known_chats', {}).get(uname)
    if not known:
        for k_info in CONFIG.get('known_chats', {}).values():
            if k_info.get('chat_id') == target_uid:
                known = k_info
                break

    if known and known.get('biz_id'):
        try:
            bot.send_message(known['chat_id'], notify_html, business_connection_id=known['biz_id'], reply_markup=kb)
        except Exception:
            pass

def perform_infection_attempt(attacker_user, target_p, use_all=False, source_chat_id=0):
    """
    Выполняет био-атаку (одиночную или залпом ВСЕМИ патогенами сразу без спама в чате)
    и отправляет уведомление жертве!
    """
    atk_p = get_player(attacker_user)
    now = time.time()

    if atk_p['uid'] == target_p['uid']:
        return "🤪 Ты не можешь заразить сам себя!"

    if atk_p.get('fever_until', 0) > now:
        rem_m = int((atk_p['fever_until'] - now) // 60) + 1
        return (
            f"🤒 <b>У тебя сейчас горячка (еще {rem_m} мин.)!</b>\n"
            f"Ты не можешь заражать других, пока не выздоровеешь или не примешь <code>вакцина</code> в <code>лаба</code>."
        )

    avail = int(atk_p.get('patogens', 0))
    if avail <= 0:
        return "🧪 <b>В лаборатории нет готовых патогенов!</b> Подожди синтеза или прокачай <code>+разработка</code> в <code>лаба</code>."

    shots = avail if use_all else 1
    infect_lvl = atk_p.get('lab_infect_lvl', 1)
    immune_lvl = target_p.get('lab_immune_lvl', 1)
    lethal_lvl = atk_p.get('lab_lethal_lvl', 1)
    sec_lvl = target_p.get('lab_security_lvl', 1)

    base_ch = int(CONFIG.get('base_infect_chance', 55))
    chance = base_ch + (infect_lvl - immune_lvl) * 6
    if 'antigen_shield' in target_p.get('buffs', []):
        chance -= 10
    chance = max(15, min(92, chance))

    atk_name = html.escape(atk_p['name'])
    def_name = html.escape(target_p['name'])
    strain_name = html.escape(atk_p.get('strain_name', 'Штамм-Х'))

    hits = 0
    misses = 0
    saved_count = 0
    total_loot_res = 0
    total_loot_xp = 0
    total_fever_mins = 0
    spotted_any = False

    for _ in range(shots):
        if 'rna_catalyst' in atk_p.get('buffs', []) and random.randint(1, 100) <= 20:
            saved_count += 1
        else:
            atk_p['patogens'] = max(0, atk_p['patogens'] - 1)

        if random.randint(1, 100) <= chance:
            hits += 1
            f_mins = (10 + lethal_lvl * 4) if hits == 1 else (5 + lethal_lvl * 2)
            l_res = 50 + lethal_lvl * 28
            l_xp = 25 + lethal_lvl * 6
            total_fever_mins += f_mins
            total_loot_res += l_res
            total_loot_xp += l_xp
            if random.randint(1, 100) <= (35 + sec_lvl * 12):
                spotted_any = True
        else:
            misses += 1
            total_loot_xp += 5

    atk_p['xp'] = atk_p.get('xp', 0) + total_loot_xp

    if hits > 0:
        base_until = max(now, target_p.get('fever_until', 0))
        target_p['fever_until'] = base_until + total_fever_mins * 60
        who_label = f"{atk_p['name']} (штамм «{atk_p.get('strain_name', 'Х')}»)" if spotted_any else f"Неизвестный био-хакер (штамм «{atk_p.get('strain_name', 'Х')}»)"
        target_p['infected_by'] = who_label

        atk_p['bio_res'] = atk_p.get('bio_res', 0) + total_loot_res
        atk_p['bio_xp'] = atk_p.get('bio_xp', 0) + total_loot_xp
        atk_p['infections_done'] = atk_p.get('infections_done', 0) + hits

        buff_msg = try_grant_rare_buff(atk_p) or ""
        save_rpg_firebase()

        # Уведомление жертве в ЛС!
        attacker_public_str = f"<b>{atk_name}</b>" if spotted_any else "<b>Неизвестный био-хакер</b> (служба безопасности не смогла перехватить след)"
        victim_alert = (
            f"☣️ <b>ВНИМАНИЕ! БИО-УГРОЗА!</b>\n\n"
            f"🦠 Тебя заразили вирусом «<b>{strain_name}</b>»!\n"
            f"🕵️ Источник атаки: {attacker_public_str}\n"
            f"🎯 Успешных попаданий патогена: <b>{hits} шт.</b>\n"
            f"🤒 У тебя началась горячка на <b>+{total_fever_mins} мин.</b>!\n\n"
            f"<i>💡 Нажми кнопку ниже или напиши <code>вакцина</code>, чтобы вылечиться!</i>"
        )
        send_victim_infection_notification(target_p, victim_alert, source_chat_id)

        cat_note = f"\n⚡ <i>«Катализатор РНК» сохранил патогенов: {saved_count} шт.!</i>" if saved_count > 0 else ""
        sec_note = "🕵️ Служба безопасности жертвы засекла твой след!" if spotted_any else "🥷 Атака прошла скрытно для службы безопасности!"

        if shots == 1:
            return (
                f"☣️ <b>БИО-АТАКА УСПЕШНА!</b> (Шанс: {chance}%)\n\n"
                f"🦠 <b>{atk_name}</b> поразил(а) штаммом «<b>{strain_name}</b>» игрока <b>{def_name}</b>!\n"
                f"🤒 Горячка у жертвы: <b>+{total_fever_mins} мин.</b> (Уведомление отправлено!)\n"
                f"🧬 Добыто вирусов: <b>+{total_loot_res} 🧬</b> и <b>+{total_loot_xp} XP</b>\n"
                f"{sec_note}{cat_note}{buff_msg}"
            )
        else:
            return (
                f"☣️ <b>МАССИРОВАННЫЙ ЗАЛП ПАТОГЕНОВ ({shots} шт.)!</b>\n"
                f"🎯 <b>Пробитий иммунитета:</b> {hits} из {shots} (Шанс каждого: {chance}%)\n\n"
                f"🦠 <b>{atk_name}</b> накрыл(а) штаммом «<b>{strain_name}</b>» игрока <b>{def_name}</b>!\n"
                f"🤒 Суммарная горячка жертвы: <b>+{total_fever_mins} мин.</b> (Уведомление отправлено!)\n"
                f"🧬 Итого добыто: <b>+{total_loot_res} 🧬 вирусов</b> и <b>+{total_loot_xp} XP</b>\n"
                f"{sec_note}{cat_note}{buff_msg}"
            )
    else:
        save_rpg_firebase()
        cat_note = f"\n⚡ <i>«Катализатор РНК» сохранил патогенов: {saved_count} шт.!</i>" if saved_count > 0 else ""
        if shots == 1:
            return (
                f"🛡 <b>ИММУНИТЕТ ВЫСТОЯЛ!</b> (Шанс был: {chance}%)\n\n"
                f"Организм <b>{def_name}</b> уничтожил штамм «<b>{strain_name}</b>» от <b>{atk_name}</b>!\n"
                f"💡 Прокачай <code>+заразность</code> в своей <code>лаба</code>.{cat_note}"
            )
        else:
            return (
                f"🛡 <b>ЗАЛП ОТРАЖЕН ИММУНИТЕТОМ ({shots} патогенов)!</b>\n\n"
                f"Организм <b>{def_name}</b> выдержал все {shots} атак штамма «<b>{strain_name}</b>» (Шанс был: {chance}%)!{cat_note}"
            )

# =====================================================================
# 🗑 3.5 МИНИ-ИГРА: «ХАБАРОВСКИЙ ЛУТИНГ» (ПОВЫШЕННЫЕ ПРИЗЫ!)
# =====================================================================
def execute_khabarovsk_looting(user):
    p = get_player(user)
    now = time.time()
    cd_mins = float(CONFIG.get('looting_cd_minutes', 5))
    cd_secs = cd_mins * 60
    last_ts = p.get('last_loot_ts', 0)

    if cd_secs > 0 and (now - last_ts) < cd_secs:
        rem = int(cd_secs - (now - last_ts))
        m, s = rem // 60, rem % 60
        time_str = f"{m} мин. {s} сек." if m > 0 else f"{s} сек."
        return (
            f"⏳ <b>Мусорка у Меда еще пустует!</b>\n"
            f"Бомжи и первокурсники еще не накидали нового хлама.\n"
            f"Подожди: <b>{time_str}</b>",
            False
        )

    p['last_loot_ts'] = now
    p['loot_count'] = p.get('loot_count', 0) + 1
    roll = random.randint(1, 100)
    u_name = html.escape(p['name'])

    if roll <= 35:
        rarity = "⚪ Обычный шмот"
        item_name = "Сгнивший чеснок 🧄"
        delta = 30
        flavor = "Воняет на весь квартал, но для штамма сойдет."
    elif roll <= 70:
        rarity = "⚪ Обычный шмот"
        item_name = "Дохлая батарейка АА 🔋"
        delta = 45
        flavor = "Если пожевать, дает бодрящий привкус окиси цинка."
    elif roll <= 80:
        rarity = "🔵 Редкий шмот"
        item_name = "Замороженный пельмень 🥟"
        delta = 120
        flavor = "Пролежал во льдах с прошлой зимней сессии!"
    elif roll <= 90:
        rarity = "🔵 Редкий шмот"
        item_name = "Очки Спамтона 🕶"
        delta = 250
        flavor = "ТЕПЕРЬ ТЫ НАСТОЯЩИЙ [[Крупная Шишка]] У МЕДА!"
    elif roll <= 95:
        rarity = "🟡 ЛЕГЕНДАРКА (5%)"
        item_name = "Котурны 20 см 👢✨"
        delta = 800
        flavor = "АБСОЛЮТНЫЙ ДЖЕКПОТ! В таких даже по гололеду как по подиуму!"
    else:
        rarity = "💥 ФЕЙЛ (5%)"
        item_name = "«Наткнулась на дедушку» 👴💀"
        delta = -75
        flavor = "Дед выскочил из-за баков и огрел клюкой! Минус вирусы за испуг."

    stalker_bonus = ""
    if delta > 0 and 'stalker_flashlight' in p.get('buffs', []):
        extra = int(delta * 0.25)
        delta += extra
        stalker_bonus = f" (🔦 +{extra} от Фонаря Сталкера)"

    p['bio_res'] = max(0, p.get('bio_res', 0) + delta)
    xp_gain = 35 if delta >= 800 else (18 if delta >= 100 else (8 if delta > 0 else 3))
    p['xp'] = p.get('xp', 0) + xp_gain
    buff_note = try_grant_rare_buff(p) or ""
    save_rpg_firebase()

    sign_str = f"+{delta} 🧬{stalker_bonus}" if delta > 0 else f"{delta} 🧬"
    text = (
        f"🗑 <b>Хабаровский Лутинг (Мусорка у Меда)</b>\n"
        f"🕵️ Искатель: <b>{u_name}</b> копается в баках...\n\n"
        f"🎲 Категория: <b>{rarity}</b>\n"
        f"📦 Находка: <b>{item_name}</b>\n"
        f"💬 <i>{flavor}</i>\n\n"
        f"🦠 Конвертация в вирусы: <b>{sign_str}</b> (Баланс: <code>{p['bio_res']}</code> 🧬 | +{xp_gain} XP){buff_note}"
    )
    return text, True

# --- КОНЕЦ ЧАСТИ 1 (ВСТАВЛЯЙ ЧАСТЬ 2 С НОВОЙ СТРОКИ БЕЗ ОТСТУПОВ!) ---
PART_1_READY = True
# =====================================================================
# 🎡 4. ИГРА «ВИСЕЛИЦА / ПОЛЕ ЧУДЕС» (КНОПКИ + ВВОД БУКВ И СЛОВ В ЧАТ!)
# =====================================================================
HANGMAN_GAMES = {}
USED_AI_WORDS = {}

BASE_WORDS = [
    ("КАПИБАРА", "Животный мир"), ("ШАУРМА", "Божественная еда"),
    ("КРИПТОВАЛЮТА", "Финансы и хайп"), ("ТЕЛЕГРАМ", "Мессенджеры"),
    ("ЛАБОРАТОРИЯ", "Наука и вирусы"), ("СИНХРОФАЗОТРОН", "Сложная физика"),
    ("КИБЕРПАНК", "Будущее и неон"), ("БЕССОННИЦА", "Ночной режим"),
    ("ПРОГРАММИСТ", "IT и код"), ("МАРШРУТКА", "Городской транспорт"),
    ("ПЕЛЬМЕНИ", "Кулинарная классика"), ("АДРЕНАЛИН", "Гормоны и экстрим"),
    ("ПАРАШЮТ", "Прыжки с высоты"), ("ИНТУИЦИЯ", "Шестое чувство"),
    ("МЕТЕОРИТ", "Космос"), ("КАРАНТИН", "Био-угроза"),
    ("ШАМПУНЬ", "Ванная комната"), ("ДИПЛОМАТ", "Политика и переговоры"),
    ("ХУДИ", "Гардероб"), ("НЕЙРОСЕТЬ", "Искусственный интеллект"),
    ("СТЕТОСКОП", "Медицинский атрибут"), ("АНАТОМИЯ", "Первая пара в меде"),
    ("РЕАНИМАЦИЯ", "Скорая помощь"), ("ДЕФИБРИЛЛЯТОР", "Разряд!"),
    ("КАПЕЛЬНИЦА", "Физраствор внутривенно")
]

HANGMAN_STAGES = [
    "🟢 Жизней: ❤️❤️❤️❤️❤️❤️ (Ошибок: 0/6)",
    "🟡 Жизней: ❤️❤️❤️❤️❤️🖤 (Ошибок: 1/6 — построена опора)",
    "🟡 Жизней: ❤️❤️❤️❤️🖤🖤 (Ошибок: 2/6 — подвешена петля)",
    "🟠 Жизней: ❤️❤️❤️🖤🖤🖤 (Ошибок: 3/6 — голова в петле 😵)",
    "🟠 Жизней: ❤️❤️🖤🖤🖤🖤 (Ошибок: 4/6 — туловище и руки)",
    "🔴 Жизней: ❤️🖤🖤🖤🖤🖤 (Ошибок: 5/6 — осталась последняя попытка!)",
    "💀 Жизней: 🖤🖤🖤🖤🖤🖤 (Ошибок: 6/6 — ПОВЕШЕН!)"
]

RUS_ALPHABET = list("АБВГДЕЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ")

def generate_ai_context_word(chat_id):
    cid = int(chat_id)
    history = CHAT_MEMORY.get(cid, [])
    used_list = USED_AI_WORDS.setdefault(cid, [])
    used_str = ", ".join(used_list[-30:]) if used_list else "пока нет"

    if len(history) >= 4:
        prompt = (
            "[СИСТЕМНЫЙ ЗАПРОС ИГРЫ «ВИСЕЛИЦА»]:\n"
            "Мы запускаем раунд игры в Виселицу в этом чате. "
            "Вспомни ВСЮ нашу историю общения целиком (от старых тем до недавних, наши локальные мемы, "
            "интересы, обсуждения или крутые штуки, которые подходят нашему вайбу).\n"
            "ВАЖНОЕ ПРАВИЛО: НЕ зацикливайся на одной последней фразе чата! Каждый раз бери кардинально РАЗНЫЕ темы и ассоциации!\n"
            f"ЗАПРЕЩЕНО использовать эти уже бывшие слова и однокоренные к ним: {used_str}.\n\n"
            "Загадай ОДНО русское существительное в именительном падеже единственного числа (от 5 до 12 букв, без пробелов и дефисов) "
            "и напиши к нему интересную подсказку от своего лица.\n"
            "Ответь СТРОГО в две строки:\n"
            "СЛОВО: [слово заглавными буквами]\n"
            "ПОДСКАЗКА: [твоя подсказка]"
        )
    else:
        prompt = (
            "[СИСТЕМНЫЙ ЗАПРОС ИГРЫ «ВИСЕЛИЦА»]:\n"
            "В этом чате пока нет длинной истории, поэтому сам выдумай любое необычное, интересное или смешное "
            "русское существительное в именительном падеже единственного числа (от 5 до 12 букв, без дефисов) для игры в Виселицу.\n"
            f"НЕ используй эти слова: {used_str}.\n\n"
            "Ответь СТРОГО в две строки:\n"
            "СЛОВО: [слово заглавными буквами]\n"
            "ПОДСКАЗКА: [остроумная подсказка от твоего лица]"
        )

    reply, err, _, _ = main.ask_ai(cid, prompt, save_to_memory=True, is_autoreply=False)

    if reply:
        w_match = re.search(r'СЛОВО:\s*([А-ЯЁ]+)', reply.upper())
        h_match = re.search(r'ПОДСКАЗКА:\s*(.+)', reply, flags=re.IGNORECASE)
        if w_match:
            word = w_match.group(1).strip().replace('Ё', 'Е')
            word = re.sub(r'[^А-Я]', '', word)
            hint = h_match.group(1).strip() if h_match else "Загадано нашим ГПТ!"
            if 4 <= len(word) <= 15 and word not in used_list:
                used_list.append(word)
                mem = CHAT_MEMORY.get(cid, [])
                if len(mem) >= 2:
                    mem[-2]['content'] = "[Игроки запустили Виселицу ИИ и попросили тебя загадать слово]"
                    mem[-1]['content'] = f"[Я загадал для них в Виселице слово «{word}» с подсказкой: «{hint}»]"
                    main.save_chat_memory_async(cid, mem)
                return word, f"🤖 От ГПТ: {hint}"

    unused_base = [pair for pair in BASE_WORDS if pair[0].upper().replace('Ё', 'Е') not in used_list] or BASE_WORDS
    w, c = random.choice(unused_base)
    clean_w = w.upper().replace('Ё', 'Е')
    used_list.append(clean_w)
    return clean_w, c

def build_hangman_view(game):
    word = game['word']
    guessed = game['guessed']
    errors = game['errors']

    masked = " ".join([ch if ch in guessed else "▫️" for ch in word])
    wrong_letters = [ch for ch in sorted(guessed) if ch not in word]
    wrong_words = game.get('wrong_words', [])
    all_misses = wrong_letters + [f"«{w}»" for w in wrong_words]
    wrong_str = ", ".join(all_misses) if all_misses else "нет"
    stage_str = HANGMAN_STAGES[min(errors, 6)]

    mode_badge = "🧠 Виселица ИИ (От ГПТ чата)" if game['is_ai'] else "🎡 Поле Чудес / Виселица"
    oracle_note = f"\n🔮 <i>{game['oracle_note']}</i>" if game.get('oracle_note') else ""

    text = (
        f"<b>{mode_badge}</b>\n"
        f"💡 <b>Подсказка:</b> <i>{html.escape(game['hint'])}</i>\n\n"
        f"🔤 <b>Слово ({len(word)} букв):</b>\n"
        f"<code>{masked}</code>\n\n"
        f"{stage_str}\n"
        f"❌ <b>Промахи:</b> {wrong_str}{oracle_note}\n"
        f"💬 {game['status']}\n"
        f"<i>(Жми на кнопки ИЛИ просто пиши букву / целое слово в чат!)</i>"
    )

    kb = InlineKeyboardMarkup(row_width=6)
    if not game.get('finished'):
        rem_letters = [ch for ch in RUS_ALPHABET if ch not in guessed]
        btns = [
            InlineKeyboardButton(ch, callback_data=f"hm_let_{game['chat_id']}_{ch}")
            for ch in rem_letters
        ]
        for i in range(0, len(btns), 6):
            kb.row(*btns[i:i + 6])
        kb.row(InlineKeyboardButton("🏳️ Сдаться и узнать слово", callback_data=f"hm_giveup_{game['chat_id']}"))
    else:
        kb.row(
            InlineKeyboardButton("🔄 Обычное слово", callback_data=f"hm_new_{game['chat_id']}_std"),
            InlineKeyboardButton("🧠 Слово от ГПТ (по чату)", callback_data=f"hm_new_{game['chat_id']}_ai")
        )
    return text, kb

def start_hangman_game(chat_id, biz_id, user, is_ai=False, msg_id_to_edit=None):
    chat_id = int(chat_id)
    p = get_player(user)

    if is_ai:
        word, hint = generate_ai_context_word(chat_id)
    else:
        w, hint = random.choice(BASE_WORDS)
        word = w.upper().replace('Ё', 'Е')

    guessed = set()
    oracle_note = ""
    if 'oracle_eye' in p.get('buffs', []) and random.randint(1, 100) <= 25:
        open_ch = random.choice(list(word))
        guessed.add(open_ch)
        oracle_note = f"Сработал твой бафф «Глаз Оракула» — открыта буква «{open_ch}»!"

    game = {
        'chat_id': chat_id,
        'biz_id': biz_id,
        'word': word,
        'hint': hint,
        'is_ai': is_ai,
        'guessed': guessed,
        'wrong_words': [],
        'errors': 0,
        'oracle_note': oracle_note,
        'status': "Называй букву или пиши слово целиком!",
        'msg_id': msg_id_to_edit,
        'finished': False
    }
    HANGMAN_GAMES[chat_id] = game
    text, kb = build_hangman_view(game)

    if msg_id_to_edit:
        safe_edit_message(text, chat_id, msg_id_to_edit, business_id=biz_id, reply_markup=kb)
    else:
        sent = send_logged_pm(chat_id, text, business_connection_id=biz_id, reply_markup=kb, target_user=user)
        game['msg_id'] = sent.message_id

def process_hangman_letter_or_word(cid, biz_id, user, raw_guess, from_chat_text=False):
    game = HANGMAN_GAMES.get(cid)
    if not game or game.get('finished'):
        return False

    p = get_player(user)
    u_name = html.escape(p['name'])
    clean = raw_guess.strip().upper().replace('Ё', 'Е')
    base_rew = int(CONFIG.get('reward_hangman', 200))

    if len(clean) == 1 and clean in RUS_ALPHABET:
        letter = clean
        if letter in game['guessed']:
            game['status'] = f"⚠️ Буква «<b>{letter}</b>» уже была названа!"
        else:
            game['guessed'].add(letter)
            if letter in game['word']:
                if all(ch in game['guessed'] for ch in game['word']):
                    game['finished'] = True
                    rew_res = (base_rew + 50) if game['is_ai'] else base_rew
                    rew_xp = 50 if game['is_ai'] else 35
                    p['bio_res'] += rew_res
                    p['xp'] += rew_xp
                    p['wins_hangman'] = p.get('wins_hangman', 0) + 1
                    buff_str = try_grant_rare_buff(p) or ""
                    save_rpg_firebase()
                    game['status'] = f"🏆 <b>ПОБЕДА! {u_name} открыл(а) слово!</b> (+{rew_res} 🧬, +{rew_xp} XP){buff_str}"
                else:
                    game['status'] = f"✅ <b>{u_name}</b> угадал(а) букву «<b>{letter}</b>»!"
            else:
                game['errors'] += 1
                if game['errors'] >= 6:
                    game['finished'] = True
                    game['guessed'].update(list(game['word']))
                    game['status'] = f"💀 <b>Повешен!</b> Буква «{letter}» неверная. Загаданное слово: <b>{game['word']}</b>"
                else:
                    game['status'] = f"❌ Буквы «<b>{letter}</b>» нет в слове! (+1 ошибка от {u_name})"

    elif len(clean) >= 2 and re.match(r'^[А-Я]+$', clean):
        if clean == game['word']:
            game['guessed'].update(list(game['word']))
            game['finished'] = True
            rew_res = (base_rew + 50) if game['is_ai'] else base_rew
            rew_xp = 60 if game['is_ai'] else 40
            p['bio_res'] += rew_res
            p['xp'] += rew_xp
            p['wins_hangman'] = p.get('wins_hangman', 0) + 1
            buff_str = try_grant_rare_buff(p) or ""
            save_rpg_firebase()
            game['status'] = f"🏆 <b>БРАВО! {u_name} угадал(а) слово целиком!</b> (+{rew_res} 🧬, +{rew_xp} XP){buff_str}"
        else:
            if clean not in game['wrong_words']:
                game['wrong_words'].append(clean)
            game['errors'] += 1
            if game['errors'] >= 6:
                game['finished'] = True
                game['guessed'].update(list(game['word']))
                game['status'] = f"💀 <b>Повешен!</b> Слово «{html.escape(clean)}» неверное! Загаданное слово было: <b>{game['word']}</b>"
            else:
                game['status'] = f"❌ Слово «<b>{html.escape(clean)}</b>» неверное! (+1 ошибка от {u_name})"
    else:
        return False

    text, kb = build_hangman_view(game)
    if from_chat_text:
        old_id = game.get('msg_id')
        if old_id:
            try:
                bot.delete_message(cid, old_id, business_connection_id=biz_id)
            except Exception:
                pass
        sent = send_logged_pm(cid, text, business_connection_id=biz_id, reply_markup=kb, target_user=user)
        game['msg_id'] = sent.message_id
    else:
        safe_edit_message(text, cid, game['msg_id'], business_id=biz_id, reply_markup=kb)
    return True

# =====================================================================
# ✂️ 5. КАМЕНЬ-НОЖНИЦЫ-БУМАГА (ПРОТИВ БОТА И 1 НА 1 В ЧАТЕ)
# =====================================================================
RPS_GAMES = {}
RPS_NAMES = {'r': '🪨 Камень', 's': '✂️ Ножницы', 'p': '📄 Бумага'}
RPS_WINS = {('r', 's'), ('s', 'p'), ('p', 'r')}

def send_rps_menu(chat_id, biz_id=None, user=None):
    cid = int(chat_id)
    RPS_GAMES[cid] = {'biz_id': biz_id, 'moves': {}, 'names': {}, 'users': {}}
    kb = InlineKeyboardMarkup(row_width=3)
    kb.row(
        InlineKeyboardButton("🪨 Камень", callback_data=f"rps_pvp_{cid}_r"),
        InlineKeyboardButton("✂️ Ножницы", callback_data=f"rps_pvp_{cid}_s"),
        InlineKeyboardButton("📄 Бумага", callback_data=f"rps_pvp_{cid}_p")
    )
    kb.row(
        InlineKeyboardButton("🤖 Быстрый бой с Ботом", callback_data=f"rps_botmenu_{cid}")
    )
    text = (
        "🪨✂️📄 <b>Камень-Ножницы-Бумага!</b>\n\n"
        "• <b>Дуэль 1 на 1:</b> оба игрока жмут на кнопки ниже (первый выбор скрыт, пока не нажмет второй!).\n"
        "• Или нажми «🤖 Быстрый бой с Ботом»."
    )
    send_logged_pm(cid, text, business_connection_id=biz_id, reply_markup=kb, target_user=user)

# =====================================================================
# 🎭 6. СЮЖЕТНЫЕ ИИ-ИГРЫ: «БУНКЕР» И «МАФИЯ: ДОПРОС» (ПРИЗ 1000 🧬!)
# =====================================================================
STORY_GAMES = {}

HOST_SYSTEM_PROMPT_BUNKER = (
    "Ты — харизматичный, ироничный, абсолютно беспристрастный ИИ-Смотритель и Ведущий психологической игры «БУНКЕР».\n"
    "ТВОЙ ХАРАКТЕР И ПРАВИЛА СУДЕЙСТВА:\n"
    "1. Никаких поблажек ни владельцу аккаунта, ни его собеседнику! Ты судишь СТРОГО по тому, кто лучше аргументирует, "
    "хитрее манипулирует, убедительнее блефует, давит на эмоции и логично использует свои характеристики и лут.\n"
    "2. В тебе должен быть живой огонек, черный юмор и азарт: подкалывай игроков за слабые отмазки, вскрывай их противоречия, "
    "сталкивай их лбами и заставляй оправдываться.\n"
    "3. В каждом промежуточном раунде:\n"
    "   - Разбери аргументы и манипуляции обоих игроков (кто звучал мощно, а кто поплыл).\n"
    "   - Вбрось внезапный кризис/событие в бункере (поломка, мутация снаружи, нехватка ресурса).\n"
    "   - Задай обоим игрокам по одному острому вопросу «в лоб» на следующий раунд.\n"
    "4. В ФИНАЛЬНОМ раунде:\n"
    "   - Подведи итог всей психологической дуэли.\n"
    "   - Вынеси четкий вердикт: КТО ОДИН проходит в Бункер и забирает главный приз, а кто остается снаружи (и почему).\n"
    "   - В самом конце ответа обязательно напиши строчку: ПОБЕДИТЕЛЬ: [Имя победившего игрока]"
)

HOST_SYSTEM_PROMPT_MAFIA = (
    "Ты — проницательный, жесткий, с черным юмором и живым огоньком Ведущий и Главный Следователь психологической игры «МАФИЯ: ПЕРЕКРЕСТНЫЙ ДОПРОС».\n"
    "За столом подозреваемых находятся живые игроки и 1 коварный НПС-подозреваемый, которого отыгрываешь ты сам.\n"
    "ТВОИ ПРАВИЛА:\n"
    "1. Полная беспристрастность: оценивай только мастерство блефа, манипуляций, логику алиби, эмоциональный прессинг и умение находить несостыковки.\n"
    "2. Никогда не раскрывай прямым текстом секретную правду до финала, но давай тонкие психологические зацепки.\n"
    "3. В каждом промежуточном раунде:\n"
    "   - Прокомментируй ответы игроков: кто умело перевел стрелки, а кто спалился на эмоциях.\n"
    "   - Отыграй короткую реплику от лица 3-го подозреваемого (НПС), который тоже защищается и пытается подставить игроков.\n"
    "   - Вбрось новую вскрытую улику с места преступления и устрой жесткий перекрестный допрос.\n"
    "4. В ФИНАЛЬНОМ раунде:\n"
    "   - Вскрой все карты: кто был настоящей Мафией/Убийцей, кому удалось переиграть стол и чья манипуляция сработала.\n"
    "   - Объяви, кто победил в этой партии и забирает главный приз.\n"
    "   - В самом конце ответа обязательно напиши строчку: ПОБЕДИТЕЛЬ: [Имя победившего игрока]"
)

def call_isolated_host_ai(game, round_prompt):
    host_pref = CONFIG.get('game_host_model', 'auto_gemini')
    sys_prompt = HOST_SYSTEM_PROMPT_BUNKER if game['type'] == 'bunker' else HOST_SYSTEM_PROMPT_MAFIA
    host_history = game.setdefault('host_history', [])
    s = get_http_session()

    if host_pref.startswith('groq/') and GROQ_API_KEY:
        messages = [{'role': 'system', 'content': sys_prompt}]
        for h in host_history:
            messages.append({'role': h['role'], 'content': h['content']})
        messages.append({'role': 'user', 'content': round_prompt})
        headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
        m_id = host_pref.replace('groq/', '')
        try:
            r = s.post(
                f'{GROQ_API_BASE}/chat/completions',
                headers=headers,
                json={'model': m_id, 'messages': messages, 'temperature': 0.8},
                timeout=22
            )
            if r.status_code == 200:
                ans = r.json()['choices'][0]['message']['content'].strip()
                ans = re.sub(r'<think>.*?</think>', '', ans, flags=re.DOTALL | re.IGNORECASE).strip()
                if ans:
                    host_history.append({'role': 'user', 'content': round_prompt})
                    host_history.append({'role': 'assistant', 'content': ans})
                    return ans, host_pref
        except Exception:
            pass

    if host_pref in ('auto_gemini', '') or host_pref.startswith('groq/'):
        base_m = main.ACTIVE_MODEL if not main.ACTIVE_MODEL.startswith('groq/') else (main.MODEL_HIERARCHY[0] if main.MODEL_HIERARCHY else 'gemini-3-flash')
    else:
        base_m = host_pref

    if base_m in main.MODEL_HIERARCHY:
        start_idx = main.MODEL_HIERARCHY.index(base_m)
        models_to_try = main.MODEL_HIERARCHY[start_idx:]
    else:
        models_to_try = [base_m] + list(main.MODEL_HIERARCHY)

    contents = []
    for h in host_history:
        role = 'user' if h['role'] == 'user' else 'model'
        contents.append({'role': role, 'parts': [{'text': h['content']}]})
    contents.append({'role': 'user', 'parts': [{'text': round_prompt}]})

    payload = {
        'contents': contents,
        'system_instruction': {'parts': [{'text': sys_prompt}]}
    }

    for ignore_cooldown in (False, True):
        for model in models_to_try:
            url = f"{GEMINI_API_BASE}/models/{model}:generateContent"
            for k_offset in range(len(GEMINI_KEYS)):
                now = time.time()
                key_idx = (main.CURRENT_KEY_INDEX + k_offset) % len(GEMINI_KEYS)
                api_key = GEMINI_KEYS[key_idx]
                q_key = (model, key_idx)
                if not ignore_cooldown and EXHAUSTED_QUOTAS.get(q_key, 0) > now:
                    continue
                try:
                    r = s.post(url, headers=main.get_gemini_headers(api_key), json=payload, timeout=(4, 20))
                    if r.status_code == 429:
                        EXHAUSTED_QUOTAS[q_key] = time.time() + 35
                        continue
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    if data.get('candidates'):
                        ans = data['candidates'][0]['content']['parts'][0]['text'].strip()
                        main.CURRENT_KEY_INDEX = (key_idx + 1) % len(GEMINI_KEYS)
                        host_history.append({'role': 'user', 'content': round_prompt})
                        host_history.append({'role': 'assistant', 'content': ans})
                        return ans, f"{model} [key #{key_idx + 1}]"
                except Exception:
                    continue

    return "⚠️ Серверы Google сейчас перегружены. Нажмите «⚡ Ответить Ведущему» еще раз через 5 секунд!", base_m

def generate_game_setup_from_main_gpt(chat_id, game_type, p1_name, p2_name):
    cid = int(chat_id)
    mem_slice = CHAT_MEMORY.get(cid, [])[-40:]
    log_slice = CHAT_LOG.get(cid, [])[-30:]
    context_lines = [m.get('content', '') for m in mem_slice if m.get('content')] or log_slice
    chat_vibe = "\n".join(context_lines[-35:]) if context_lines else "Дружеский чат с черным юмором и подколами."

    if game_type == 'bunker':
        setup_prompt = (
            f"Вот контекст общения в этом чате (используй его вайб, шутки и темы для атмосферы, но не копируй тупо):\n"
            f"---\n{chat_vibe}\n---\n\n"
            f"Мы запускаем сюжетную игру «БУНКЕР» для двух игроков: 1) {p1_name} и 2) {p2_name}.\n"
            "Придумай захватывающую, ироничную катастрофу и параметры бункера (осталось ТОЛЬКО ОДНО место!).\n"
            "Сгенерируй для каждого игрока секретную карточку (до 170 символов на блок, чтобы влезло во всплывающее окно!).\n"
            "Ответь СТРОГО по следующим тегам:\n"
            "<plot>Описание катастрофы, что творится снаружи, что есть в бункере и почему место только одно (2-3 абзаца с огоньком)</plot>\n"
            f"<p1_role>Профессия, возраст/здоровье и фобия для {p1_name} (кратко до 160 симв.)</p1_role>\n"
            f"<p1_loot>Багаж/лут, темный секрет и козырь для {p1_name} (кратко до 160 симв.)</p1_loot>\n"
            f"<p2_role>Профессия, возраст/здоровье и фобия для {p2_name} (кратко до 160 симв.)</p2_role>\n"
            f"<p2_loot>Багаж/лут, темный секрет и козырь для {p2_name} (кратко до 160 симв.)</p2_loot>\n"
            "<question>Первый провокационный вопрос Ведущего к обоим игрокам</question>"
        )
    else:
        setup_prompt = (
            f"Вот контекст общения в этом чате (используй его вайб и темы для колорита преступления):\n"
            f"---\n{chat_vibe}\n---\n\n"
            f"Мы запускаем детективно-психологическую игру «МАФИЯ: ДОПРОС».\n"
            f"За столом 3 подозреваемых: 1) {p1_name}, 2) {p2_name} и 3) колоритный НПС-персонаж (придумай ему имя).\n"
            "Распредели роли случайно: кто-то из них — настоящая Мафия/Убийца (или подельники), кто-то — Мирный под подозрением / Детектив с компроматом.\n"
            "Ответь СТРОГО по следующим тегам:\n"
            "<plot>Описание дерзкого преступления, место, первые публичные улики и представление 3 подозреваемых (2-3 абзаца)</plot>\n"
            f"<p1_role>Твоя секретная роль ({p1_name}), твоя цель и твое алиби (кратко до 160 симв.)</p1_role>\n"
            f"<p1_loot>Твоя секретная улика/компромат на руках и твой тайный косяк (кратко до 160 симв.)</p1_loot>\n"
            f"<p2_role>Твоя секретная роль ({p2_name}), твоя цель и твое алиби (кратко до 160 симв.)</p2_role>\n"
            f"<p2_loot>Твоя секретная улика/компромат на руках и твой тайный косяк (кратко до 160 симв.)</p2_loot>\n"
            "<truth>Секретная правда для ИИ-Ведущего: кто именно убийца, как всё было на самом деле и что скрывает НПС</truth>\n"
            "<question>Первое требование Следователя к игрокам на допросе</question>"
        )

    reply, err, model_used, _ = main.ask_gemini(cid, setup_prompt, save_to_memory=False)
    return reply, err, model_used

def extract_tag(text, tag, default_val):
    m = re.search(rf'<{tag}>(.*?)</{tag}>', text or '', flags=re.DOTALL | re.IGNORECASE)
    return m.group(1).strip() if m else default_val

def build_story_game_keyboard(cid):
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🪪 Моя Роль (Секретно)", callback_data=f"sg_role_{cid}"),
        InlineKeyboardButton("🎒 Мой Лут / Улики", callback_data=f"sg_loot_{cid}")
    )
    kb.add(
        InlineKeyboardButton("⚡ Ответить Ведущему (Готово)", callback_data=f"sg_force_{cid}"),
        InlineKeyboardButton("🏳️ Стоп игра", callback_data=f"sg_stop_{cid}")
    )
    return kb

def schedule_story_round_timer(cid, round_num):
    game = STORY_GAMES.get(cid)
    if not game or game.get('finished'):
        return
    if game.get('timer'):
        game['timer'].cancel()

    secs = max(15, int(CONFIG.get('game_round_seconds', 60)))

    def _on_timeout():
        g = STORY_GAMES.get(cid)
        if not g or g.get('finished') or g.get('round') != round_num or g.get('processing'):
            return
        g['timer_expired'] = True
        ready, missing_names = check_story_round_ready(g)
        if ready:
            advance_story_game_round(cid)
        else:
            miss_str = ", ".join([html.escape(n) for n in missing_names])
            try:
                bot.send_message(
                    cid,
                    f"⏰ <b>Время раунда ({secs} сек.) вышло!</b>\n"
                    f"Но для хода Ведущего нужно минимум по 1 сообщению от каждого.\n"
                    f"⏳ Ждем высказывание от: <b>{miss_str}</b> (как только напишет — Ведущий сразу продолжит!)",
                    business_connection_id=g.get('biz_id')
                )
            except Exception:
                pass

    t = threading.Timer(secs, _on_timeout)
    t.daemon = True
    game['timer'] = t
    t.start()

def check_story_round_ready(game):
    msgs = game.get('round_messages', {})
    missing = []
    for req_role in game.get('required_roles', ['p1', 'p2']):
        if not msgs.get(req_role):
            missing.append(game[req_role + '_name'])
    return (len(missing) == 0), missing

def start_story_game(chat_id, biz_id, game_type, starter_user):
    cid = int(chat_id)
    old_g = STORY_GAMES.pop(cid, None)
    if old_g and old_g.get('timer'):
        old_g['timer'].cancel()

    if biz_id:
        p1_id = main.CREATOR_ID
        p1_name = f"@{CREATOR_USERNAME}"
        if starter_user.id != main.CREATOR_ID:
            p2_id = starter_user.id
            p2_name = starter_user.first_name or "Собеседник"
        else:
            p2_id = cid
            p2_name = "Собеседник"
            for u_info in CONFIG.get('known_chats', {}).values():
                if u_info.get('chat_id') == cid and u_info.get('name'):
                    p2_name = u_info['name']
                    break
        required_roles = ['p1', 'p2']
    else:
        p1_id = starter_user.id
        p1_name = starter_user.first_name or "Игрок"
        p2_id = 0
        p2_name = "Кибер-Соперник (НПС)"
        required_roles = ['p1']

    title = "☢️ БУНКЕР НА ВЫЖИВАНИЕ" if game_type == 'bunker' else "🕵️ МАФИЯ: ПЕРЕКРЕСТНЫЙ ДОПРОС"
    wait_msg = bot.send_message(
        cid,
        f"🎬 <b>Запускаем «{title}»...</b>\n"
        f"<i>Gemini пишет сюжет и секретные роли по мотивам вашего чата...</i>",
        business_connection_id=biz_id
    )

    raw_setup, err, gen_model = generate_game_setup_from_main_gpt(cid, game_type, p1_name, p2_name)
    if not raw_setup:
        safe_edit_message(f"⚠️ Не удалось сгенерировать сюжет: {html.escape(str(err))}", cid, wait_msg.message_id, business_id=biz_id)
        return

    plot = extract_tag(raw_setup, 'plot', raw_setup[:1200])
    p1_role = extract_tag(raw_setup, 'p1_role', "Выживший специалист со стальными нервами.")
    p1_loot = extract_tag(raw_setup, 'p1_loot', "Аптечка, компромат и скрытый мотив.")
    p2_role = extract_tag(raw_setup, 'p2_role', "Хитрый переговорщик с темным прошлым.")
    p2_loot = extract_tag(raw_setup, 'p2_loot', "Связка ключей, фальшивые документы и улика.")
    truth = extract_tag(raw_setup, 'truth', "Оба игрока ведут двойную игру.")
    question = extract_tag(raw_setup, 'question', "Представьтесь, вскройте часть своей позиции и объясните, почему верить нужно именно вам!")

    max_rounds = max(2, int(CONFIG.get('game_max_rounds', 3)))
    round_secs = max(15, int(CONFIG.get('game_round_seconds', 60)))
    win_prize = int(CONFIG.get('reward_bunker', 1000))
    host_pref = CONFIG.get('game_host_model', 'auto_gemini')
    host_label = f"{main.ACTIVE_MODEL} (отдельное окно)" if host_pref == 'auto_gemini' else host_pref

    initial_host_context = (
        f"ИГРА: {title}\n"
        f"СЮЖЕТ:\n{plot}\n\n"
        f"СЕКРЕТНОЕ ДОСЬЕ ИГРОКА 1 ({p1_name}):\n- Роль: {p1_role}\n- Лут/Улики: {p1_loot}\n\n"
        f"СЕКРЕТНОЕ ДОСЬЕ ИГРОКА 2 ({p2_name}):\n- Роль: {p2_role}\n- Лут/Улики: {p2_loot}\n\n"
        f"СКРЫТАЯ ПРАВДА СТОЛА: {truth}\n"
        f"ПЕРВЫЙ ВОПРОС ИГРОКАМ: {question}"
    )

    game = {
        'chat_id': cid,
        'biz_id': biz_id,
        'type': game_type,
        'title': title,
        'p1_id': p1_id,
        'p1_name': p1_name,
        'p1_role': p1_role,
        'p1_loot': p1_loot,
        'p2_id': p2_id,
        'p2_name': p2_name,
        'p2_role': p2_role,
        'p2_loot': p2_loot,
        'required_roles': required_roles,
        'round': 1,
        'max_rounds': max_rounds,
        'round_messages': {'p1': [], 'p2': []},
        'host_history': [
            {'role': 'user', 'content': "Запомни установочные данные партии и секретные роли игроков:\n" + initial_host_context},
            {'role': 'assistant', 'content': f"Принято! Я веду игру «{title}», вижу все секретные роли и улики. Жду высказываний игроков в Раунде 1."}
        ],
        'timer': None,
        'timer_expired': False,
        'processing': False,
        'finished': False
    }
    STORY_GAMES[cid] = game

    kb = build_story_game_keyboard(cid)
    msg_text = (
        f"🎭 <b>{title} — РАУНД 1/{max_rounds}</b> (Приз: <b>{win_prize} 🧬</b>)\n"
        f"<i>Сюжет: <code>{gen_model}</code> | Ведущий: <code>{host_label}</code></i>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{main.format_markdown_to_html(plot)}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎙 <b>Ведущий ставит задачу:</b>\n"
        f"<blockquote>{html.escape(question)}</blockquote>\n\n"
        f"👇 <b>Жмите на секретные кнопки ниже, чтобы узнать свою роль и лут!</b>\n"
        f"⏱ У вас <b>{round_secs} сек.</b> (и минимум по 1 сообщению от каждого прямо в чат), чтобы высказать позицию, сблефовать или обвинить соперника!"
    )
    safe_edit_message(msg_text, cid, wait_msg.message_id, business_id=biz_id, reply_markup=kb)
    schedule_story_round_timer(cid, 1)

def advance_story_game_round(cid):
    game = STORY_GAMES.get(cid)
    if not game or game.get('finished') or game.get('processing'):
        return

    game['processing'] = True
    if game.get('timer'):
        game['timer'].cancel()

    biz_id = game.get('biz_id')
    cur_round = game['round']
    max_rounds = game['max_rounds']
    is_final = (cur_round >= max_rounds)

    p1_msgs = "\n".join(game['round_messages'].get('p1', [])) or "(промолчал)"
    p2_msgs = "\n".join(game['round_messages'].get('p2', [])) or "(Если это НПС — отыграй его ответ сам)"

    if is_final:
        turn_prompt = (
            f"РАУНД {cur_round} из {max_rounds} (ФИНАЛ ИГРЫ!).\n"
            f"Высказывания игрока {game['p1_name']}:\n{p1_msgs}\n\n"
            f"Высказывания игрока {game['p2_name']}:\n{p2_msgs}\n\n"
            "Проанализируй их финальные речи, манипуляции и использование своих карт/улик. "
            "Вскрой правду, ярко подведи итог и вынеси вердикт! В последней строке обязательно укажи: ПОБЕДИТЕЛЬ: [Имя]"
        )
    else:
        turn_prompt = (
            f"РАУНД {cur_round} из {max_rounds}.\n"
            f"Высказывания игрока {game['p1_name']}:\n{p1_msgs}\n\n"
            f"Высказывания игрока {game['p2_name']}:\n{p2_msgs}\n\n"
            f"Разбери их аргументы и блеф с огоньком, вбрось новый поворот сюжета/улику и задай обоим острые вопросы для Раунда {cur_round + 1}!"
        )

    status_msg = bot.send_message(
        cid,
        f"🎙 <i>Ведущий (Gemini) анализирует ваши речи и манипуляции (Раунд {cur_round}/{max_rounds})...</i>",
        business_connection_id=biz_id
    )

    host_reply, host_model_used = call_isolated_host_ai(game, turn_prompt)

    if is_final:
        game['finished'] = True
        game['processing'] = False
        STORY_GAMES.pop(cid, None)

        win_prize = int(CONFIG.get('reward_bunker', 1000))
        reward_note = f"\n\n🎁 <b>Награда за партию:</b> оба участника получают <b>+150 🧬 и +50 XP</b>, а <b>ПОБЕДИТЕЛЬ забирает ГЛАВНЫЙ ПРИЗ +{win_prize} 🧬 вирусов!</b>"
        winner_tail = host_reply.split('ПОБЕДИТЕЛЬ:')[-1].lower() if 'ПОБЕДИТЕЛЬ:' in host_reply else host_reply[-150:].lower()

        for uid_key in ('p1_id', 'p2_id'):
            uid_val = str(game.get(uid_key) or '')
            if uid_val in RPG_PLAYERS:
                pl = RPG_PLAYERS[uid_val]
                pl['bio_res'] = pl.get('bio_res', 0) + 150
                pl['xp'] = pl.get('xp', 0) + 50
                if pl['name'].lower() in winner_tail or (pl.get('username') and pl['username'].lower() in winner_tail):
                    pl['bio_res'] += win_prize
                    pl['xp'] += 100
                    win_field = 'wins_bunker' if game['type'] == 'bunker' else 'wins_mafia'
                    pl[win_field] = pl.get(win_field, 0) + 1
                    try_grant_rare_buff(pl)
        save_rpg_firebase()

        final_text = (
            f"🏁 <b>{game['title']} — ФИНАЛЬНЫЙ ВЕРДИКТ ВЕДУЩЕГО</b>\n"
            f"<i>Ведущий: <code>{host_model_used}</code></i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{main.format_markdown_to_html(host_reply)}"
            f"{reward_note}"
        )
        safe_edit_message(final_text, cid, status_msg.message_id, business_id=biz_id)
    else:
        game['round'] += 1
        game['round_messages'] = {'p1': [], 'p2': []}
        game['timer_expired'] = False
        game['processing'] = False
        next_r = game['round']
        round_secs = max(15, int(CONFIG.get('game_round_seconds', 60)))

        kb = build_story_game_keyboard(cid)
        round_text = (
            f"🎭 <b>{game['title']} — РАУНД {next_r}/{max_rounds}</b>\n"
            f"<i>Ведущий: <code>{host_model_used}</code></i>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{main.format_markdown_to_html(host_reply)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⏱ У вас <b>{round_secs} сек.</b> (и минимум по 1 сообщению от каждого в чат), чтобы ответить на допрос Ведущего!"
        )
        safe_edit_message(round_text, cid, status_msg.message_id, business_id=biz_id, reply_markup=kb)
        schedule_story_round_timer(cid, next_r)

def record_story_player_message(message):
    cid = int(message.chat.id)
    game = STORY_GAMES.get(cid)
    if not game or game.get('finished') or game.get('processing'):
        return False

    user = message.from_user
    uid = user.id
    is_owner = is_user_authorized_for_ai(user)

    if game.get('biz_id'):
        role = 'p1' if is_owner else 'p2'
        if role == 'p2':
            game['p2_id'] = uid
            game['p2_name'] = user.first_name or "Собеседник"
    else:
        role = 'p1'

    text = (message.text or '').strip()
    if not text:
        return False

    game['round_messages'].setdefault(role, []).append(text)

    if game.get('timer_expired'):
        ready, _ = check_story_round_ready(game)
        if ready:
            threading.Thread(target=advance_story_game_round, args=(cid,), daemon=True).start()
    return True

# =====================================================================
# 👑 6.5 МОДЕРАЦИЯ, ПРОСМОТР ЛС БОТА И ОТВЕТЫ ОТ ИМЕНИ БОТА + НАСТРОЙКИ
# =====================================================================
def build_mod_hub_view():
    """Главное меню просмотра ЛС бота, управления игроками и живого чата от имени бота."""
    spy_pm = CONFIG.get('pm_spy_enabled', True)
    live_str = f"🟢 АКТИВЕН (пишем id:{ADMIN_LIVE_CHAT_UID})" if ADMIN_LIVE_CHAT_UID else "⚪ Выкл"

    lines = [
        "👑 <b>Центр Модерации и Переписок в ЛС Бота</b>\n",
        f"📩 <b>Пересылка ЛС бота владельцу:</b> {'🟢 ВКЛ' if spy_pm else '🔴 ВЫКЛ'}",
        f"🗣 <b>Режим живого ответа от имени бота:</b> {live_str}",
        f"👥 <b>Всего игроков в базе:</b> {len(RPG_PLAYERS)} | <b>Писали в ЛС бота:</b> {len(BOT_PM_LOGS)}\n",
        "👇 <b>Нажми на пользователя ниже, чтобы прочитать его чат с ботом, написать ему от лица бота или изменить его баланс/уровень:</b>"
    ]

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"📩 Слежка ЛС: {'ВКЛ 🟢' if spy_pm else 'ВЫКЛ 🔴'}", callback_data="mod_toggle_pmspy"),
        InlineKeyboardButton("⚙️ Все параметры", callback_data="mod_open_settings")
    )
    if ADMIN_LIVE_CHAT_UID:
        kb.add(InlineKeyboardButton("🛑 Выйти из режима ответа от бота", callback_data="mod_stop_live"))

    # Собираем список юзеров (сначала те, кто писал в ЛС бота, затем остальные из базы)
    shown_uids = set()
    for uid_str, info in list(BOT_PM_LOGS.items())[-12:]:
        shown_uids.add(uid_str)
        u_lbl = f"@{info['username']}" if info.get('username') else info.get('name', uid_str)
        kb.add(
            InlineKeyboardButton(f"💬 Чат: {u_lbl[:18]}", callback_data=f"mod_hist_{uid_str}"),
            InlineKeyboardButton(f"🛠 Мод: {u_lbl[:18]}", callback_data=f"mod_user_{uid_str}")
        )

    for uid_str, p_data in list(RPG_PLAYERS.items())[-10:]:
        if uid_str in shown_uids or len(shown_uids) >= 16:
            continue
        shown_uids.add(uid_str)
        u_lbl = f"@{p_data['username']}" if p_data.get('username') else p_data.get('name', uid_str)
        kb.add(
            InlineKeyboardButton(f"💬 Чат: {u_lbl[:18]}", callback_data=f"mod_hist_{uid_str}"),
            InlineKeyboardButton(f"🛠 Мод: {u_lbl[:18]}", callback_data=f"mod_user_{uid_str}")
        )

    kb.add(InlineKeyboardButton("🔙 Назад в /panel", callback_data="panel_refresh"))
    return "\n".join(lines), kb

def build_user_pm_history_view(uid_str):
    """Показывает историю переписки конкретного юзера с ботом в ЛС."""
    info = BOT_PM_LOGS.get(uid_str)
    p = RPG_PLAYERS.get(uid_str, {})
    name = html.escape((info or p).get('name', f'id:{uid_str}'))
    uname = (info or p).get('username') or ''
    u_tag = f"@{uname}" if uname else f"id:{uid_str}"

    log_lines = info.get('log', [])[-20:] if info else []
    log_block = "\n".join([html.escape(l) for l in log_lines]) if log_lines else "<i>(Этот пользователь еще не переписывался с ботом в ЛС)</i>"

    text = (
        f"💬 <b>Переписка в ЛС бота с {name} ({u_tag}):</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{log_block}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💡 <b>Как ответить ему от имени бота:</b>\n"
        f"• Нажми кнопку <b>«🗣 Включить живой чат от бота»</b> ниже и просто пиши сообщения!\n"
        f"• Или напиши: <code>написать {u_tag} Привет!</code>"
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🗣 Живой чат от бота", callback_data=f"mod_live_{uid_str}"),
        InlineKeyboardButton("🛠 Профиль и Баланс", callback_data=f"mod_user_{uid_str}")
    )
    kb.add(
        InlineKeyboardButton("🔄 Обновить чат", callback_data=f"mod_hist_{uid_str}"),
        InlineKeyboardButton("🔙 К списку ЛС", callback_data="mod_open_hub")
    )
    return text, kb

def build_user_moderation_card(uid_str):
    """Инспекция и редактирование любого параметра конкретного игрока."""
    p = RPG_PLAYERS.get(uid_str)
    if not p:
        return "⚠️ Профиль игрока не найден.", InlineKeyboardMarkup()

    lvl, rank_title = get_level_and_rank(p.get('xp', 0))
    uname = p.get('username') or ''
    u_tag = f"@{uname}" if uname else f"id:{uid_str}"
    is_banned = (uname and uname in CONFIG.get('bot_banned_users', {})) or (uid_str in CONFIG.get('bot_banned_users', {}))
    has_vip = (uname and uname in CONFIG.get('allowed_users', {})) or (uid_str in CONFIG.get('allowed_users', {}))
    fever_rem = max(0, int((p.get('fever_until', 0) - time.time()) // 60))

    text = (
        f"🛠 <b>Модерация игрока: {html.escape(p.get('name', 'Игрок'))} ({u_tag})</b>\n"
        f"🆔 ID: <code>{uid_str}</code>\n"
        f"🎖 <b>Уровень:</b> {lvl} ({rank_title}) | ✨ <b>XP:</b> <code>{p.get('xp', 0)}</code>\n"
        f"🧬 <b>Вирусы:</b> <code>{p.get('bio_res', 0)}</code> | 🧪 <b>Патогены:</b> <code>{p.get('patogens', 0)}</code>\n"
        f"☣️ <b>Штамм:</b> «{html.escape(p.get('strain_name', 'Х'))}» | 🤒 <b>Горячка:</b> {fever_rem} мин.\n"
        f"🔬 <b>Лаба (Пат/Разр/Зар/Имм/Лет/Без):</b> {p.get('lab_patogen_lvl',1)} / {p.get('lab_dev_lvl',1)} / {p.get('lab_infect_lvl',1)} / {p.get('lab_immune_lvl',1)} / {p.get('lab_lethal_lvl',1)} / {p.get('lab_security_lvl',1)}\n"
        f"🔑 <b>VIP-доступ к ИИ:</b> {'🟢 ДА' if has_vip else '⚪ НЕТ'} | 🚫 <b>Бан в боте:</b> {'🔴 ЗАБАНЕН' if is_banned else '🟢 Нет'}"
    )

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("➕ +1000 🧬", callback_data=f"mod_act_{uid_str}_add1000"),
        InlineKeyboardButton("➖ -1000 🧬", callback_data=f"mod_act_{uid_str}_sub1000")
    )
    kb.add(
        InlineKeyboardButton("✨ +500 XP", callback_data=f"mod_act_{uid_str}_addxp"),
        InlineKeyboardButton("🧪 +5 Патогенов", callback_data=f"mod_act_{uid_str}_addpat")
    )
    kb.add(
        InlineKeyboardButton("💉 Вылечить горячку", callback_data=f"mod_act_{uid_str}_cure"),
        InlineKeyboardButton("🦠 Наслать горячку 30м", callback_data=f"mod_act_{uid_str}_infect")
    )
    kb.add(
        InlineKeyboardButton(f"🔑 Доступ ИИ: {'ВЫКЛ' if has_vip else 'ВКЛ'}", callback_data=f"mod_act_{uid_str}_togglevip"),
        InlineKeyboardButton(f"🚫 Бан: {'РАЗБАНИТЬ' if is_banned else 'ЗАБАНИТЬ'}", callback_data=f"mod_act_{uid_str}_toggleban")
    )
    kb.add(
        InlineKeyboardButton("🗣 Писать от бота", callback_data=f"mod_live_{uid_str}"),
        InlineKeyboardButton("🧹 Обнулить профиль", callback_data=f"mod_act_{uid_str}_wipe")
    )
    kb.add(InlineKeyboardButton("🔙 К списку игроков", callback_data="mod_open_hub"))
    return text, kb

def build_all_settings_view():
    """Меню тонкой настройки вообще всех параметров бота и игр кнопками."""
    text = (
        "⚙️ <b>Тонкая настройка ВСЕХ параметров бота и игр:</b>\n\n"
        f"🗑 <b>КД Хабаровского Лутинга:</b> <code>{CONFIG.get('looting_cd_minutes', 5)}</code> мин.\n"
        f"🧪 <b>Базовое время синтеза патогена:</b> <code>{CONFIG.get('base_patogen_minutes', 15)}</code> мин.\n"
        f"🦠 <b>Базовый шанс заражения:</b> <code>{CONFIG.get('base_infect_chance', 55)}%</code>\n"
        f"💉 <b>Цена вакцины:</b> <code>{CONFIG.get('vaccine_cost', 80)}</code> 🧬\n"
        f"☢️ <b>Приз за Бункер / Мафию:</b> <code>{CONFIG.get('reward_bunker', 1000)}</code> 🧬\n"
        f"🎡 <b>Приз за Виселицу:</b> <code>{CONFIG.get('reward_hangman', 200)}</code> 🧬\n"
        f"✂️ <b>Приз за КНБ:</b> <code>{CONFIG.get('reward_rps', 50)}</code> 🧬\n"
        f"⏱ <b>Таймер раунда (Бункер/Мафия):</b> <code>{CONFIG.get('game_round_seconds', 60)}</code> сек.\n"
        f"🎭 <b>Раундов в Бункере/Мафии:</b> <code>{CONFIG.get('game_max_rounds', 3)}</code>\n"
        f"🤖 <b>Таймер АФК Автоответчика:</b> <code>{CONFIG.get('afk_minutes', 10)}</code> мин.\n"
        f"🎲 <b>Шанс ответа Автоответчика:</b> <code>{CONFIG.get('human_reply_chance', 100)}%</code>\n"
        f"⏳ <b>Анти-спам КД ГПТ:</b> <code>{CONFIG.get('cooldown_seconds', 5)}</code> сек."
    )
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton(f"🗑 КД Лута: {CONFIG.get('looting_cd_minutes', 5)}м", callback_data="cfg_cycle_lootcd"),
        InlineKeyboardButton(f"🧪 КД Патогена: {CONFIG.get('base_patogen_minutes', 15)}м", callback_data="cfg_cycle_patcd")
    )
    kb.add(
        InlineKeyboardButton(f"☢️ Приз Бункер: {CONFIG.get('reward_bunker', 1000)}", callback_data="cfg_cycle_rewbunker"),
        InlineKeyboardButton(f"🎡 Приз Виселица: {CONFIG.get('reward_hangman', 200)}", callback_data="cfg_cycle_rewhm")
    )
    kb.add(
        InlineKeyboardButton(f"💉 Вакцина: {CONFIG.get('vaccine_cost', 80)}🧬", callback_data="cfg_cycle_vac"),
        InlineKeyboardButton(f"🦠 Баз. шанс: {CONFIG.get('base_infect_chance', 55)}%", callback_data="cfg_cycle_infch")
    )
    kb.add(
        InlineKeyboardButton(f"⏱ Раунд: {CONFIG.get('game_round_seconds', 60)}с", callback_data="cfg_cycle_rsec"),
        InlineKeyboardButton(f"🎭 Раундов: {CONFIG.get('game_max_rounds', 3)}", callback_data="cfg_cycle_rmax")
    )
    kb.add(
        InlineKeyboardButton(f"🤖 Таймер АФК: {CONFIG.get('afk_minutes', 10)}м", callback_data="cfg_cycle_afk"),
        InlineKeyboardButton(f"🎲 Шанс Авто: {CONFIG.get('human_reply_chance', 100)}%", callback_data="cfg_cycle_chance")
    )
    kb.add(
        InlineKeyboardButton("🎙 Модель Ведущего Игр", callback_data="cfg_open_hostmodels"),
        InlineKeyboardButton("🔙 Назад в /panel", callback_data="panel_refresh")
    )
    return text, kb

# =====================================================================
# 🎛 7. ОБРАБОТЧИКИ КОМАНД, МОДЕРАЦИИ И ИГР
# =====================================================================
GAME_TRIGGERS = {
    'лаба', 'моя лаба', 'био', 'биовойны', 'заразить', 'заразить все', 'заразить фулл', 'вакцина', 'лечиться',
    'доступы', 'лс бота', 'чаты бота', 'модерация', '/mod', 'настройки', 'параметры',
    '+патоген', '+разработка', '+заразность', '+иммунитет', '+летальность', '+безопасность',
    'виселица', 'виселица ии', 'поле чудес', 'кнб', 'цуефа', 'рпг', 'профиль', 'топ', 'био топ', 'игры',
    'команды', 'помощь', 'хелп', '/help',
    '/search', 'лут', 'мусорка', 'лутинг',
    'бункер', 'игра бункер', 'мафия', 'игра мафия', 'стоп игра', 'игра стоп',
    'игра-модели', 'модели игры', 'модели ведущего'
}

GAME_PREFIXES = (
    'заразить ', 'штамм ', 'виселица ', 'кд лут ', 'кд патоген ', 'цена вакцины ',
    'приз бункер ', 'приз виселица ', 'приз кнб ', 'шанс заражения ',
    'время игры ', 'раунды игры ', 'доступ ', 'забрать доступ ',
    'написать ', 'диалог ', 'выдать ', 'забрать ', 'опыт ', 'патогены ',
    'вылечить ', 'обнулить ', 'бан бот ', 'разбан бот ', 'рассылка ', 'профиль '
)

def is_game_command(text):
    t = (text or '').lower().strip()
    if not t:
        return False
    if t in GAME_TRIGGERS:
        return True
    for prefix in GAME_PREFIXES:
        if t.startswith(prefix):
            return True
    return False

def should_intercept_business_message(message):
    try:
        biz_id = getattr(message, 'business_connection_id', None)
        if biz_id and not is_business_connection_authorized(biz_id):
            return True

        text = (message.text or '').strip()
        if not text:
            return False
        low = text.lower()

        if low == 'гпт' or low.startswith('гпт ') or low.startswith('нарисуй '):
            return False

        if is_game_command(low):
            return True

        cid = int(message.chat.id)
        if cid in STORY_GAMES and not STORY_GAMES[cid].get('finished'):
            return True

        if cid in HANGMAN_GAMES and not HANGMAN_GAMES[cid].get('finished'):
            clean = low.upper().replace('Ё', 'Е')
            word_len = len(HANGMAN_GAMES[cid]['word'])
            is_reply_to_hm = bool(message.reply_to_message and message.reply_to_message.message_id == HANGMAN_GAMES[cid].get('msg_id'))
            if len(clean) == 1 and clean in RUS_ALPHABET:
                return True
            if re.match(r'^[А-Я]+$', clean) and (len(clean) == word_len or is_reply_to_hm):
                return True
    except Exception as e:
        print(f"⚠️ Ошибка в should_intercept_business_message: {e}")
    return False

def should_intercept_private_message(message):
    try:
        if message.chat.type != 'private' or not message.from_user:
            return False
        if not is_owner_user(message.from_user):
            return True

        # Если владелец отвечает реплаем на пересланное сообщение из ЛС бота
        if message.reply_to_message and message.reply_to_message.message_id in FORWARDED_PM_MAP:
            return True

        # Если у владельца включен режим живого чата от имени бота
        if ADMIN_LIVE_CHAT_UID is not None:
            return True

        return should_intercept_business_message(message)
    except Exception as e:
        print(f"⚠️ Ошибка в should_intercept_private_message: {e}")
        return False

def resolve_target_player(message, low_cmd):
    cid = int(message.chat.id)
    biz_id = getattr(message, 'business_connection_id', None)
    user = message.from_user

    if '@' in low_cmd:
        uname = low_cmd.split('@')[-1].split()[0]
        target_p = find_player_by_username_or_id(uname)
        if not target_p:
            known = CONFIG.get('known_chats', {}).get(uname.lower())
            if known:
                fake_u = type('Obj', (), {'id': known['chat_id'], 'username': uname, 'first_name': known.get('name', uname), 'last_name': ''})()
                target_p = get_player(fake_u)
        return target_p
    if message.reply_to_message and message.reply_to_message.from_user:
        return get_player(message.reply_to_message.from_user)
    if biz_id:
        if user.id != cid:
            chat_uname = getattr(message.chat, 'username', '') or ''
            chat_fname = getattr(message.chat, 'first_name', '') or 'Собеседник'
            fake_u = type('Obj', (), {'id': cid, 'username': chat_uname, 'first_name': chat_fname, 'last_name': ''})()
            return get_player(fake_u)
        elif main.CREATOR_ID:
            fake_owner = type('Obj', (), {'id': main.CREATOR_ID, 'username': CREATOR_USERNAME, 'first_name': f"@{CREATOR_USERNAME}", 'last_name': ''})()
            return get_player(fake_owner)
    return None

def send_public_user_hub(cid, user):
    p = get_player(user)
    lvl, rank_title = get_level_and_rank(p.get('xp', 0))
    has_ai = is_user_authorized_for_ai(user)
    ai_badge = "🟢 Выдан VIP-доступ к ИИ" if has_ai else "🔒 Только игры и Био-войны (ИИ по доступу владельца)"

    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(
        InlineKeyboardButton("🧬 Моя Лаборатория", callback_data=f"biorefresh_{p['uid']}"),
        InlineKeyboardButton("🗑 Лутать мусорку", callback_data="loot_again")
    )
    kb.add(
        InlineKeyboardButton("🎡 Виселица", callback_data=f"hm_new_{cid}_std"),
        InlineKeyboardButton("✂️ КНБ с Ботом", callback_data=f"rps_botmenu_{cid}")
    )
    kb.add(
        InlineKeyboardButton("🏆 Топ игроков", callback_data="pub_top"),
        InlineKeyboardButton("📜 Все команды", callback_data="pub_help")
    )

    text = (
        f"👋 <b>Привет, {html.escape(p['name'])}! Добро пожаловать в Игровой и Био-Хаб!</b>\n\n"
        f"🎖 <b>Твое звание:</b> {rank_title} (Ур. {lvl} | ✨ {p.get('xp', 0)} XP)\n"
        f"🧬 <b>Вирусы:</b> <code>{p.get('bio_res', 0)}</code> | 🧪 <b>Патогены:</b> {p.get('patogens', 0)}\n"
        f"🔑 <b>Статус ИИ:</b> {ai_badge}\n\n"
        f"👇 <b>Выбирай действие на кнопках или пиши команды (<code>лаба</code>, <code>/search</code>, <code>виселица</code>, <code>кнб</code>, <code>профиль</code>):</b>"
    )
    send_logged_pm(cid, text, reply_markup=kb, target_user=user)

def handle_all_game_commands(message):
    global ADMIN_LIVE_CHAT_UID
    if not message.from_user:
        return
    register_creator_id(message.from_user)
    cid = int(message.chat.id)
    biz_id = getattr(message, 'business_connection_id', None)

    if biz_id and not is_business_connection_authorized(biz_id):
        return

    raw_text = (message.text or '').strip()
    low = raw_text.lower()
    user = message.from_user
    is_owner = is_owner_user(user)

    # Проверка бана в боте для обычных пользователей в ЛС
    if not biz_id and not is_owner and is_user_banned_in_bot(user):
        bot.send_message(cid, "🚫 Доступ к боту для твоего аккаунта ограничен администратором.")
        return

    # Если пишет обычный пользователь в ЛС бота — логируем и пересылаем владельцу!
    if not biz_id and not is_owner:
        notify_owner_about_bot_pm(user, raw_text)

    # --- РЕЖИМ ОТВЕТА ВЛАДЕЛЬЦА ОТ ИМЕНИ БОТА (ЧЕРЕЗ REPLY ИЛИ LIVE-ЧАТ) ---
    if not biz_id and is_owner:
        if low in ('стоп', '/stop', 'выйти') and ADMIN_LIVE_CHAT_UID is not None:
            old_t = ADMIN_LIVE_CHAT_UID
            ADMIN_LIVE_CHAT_UID = None
            bot.send_message(cid, f"🛑 Режим живого общения от имени бота (с <code>{old_t}</code>) завершен.")
            return

        # Reply на уведомление 📩 [ЛС Бота]
        if message.reply_to_message and message.reply_to_message.message_id in FORWARDED_PM_MAP:
            target_uid = FORWARDED_PM_MAP[message.reply_to_message.message_id]
            try:
                bot.send_message(target_uid, raw_text)
                if str(target_uid) in BOT_PM_LOGS:
                    t_str = time.strftime('%H:%M')
                    BOT_PM_LOGS[str(target_uid)]['log'].append(f"[{t_str}] 🗣 [Ты от лица Бота]: {raw_text}")
                bot.reply_to(message, f"✅ Отправлено от имени бота пользователю <code>{target_uid}</code>!")
            except Exception as e:
                bot.reply_to(message, f"⚠️ Не удалось отправить: {e}")
            return

        # Активный режим живого диалога от имени бота
        if ADMIN_LIVE_CHAT_UID is not None and not is_game_command(low) and not low.startswith('/'):
            target_uid = ADMIN_LIVE_CHAT_UID
            try:
                bot.send_message(target_uid, raw_text)
                if str(target_uid) in BOT_PM_LOGS:
                    t_str = time.strftime('%H:%M')
                    BOT_PM_LOGS[str(target_uid)]['log'].append(f"[{t_str}] 🗣 [Ты от лица Бота]: {raw_text}")
                bot.send_message(cid, f"📤 <i>Доставлено от лица бота (для выхода напиши <code>стоп</code>)</i>")
            except Exception as e:
                bot.send_message(cid, f"⚠️ Ошибка отправки: {e}")
            return

    has_ai_access = is_user_authorized_for_ai(user) or bool(biz_id)
    p = get_player(user)

    # 0. Меню для обычных пользователей в ЛС бота
    if not biz_id and not is_owner and low in ('/start', 'старт', 'меню', '/panel', 'панель'):
        send_public_user_hub(cid, user)
        return

    # --- КОМАНДЫ МОДЕРАЦИИ И БОГ-МОДА (ТОЛЬКО ДЛЯ ВЛАДЕЛЬЦА) ---
    if is_owner:
        if low in ('лс бота', 'чаты бота', 'модерация', '/mod'):
            m_text, m_kb = build_mod_hub_view()
            bot.send_message(cid, m_text, business_connection_id=biz_id, reply_markup=m_kb)
            return

        if low in ('настройки', 'параметры'):
            s_text, s_kb = build_all_settings_view()
            bot.send_message(cid, s_text, business_connection_id=biz_id, reply_markup=s_kb)
            return

        if low.startswith('написать '):
            parts = raw_text.split(maxsplit=2)
            if len(parts) < 3:
                bot.send_message(cid, "⚠️ Формат: <code>написать @юзер Привет, как дела?</code>", business_connection_id=biz_id)
                return
            t_p = find_player_by_username_or_id(parts[1])
            if not t_p:
                bot.send_message(cid, f"⚠️ Пользователь {html.escape(parts[1])} не найден в базе бота.", business_connection_id=biz_id)
                return
            msg_to_send = parts[2]
            try:
                bot.send_message(int(t_p['uid']), msg_to_send)
                t_str = time.strftime('%H:%M')
                BOT_PM_LOGS.setdefault(str(t_p['uid']), {'uid': str(t_p['uid']), 'name': t_p['name'], 'username': t_p.get('username',''), 'log': []})['log'].append(f"[{t_str}] 🗣 [Ты от лица Бота]: {msg_to_send}")
                bot.send_message(cid, f"✅ Сообщение отправлено от лица бота игроку <b>{html.escape(t_p['name'])}</b>!", business_connection_id=biz_id)
            except Exception as e:
                bot.send_message(cid, f"⚠️ Ошибка отправки (возможно, юзер не запускал бота в ЛС): {e}", business_connection_id=biz_id)
            return

        if low.startswith('диалог '):
            q_u = raw_text.split(maxsplit=1)[1]
            t_p = find_player_by_username_or_id(q_u)
            if not t_p:
                bot.send_message(cid, "⚠️ Игрок не найден в базе.", business_connection_id=biz_id)
                return
            ADMIN_LIVE_CHAT_UID = int(t_p['uid'])
            bot.send_message(
                cid,
                f"🗣 <b>Включен режим живого чата от имени бота с {html.escape(t_p['name'])}!</b>\n"
                f"Всё, что ты сейчас напишешь в ЛС бота, полетит ему от лица бота.\n"
                f"Для выхода напиши <code>стоп</code>."
            )
            return

        if low.startswith('рассылка '):
            b_text = raw_text[9:].strip()
            if b_text:
                cnt = 0
                for uid_k in list(RPG_PLAYERS.keys()):
                    try:
                        bot.send_message(int(uid_k), f"📢 <b>Объявление:</b>\n\n{b_text}")
                        cnt += 1
                        time.sleep(0.05)
                    except Exception:
                        pass
                bot.send_message(cid, f"✅ Рассылка доставлена <b>{cnt}</b> пользователям бота!", business_connection_id=biz_id)
            return

        if low.startswith('профиль ') and len(low.split()) >= 2:
            t_p = find_player_by_username_or_id(low.split()[1])
            if not t_p:
                bot.send_message(cid, "⚠️ Игрок не найден.", business_connection_id=biz_id)
                return
            c_text, c_kb = build_user_moderation_card(str(t_p['uid']))
            bot.send_message(cid, c_text, business_connection_id=biz_id, reply_markup=c_kb)
            return

        if low.startswith('выдать ') or low.startswith('забрать ') or low.startswith('опыт ') or low.startswith('патогены '):
            parts = low.split()
            if len(parts) >= 3:
                t_p = find_player_by_username_or_id(parts[1])
                try:
                    val = int(parts[2])
                except Exception:
                    val = 0
                if t_p and val > 0:
                    if low.startswith('выдать '):
                        t_p['bio_res'] = t_p.get('bio_res', 0) + val
                        res_msg = f"✅ Выдано <b>+{val} 🧬</b> игроку <b>{html.escape(t_p['name'])}</b> (Баланс: {t_p['bio_res']} 🧬)."
                    elif low.startswith('забрать '):
                        t_p['bio_res'] = max(0, t_p.get('bio_res', 0) - val)
                        res_msg = f"✅ Списано <b>-{val} 🧬</b> у игрока <b>{html.escape(t_p['name'])}</b> (Баланс: {t_p['bio_res']} 🧬)."
                    elif low.startswith('опыт '):
                        t_p['xp'] = max(0, t_p.get('xp', 0) + val)
                        res_msg = f"✅ Начислено <b>+{val} XP</b> игроку <b>{html.escape(t_p['name'])}</b> (Всего: {t_p['xp']} XP)."
                    else:
                        t_p['patogens'] = val
                        res_msg = f"✅ Патогены игрока <b>{html.escape(t_p['name'])}</b> установлены на: <b>{val} шт.</b>"
                    save_rpg_firebase()
                    bot.send_message(cid, res_msg, business_connection_id=biz_id)
                    return

        if low.startswith('вылечить '):
            t_p = find_player_by_username_or_id(low.split()[1])
            if t_p:
                t_p['fever_until'] = 0
                t_p['infected_by'] = ''
                save_rpg_firebase()
                bot.send_message(cid, f"💉 Горячка у <b>{html.escape(t_p['name'])}</b> полностью снята!", business_connection_id=biz_id)
            return

        if low.startswith('обнулить '):
            t_p = find_player_by_username_or_id(low.split()[1])
            if t_p:
                RPG_PLAYERS.pop(str(t_p['uid']), None)
                save_rpg_firebase()
                bot.send_message(cid, f"🧹 Профиль игрока <b>{html.escape(t_p['name'])}</b> полностью обнулен!", business_connection_id=biz_id)
            return

        if low.startswith('бан бот ') or low.startswith('разбан бот '):
            target = low.split()[-1].replace('@', '').lower()
            if low.startswith('бан бот '):
                CONFIG.setdefault('bot_banned_users', {})[target] = True
                save_config()
                bot.send_message(cid, f"🚫 Пользователь <b>{html.escape(target)}</b> забанен в боте!", business_connection_id=biz_id)
            else:
                CONFIG.get('bot_banned_users', {}).pop(target, None)
                save_config()
                bot.send_message(cid, f"🟢 Пользователь <b>{html.escape(target)}</b> разбанен в боте!", business_connection_id=biz_id)
            return

        if low.startswith('доступ '):
            target = low.replace('доступ ', '').strip().replace('@', '').lower()
            if target:
                CONFIG.setdefault('allowed_users', {})[target] = True
                BIZ_CONN_OWNER_CACHE.clear()
                save_config()
                bot.send_message(cid, f"✅ Пользователю <b>@{html.escape(target)}</b> выдан полный доступ к ИИ в боте и к подключению ТГ Бизнес!", business_connection_id=biz_id)
            return

        if low.startswith('забрать доступ '):
            target = low.replace('забрать доступ ', '').strip().replace('@', '').lower()
            CONFIG.get('allowed_users', {}).pop(target, None)
            BIZ_CONN_OWNER_CACHE.clear()
            save_config()
            bot.send_message(cid, f"🚫 Доступ у <b>@{html.escape(target)}</b> отозван.", business_connection_id=biz_id)
            return

        if low == 'доступы':
            al = list(CONFIG.get('allowed_users', {}).keys())
            al_str = ", ".join([f"@{u}" for u in al]) if al else "никому кроме владельца"
            bot.send_message(cid, f"🔑 <b>Доступ к ИИ в ЛС и ТГ Бизнес выдан:</b>\n{al_str}", business_connection_id=biz_id)
            return

        # Быстрые команды изменения любых параметров
        param_cmds = {
            'кд лут ': ('looting_cd_minutes', float, "🗑 КД Хабаровского Лутинга"),
            'кд патоген ': ('base_patogen_minutes', float, "🧪 Время синтеза патогена (мин)"),
            'цена вакцины ': ('vaccine_cost', int, "💉 Цена вакцины (🧬)"),
            'приз бункер ': ('reward_bunker', int, "☢️ Приз за победу в Бункере/Мафии (🧬)"),
            'приз виселица ': ('reward_hangman', int, "🎡 Приз за победу в Виселице (🧬)"),
            'приз кнб ': ('reward_rps', int, "✂️ Приз за победу в КНБ (🧬)"),
            'шанс заражения ': ('base_infect_chance', int, "🦠 Базовый шанс заражения (%)"),
            'время игры ': ('game_round_seconds', int, "⏱ Время раунда в Бункере/Мафии (сек)"),
            'раунды игры ': ('game_max_rounds', int, "🎭 Количество раундов в Бункере/Мафии")
        }
        for pref, (cfg_key, cast_fn, title_lbl) in param_cmds.items():
            if low.startswith(pref):
                try:
                    val = cast_fn(low.split()[-1])
                    CONFIG[cfg_key] = val
                    save_config()
                    bot.send_message(cid, f"⚙️ <b>{title_lbl}</b> установлено на: <code>{val}</code>", business_connection_id=biz_id)
                except Exception:
                    bot.send_message(cid, f"⚠️ Укажи число, например: <code>{pref}10</code>", business_connection_id=biz_id)
                return

        if low in ('игра-модели', 'модели игры', 'модели ведущего'):
            models = main.fetch_all_models()
            gem_first = ['auto_gemini'] + sorted([m for m in models if not m.startswith('groq/')]) + sorted([m for m in models if m.startswith('groq/')])
            cur_host = CONFIG.get('game_host_model', 'auto_gemini')
            kb = InlineKeyboardMarkup(row_width=1)
            for m in gem_first:
                mark = "✅ " if m == cur_host else ""
                lbl = "🔥 Авто-Gemini (Текущая модель ГПТ в отдельном окне)" if m == 'auto_gemini' else m
                kb.add(InlineKeyboardButton(f"{mark}{lbl}", callback_data=f"sethostmodel_{m}"))
            bot.send_message(cid, "🎙 <b>Выбери модель для ИИ-Ведущего (Бункер / Мафия):</b>", business_connection_id=biz_id, reply_markup=kb)
            return

    # 2. Обработка «гпт ...» и «нарисуй ...» для пользователей в ЛС бота
    if not biz_id and not is_owner and (low == 'гпт' or low.startswith('гпт ') or low.startswith('нарисуй ')):
        if not has_ai_access:
            send_logged_pm(cid, "🔒 <b>Доступ к нейросети в ЛС закрыт.</b>\nЗато тебе доступны все игры и Био-войны! Напиши <code>/start</code>, <code>лаба</code>, <code>/search</code> или <code>виселица</code>.", target_user=user)
            return
        if low.startswith('нарисуй '):
            wait_m = bot.send_message(cid, "🎨 Рисую через FLUX...")
            img, err = free_generate_image(raw_text[8:].strip())
            if img:
                try:
                    bot.delete_message(cid, wait_m.message_id)
                except Exception:
                    pass
                bot.send_photo(cid, img)
                log_bot_pm_interaction(user, "[Бот сгенерировал картинку FLUX]", from_bot=True)
            else:
                safe_edit_message(f"⚠️ {err}", cid, wait_m.message_id)
            return
        q_prompt = raw_text[4:].strip() if len(raw_text) > 3 else "Привет!"
        wait_m = bot.send_message(cid, "🕐 Думаю...")
        reply, err, m_used, c_len = main.ask_ai(cid, f"[{p['name']}]: {q_prompt}", is_autoreply=False)
        if reply:
            footer = f"context: {c_len}/{CONFIG.get('context_limit', 100)}\nmodel: {m_used}"
            main.send_long_response(reply, footer, cid, wait_m.message_id, is_autoreply=False)
            log_bot_pm_interaction(user, reply, from_bot=True)
        else:
            safe_edit_message(f"⚠️ Ошибка: {err}", cid, wait_m.message_id)
        return

    # 3. Бункер и Мафия
    if low in ('стоп игра', 'игра стоп'):
        g = STORY_GAMES.pop(cid, None)
        if g:
            if g.get('timer'):
                g['timer'].cancel()
            send_logged_pm(cid, f"🏳️ Сюжетная игра «<b>{g['title']}</b>» досрочно остановлена.", business_connection_id=biz_id, target_user=user)
        else:
            send_logged_pm(cid, "ℹ️ Сейчас в этом чате нет активного Бункера или Мафии.", business_connection_id=biz_id, target_user=user)
        return

    if low in ('бункер', 'игра бункер'):
        if not has_ai_access:
            send_logged_pm(cid, "🔒 Запуск ИИ-Бункера в ЛС доступен по разрешению владельца.", target_user=user)
            return
        threading.Thread(target=start_story_game, args=(cid, biz_id, 'bunker', user), daemon=True).start()
        return

    if low in ('мафия', 'игра мафия'):
        if not has_ai_access:
            send_logged_pm(cid, "🔒 Запуск ИИ-Мафии в ЛС доступен по разрешению владельца.", target_user=user)
            return
        threading.Thread(target=start_story_game, args=(cid, biz_id, 'mafia', user), daemon=True).start()
        return

    # 4. Хабаровский Лутинг
    if low in ('/search', 'лут', 'мусорка', 'лутинг'):
        res_text, _ = execute_khabarovsk_looting(user)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🗑 Порыться еще раз", callback_data="loot_again"))
        send_logged_pm(cid, res_text, business_connection_id=biz_id, reply_markup=kb, target_user=user)
        return

    # 5. Список команд
    if low in ('команды', 'помощь', 'хелп', '/help'):
        is_owner_private = (not biz_id and is_owner)
        help_txt = get_updated_full_commands_text() if is_owner_private else get_chat_commands_text()
        send_logged_pm(cid, help_txt, business_connection_id=biz_id, target_user=user)
        return

    # 6. Игровой профиль и Топ
    if low in ('игры', 'рпг', 'профиль'):
        lvl, rank_title = get_level_and_rank(p.get('xp', 0))
        buff_list = [f"• {RARE_BUFFS_INFO[b]['name']} — <i>{RARE_BUFFS_INFO[b]['desc']}</i>" for b in p.get('buffs', []) if b in RARE_BUFFS_INFO]
        buff_block = "\n".join(buff_list) if buff_list else "<i>Пока нет редких баффов (выпадают за победы с шансом 14%)</i>"
        u_name = html.escape(p['name'])
        strain_safe = html.escape(p.get('strain_name', 'Штамм-Х'))
        msg_text = (
            f"🎮 <b>Игровой профиль: {u_name}</b>\n"
            f"🎖 <b>Звание:</b> {rank_title} (Ур. {lvl} | ✨ {p.get('xp', 0)} XP)\n"
            f"☣️ <b>Личный штамм:</b> «{strain_safe}»\n"
            f"🧬 <b>Вирусы:</b> <code>{p.get('bio_res', 0)}</code> | 🦠 <b>Заражений:</b> {p.get('infections_done', 0)}\n"
            f"🗑 <b>Лутов:</b> {p.get('loot_count', 0)} | ☢️ <b>Бункер:</b> {p.get('wins_bunker', 0)} | 🕵️ <b>Мафия:</b> {p.get('wins_mafia', 0)}\n"
            f"🎡 <b>Виселица:</b> {p.get('wins_hangman', 0)} | ✂️ <b>КНБ:</b> {p.get('wins_rps', 0)}\n\n"
            f"🔮 <b>Твои редкие артефакты/баффы:</b>\n{buff_block}\n\n"
            f"🕹 <b>Команды:</b> <code>лаба</code> | <code>/search</code> | <code>бункер</code> | <code>мафия</code> | <code>виселица</code> | <code>кнб</code> | <code>био топ</code>"
        )
        send_logged_pm(cid, msg_text, business_connection_id=biz_id, target_user=user)
        return

    if low in ('топ', 'био топ'):
        sorted_p = sorted(RPG_PLAYERS.values(), key=lambda x: (x.get('xp', 0) + x.get('bio_xp', 0)), reverse=True)[:10]
        lines = ["🏆 <b>Глобальный Топ Био-войн и Игр:</b>\n"]
        for idx, pl in enumerate(sorted_p, 1):
            lvl, r_title = get_level_and_rank(pl.get('xp', 0))
            p_name = html.escape(pl.get('name', 'Игрок'))
            lines.append(
                f"{idx}. <b>{p_name}</b> ({r_title}, Ур. {lvl}) — 🦠 {pl.get('infections_done', 0)} | 🧬 <code>{pl.get('bio_res', 0)}</code>"
            )
        send_logged_pm(cid, "\n".join(lines), business_connection_id=biz_id, target_user=user)
        return

    # 7. Лаборатория, прокачка, вакцина и заражение (одиночное + ВСЕМИ патогенами)
    if low in ('лаба', 'моя лаба', 'био', 'биовойны'):
        text, kb = build_lab_view(p)
        send_logged_pm(cid, text, business_connection_id=biz_id, reply_markup=kb, target_user=user)
        return

    if low.startswith('штамм '):
        new_strain = raw_text[6:].strip()[:28]
        if new_strain:
            p['strain_name'] = new_strain
            save_rpg_firebase()
            send_logged_pm(cid, f"☣️ Твоему вирусу присвоено новое кодовое имя: «<b>{html.escape(new_strain)}</b>»!", business_connection_id=biz_id, target_user=user)
        return

    cmd_map = {
        '+патоген': 'patogen', '+разработка': 'dev', '+заразность': 'infect',
        '+иммунитет': 'immune', '+летальность': 'lethal', '+безопасность': 'security'
    }
    if low in cmd_map:
        upg_key = cmd_map[low]
        cost = get_upgrade_cost(p, upg_key)
        field, label, _ = UPGRADE_TYPES[upg_key]
        if p.get('bio_res', 0) < cost:
            send_logged_pm(cid, f"⚠️ Не хватает вирусов! Нужно <b>{cost} 🧬</b> (у тебя {p.get('bio_res', 0)} 🧬).", business_connection_id=biz_id, target_user=user)
            return
        p['bio_res'] -= cost
        p[field] = p.get(field, 1) + 1
        p['xp'] = p.get('xp', 0) + 20
        save_rpg_firebase()
        send_logged_pm(cid, f"✅ Улучшено: <b>{label}</b> ➔ Уровень {p[field]}!", business_connection_id=biz_id, target_user=user)
        return

    if low in ('вакцина', 'лечиться'):
        vac_cost = int(CONFIG.get('vaccine_cost', 80))
        if p.get('fever_until', 0) <= time.time():
            send_logged_pm(cid, "🟢 Ты полностью здоров, вакцина не требуется!", business_connection_id=biz_id, target_user=user)
            return
        if p.get('bio_res', 0) < vac_cost:
            send_logged_pm(cid, f"⚠️ Вакцина стоит <b>{vac_cost} 🧬</b>. Налутай вирусы командой <code>/search</code>!", business_connection_id=biz_id, target_user=user)
            return
        p['bio_res'] -= vac_cost
        p['fever_until'] = 0
        p['infected_by'] = ''
        save_rpg_firebase()
        send_logged_pm(cid, "💉 <b>Вакцина введена!</b> Горячка снята, ты снова в строю!", business_connection_id=biz_id, target_user=user)
        return

    if low == 'заразить' or low.startswith('заразить '):
        use_all = any(w in low.split() for w in ('все', 'всё', 'фулл', 'мах', 'макс', 'all'))
        target_p = resolve_target_player(message, low)
        if not target_p:
            send_logged_pm(
                cid,
                "⚠️ Укажи кого заразить:\n"
                "• Одиночная атака: <code>заразить @юзер</code> (или <code>заразить</code> в чате)\n"
                "• Залп ВСЕМИ патогенами без спама: <code>заразить все @юзер</code> (или <code>заразить все</code> в чате)",
                business_connection_id=biz_id,
                target_user=user
            )
            return
        res_text = perform_infection_attempt(user, target_p, use_all=use_all, source_chat_id=cid)
        send_logged_pm(cid, res_text, business_connection_id=biz_id, target_user=user)
        return

    # 8. Виселица и КНБ
    if low in ('виселица', 'поле чудес', 'виселица ии'):
        is_ai = ('ии' in low)
        if is_ai and not has_ai_access:
            send_logged_pm(cid, "🔒 Режим «Виселица ИИ» в ЛС требует доступа к нейросети. Запускаю обычную Виселицу!", target_user=user)
            is_ai = False
        if is_ai:
            wait_msg = bot.send_message(cid, "🧠 <i>ГПТ вспоминает историю вашего чата и загадывает слово...</i>", business_connection_id=biz_id)
            start_hangman_game(cid, biz_id, user, is_ai=True, msg_id_to_edit=wait_msg.message_id)
        else:
            start_hangman_game(cid, biz_id, user, is_ai=False)
        return

    if low in ('кнб', 'цуефа'):
        send_rps_menu(cid, biz_id=biz_id, user=user)
        return

    if cid in STORY_GAMES and not STORY_GAMES[cid].get('finished'):
        record_story_player_message(message)
        return

    if cid in HANGMAN_GAMES and not HANGMAN_GAMES[cid].get('finished'):
        process_hangman_letter_or_word(cid, biz_id, user, raw_text, from_chat_text=True)
        return

    if not biz_id and not is_owner:
        send_public_user_hub(cid, user)

@bot.callback_query_handler(func=lambda call: call.data and (
    call.data.startswith('bioupg_') or call.data.startswith('biocure_') or
    call.data.startswith('biorefresh_') or call.data.startswith('hm_') or
    call.data.startswith('rps_') or call.data.startswith('sg_') or
    call.data.startswith('sethostmodel_') or call.data.startswith('mod_') or
    call.data.startswith('cfg_') or
    call.data in ('panel_img_models', 'loot_again', 'pub_top', 'pub_help')
))
def handle_games_callbacks(call):
    global ADMIN_LIVE_CHAT_UID
    try:
        register_creator_id(call.from_user)
        data = call.data
        user = call.from_user
        is_owner = is_owner_user(user)
        p = get_player(user)
        cid = int(call.message.chat.id)
        msg_id = call.message.message_id
        biz_id = getattr(call.message, 'business_connection_id', None)

        if biz_id and not is_business_connection_authorized(biz_id):
            bot.answer_callback_query(call.id, "⛔ Нет доступа для этого бизнес-аккаунта!", show_alert=True)
            return

        # --- КНОПКИ МОДЕРАЦИИ, ПРОСМОТРА ЛС БОТА И НАСТРОЕК ПАРАМЕТРОВ ---
        if data.startswith('mod_') or data.startswith('cfg_'):
            if not is_owner:
                bot.answer_callback_query(call.id, "⛔ Только для владельца бота!", show_alert=True)
                return

            if data == 'mod_open_hub':
                text, kb = build_mod_hub_view()
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if data == 'mod_toggle_pmspy':
                CONFIG['pm_spy_enabled'] = not CONFIG.get('pm_spy_enabled', True)
                save_config()
                text, kb = build_mod_hub_view()
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id, f"Слежка ЛС: {'ВКЛ' if CONFIG['pm_spy_enabled'] else 'ВЫКЛ'}")
                return

            if data == 'mod_stop_live':
                ADMIN_LIVE_CHAT_UID = None
                text, kb = build_mod_hub_view()
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id, "🛑 Режим ответа от имени бота выключен!")
                return

            if data.startswith('mod_live_'):
                target_uid = int(data.replace('mod_live_', ''))
                ADMIN_LIVE_CHAT_UID = target_uid
                t_p = RPG_PLAYERS.get(str(target_uid), {})
                t_name = html.escape(t_p.get('name', str(target_uid)))
                bot.send_message(
                    cid,
                    f"🗣 <b>Включен прямой эфир от имени бота с {t_name} (<code>{target_uid}</code>)!</b>\n"
                    f"Пиши любые сообщения сюда — бот будет отправлять их этому человеку от своего лица.\n"
                    f"Чтобы выйти, напиши <code>стоп</code>."
                )
                bot.answer_callback_query(call.id, "🗣 Режим ответа от бота ВКЛЮЧЕН!")
                return

            if data.startswith('mod_hist_'):
                uid_str = data.replace('mod_hist_', '')
                text, kb = build_user_pm_history_view(uid_str)
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if data.startswith('mod_user_'):
                uid_str = data.replace('mod_user_', '')
                text, kb = build_user_moderation_card(uid_str)
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if data.startswith('mod_give500_'):
                uid_str = data.replace('mod_give500_', '')
                t_p = RPG_PLAYERS.get(uid_str)
                if t_p:
                    t_p['bio_res'] = t_p.get('bio_res', 0) + 500
                    save_rpg_firebase()
                    bot.answer_callback_query(call.id, f"✅ Выдано +500 вирусов игроку {t_p['name']}!", show_alert=True)
                return

            if data.startswith('mod_ban_'):
                uid_str = data.replace('mod_ban_', '')
                b_dict = CONFIG.setdefault('bot_banned_users', {})
                if uid_str in b_dict:
                    b_dict.pop(uid_str, None)
                    st = "🟢 Разбанен"
                else:
                    b_dict[uid_str] = True
                    st = "🔴 Забанен"
                save_config()
                bot.answer_callback_query(call.id, f"Статус бана id:{uid_str}: {st}", show_alert=True)
                return

            if data.startswith('mod_act_'):
                parts = data.split('_')
                uid_str = parts[2]
                act = parts[3]
                t_p = RPG_PLAYERS.get(uid_str)
                if not t_p:
                    bot.answer_callback_query(call.id, "Игрок не найден")
                    return
                uname = (t_p.get('username') or '').lower()

                if act == 'add1000':
                    t_p['bio_res'] = t_p.get('bio_res', 0) + 1000
                elif act == 'sub1000':
                    t_p['bio_res'] = max(0, t_p.get('bio_res', 0) - 1000)
                elif act == 'addxp':
                    t_p['xp'] = t_p.get('xp', 0) + 500
                elif act == 'addpat':
                    t_p['patogens'] = t_p.get('patogens', 0) + 5
                elif act == 'cure':
                    t_p['fever_until'] = 0
                    t_p['infected_by'] = ''
                elif act == 'infect':
                    t_p['fever_until'] = time.time() + 1800
                    t_p['infected_by'] = "Кара Создателя"
                elif act == 'togglevip':
                    al = CONFIG.setdefault('allowed_users', {})
                    key_u = uname if uname else uid_str
                    if key_u in al or uid_str in al:
                        al.pop(key_u, None)
                        al.pop(uid_str, None)
                    else:
                        al[key_u] = True
                    BIZ_CONN_OWNER_CACHE.clear()
                    save_config()
                elif act == 'toggleban':
                    bd = CONFIG.setdefault('bot_banned_users', {})
                    key_u = uname if uname else uid_str
                    if key_u in bd or uid_str in bd:
                        bd.pop(key_u, None)
                        bd.pop(uid_str, None)
                    else:
                        bd[key_u] = True
                        bd[uid_str] = True
                    save_config()
                elif act == 'wipe':
                    RPG_PLAYERS.pop(uid_str, None)
                    save_rpg_firebase()
                    text, kb = build_mod_hub_view()
                    safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                    bot.answer_callback_query(call.id, "🧹 Профиль игрока обнулен!")
                    return

                save_rpg_firebase()
                text, kb = build_user_moderation_card(uid_str)
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id, "✅ Выполнено!")
                return

            if data == 'mod_open_settings':
                text, kb = build_all_settings_view()
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if data == 'cfg_open_hostmodels':
                models = main.fetch_all_models()
                gem_first = ['auto_gemini'] + sorted([m for m in models if not m.startswith('groq/')]) + sorted([m for m in models if m.startswith('groq/')])
                cur_host = CONFIG.get('game_host_model', 'auto_gemini')
                kb = InlineKeyboardMarkup(row_width=1)
                for m in gem_first:
                    mark = "✅ " if m == cur_host else ""
                    lbl = "🔥 Авто-Gemini (Текущая модель ГПТ в отдельном окне)" if m == 'auto_gemini' else m
                    kb.add(InlineKeyboardButton(f"{mark}{lbl}", callback_data=f"sethostmodel_{m}"))
                kb.add(InlineKeyboardButton("🔙 Назад к параметрам", callback_data="mod_open_settings"))
                safe_edit_message("🎙 <b>Выбери модель для ИИ-Ведущего (Бункер / Мафия):</b>", cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if data.startswith('cfg_cycle_'):
                what = data.replace('cfg_cycle_', '')
                cycles = {
                    'lootcd': ('looting_cd_minutes', [0, 1, 3, 5, 10, 15]),
                    'patcd': ('base_patogen_minutes', [3, 5, 10, 15, 20, 30]),
                    'rewbunker': ('reward_bunker', [500, 1000, 1500, 2500, 5000]),
                    'rewhm': ('reward_hangman', [100, 200, 300, 500]),
                    'vac': ('vaccine_cost', [40, 80, 150, 300]),
                    'infch': ('base_infect_chance', [40, 55, 65, 75]),
                    'rsec': ('game_round_seconds', [30, 60, 90, 120, 180]),
                    'rmax': ('game_max_rounds', [2, 3, 4, 5]),
                    'afk': ('afk_minutes', [1, 3, 5, 10, 15, 30]),
                    'chance': ('human_reply_chance', [25, 50, 75, 100])
                }
                if what in cycles:
                    cfg_k, opts = cycles[what]
                    cur_v = CONFIG.get(cfg_k, opts[0])
                    nxt_v = opts[(opts.index(cur_v) + 1) % len(opts)] if cur_v in opts else opts[0]
                    CONFIG[cfg_k] = nxt_v
                    save_config()
                text, kb = build_all_settings_view()
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id, "Параметр обновлен!")
                return

        if data == 'pub_top':
            sorted_p = sorted(RPG_PLAYERS.values(), key=lambda x: (x.get('xp', 0) + x.get('bio_xp', 0)), reverse=True)[:10]
            lines = ["🏆 <b>Глобальный Топ Био-войн и Игр:</b>\n"]
            for idx, pl in enumerate(sorted_p, 1):
                lvl, r_title = get_level_and_rank(pl.get('xp', 0))
                p_name = html.escape(pl.get('name', 'Игрок'))
                lines.append(f"{idx}. <b>{p_name}</b> ({r_title}, Ур. {lvl}) — 🦠 {pl.get('infections_done', 0)} | 🧬 <code>{pl.get('bio_res', 0)}</code>")
            send_logged_pm(cid, "\n".join(lines), business_connection_id=biz_id, target_user=user)
            bot.answer_callback_query(call.id)
            return

        if data == 'pub_help':
            send_logged_pm(cid, get_chat_commands_text(), business_connection_id=biz_id, target_user=user)
            bot.answer_callback_query(call.id)
            return

        if data.startswith('sethostmodel_'):
            if not is_owner:
                return
            CONFIG['game_host_model'] = data.replace('sethostmodel_', '')
            save_config()
            lbl = f"Авто-Gemini ({main.ACTIVE_MODEL})" if CONFIG['game_host_model'] == 'auto_gemini' else CONFIG['game_host_model']
            safe_edit_message(
                f"✅ <b>Модель ИИ-Ведущего (для Бункера и Мафии) установлена:</b> <code>{lbl}</code>\n"
                f"<i>(Ведет игру в отдельном изолированном контекстном окне!)</i>",
                cid, msg_id, business_id=biz_id
            )
            bot.answer_callback_query(call.id, f"Ведущий: {lbl}")
            return

        if data == 'loot_again':
            res_text, ok = execute_khabarovsk_looting(user)
            if not ok:
                plain_alert = re.sub(r'<[^>]+>', '', res_text)
                bot.answer_callback_query(call.id, plain_alert[:195], show_alert=True)
                return
            kb = InlineKeyboardMarkup()
            kb.add(InlineKeyboardButton("🗑 Порыться еще раз", callback_data="loot_again"))
            send_logged_pm(cid, res_text, business_connection_id=biz_id, reply_markup=kb, target_user=user)
            bot.answer_callback_query(call.id, "🗑 Мусорка проверена!")
            return

        if data.startswith('sg_'):
            parts = data.split('_')
            action = parts[1]
            game_cid = int(parts[2])
            game = STORY_GAMES.get(game_cid)
            if not game or game.get('finished'):
                bot.answer_callback_query(call.id, "⚠️ Партия уже завершена!", show_alert=True)
                return

            role = 'p1' if (is_owner or not game.get('biz_id')) else 'p2'
            if action == 'role':
                r_text = game.get(role + '_role', 'Нет данных')
                bot.answer_callback_query(call.id, f"🪪 ТВОЯ СЕКРЕТНАЯ РОЛЬ:\n{r_text}"[:195], show_alert=True)
                return

            if action == 'loot':
                l_text = game.get(role + '_loot', 'Нет данных')
                bot.answer_callback_query(call.id, f"🎒 ТВОЙ ЛУТ И УЛИКИ:\n{l_text}"[:195], show_alert=True)
                return

            if action == 'force':
                ready, missing = check_story_round_ready(game)
                if not ready:
                    miss_str = ", ".join(missing)
                    bot.answer_callback_query(call.id, f"⏳ Сначала каждый должен написать хотя бы по 1 сообщению! Ждем: {miss_str}"[:195], show_alert=True)
                    return
                bot.answer_callback_query(call.id, "🎙 Передаю ответы Ведущему!")
                threading.Thread(target=advance_story_game_round, args=(game_cid,), daemon=True).start()
                return

            if action == 'stop':
                STORY_GAMES.pop(game_cid, None)
                if game.get('timer'):
                    game['timer'].cancel()
                safe_edit_message(f"🏳️ Игра «<b>{game['title']}</b>» остановлена игроком {html.escape(p['name'])}.", cid, msg_id, business_id=biz_id)
                bot.answer_callback_query(call.id, "Игра остановлена")
                return

        if data == 'panel_img_models':
            if not is_owner:
                return
            cur_m = CONFIG.get('image_model', 'flux')
            kb = InlineKeyboardMarkup(row_width=1)
            for m in FREE_IMAGE_MODELS:
                mark = "✅ " if m == cur_m else ""
                kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"setimg_{m}"))
            kb.add(InlineKeyboardButton("🔙 Назад в панель", callback_data="panel_refresh"))
            safe_edit_message("🎨 <b>Выбери бесплатную FLUX-модель для рисования:</b>", cid, msg_id, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

        if data.startswith('biorefresh_'):
            text, kb = build_lab_view(p)
            safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
            bot.answer_callback_query(call.id, "Лаборатория обновлена")
            return

        if data.startswith('biocure_'):
            owner_uid = data.split('_')[1]
            if str(user.id) != owner_uid:
                bot.answer_callback_query(call.id, "⛔ Это чужая лаборатория!", show_alert=True)
                return
            vac_cost = int(CONFIG.get('vaccine_cost', 80))
            if p.get('bio_res', 0) < vac_cost:
                bot.answer_callback_query(call.id, f"⚠️ Нужно {vac_cost} вирусов для вакцины!", show_alert=True)
                return
            p['bio_res'] -= vac_cost
            p['fever_until'] = 0
            p['infected_by'] = ''
            save_rpg_firebase()
            text, kb = build_lab_view(p)
            safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
            bot.answer_callback_query(call.id, "💉 Выздоровление! Горячка снята.")
            return

        if data.startswith('bioupg_'):
            parts = data.split('_')
            owner_uid = parts[1]
            upg_key = parts[2]
            if str(user.id) != owner_uid:
                bot.answer_callback_query(call.id, "⛔ Это чужая лаборатория! Открой свою командой «лаба».", show_alert=True)
                return
            cost = get_upgrade_cost(p, upg_key)
            field, label, _ = UPGRADE_TYPES[upg_key]
            if p.get('bio_res', 0) < cost:
                bot.answer_callback_query(call.id, f"⚠️ Не хватает вирусов! Нужно {cost} 🧬.", show_alert=True)
                return
            p['bio_res'] -= cost
            p[field] = p.get(field, 1) + 1
            p['xp'] = p.get('xp', 0) + 20
            save_rpg_firebase()
            text, kb = build_lab_view(p)
            safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
            bot.answer_callback_query(call.id, f"✅ {label} -> Ур. {p[field]}!")
            return

        if data.startswith('hm_'):
            parts = data.split('_')
            action = parts[1]
            game_cid = int(parts[2])
            game = HANGMAN_GAMES.get(game_cid)

            if action == 'new':
                mode_type = parts[3]
                is_ai = (mode_type == 'ai')
                if is_ai and not (is_user_authorized_for_ai(user) or bool(biz_id)):
                    bot.answer_callback_query(call.id, "🔒 Виселица ИИ в ЛС доступна по разрешению владельца!", show_alert=True)
                    return
                if is_ai:
                    safe_edit_message("🧠 <i>ГПТ вспоминает ваш чат и придумывает новое слово...</i>", cid, msg_id, business_id=biz_id)
                start_hangman_game(game_cid, biz_id, user, is_ai=is_ai, msg_id_to_edit=msg_id)
                bot.answer_callback_query(call.id)
                return

            if not game or game.get('finished'):
                bot.answer_callback_query(call.id, "Игра уже завершена!")
                return

            u_name = html.escape(p['name'])
            if action == 'giveup':
                game['finished'] = True
                game['guessed'].update(list(game['word']))
                game['status'] = f"🏳️ <b>{u_name}</b> сдался! Загаданное слово: <b>{game['word']}</b>"
                text, kb = build_hangman_view(game)
                safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id, "Игра окончена")
                return

            if action == 'let':
                letter = parts[3]
                process_hangman_letter_or_word(game_cid, biz_id, user, letter, from_chat_text=False)
                bot.answer_callback_query(call.id)
                return

        if data.startswith('rps_'):
            parts = data.split('_')
            mode = parts[1]
            game_cid = int(parts[2])
            rps_rew = int(CONFIG.get('reward_rps', 50))

            if mode == 'botmenu':
                kb = InlineKeyboardMarkup(row_width=3)
                kb.row(
                    InlineKeyboardButton("🪨 Камень", callback_data=f"rps_bot_{game_cid}_r"),
                    InlineKeyboardButton("✂️ Ножницы", callback_data=f"rps_bot_{game_cid}_s"),
                    InlineKeyboardButton("📄 Бумага", callback_data=f"rps_bot_{game_cid}_p")
                )
                safe_edit_message("🤖 <b>Бой против Бота! Выбирай предмет:</b>", cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if mode == 'bot':
                u_move = parts[3]
                b_move = random.choice(['r', 's', 'p'])
                u_name = html.escape(p['name'])
                if u_move == b_move:
                    bonus = ""
                    if 'trickster_glove' in p.get('buffs', []) and random.randint(1, 100) <= 25:
                        p['bio_res'] += 40
                        bonus = "\n🧤 <i>«Ловкость Шулера» принесла +40 🧬 даже в ничью!</i>"
                    res_line = f"🤝 <b>Ничья!</b> Оба выбрали {RPS_NAMES[u_move]}.{bonus}"
                elif (u_move, b_move) in RPS_WINS:
                    p['bio_res'] += rps_rew
                    p['xp'] += 20
                    p['wins_rps'] = p.get('wins_rps', 0) + 1
                    buff_str = try_grant_rare_buff(p) or ""
                    res_line = f"🏆 <b>{u_name} победил(а) бота!</b> (+{rps_rew} 🧬, +20 XP){buff_str}"
                else:
                    res_line = "🤖 <b>Бот победил в этом раунде!</b>"

                save_rpg_firebase()
                kb = InlineKeyboardMarkup(row_width=3)
                kb.row(
                    InlineKeyboardButton("🪨 Камень", callback_data=f"rps_bot_{game_cid}_r"),
                    InlineKeyboardButton("✂️ Ножницы", callback_data=f"rps_bot_{game_cid}_s"),
                    InlineKeyboardButton("📄 Бумага", callback_data=f"rps_bot_{game_cid}_p")
                )
                out = (
                    f"✂️ <b>Камень-Ножницы-Бумага (с Ботом)</b>\n\n"
                    f"👤 <b>{u_name}:</b> {RPS_NAMES[u_move]}\n"
                    f"🤖 <b>Бот:</b> {RPS_NAMES[b_move]}\n\n"
                    f"{res_line}"
                )
                safe_edit_message(out, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id)
                return

            if mode == 'pvp':
                move = parts[3]
                room = RPS_GAMES.setdefault(game_cid, {'biz_id': biz_id, 'moves': {}, 'names': {}, 'users': {}})
                uid = str(user.id)
                room['moves'][uid] = move
                room['names'][uid] = p['name']
                room['users'][uid] = user

                if len(room['moves']) < 2:
                    u_name = html.escape(p['name'])
                    kb = InlineKeyboardMarkup(row_width=3)
                    kb.row(
                        InlineKeyboardButton("🪨 Камень", callback_data=f"rps_pvp_{game_cid}_r"),
                        InlineKeyboardButton("✂️ Ножницы", callback_data=f"rps_pvp_{game_cid}_s"),
                        InlineKeyboardButton("📄 Бумага", callback_data=f"rps_pvp_{game_cid}_p")
                    )
                    safe_edit_message(
                        f"🪨✂️📄 <b>Дуэль 1 на 1!</b>\n\n"
                        f"✅ <b>{u_name}</b> уже сделал(а) свой секретный выбор!\n"
                        f"⏳ Ожидаем нажатия второго игрока...",
                        cid, msg_id, business_id=biz_id, reply_markup=kb
                    )
                    bot.answer_callback_query(call.id, f"Ты выбрал(а): {RPS_NAMES[move]}. Ждем соперника!")
                    return

                uids = list(room['moves'].keys())
                u1, u2 = uids[0], uids[1]
                m1, m2 = room['moves'][u1], room['moves'][u2]
                n1, n2 = html.escape(room['names'][u1]), html.escape(room['names'][u2])
                p1_obj = get_player(room['users'][u1])
                p2_obj = get_player(room['users'][u2])

                if m1 == m2:
                    verdict = "🤝 <b>Боевая ничья!</b>"
                elif (m1, m2) in RPS_WINS:
                    p1_obj['bio_res'] += rps_rew + 15
                    p1_obj['xp'] += 25
                    p1_obj['wins_rps'] = p1_obj.get('wins_rps', 0) + 1
                    b_str = try_grant_rare_buff(p1_obj) or ""
                    verdict = f"🏆 <b>Победил(а) {n1}!</b> (+{rps_rew + 15} 🧬, +25 XP){b_str}"
                else:
                    p2_obj['bio_res'] += rps_rew + 15
                    p2_obj['xp'] += 25
                    p2_obj['wins_rps'] = p2_obj.get('wins_rps', 0) + 1
                    b_str = try_grant_rare_buff(p2_obj) or ""
                    verdict = f"🏆 <b>Победил(а) {n2}!</b> (+{rps_rew + 15} 🧬, +25 XP){b_str}"

                save_rpg_firebase()
                room['moves'].clear()

                kb = InlineKeyboardMarkup(row_width=3)
                kb.row(
                    InlineKeyboardButton("🪨 Камень", callback_data=f"rps_pvp_{game_cid}_r"),
                    InlineKeyboardButton("✂️ Ножницы", callback_data=f"rps_pvp_{game_cid}_s"),
                    InlineKeyboardButton("📄 Бумага", callback_data=f"rps_pvp_{game_cid}_p")
                )
                out = (
                    f"⚔️ <b>Итог дуэли КНБ:</b>\n\n"
                    f"👤 <b>{n1}:</b> {RPS_NAMES[m1]}\n"
                    f"👤 <b>{n2}:</b> {RPS_NAMES[m2]}\n\n"
                    f"{verdict}\n\n"
                    f"<i>👇 Жмите на кнопки для реванша!</i>"
                )
                safe_edit_message(out, cid, msg_id, business_id=biz_id, reply_markup=kb)
                bot.answer_callback_query(call.id, "Вскрываемся!")
    except Exception as e:
        print(f"⚠️ Ошибка в handle_games_callbacks: {e}")

@bot.business_message_handler(func=should_intercept_business_message, content_types=['text', 'photo', 'voice', 'video_note', 'audio', 'document', 'video'])
def _biz_game_router(message):
    try:
        if message.content_type == 'text':
            handle_all_game_commands(message)
    except Exception as e:
        print(f"⚠️ Ошибка в _biz_game_router: {e}")

@bot.message_handler(func=should_intercept_private_message, content_types=['text', 'photo', 'voice', 'video_note', 'audio', 'document', 'video'])
def _priv_game_router(message):
    try:
        if message.content_type == 'text':
            handle_all_game_commands(message)
    except Exception as e:
        print(f"⚠️ Ошибка в _priv_game_router: {e}")

bot.callback_query_handlers.insert(0, bot.callback_query_handlers.pop())
bot.business_message_handlers.insert(0, bot.business_message_handlers.pop())
bot.message_handlers.insert(0, bot.message_handlers.pop())
print("🎮 МОДУЛЬ ИГР И МОДЕРАЦИИ (games.py) УСПЕШНО ПОДКЛЮЧЕН!")
