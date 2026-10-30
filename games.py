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
get_http_session = main.get_http_session
safe_edit_message = main.safe_edit_message
save_config = main.save_config
ask_gemini = main.ask_gemini
register_creator_id = main.register_creator_id

# =====================================================================
# 📜 0. ПОЛНЫЙ СПИСОК КОМАНД (ДЛЯ АДМИНКИ И ДЛЯ ОБЫЧНЫХ ЧАТОВ)
# =====================================================================
def get_updated_full_commands_text():
    return (
        "📜 <b>Полный список всех команд бота:</b>\n\n"
        "🔹 <b>1. Обычный ГПТ и Модели (ВСЕГДА показывает context и model):</b>\n"
        "• <code>/panel</code> (или <code>панель</code>) — главный пульт с кнопками (в ЛС бота)\n"
        "• <code>команды</code> (или <code>помощь</code> / <code>хелп</code>) — показать список команд в любом чате\n"
        "• <code>модели</code> — выбрать модель для обычного <code>гпт</code>\n"
        "• <code>авто-модели</code> — выбрать модель отдельно для Автоответчика (Groq / Gemini)\n"
        "• <code>фото-модели</code> — выбрать бесплатную модель FLUX для рисования\n"
        "• <code>гпт [текст]</code> — задать вопрос ИИ (или Reply на фото/файл/ГС)\n"
        "• <code>нарисуй [описание]</code> — сгенерировать картинку через бесплатный FLUX\n"
        "• <code>контекст 100</code> — изменить глубину памяти диалога\n"
        "• <code>сброс</code> — очистить общий контекст чата (у тебя и у собеседника!)\n"
        "• <code>сброс все</code> — очистить память вообще всех чатов\n"
        "• <code>характер [текст]</code> — задать системный промпт для обычного <code>гпт</code>\n"
        "• <code>характер сброс</code> — вернуть стандартный промпт для <code>гпт</code>\n"
        "• <code>ключи</code> — статус всех ключей Gemini и пинг-тест\n"
        "• <code>список</code> — посмотреть все списки и настройки\n\n"
        "🔹 <b>2. Автоответчик (Работает отдельно на Groq + Обучение + Стелс):</b>\n"
        "• <code>автоответчик @юзер</code> (или <code>ао @юзер</code>) — вкл/выкл автоответчик для конкретного человека\n"
        "• <code>ао вкл</code> / <code>ао выкл</code> — (прямо в чате с человеком) скрытно вкл/выкл автоответчик для этого чата\n"
        "• <code>автоответчик вкл</code> / <code>выкл</code> — глобальный автоответчик для всех\n"
        "• <code>обучение</code> — диалоговое обучение ТОЛЬКО для автоответчика (выход: <code>стоп</code>)\n"
        "• <code>обучение сброс</code> — стереть выученные правила автоответчика\n"
        "• <code>стелс вкл</code> / <code>выкл</code> — скрывать model/context и часики в режиме автоответчика\n"
        "• <code>таймер 5</code> — через сколько минут молчания отвечает автоответчик\n"
        "• <code>шанс 75</code> — вероятность ответа автоответчика в % (от 10 до 100)\n\n"
        "🔹 <b>3. Голосовые и Кружки (рс + живой ответ):</b>\n"
        "• Ответь <code>рс</code> на ГС/кружок — чистая текстовая расшифровка без воды\n"
        "• Ответь <code>рс [задача]</code> — например: <code>рс перескажи кратко</code>\n"
        "• <code>рс @юзер</code> / <code>рс все вкл</code> — постоянная авто-расшифровка\n"
        "• <i>Если ответить на сообщение бота голосовым — он сам расшифрует его и ответит!</i>\n\n"
        "🔹 <b>4. Третий собеседник, Будильник и Шпион:</b>\n"
        "• <code>вставка 25</code> (или <code>вставка выкл</code>) — раз в N сообщений бот вставляет «5 копеек»\n"
        "• <code>рассуди</code> / <code>5 копеек</code> / <code>анализ</code> — разбор последних 50 сообщений чата\n"
        "• <code>будильник 30 [задача]</code> — через 30 мин бот сам напишет в чат\n"
        "• <code>будильник @юзер 60 [задача]</code> — будильник для юзера из админки\n"
        "• <code>шпион вкл</code> / <code>выкл</code> — перехват удаленных и измененных СМС\n"
        "• <code>запретить @юзер</code> / <code>разрешить @юзер</code> — ЧС для бота\n\n"
        "🔹 <b>5. Игры, Хабаровский Лутинг, Био-войны, Бункер и Мафия:</b>\n"
        "• <code>/search</code> (или <code>лут</code> / <code>мусорка</code>) — «Хабаровский Лутинг» (гача у Меда на вирусы)\n"
        "• <code>кд лут 5</code> — изменить кулдаун лутинга в минутах (0 = без кд)\n"
        "• <code>бункер</code> — сюжетная игра «Бункер» (ГПТ создает катастрофу по чату, ведет ИИ-ведущий)\n"
        "• <code>мафия</code> — психологическая «Мафия: Допрос» (с ролями, уликами и ИИ-подозреваемыми)\n"
        "• <code>виселица</code> / <code>виселица ии</code> — Поле Чудес (можно жать кнопки ИЛИ писать буквы/слово в чат!)\n"
        "• <code>кнб</code> — Камень-Ножницы-Бумага (1 на 1 или против бота)\n"
        "• <code>лаба</code> (или <code>моя лаба</code> / <code>био</code>) — твоя Био-Лаборатория (прокачка вирусов как в Ирисе)\n"
        "• <code>заразить</code> — заразить собеседника патогеном\n"
        "• <code>вакцина</code> — снять горячку за 80 🧬\n"
        "• <code>игры</code> (или <code>профиль</code>) — твой уровень, опыт и редкие баффы\n"
        "• <code>био топ</code> (или <code>топ</code>) — таблица лидеров"
    )

