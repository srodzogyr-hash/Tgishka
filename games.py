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
BOT_GPT_MESSAGES = main.BOT_GPT_MESSAGES
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
        "• <code>команды</code> (или <code>помощь</code> / <code>хелп</code>) — показать этот список команд в любом чате\n"
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
        "🔹 <b>5. Игры, РПГ и Био-войны (работают прямо в чате!):</b>\n"
        "• <code>игры</code> (или <code>профиль</code> / <code>рпг</code>) — твой игровой профиль, уровень и редкие баффы\n"
        "• <code>лаба</code> (или <code>моя лаба</code> / <code>био</code>) — твоя Био-Лаборатория (прокачка вирусов как в Ирисе)\n"
        "• <code>заразить</code> (в чате, Reply или <code>заразить @юзер</code>) — заразить собеседника патогеном\n"
        "• <code>вакцина</code> (или <code>лечиться</code>) — снять горячку за 80 🧬\n"
        "• <code>+патоген</code>, <code>+разработка</code>, <code>+заразность</code>, <code>+иммунитет</code>, <code>+летальность</code>, <code>+безопасность</code> — быстрая прокачка лабы\n"
        "• <code>био топ</code> (или <code>топ</code>) — рейтинг лучших вирусологов и игроков\n"
        "• <code>виселица</code> (или <code>поле чудес</code>) — классическая игра в слова на кнопках\n"
        "• <code>виселица ии</code> — ваш ГПТ сам загадывает слово по всей памяти вашего чата!\n"
        "• <code>кнб</code> (или <code>цуефа</code>) — Камень-Ножницы-Бумага (1 на 1 или против бота)"
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
        "🧬 <b>Био-войны (Коронаирис) и РПГ:</b>\n"
        "• <code>лаба</code> (или <code>моя лаба</code>) — открыть свою Био-Лабораторию и прокачку\n"
        "• <code>заразить</code> — пустить вирус в собеседника и добыть био-ресурсы 🧬\n"
        "• <code>вакцина</code> — вылечить горячку (80 🧬)\n"
        "• <code>+патоген</code> | <code>+разработка</code> | <code>+заразность</code> | <code>+иммунитет</code> | <code>+летальность</code> | <code>+безопасность</code> — прокачка навыков\n"
        "• <code>профиль</code> (или <code>игры</code>) — твой уровень, опыт и редкие баффы\n"
        "• <code>био топ</code> — таблица лидеров\n\n"
        "🕹 <b>Мини-игры на кнопках:</b>\n"
        "• <code>виселица</code> — классическое Поле Чудес\n"
        "• <code>виселица ии</code> — ГПТ сам загадывает слово по контексту вашего общения!\n"
        "• <code>кнб</code> — Камень-Ножницы-Бумага (дуэль 1 на 1 или с ботом)"
    )

# Обновляем функцию списка команд в основном файле bot.py
main.get_full_commands_text = get_updated_full_commands_text

# =====================================================================
# 🎨 1. БЕСПЛАТНАЯ РИСОВАЛКА FLUX (АВТОМАТИЧЕСКИ ЗАМЕНЯЕТ ПЛАТНУЮ GEMINI)
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
        'desc': '20% шанс получить +25 био-ресурсов даже при ничьей в КНБ'
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
        (3, "Лаборант"),
        (6, "Охотник за штаммами"),
        (10, "Вирусолог"),
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
        f"🔋 <b>Био-ресурсы:</b> <code>{p.get('bio_res', 0)}</code> 🧬 | <b>Био-опыт:</b> <code>{p.get('bio_xp', 0)}</code> ☣️\n"
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
        f"<i>💡 В чате с человеком напиши <code>заразить</code>, чтобы пустить вирус!</i>"
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
    kb.add(InlineKeyboardButton("🔄 Обновить лабу", callback_data=f"biorefresh_{uid}"))
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
            f"🧬 Добыто био-ресурсов: <b>+{loot_res} 🧬</b> и <b>+{loot_xp} XP</b>\n"
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
# 🎡 4. ИГРА «ВИСЕЛИЦА / ПОЛЕ ЧУДЕС» (ОБЫЧНАЯ + УМНАЯ ИИ ПО ПАМЯТИ ГПТ!)
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
    ("ХУДИ", "Гардероб"), ("НЕЙРОСЕТЬ", "Искусственный интеллект")
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
    """
    Запрашивает слово напрямую у основного ГПТ чата по всей глубокой памяти CHAT_MEMORY
    и сохраняет факт загадывания в контекст самого ГПТ!
    """
    cid = int(chat_id)
    history = CHAT_MEMORY.get(cid, [])
    used_list = USED_AI_WORDS.setdefault(cid, [])
    used_str = ", ".join(used_list[-30:]) if used_list else "пока нет"

    if len(history) >= 4:
        prompt = (
            "[СИСТЕМНЫЙ ЗАПРОС ИГРЫ «ВИСЕЛИЦА»]:\n"
            "Мы с тобой запускаем раунд игры в Виселицу в этом чате. "
            "Вспомни ВСЮ нашу с тобой историю общения целиком (от самых старых тем до недавних, наши локальные мемы, "
            "интересы, обсуждения или просто крутые штуки, которые подходят нашему вайбу).\n"
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
    wrong_letters = [ch for ch in guessed if ch not in word]
    wrong_str = ", ".join(wrong_letters) if wrong_letters else "нет"
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
        f"💬 {game['status']}"
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
        'errors': 0,
        'oracle_note': oracle_note,
        'status': "Жми на буквы ниже или напиши слово целиком в чат!",
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
        if biz_id:
            BOT_GPT_MESSAGES.add(sent.message_id)

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
    sent = bot.send_message(cid, text, business_connection_id=biz_id, reply_markup=kb)
    if biz_id and sent:
        BOT_GPT_MESSAGES.add(sent.message_id)

# =====================================================================
# 🎛 6. ОБРАБОТЧИКИ КОМАНД И КНОПОК ДЛЯ ВСЕХ ИГР, КОМАНД И БИО-ВОЙН
# =====================================================================
GAME_TRIGGERS = {
    'лаба', 'моя лаба', 'био', 'биовойны', 'заразить', 'вакцина', 'лечиться',
    '+патоген', '+разработка', '+заразность', '+иммунитет', '+летальность', '+безопасность',
    'виселица', 'виселица ии', 'поле чудес', 'кнб', 'цуефа', 'рпг', 'профиль', 'топ', 'био топ', 'игры',
    'команды', 'помощь', 'хелп', '/help'
}

def is_game_command(text):
    t = (text or '').lower().strip()
    if t in GAME_TRIGGERS:
        return True
    if t.startswith('заразить ') or t.startswith('виселица '):
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
    p = get_player(user)

    # Попытка угадать слово целиком в активной Виселице
    if cid in HANGMAN_GAMES and not HANGMAN_GAMES[cid].get('finished'):
        clean_w = low.upper().replace('Ё', 'Е')
        if clean_w == HANGMAN_GAMES[cid]['word']:
            game = HANGMAN_GAMES[cid]
            game['guessed'].update(list(game['word']))
            game['finished'] = True
            reward_res = 60 if game['is_ai'] else 40
            reward_xp = 35 if game['is_ai'] else 25
            p['bio_res'] += reward_res
            p['xp'] += reward_xp
            p['wins_hangman'] = p.get('wins_hangman', 0) + 1
            buff_str = try_grant_rare_buff(p) or ""
            save_rpg_firebase()
            u_name = html.escape(p['name'])
            game['status'] = f"🏆 <b>{u_name} назвал(а) слово целиком!</b> (+{reward_res} 🧬, +{reward_xp} XP){buff_str}"
            text, kb = build_hangman_view(game)
            safe_edit_message(text, cid, game['msg_id'], business_id=biz_id, reply_markup=kb)
            return

    if not is_game_command(low):
        return

    # Вывод списка команд (в бизнес-чате — чат-команды, в ЛС бота владельцу — полный список)
    if low in ('команды', 'помощь', 'хелп', '/help'):
        is_owner_private = (not biz_id and (user.username or '').lower() == CREATOR_USERNAME)
        help_txt = get_updated_full_commands_text() if is_owner_private else get_chat_commands_text()
        sent = bot.send_message(cid, help_txt, business_connection_id=biz_id)
        if biz_id and sent:
            BOT_GPT_MESSAGES.add(sent.message_id)
        return

    # Меню всех игр и РПГ-профиль
    if low in ('игры', 'рпг', 'профиль'):
        lvl, rank_title = get_level_and_rank(p.get('xp', 0))
        buff_list = [f"• {RARE_BUFFS_INFO[b]['name']} — <i>{RARE_BUFFS_INFO[b]['desc']}</i>" for b in p.get('buffs', []) if b in RARE_BUFFS_INFO]
        buff_block = "\n".join(buff_list) if buff_list else "<i>Пока нет редких баффов (выпадают за победы в играх с шансом 12%)</i>"
        u_name = html.escape(p['name'])
        msg_text = (
            f"🎮 <b>Игровой профиль: {u_name}</b>\n"
            f"🎖 <b>Уровень:</b> {lvl} ({rank_title}) | ✨ <b>Опыт:</b> {p.get('xp', 0)} XP\n"
            f"🧬 <b>Био-ресурсы:</b> {p.get('bio_res', 0)} | 🦠 <b>Заражений:</b> {p.get('infections_done', 0)}\n"
            f"🎡 <b>Побед в Виселице:</b> {p.get('wins_hangman', 0)} | ✂️ <b>Побед в КНБ:</b> {p.get('wins_rps', 0)}\n\n"
            f"🔮 <b>Твои редкие артефакты/баффы:</b>\n{buff_block}\n\n"
            f"🕹 <b>Доступные игры и команды:</b>\n"
            f"• <code>лаба</code> (или <code>моя лаба</code>) — твоя Био-Лаборатория (как в Ирисе)\n"
            f"• <code>заразить</code> (в чате или <code>заразить @юзер</code>) — пустить патоген\n"
            f"• <code>виселица</code> — классическое Поле Чудес на кнопках\n"
            f"• <code>виселица ии</code> — ГПТ загадывает слово по контексту вашего чата!\n"
            f"• <code>кнб</code> — Камень-Ножницы-Бумага (1 на 1 или с ботом)\n"
            f"• <code>био топ</code> — таблица лидеров\n"
            f"• <code>команды</code> — все команды чата"
        )
        sent = bot.send_message(cid, msg_text, business_connection_id=biz_id)
        if biz_id and sent:
            BOT_GPT_MESSAGES.add(sent.message_id)
        return

    # Топ игроков
    if low in ('топ', 'био топ'):
        sorted_p = sorted(RPG_PLAYERS.values(), key=lambda x: (x.get('xp', 0) + x.get('bio_xp', 0)), reverse=True)[:10]
        lines = ["🏆 <b>Глобальный Топ Био-войн и Игр:</b>\n"]
        for idx, pl in enumerate(sorted_p, 1):
            lvl, _ = get_level_and_rank(pl.get('xp', 0))
            p_name = html.escape(pl.get('name', 'Игрок'))
            lines.append(
                f"{idx}. <b>{p_name}</b> — Ур. {lvl} | 🦠 Заражений: {pl.get('infections_done', 0)} | 🧬 {pl.get('bio_res', 0)}"
            )
        sent = bot.send_message(cid, "\n".join(lines), business_connection_id=biz_id)
        if biz_id and sent:
            BOT_GPT_MESSAGES.add(sent.message_id)
        return

    # Лаборатория Ириса
    if low in ('лаба', 'моя лаба', 'био', 'биовойны'):
        text, kb = build_lab_view(p)
        sent = bot.send_message(cid, text, business_connection_id=biz_id, reply_markup=kb)
        if biz_id and sent:
            BOT_GPT_MESSAGES.add(sent.message_id)
        return

    # Текстовая прокачка навыков (+заразность, +иммунитет и т.д.)
    cmd_map = {
        '+патоген': 'patogen', '+разработка': 'dev', '+заразность': 'infect',
        '+иммунитет': 'immune', '+летальность': 'lethal', '+безопасность': 'security'
    }
    if low in cmd_map:
        upg_key = cmd_map[low]
        cost = get_upgrade_cost(p, upg_key)
        field, label, _ = UPGRADE_TYPES[upg_key]
        if p.get('bio_res', 0) < cost:
            bot.send_message(cid, f"⚠️ Не хватает био-ресурсов! Нужно <b>{cost} 🧬</b> (у тебя {p.get('bio_res', 0)} 🧬).", business_connection_id=biz_id)
            return
        p['bio_res'] -= cost
        p[field] = p.get(field, 1) + 1
        p['xp'] = p.get('xp', 0) + 15
        save_rpg_firebase()
        bot.send_message(cid, f"✅ Улучшено: <b>{label}</b> ➔ Уровень {p[field]}!", business_connection_id=biz_id)
        return

    # Вакцина / Лечение
    if low in ('вакцина', 'лечиться'):
        if p.get('fever_until', 0) <= time.time():
            bot.send_message(cid, "🟢 Ты полностью здоров, вакцина не требуется!", business_connection_id=biz_id)
            return
        if p.get('bio_res', 0) < 80:
            bot.send_message(cid, "⚠️ Вакцина стоит <b>80 🧬</b>. Заработай ресурсы в <code>виселица</code> или <code>кнб</code>!", business_connection_id=biz_id)
            return
        p['bio_res'] -= 80
        p['fever_until'] = 0
        p['infected_by'] = ''
        save_rpg_firebase()
        bot.send_message(cid, "💉 <b>Вакцина введена!</b> Горячка снята, ты снова можешь заражать людей!", business_connection_id=biz_id)
        return

    # Команда «заразить»
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
        sent = bot.send_message(cid, res_text, business_connection_id=biz_id)
        if biz_id and sent:
            BOT_GPT_MESSAGES.add(sent.message_id)
        return

    # Запуск Виселицы (обычной или ИИ)
    if low in ('виселица', 'поле чудес', 'виселица ии'):
        is_ai = ('ии' in low)
        if is_ai:
            wait_msg = bot.send_message(cid, "🧠 <i>ГПТ вспоминает историю вашего чата и загадывает слово...</i>", business_connection_id=biz_id)
            start_hangman_game(cid, biz_id, user, is_ai=True, msg_id_to_edit=wait_msg.message_id)
        else:
            start_hangman_game(cid, biz_id, user, is_ai=False)
        return

    # Запуск Камень-Ножницы-Бумага
    if low in ('кнб', 'цуефа'):
        send_rps_menu(cid, biz_id=biz_id)
        return

@bot.callback_query_handler(func=lambda call: call.data and (
    call.data.startswith('bioupg_') or call.data.startswith('biocure_') or
    call.data.startswith('biorefresh_') or call.data.startswith('hm_') or
    call.data.startswith('rps_') or call.data == 'panel_img_models'
))
def handle_games_callbacks(call):
    register_creator_id(call.from_user)
    data = call.data
    user = call.from_user
    p = get_player(user)
    cid = int(call.message.chat.id)
    msg_id = call.message.message_id
    biz_id = getattr(call.message, 'business_connection_id', None)

    if data == 'panel_img_models':
        if (user.username or '').lower() != CREATOR_USERNAME:
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
        if p.get('bio_res', 0) < 80:
            bot.answer_callback_query(call.id, "⚠️ Нужно 80 био-ресурсов для вакцины!", show_alert=True)
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
            bot.answer_callback_query(call.id, f"⚠️ Не хватает ресурсов! Нужно {cost} 🧬.", show_alert=True)
            return
        p['bio_res'] -= cost
        p[field] = p.get(field, 1) + 1
        p['xp'] = p.get('xp', 0) + 15
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
            if letter in game['guessed']:
                bot.answer_callback_query(call.id, "Эта буква уже проверена!")
                return
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
                    game['status'] = f"✅ <b>{u_name}</b> открыл(а) букву «<b>{letter}</b>»!"
            else:
                game['errors'] += 1
                if game['errors'] >= 6:
                    game['finished'] = True
                    game['guessed'].update(list(game['word']))
                    game['status'] = f"💀 <b>Повешен!</b> Загаданное слово было: <b>{game['word']}</b>"
                else:
                    game['status'] = f"❌ Буквы «<b>{letter}</b>» нет в слове! (Ходил: {u_name})"

            text, kb = build_hangman_view(game)
            safe_edit_message(text, cid, msg_id, business_id=biz_id, reply_markup=kb)
            bot.answer_callback_query(call.id)
            return

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

@bot.business_message_handler(func=lambda m: is_game_command(m.text) or (int(m.chat.id) in HANGMAN_GAMES and not HANGMAN_GAMES[int(m.chat.id)].get('finished') and len((m.text or '').strip()) == len(HANGMAN_GAMES[int(m.chat.id)]['word'])))
def _biz_game_router(message):
    handle_all_game_commands(message)

@bot.message_handler(func=lambda m: m.chat.type == 'private' and (is_game_command(m.text) or (int(m.chat.id) in HANGMAN_GAMES and not HANGMAN_GAMES[int(m.chat.id)].get('finished') and len((m.text or '').strip()) == len(HANGMAN_GAMES[int(m.chat.id)]['word']))))
def _priv_game_router(message):
    handle_all_game_commands(message)

# Ставим игровые обработчики первыми в очередь
bot.callback_query_handlers.insert(0, bot.callback_query_handlers.pop())
bot.business_message_handlers.insert(0, bot.business_message_handlers.pop())
bot.message_handlers.insert(0, bot.message_handlers.pop())