def get_chat_commands_text():
    return (
        "📜 <b>Команды, доступные прямо в этом чате:</b>\n\n"
        "🤖 <b>ИИ-Ассистент и Анализ:</b>\n"
        "• <code>гпт [запрос]</code> — спросить ИИ (понимает текст, фото, PDF/DOCX и голосовые реплаем)\n"
        "• <code>нарисуй [описание]</code> — нарисовать картинку (FLUX)\n"
        "• <code>рс</code> (ответом на ГС или кружок) — дословная расшифровка в текст\n"
        "• <code>рассуди</code> — ИИ разберет ваш спор и вынесет вердикт, кто прав\n"
        "• <code>анализ</code> — разбор последних 50 сообщений переписки\n"
        "• <code>5 копеек</code> — бот вставит живую реплику по теме разговора\n"
        "• <code>сброс</code> — очистить память ИИ в этом чате\n\n"
        "🎭 <b>Большие ИИ-Игры (Бункер и Мафия):</b>\n"
        "• <code>бункер</code> — запуск игры «Бункер» (сюжет по вашему чату, скрытые карты, споры за выживание)\n"
        "• <code>мафия</code> — детективная «Мафия» (скрытые роли, улики, допрос и голосование)\n"
        "• <code>стоп игра</code> — досрочно завершить Бункер или Мафию\n\n"
        "🗑 <b>Хабаровский Лутинг и Мини-игры:</b>\n"
        "• <code>/search</code> (или <code>лут</code> / <code>мусорка</code>) — порыться в мусорке у Меда (гача на вирусы 🧬)\n"
        "• <code>виселица</code> — классическое Поле Чудес (можно писать буквы и слова прямо в чат!)\n"
        "• <code>виселица ии</code> — ГПТ сам загадывает слово по контексту вашего общения!\n"
        "• <code>кнб</code> — Камень-Ножницы-Бумага (дуэль 1 на 1 или с ботом)\n\n"
        "🧬 <b>Био-войны (Коронаирис) и РПГ:</b>\n"
        "• <code>лаба</code> — открыть свою Био-Лабораторию и прокачку\n"
        "• <code>заразить</code> — пустить вирус в собеседника и добыть вирусы 🧬\n"
        "• <code>вакцина</code> — вылечить горячку (80 🧬)\n"
        "• <code>профиль</code> (или <code>игры</code>) — твой уровень, опыт и редкие баффы\n"
        "• <code>био топ</code> — таблица лидеров"
    )

main.get_full_commands_text = get_updated_full_commands_text

# =====================================================================
# 🎨 1. БЕСПЛАТНАЯ РИСОВАЛКА FLUX (АВТОМАТИЧЕСКИ ЗАМЕНЯЕТ ПЛАТНУЮ GEMINI)
# =====================================================================
FREE_IMAGE_MODELS = ['flux', 'flux-realism', 'flux-anime', 'flux-3d', 'turbo']
if CONFIG.get('image_model') not in FREE_IMAGE_MODELS:
    CONFIG['image_model'] = 'flux'

if 'looting_cd_minutes' not in CONFIG:
    CONFIG['looting_cd_minutes'] = 5

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
# 🏆 2. БАЗА РПГ, РЕЙТИНГА, РЕДКИХ БАФФОВ И БИО-ЛАБОРАТОРИЙ (FIREBASE)
# =====================================================================
RPG_PLAYERS = {}

RARE_BUFFS_INFO = {
    'oracle_eye': {
        'name': '🔮 Глаз Оракула',
        'desc': '20% шанс сразу открыть 1 букву на старте Виселицы'
    },
    'trickster_glove': {
        'name': '🧤 Ловкость Шулера',
        'desc': '20% шанс получить +25 вирусов даже при ничьей в КНБ'
    },
    'antigen_shield': {
        'name': '🧬 Антиген-Щит',
        'desc': 'Дает +8% к защите от заражения в Био-войнах'
    },
    'rna_catalyst': {
        'name': '⚡ Катализатор РНК',
        'desc': '15% шанс не потратить патоген при атаке «заразить»'
    }
}

def load_rpg_firebase():
    if not FIREBASE_URL:
        return
    try:
        r = get_http_session().get(f'{FIREBASE_URL}/rpg_data.json', timeout=8)
        if r.status_code == 200 and isinstance(r.json(), dict):
            RPG_PLAYERS.update(r.json())
            print(f"🧬 Данные Био-войн и РПГ загружены ({len(RPG_PLAYERS)} игроков).")
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
            'xp': 0,
            'bio_res': 150,
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

    sync_lab_patogens(RPG_PLAYERS[uid])
    return RPG_PLAYERS[uid]

def find_player_by_username(uname_clean):
    uname_clean = uname_clean.lower().replace('@', '').strip()
    for uid, p in RPG_PLAYERS.items():
        if (p.get('username') or '').lower() == uname_clean:
            sync_lab_patogens(p)
            return p
    return None

def sync_lab_patogens(p):
    now = time.time()
    max_cells = 2 + p.get('lab_patogen_lvl', 1)
    dev_lvl = p.get('lab_dev_lvl', 1)
    interval_sec = max(180, 900 - (dev_lvl - 1) * 70)

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
        (1, "Новичок пробирки"),
        (3, "Санитар у Меда"),
        (6, "Охотник за штаммами"),
        (10, "Главврач Лаборатории"),
        (15, "Архитектор Пандемии"),
        (25, "Био-Лорд Ириса")
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
    if random.randint(1, 100) <= 12:
        chosen = random.choice(available)
        p.setdefault('buffs', []).append(chosen)
        b_info = RARE_BUFFS_INFO[chosen]
        return f"\n🎁 <b>РЕДКИЙ БАФФ ПОЛУЧЕН:</b> {b_info['name']} (<i>{b_info['desc']}</i>)!"
    return None

# =====================================================================
# 🦠 3. БИО-ВОЙНЫ: КОРОНАИРИС (ЛАБОРАТОРИЯ, ПРОКАЧКА, ЗАРАЖЕНИЕ)
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
    interval_min = max(3, round((900 - (dev_lvl - 1) * 70) / 60, 1))

    fever_rem = int(p.get('fever_until', 0) - now)
    if fever_rem > 0:
        mins = (fever_rem // 60) + 1
        by_who = p.get('infected_by') or 'неизвестного вируса'
        status_line = f"🤒 <b>ГОРЯЧКА (Заражен от {html.escape(by_who)})!</b> Осталось: <b>{mins} мин.</b>"
    else:
        status_line = "🟢 <b>Здоров (Лаборатория работает штатно)</b>"

    buff_names = [RARE_BUFFS_INFO[b]['name'] for b in p.get('buffs', []) if b in RARE_BUFFS_INFO]
    buff_str = ", ".join(buff_names) if buff_names else "нет (выпадают в играх)"

    name_safe = html.escape(p.get('name', 'Игрок'))
    text = (
        f"🧬 <b>Био-Лаборатория: {name_safe}</b>\n"
        f"🎖 <b>Ранг:</b> {rank_title} (Ур. {lvl} | ✨ {p.get('xp', 0)} XP)\n"
        f"🩺 <b>Статус:</b> {status_line}\n\n"
        f"🔋 <b>Вирусы (био-ресурсы):</b> <code>{p.get('bio_res', 0)}</code> 🧬 | <b>Био-опыт:</b> <code>{p.get('bio_xp', 0)}</code> ☣️\n"
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
        f"<i>💡 Ищи лут командой <code>/search</code> или пиши <code>заразить</code> в чате!</i>"
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
        kb.add(InlineKeyboardButton("💉 Купить вакцину (80 🧬)", callback_data=f"biocure_{uid}"))
    kb.add(
        InlineKeyboardButton("🗑 Лутать мусорку", callback_data="loot_again"),
        InlineKeyboardButton("🔄 Обновить лабу", callback_data=f"biorefresh_{uid}")
    )
    return text, kb

def perform_infection_attempt(attacker_user, target_p):
    atk_p = get_player(attacker_user)
    now = time.time()

    if atk_p['uid'] == target_p['uid']:
        return "🤪 Ты не можешь заразить сам себя!"

    if atk_p.get('fever_until', 0) > now:
        rem_m = int((atk_p['fever_until'] - now) // 60) + 1
        return (
            f"🤒 <b>У тебя сейчас горячка (еще {rem_m} мин.)!</b>\n"
            f"Ты не можешь заражать других, пока не выздоровеешь или не примешь вакцину в <code>лаба</code>."
        )

    if atk_p.get('patogens', 0) <= 0:
        return "🧪 <b>В лаборатории нет готовых патогенов!</b> Подожди синтеза или прокачай <code>+разработка</code> в <code>лаба</code>."

    saved_patogen = False
    if 'rna_catalyst' in atk_p.get('buffs', []) and random.randint(1, 100) <= 15:
        saved_patogen = True
    else:
        atk_p['patogens'] -= 1

    infect_lvl = atk_p.get('lab_infect_lvl', 1)
    immune_lvl = target_p.get('lab_immune_lvl', 1)
    lethal_lvl = atk_p.get('lab_lethal_lvl', 1)
    sec_lvl = target_p.get('lab_security_lvl', 1)

    chance = 55 + (infect_lvl - immune_lvl) * 6
    if 'antigen_shield' in target_p.get('buffs', []):
        chance -= 8
    chance = max(15, min(90, chance))

    roll = random.randint(1, 100)
    atk_name = html.escape(atk_p['name'])
    def_name = html.escape(target_p['name'])
    catalyst_note = "\n⚡ <i>Сработал «Катализатор РНК» — патоген сохранен!</i>" if saved_patogen else ""

    if roll <= chance:
        fever_mins = 10 + lethal_lvl * 4
        loot_res = 45 + lethal_lvl * 25
        loot_xp = 25 + lethal_lvl * 5

        spotted = (random.randint(1, 100) <= (35 + sec_lvl * 12))
        target_p['fever_until'] = now + fever_mins * 60
        target_p['infected_by'] = atk_p['name'] if spotted else "Неизвестный био-хакер"

        atk_p['bio_res'] = atk_p.get('bio_res', 0) + loot_res
        atk_p['bio_xp'] = atk_p.get('bio_xp', 0) + loot_xp
        atk_p['xp'] = atk_p.get('xp', 0) + loot_xp
        atk_p['infections_done'] = atk_p.get('infections_done', 0) + 1

        buff_msg = try_grant_rare_buff(atk_p) or ""
        save_rpg_firebase()

        sec_note = "🕵️ Служба безопасности жертвы засекла твой след!" if spotted else "🥷 Атака прошла скрытно для службы безопасности!"
        return (
            f"☣️ <b>БИО-АТАКА УСПЕШНА!</b> (Шанс пробития: {chance}%)\n\n"
            f"🦠 <b>{atk_name}</b> заразил(а) патогеном <b>{def_name}</b>!\n"
            f"🤒 У жертвы началась горячка на <b>{fever_mins} мин.</b>\n"
            f"🧬 Добыто вирусов: <b>+{loot_res} 🧬</b> и <b>+{loot_xp} XP</b>\n"
            f"{sec_note}{catalyst_note}{buff_msg}"
        )
    else:
        atk_p['xp'] = atk_p.get('xp', 0) + 5
        save_rpg_firebase()
        return (
            f"🛡 <b>ИММУНИТЕТ ВЫСТОЯЛ!</b> (Шанс был: {chance}%)\n\n"
            f"Организм <b>{def_name}</b> уничтожил штамм от <b>{atk_name}</b>!\n"
            f"💡 Прокачай <code>+заразность</code> в своей <code>лаба</code>.{catalyst_note}"
        )

# =====================================================================
# 🗑 3.5 МИНИ-ИГРА: «ХАБАРОВСКИЙ ЛУТИНГ» (СОЛО-ПОИСК МУСОРА / ГАЧА)
# =====================================================================
def execute_khabarovsk_looting(user):
    """
    Копается в мусорке у Меда и с точным шансом выдает лут в вирусах:
    - ⚪ Обычный шмот (70%): Сгнивший чеснок (+10), Дохлая батарейка АА (+15)
    - 🔵 Редкий шмот (20%): Замороженный пельмень (+50), Очки Спамтона (+100)
    - 🟡 Легендарка (5%): Котурны 20 см (+500)
    - 💥 Фейл (5%): «Наткнулась на дедушку» (-50 за испуг)
    """
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
        delta = 10
        flavor = "Воняет на весь квартал, но для штамма сойдет."
    elif roll <= 70:
        rarity = "⚪ Обычный шмот"
        item_name = "Дохлая батарейка АА 🔋"
        delta = 15
        flavor = "Если пожевать, дает легкий привкус окиси цинка."
    elif roll <= 80:
        rarity = "🔵 Редкий шмот"
        item_name = "Замороженный пельмень 🥟"
        delta = 50
        flavor = "Пролежал во льдах с прошлой зимней сессии!"
    elif roll <= 90:
        rarity = "🔵 Редкий шмот"
        item_name = "Очки Спамтона 🕶"
        delta = 100
        flavor = "ТЕПЕРЬ ТЫ НАСТОЯЩИЙ [[Крупная Шишка]] У МЕДА!"
    elif roll <= 95:
        rarity = "🟡 ЛЕГЕНДАРКА (5%)"
        item_name = "Котурны 20 см 👢✨"
        delta = 500
        flavor = "АБСОЛЮТНЫЙ ДЖЕКПОТ! В таких даже по гололеду как по подиуму!"
    else:
        rarity = "💥 ФЕЙЛ (5%)"
        item_name = "«Наткнулась на дедушку» 👴💀"
        delta = -50
        flavor = "Дед выскочил из-за баков и огрел клюкой! Минус вирусы за испуг."

    p['bio_res'] = max(0, p.get('bio_res', 0) + delta)
    xp_gain = 25 if delta >= 500 else (12 if delta >= 50 else (5 if delta > 0 else 2))
    p['xp'] = p.get('xp', 0) + xp_gain
    save_rpg_firebase()

    sign_str = f"+{delta} 🧬" if delta > 0 else f"{delta} 🧬"
    text = (
        f"🗑 <b>Хабаровский Лутинг (Мусорка у Меда)</b>\n"
        f"🕵️ Искатель: <b>{u_name}</b> копается в баках...\n\n"
        f"🎲 Категория: <b>{rarity}</b>\n"
        f"📦 Находка: <b>{item_name}</b>\n"
        f"💬 <i>{flavor}</i>\n\n"
        f"🦠 Конвертация в вирусы: <b>{sign_str}</b> (Баланс: <code>{p['bio_res']}</code> 🧬 | +{xp_gain} XP)"
    )
    return text, True

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
    ("СТЕТОСКОП", "Медицинский атрибут"), ("АНАТОМИЯ", "Первая пара в меде")
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
    if 'oracle_eye' in p.get('buffs', []) and random.randint(1, 100) <= 20:
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
        sent = bot.send_message(chat_id, text, business_connection_id=biz_id, reply_markup=kb)
        game['msg_id'] = sent.message_id

def process_hangman_letter_or_word(cid, biz_id, user, raw_guess, from_chat_text=False):
    """Обрабатывает ввод одной буквы ИЛИ целого слова (как с кнопок, так и текстом из чата)."""
    game = HANGMAN_GAMES.get(cid)
    if not game or game.get('finished'):
        return False

    p = get_player(user)
    u_name = html.escape(p['name'])
    clean = raw_guess.strip().upper().replace('Ё', 'Е')

    # 1. Если введена ОДНА русская буква
    if len(clean) == 1 and clean in RUS_ALPHABET:
        letter = clean
        if letter in game['guessed']:
            game['status'] = f"⚠️ Буква «<b>{letter}</b>» уже была названа!"
        else:
            game['guessed'].add(letter)
            if letter in game['word']:
                if all(ch in game['guessed'] for ch in game['word']):
                    game['finished'] = True
                    rew_res = 55 if game['is_ai'] else 35
                    rew_xp = 30 if game['is_ai'] else 20
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

    # 2. Если введено СЛОВО ЦЕЛИКОМ
    elif len(clean) >= 2 and re.match(r'^[А-Я]+$', clean):
        if clean == game['word']:
            game['guessed'].update(list(game['word']))
            game['finished'] = True
            rew_res = 65 if game['is_ai'] else 45
            rew_xp = 35 if game['is_ai'] else 25
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
        # При вводе текста в чат переносим табло вниз, чтобы игрокам было удобно!
        old_id = game.get('msg_id')
        if old_id:
            try:
                bot.delete_message(cid, old_id, business_connection_id=biz_id)
            except Exception:
                pass
        sent = bot.send_message(cid, text, business_connection_id=biz_id, reply_markup=kb)
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

def send_rps_menu(chat_id, biz_id=None):
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
    bot.send_message(cid, text, business_connection_id=biz_id, reply_markup=kb)

# =====================================================================
# 🎭 6. СЮЖЕТНЫЕ ИИ-ИГРЫ: «БУНКЕР» И «МАФИЯ: ДОПРОС»
# (Запуск от ГПТ с контекстом чата + Ведение от отдельного ИИ с чистой памятью)
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
    "   - Вынеси четкий вердикт: КТО ОДИН проходит в Бункер, а кто остается снаружи (и почему).\n"
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
    "   - Объяви, кто победил в этой партии (вычислил убийцу или идеально ушел от наказания).\n"
    "   - В самом конце ответа обязательно напиши строчку: ПОБЕДИТЕЛЬ: [Имя победившего игрока]"
)

def call_isolated_host_ai(game, round_prompt):
    """
    Вызывает ОТДЕЛЬНУЮ нейронку-ведущего с обнуленным контекстным окном (хранит только ход текущей игры!).
    Не засоряет CHAT_MEMORY основного ГПТ.
    """
    host_model = CONFIG.get('game_host_model', 'groq/openai/gpt-oss-120b')
    sys_prompt = HOST_SYSTEM_PROMPT_BUNKER if game['type'] == 'bunker' else HOST_SYSTEM_PROMPT_MAFIA
    host_history = game.setdefault('host_history', [])

    s = get_http_session()

    # 1. Если для ведущего выбрана модель Groq
    if host_model.startswith('groq/') and GROQ_API_KEY:
        candidates = [host_model, 'groq/openai/gpt-oss-120b', 'groq/qwen/qwen3.8-27b', 'groq/openai/gpt-oss-20b']
        seen = set()
        groq_list = [m for m in candidates if not (m in seen or seen.add(m))]

        messages = [{'role': 'system', 'content': sys_prompt}]
        for h in host_history:
            messages.append({'role': h['role'], 'content': h['content']})
        messages.append({'role': 'user', 'content': round_prompt})

        headers = {'Authorization': f'Bearer {GROQ_API_KEY}', 'Content-Type': 'application/json'}
        for g_m in groq_list:
            m_id = g_m.replace('groq/', '')
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
                    if ans and "can't comply" not in ans.lower():
                        host_history.append({'role': 'user', 'content': round_prompt})
                        host_history.append({'role': 'assistant', 'content': ans})
                        return ans, g_m
            except Exception:
                continue

    # 2. Если для ведущего выбрана модель Gemini (или Groq недоступен)
    gem_model = host_model if not host_model.startswith('groq/') else main.ACTIVE_MODEL
    if gem_model.startswith('groq/'):
        gem_model = main.MODEL_HIERARCHY[0] if main.MODEL_HIERARCHY else 'gemini-3-flash'

    contents = []
    for h in host_history:
        role = 'user' if h['role'] == 'user' else 'model'
        contents.append({'role': role, 'parts': [{'text': h['content']}]})
    contents.append({'role': 'user', 'parts': [{'text': round_prompt}]})

    payload = {
        'contents': contents,
        'system_instruction': {'parts': [{'text': sys_prompt}]}
    }
    url = f"{GEMINI_API_BASE}/models/{gem_model}:generateContent"
    for api_key in GEMINI_KEYS:
        try:
            r = s.post(url, headers=main.get_gemini_headers(api_key), json=payload, timeout=22)
            if r.status_code == 200:
                data = r.json()
                if data.get('candidates'):
                    ans = data['candidates'][0]['content']['parts'][0]['text'].strip()
                    host_history.append({'role': 'user', 'content': round_prompt})
                    host_history.append({'role': 'assistant', 'content': ans})
                    return ans, gem_model
        except Exception:
            continue

    return "⚠️ Ведущий задумался над вашими словами... Повторите действие!", host_model

def generate_game_setup_from_main_gpt(chat_id, game_type, p1_name, p2_name):
    """
    ЗАДАЮЩАЯ МОДЕЛЬ: Всегда использует основную модель ГПТ (main.ACTIVE_MODEL) и читает контекст чата,
    чтобы создать сюжет, роли и лут с вайбом этого чата, НО не мусорит в CHAT_MEMORY!
    """
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

    # Вызываем именно задающую модель из ГПТ (ACTIVE_MODEL), но save_to_memory=False чтобы не мусорить!
    reply, err, model_used, _ = main.ask_ai(cid, setup_prompt, save_to_memory=False, is_autoreply=False)
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
    """Проверяет, написал ли каждый обязательный игрок минимум по 1 сообщению в текущем раунде."""
    msgs = game.get('round_messages', {})
    missing = []
    for req_role in game.get('required_roles', ['p1', 'p2']):
        if not msgs.get(req_role):
            missing.append(game[f'{req_role}_name'])
    return (len(missing) == 0), missing

def start_story_game(chat_id, biz_id, game_type, starter_user):
    cid = int(chat_id)
    old_g = STORY_GAMES.pop(cid, None)
    if old_g and old_g.get('timer'):
        old_g['timer'].cancel()

    # Определяем участников чата
    if biz_id:
        p1_id = main.CREATOR_ID
        p1_name = f"@{CREATOR_USERNAME}"
        if starter_user.id != main.CREATOR_ID:
            p2_id = starter_user.id
            p2_name = starter_user.first_name or "Собеседник"
        else:
            p2_id = cid
            # Ищем имя собеседника
            p2_name = "Собеседник"
            for u_info in CONFIG.get('known_chats', {}).values():
                if u_info.get('chat_id') == cid and u_info.get('name'):
                    p2_name = u_info['name']
                    break
        required_roles = ['p1', 'p2']
    else:
        # Если запущено в ЛС бота (соло против ИИ)
        p1_id = starter_user.id
        p1_name = starter_user.first_name or "Игрок"
        p2_id = 0
        p2_name = "Кибер-Соперник (НПС)"
        required_roles = ['p1']

    title = "☢️ БУНКЕР НА ВЫЖИВАНИЕ" if game_type == 'bunker' else "🕵️ МАФИЯ: ПЕРЕКРЕСТНЫЙ ДОПРОС"
    wait_msg = bot.send_message(
        cid,
        f"🎬 <b>Запускаем «{title}»...</b>\n"
        f"<i>Основной ГПТ ({main.ACTIVE_MODEL}) пишет сюжет и секретные роли по мотивам вашего чата...</i>",
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
    host_model = CONFIG.get('game_host_model', 'groq/openai/gpt-oss-120b')

    # Формируем стартовую сводку для изолированной памяти Ведущего
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
        f"🎭 <b>{title} — РАУНД 1/{max_rounds}</b>\n"
        f"<i>Сюжет создал: <code>{gen_model}</code> | Ведущий: <code>{host_model}</code></i>\n"
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
        f"🎙 <i>Ведущий анализирует ваши речи и манипуляции (Раунд {cur_round}/{max_rounds})...</i>",
        business_connection_id=biz_id
    )

    host_reply, host_model_used = call_isolated_host_ai(game, turn_prompt)

    if is_final:
        game['finished'] = True
        game['processing'] = False
        STORY_GAMES.pop(cid, None)

        # Награждаем участников вирусами и опытом
        reward_note = "\n\n🎁 <b>Награда за партию:</b> оба участника получают <b>+40 🧬 и +30 XP</b> (а победитель — еще <b>+70 🧬 бонусом!</b>)"
        for uid_key in ('p1_id', 'p2_id'):
            uid_val = str(game.get(uid_key) or '')
            if uid_val in RPG_PLAYERS:
                pl = RPG_PLAYERS[uid_val]
                pl['bio_res'] = pl.get('bio_res', 0) + 40
                pl['xp'] = pl.get('xp', 0) + 30
                if pl['name'].lower() in host_reply.split('ПОБЕДИТЕЛЬ:')[-1].lower():
                    pl['bio_res'] += 70
                    pl['xp'] += 25
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
    is_owner = (uid == main.CREATOR_ID) or ((user.username or '').lower() == CREATOR_USERNAME)

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

    # Если таймер минуты уже истек и мы ждали только последнего молчуна — сразу запускаем ход Ведущего!
    if game.get('timer_expired'):
        ready, _ = check_story_round_ready(game)
        if ready:
            threading.Thread(target=advance_story_game_round, args=(cid,), daemon=True).start()
    return True

# =====================================================================
# 🎛 7. ОБРАБОТЧИКИ КОМАНД И КНОПОК ДЛЯ ВСЕХ ИГР, ЛУТИНГА И БИО-ВОЙН
# =====================================================================
GAME_TRIGGERS = {
    'лаба', 'моя лаба', 'био', 'биовойны', 'заразить', 'вакцина', 'лечиться',
    '+патоген', '+разработка', '+заразность', '+иммунитет', '+летальность', '+безопасность',
    'виселица', 'виселица ии', 'поле чудес', 'кнб', 'цуефа', 'рпг', 'профиль', 'топ', 'био топ', 'игры',
    'команды', 'помощь', 'хелп', '/help',
    '/search', 'лут', 'мусорка', 'лутинг',
    'бункер', 'игра бункер', 'мафия', 'игра мафия', 'стоп игра', 'игра стоп',
    'игра-модели', 'модели игры', 'модели ведущего'
}

def is_game_command(text):
    t = (text or '').lower().strip()
    if not t:
        return False
    if t in GAME_TRIGGERS:
        return True
    for prefix in ('заразить ', 'виселица ', 'кд лут ', 'время игры ', 'раунды игры '):
        if t.startswith(prefix):
            return True
    return False

def should_intercept_message(message):
    """Определяет, относится ли сообщение к играм (чтобы обычный ГПТ не триггерился на реплаи!)."""
    text = (message.text or '').strip()
    if not text:
        return False
    low = text.lower()

    # Если человек явно вызывает обычный ГПТ — пропускаем к основному боту
    if low == 'гпт' or low.startswith('гпт ') or low.startswith('нарисуй '):
        return False

    if is_game_command(low):
        return True

    cid = int(message.chat.id)

    # Если идет Бункер или Мафия — все реплики игроков в чате идут Ведущему игры!
    if cid in STORY_GAMES and not STORY_GAMES[cid].get('finished'):
        return True

    # Если идет Виселица — перехватываем одиночные русские буквы ИЛИ слова той же длины (либо реплай на Виселицу)
    if cid in HANGMAN_GAMES and not HANGMAN_GAMES[cid].get('finished'):
        clean = low.upper().replace('Ё', 'Е')
        word_len = len(HANGMAN_GAMES[cid]['word'])
        is_reply_to_hm = bool(message.reply_to_message and message.reply_to_message.message_id == HANGMAN_GAMES[cid].get('msg_id'))
        if len(clean) == 1 and clean in RUS_ALPHABET:
            return True
        if re.match(r'^[А-Я]+$', clean) and (len(clean) == word_len or is_reply_to_hm):
            return True

    return False

def handle_all_game_commands(message):
    if not message.from_user:
        return
    register_creator_id(message.from_user)
    cid = int(message.chat.id)
    biz_id = getattr(message, 'business_connection_id', None)
    raw_text = (message.text or '').strip()
    low = raw_text.lower()
    user = message.from_user
    is_owner = (user.id == main.CREATOR_ID) or ((user.username or '').lower() == CREATOR_USERNAME)
    p = get_player(user)

    # 1. Настройки параметров игр (для владельца)
    if is_owner and low.startswith('кд лут '):
        try:
            mins = max(0.0, float(low.split()[-1]))
            CONFIG['looting_cd_minutes'] = mins
            save_config()
            bot.send_message(cid, f"🗑 Кулдаун «Хабаровского Лутинга» установлен на: <b>{mins:g} мин.</b>", business_connection_id=biz_id)
        except Exception:
            bot.send_message(cid, "⚠️ Укажи минуты числом, например: <code>кд лут 5</code> или <code>кд лут 0</code>", business_connection_id=biz_id)
        return

    if is_owner and low.startswith('время игры '):
        try:
            secs = max(15, min(600, int(low.split()[-1])))
            CONFIG['game_round_seconds'] = secs
            save_config()
            bot.send_message(cid, f"⏱ Время одного раунда в Бункере/Мафии: <b>{secs} сек.</b>", business_connection_id=biz_id)
        except Exception:
            bot.send_message(cid, "⚠️ Формат: <code>время игры 60</code> (в секундах)", business_connection_id=biz_id)
        return

    if is_owner and low.startswith('раунды игры '):
        try:
            rds = max(2, min(10, int(low.split()[-1])))
            CONFIG['game_max_rounds'] = rds
            save_config()
            bot.send_message(cid, f"🎭 Количество раундов в Бункере/Мафии: <b>{rds}</b>", business_connection_id=biz_id)
        except Exception:
            bot.send_message(cid, "⚠️ Формат: <code>раунды игры 3</code>", business_connection_id=biz_id)
        return

    if is_owner and low in ('игра-модели', 'модели игры', 'модели ведущего'):
        models = main.fetch_all_models()
        groq_first = sorted([m for m in models if m.startswith('groq/')]) + sorted([m for m in models if not m.startswith('groq/')])
        cur_host = CONFIG.get('game_host_model', 'groq/openai/gpt-oss-120b')
        kb = InlineKeyboardMarkup(row_width=1)
        for m in groq_first:
            mark = "✅ " if m == cur_host else ""
            kb.add(InlineKeyboardButton(f"{mark}{m}", callback_data=f"sethostmodel_{m}"))
        bot.send_message(
            cid,
            f"🎙 <b>Выбери модель для ИИ-Ведущего (Бункер / Мафия):</b>\n"
            f"<i>Задающая сюжет модель всегда берется из твоего ГПТ (<code>{main.ACTIVE_MODEL}</code>), а выбранная ниже модель ведет саму игру с чистым контекстом!</i>",
            business_connection_id=biz_id,
            reply_markup=kb
        )
        return

    # 2. Остановка сюжетной игры
    if low in ('стоп игра', 'игра стоп'):
        g = STORY_GAMES.pop(cid, None)
        if g:
            if g.get('timer'):
                g['timer'].cancel()
            bot.send_message(cid, f"🏳️ Сюжетная игра «<b>{g['title']}</b>» досрочно остановлена.", business_connection_id=biz_id)
        else:
            bot.send_message(cid, "ℹ️ Сейчас в этом чате нет активного Бункера или Мафии.", business_connection_id=biz_id)
        return

    # 3. Запуск Бункера или Мафии
    if low in ('бункер', 'игра бункер'):
        threading.Thread(target=start_story_game, args=(cid, biz_id, 'bunker', user), daemon=True).start()
        return

    if low in ('мафия', 'игра мафия'):
        threading.Thread(target=start_story_game, args=(cid, biz_id, 'mafia', user), daemon=True).start()
        return

    # 4. Хабаровский Лутинг (/search, лут, мусорка)
    if low in ('/search', 'лут', 'мусорка', 'лутинг'):
        res_text, _ = execute_khabarovsk_looting(user)
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🗑 Порыться еще раз", callback_data="loot_again"))
        bot.send_message(cid, res_text, business_connection_id=biz_id, reply_markup=kb)
        return

    # 5. Вывод списка команд
    if low in ('команды', 'помощь', 'хелп', '/help'):
        is_owner_private = (not biz_id and is_owner)
        help_txt = get_updated_full_commands_text() if is_owner_private else get_chat_commands_text()
        bot.send_message(cid, help_txt, business_connection_id=biz_id)
        return

    # 6. Игровой профиль и Топ
    if low in ('игры', 'рпг', 'профиль'):
        lvl, rank_title = get_level_and_rank(p.get('xp', 0))
        buff_list = [f"• {RARE_BUFFS_INFO[b]['name']} — <i>{RARE_BUFFS_INFO[b]['desc']}</i>" for b in p.get('buffs', []) if b in RARE_BUFFS_INFO]
        buff_block = "\n".join(buff_list) if buff_list else "<i>Пока нет редких баффов (выпадают за победы с шансом 12%)</i>"
        u_name = html.escape(p['name'])
        msg_text = (
            f"🎮 <b>Игровой профиль: {u_name}</b>\n"
            f"🎖 <b>Уровень:</b> {lvl} ({rank_title}) | ✨ <b>Опыт:</b> {p.get('xp', 0)} XP\n"
            f"🧬 <b>Вирусы:</b> {p.get('bio_res', 0)} | 🦠 <b>Заражений:</b> {p.get('infections_done', 0)} | 🗑 <b>Лутов:</b> {p.get('loot_count', 0)}\n"
            f"☢️ <b>Бункер:</b> {p.get('wins_bunker', 0)} | 🕵️ <b>Мафия:</b> {p.get('wins_mafia', 0)} | 🎡 <b>Виселица:</b> {p.get('wins_hangman', 0)} | ✂️ <b>КНБ:</b> {p.get('wins_rps', 0)}\n\n"
            f"🔮 <b>Твои редкие артефакты/баффы:</b>\n{buff_block}\n\n"
            f"🕹 <b>Команды игр:</b>\n"
            f"• <code>/search</code> (или <code>лут</code>) — Хабаровский Лутинг у Меда\n"
            f"• <code>бункер</code> | <code>мафия</code> — большие игры с ИИ-Ведущим\n"
            f"• <code>виселица</code> | <code>виселица ии</code> — Поле Чудес\n"
            f"• <code>кнб</code> — Камень-Ножницы-Бумага\n"
            f"• <code>лаба</code> | <code>заразить</code> | <code>био топ</code> — Био-войны"
        )
        bot.send_message(cid, msg_text, business_connection_id=biz_id)
        return

    if low in ('топ', 'био топ'):
        sorted_p = sorted(RPG_PLAYERS.values(), key=lambda x: (x.get('xp', 0) + x.get('bio_xp', 0)), reverse=True)[:10]
        lines = ["🏆 <b>Глобальный Топ Био-войн и Игр:</b>\n"]
        for idx, pl in enumerate(sorted_p, 1):
            lvl, _ = get_level_and_rank(pl.get('xp', 0))
            p_name = html.escape(pl.get('name', 'Игрок'))
            lines.append(
                f"{idx}. <b>{p_name}</b> — Ур. {lvl} | 🦠 Заражений: {pl.get('infections_done', 0)} | 🧬 {pl.get('bio_res', 0)}"
            )
        bot.send_message(cid, "\n".join(lines), business_connection_id=biz_id)
        return

    # 7. Лаборатория, прокачка и заражение
    if low in ('лаба', 'моя лаба', 'био', 'биовойны'):
        text, kb = build_lab_view(p)
        bot.send_message(cid, text, business_connection_id=biz_id, reply_markup=kb)
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
            bot.send_message(cid, f"⚠️ Не хватает вирусов! Нужно <b>{cost} 🧬</b> (у тебя {p.get('bio_res', 0)} 🧬).", business_connection_id=biz_id)
            return
        p['bio_res'] -= cost
        p[field] = p.get(field, 1) + 1
        p['xp'] = p.get('xp', 0) + 15
        save_rpg_firebase()
        bot.send_message(cid, f"✅ Улучшено: <b>{label}</b> ➔ Уровень {p[field]}!", business_connection_id=biz_id)
        return

    if low in ('вакцина', 'лечиться'):
        if p.get('fever_until', 0) <= time.time():
            bot.send_message(cid, "🟢 Ты полностью здоров, вакцина не требуется!", business_connection_id=biz_id)
            return
        if p.get('bio_res', 0) < 80:
            bot.send_message(cid, "⚠️ Вакцина стоит <b>80 🧬</b>. Налутай вирусы командой <code>/search</code>!", business_connection_id=biz_id)
            return
        p['bio_res'] -= 80
        p['fever_until'] = 0
        p['infected_by'] = ''
        save_rpg_firebase()
        bot.send_message(cid, "💉 <b>Вакцина введена!</b> Горячка снята, ты снова можешь заражать людей!", business_connection_id=biz_id)
        return

    if low == 'заразить' or low.startswith('заразить '):
        target_p = None
        if '@' in low:
            uname = low.split('@')[-1].split()[0]
            target_p = find_player_by_username(uname)
            if not target_p:
                known = CONFIG.get('known_chats', {}).get(uname.lower())
                if known:
                    fake_u = type('Obj', (), {'id': known['chat_id'], 'username': uname, 'first_name': known.get('name', uname), 'last_name': ''})()
                    target_p = get_player(fake_u)
        elif message.reply_to_message and message.reply_to_message.from_user:
            target_p = get_player(message.reply_to_message.from_user)
        elif biz_id:
            if user.id != cid:
                chat_uname = getattr(message.chat, 'username', '') or ''
                chat_fname = getattr(message.chat, 'first_name', '') or 'Собеседник'
                fake_u = type('Obj', (), {'id': cid, 'username': chat_uname, 'first_name': chat_fname, 'last_name': ''})()
                target_p = get_player(fake_u)
            elif main.CREATOR_ID:
                fake_owner = type('Obj', (), {'id': main.CREATOR_ID, 'username': CREATOR_USERNAME, 'first_name': f"@{CREATOR_USERNAME}", 'last_name': ''})()
                target_p = get_player(fake_owner)

        if not target_p:
            bot.send_message(cid, "⚠️ Укажи кого заразить: напиши <code>заразить</code> прямо в чате с человеком, ответь (Reply) на его сообщение или напиши <code>заразить @юзер</code>.", business_connection_id=biz_id)
            return

        res_text = perform_infection_attempt(user, target_p)
        bot.send_message(cid, res_text, business_connection_id=biz_id)
        return

    # 8. Запуск Виселицы и КНБ
    if low in ('виселица', 'поле чудес', 'виселица ии'):
        is_ai = ('ии' in low)
        if is_ai:
            wait_msg = bot.send_message(cid, "🧠 <i>ГПТ вспоминает историю вашего чата и загадывает слово...</i>", business_connection_id=biz_id)
            start_hangman_game(cid, biz_id, user, is_ai=True, msg_id_to_edit=wait_msg.message_id)
        else:
            start_hangman_game(cid, biz_id, user, is_ai=False)
        return

    if low in ('кнб', 'цуефа'):
        send_rps_menu(cid, biz_id=biz_id)
        return

    # 9. Если в этом чате идет Бункер или Мафия — записываем сообщение игрока в раунд!
    if cid in STORY_GAMES and not STORY_GAMES[cid].get('finished'):
        record_story_player_message(message)
        return

    # 10. Если в этом чате идет Виселица — проверяем введенную букву или слово!
    if cid in HANGMAN_GAMES and not HANGMAN_GAMES[cid].get('finished'):
        process_hangman_letter_or_word(cid, biz_id, user, raw_text, from_chat_text=True)
        return

@bot.callback_query_handler(func=lambda call: call.data and (
    call.data.startswith('bioupg_') or call.data.startswith('biocure_') or
    call.data.startswith('biorefresh_') or call.data.startswith('hm_') or
    call.data.startswith('rps_') or call.data.startswith('sg_') or
    call.data.startswith('sethostmodel_') or call.data in ('panel_img_models', 'loot_again')
))
def handle_games_callbacks(call):
    register_creator_id(call.from_user)
    data = call.data
    user = call.from_user
    is_owner = (user.id == main.CREATOR_ID) or ((user.username or '').lower() == CREATOR_USERNAME)
    p = get_player(user)
    cid = int(call.message.chat.id)
    msg_id = call.message.message_id
    biz_id = getattr(call.message, 'business_connection_id', None)

    # Выбор модели ИИ-Ведущего
    if data.startswith('sethostmodel_'):
        if not is_owner:
            return
        CONFIG['game_host_model'] = data.replace('sethostmodel_', '')
        save_config()
        safe_edit_message(
            f"✅ <b>Модель ИИ-Ведущего (для Бункера и Мафии) установлена:</b> <code>{CONFIG['game_host_model']}</code>\n"
            f"<i>(Сюжет и роли на старте по-прежнему генерирует твоя основная модель ГПТ: <code>{main.ACTIVE_MODEL}</code>)</i>",
            cid, msg_id, business_id=biz_id
        )
        bot.answer_callback_query(call.id, f"Ведущий: {CONFIG['game_host_model']}")
        return

    # Кнопка повторного Хабаровского Лутинга
    if data == 'loot_again':
        res_text, ok = execute_khabarovsk_looting(user)
        if not ok:
            plain_alert = re.sub(r'<[^>]+>', '', res_text)
            bot.answer_callback_query(call.id, plain_alert[:195], show_alert=True)
            return
        kb = InlineKeyboardMarkup()
        kb.add(InlineKeyboardButton("🗑 Порыться еще раз", callback_data="loot_again"))
        bot.send_message(cid, res_text, business_connection_id=biz_id, reply_markup=kb)
        bot.answer_callback_query(call.id, "🗑 Мусорка проверена!")
        return

    # Кнопки Бункера и Мафии (секретная роль, лут, досрочный ответ, стоп)
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
            r_text = game.get(f'{role}_role', 'Нет данных')
            alert_msg = f"🪪 ТВОЯ СЕКРЕТНАЯ РОЛЬ:\n{r_text}"
            bot.answer_callback_query(call.id, alert_msg[:195], show_alert=True)
            return

        if action == 'loot':
            l_text = game.get(f'{role}_loot', 'Нет данных')
            alert_msg = f"🎒 ТВОЙ ЛУТ И УЛИКИ:\n{l_text}"
            bot.answer_callback_query(call.id, alert_msg[:195], show_alert=True)
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

    # Меню бесплатных FLUX моделей рисования в админке
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

    # Кнопки Лаборатории Ириса
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
        if p.get('bio_res', 0) < 80:
            bot.answer_callback_query(call.id, "⚠️ Нужно 80 вирусов для вакцины!", show_alert=True)
            return
        p['bio_res'] -= 80
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
        p['xp'] = p.get('xp', 0) + 15
        save_rpg_firebase()
        text, kb = build_lab_view(p)
        safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
        bot.answer_callback_query(call.id, f"✅ {label} -> Ур. {p[field]}!")
        return

    # Кнопки Виселицы
    if data.startswith('hm_'):
        parts = data.split('_')
        action = parts[1]
        game_cid = int(parts[2])
        game = HANGMAN_GAMES.get(game_cid)

        if action == 'new':
            mode_type = parts[3]
            is_ai = (mode_type == 'ai')
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

    # Кнопки Камень-Ножницы-Бумага
    if data.startswith('rps_'):
        parts = data.split('_')
        mode = parts[1]
        game_cid = int(parts[2])

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
                if 'trickster_glove' in p.get('buffs', []) and random.randint(1, 100) <= 20:
                    p['bio_res'] += 25
                    bonus = "\n🧤 <i>«Ловкость Шулера» принесла +25 🧬 даже в ничью!</i>"
                res_line = f"🤝 <b>Ничья!</b> Оба выбрали {RPS_NAMES[u_move]}.{bonus}"
            elif (u_move, b_move) in RPS_WINS:
                p['bio_res'] += 25
                p['xp'] += 15
                p['wins_rps'] = p.get('wins_rps', 0) + 1
                buff_str = try_grant_rare_buff(p) or ""
                res_line = f"🏆 <b>{u_name} победил(а) бота!</b> (+25 🧬, +15 XP){buff_str}"
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
                p1_obj['bio_res'] += 35
                p1_obj['xp'] += 20
                p1_obj['wins_rps'] = p1_obj.get('wins_rps', 0) + 1
                b_str = try_grant_rare_buff(p1_obj) or ""
                verdict = f"🏆 <b>Победил(а) {n1}!</b> (+35 🧬, +20 XP){b_str}"
            else:
                p2_obj['bio_res'] += 35
                p2_obj['xp'] += 20
                p2_obj['wins_rps'] = p2_obj.get('wins_rps', 0) + 1
                b_str = try_grant_rare_buff(p2_obj) or ""
                verdict = f"🏆 <b>Победил(а) {n2}!</b> (+35 🧬, +20 XP){b_str}"

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

@bot.business_message_handler(func=should_intercept_message)
def _biz_game_router(message):
    handle_all_game_commands(message)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and should_intercept_message(m))
def _priv_game_router(message):
    handle_all_game_commands(message)

# Ставим игровые обработчики первыми в очередь
bot.callback_query_handlers.insert(0, bot.callback_query_handlers.pop())
bot.business_message_handlers.insert(0, bot.business_message_handlers.pop())
bot.message_handlers.insert(0, bot.message_handlers.pop())
