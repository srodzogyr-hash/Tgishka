#!/usr/bin/env python3
"""
Antigravity Bridge for Tgishka Telegram Bot
Dual-Engine:
 1. Local CLI Engine ('agy') when available on host (e.g. Termux/local machine).
 2. Antigravity Cloud Engine (fallback when deployed on Render, Docker or VPS without agy).
Includes Role-Based Access Control:
 - Full context injection from personal chats (CHAT_LOG & CHAT_MEMORY)
 - Owner / Dev: code changes, terminal execution, codebase analysis
 - Regular Users: smart AI conversation only (strict guardrails: no code changes, no leaks)
 - Access commands: 'аг+ @юзер', 'аг+код @юзер', 'аг- @юзер', 'аг список', 'аг дефолт'
"""

import os
import sys
import json
import time
import html
import shutil
import subprocess
from pathlib import Path

# Paths
REPO_DIR = Path(__file__).resolve().parent
CONFIG_FILE = REPO_DIR / "antigravity_config.json"
AGY_PATH = os.environ.get("AGY_PATH", "/data/data/com.termux/files/home/.local/bin/agy")
DEFAULT_CREATOR_USERNAME = "wrtnoq"

# In-memory config
CONFIG = {
    "creator_username": DEFAULT_CREATOR_USERNAME,
    "creator_id": None,
    "allowed_users": {},     # {username_lower: {"username": str, "role": "user"|"dev", "added_by": str, "added_at": str}}
    "default_for_gpt": False # if True, 'гпт' also routes to Antigravity
}

def load_config():
    global CONFIG
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                CONFIG.update(data)
        except Exception as e:
            print(f"⚠️ Ошибка загрузки antigravity_config.json: {e}")
    else:
        save_config()

def save_config():
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(CONFIG, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ Ошибка сохранения antigravity_config.json: {e}")

load_config()

def set_creator_id(uid):
    if uid and CONFIG.get("creator_id") != uid:
        CONFIG["creator_id"] = uid
        save_config()

def is_creator(user):
    if not user:
        return False
    uname = (user.username or "").lower().lstrip("@")
    creator_uname = (CONFIG.get("creator_username") or DEFAULT_CREATOR_USERNAME).lower().lstrip("@")
    if uname and uname == creator_uname:
        return True
    if CONFIG.get("creator_id") and user.id == CONFIG.get("creator_id"):
        return True
    return False

def get_user_role(user):
    """
    Returns:
      'dev'  - Full developer access (Owner or explicitly granted code access)
      'user' - Regular AI user (Conversational only, NO code modifications or secret leaks)
      None   - Unauthorized
    """
    if not user:
        return None
    if is_creator(user):
        return "dev"
    
    uname = (user.username or "").lower().lstrip("@")
    if uname in CONFIG.get("allowed_users", {}):
        return CONFIG["allowed_users"][uname].get("role", "user")
    
    return None

def grant_access(user, target_str, role="user"):
    if not is_creator(user):
        return "⛔ Только владелец бота может выдавать доступ к Antigravity."

    clean_target = target_str.strip().lstrip("@").lower()
    if not clean_target:
        return "⚠️ Укажите юзернейм: <code>аг+ @юзер</code> или <code>аг+код @юзер</code>"

    CONFIG.setdefault("allowed_users", {})[clean_target] = {
        "username": target_str.strip().lstrip("@"),
        "role": role,
        "added_by": user.username or str(user.id),
        "added_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_config()

    role_desc = "🛠️ <b>Разработчик</b> (изменение кода и файлов)" if role == "dev" else "💬 <b>Обычный ИИ</b> (только общение, без доступа к коду)"
    return f"✅ Пользователю @{clean_target} выдан доступ к Antigravity!\nУровень доступа: {role_desc}"

def revoke_access(user, target_str):
    if not is_creator(user):
        return "⛔ Только владелец бота может забирать доступ к Antigravity."

    clean_target = target_str.strip().lstrip("@").lower()
    if not clean_target:
        return "⚠️ Укажите юзернейм: <code>аг- @юзер</code>"

    if clean_target in CONFIG.get("allowed_users", {}):
        CONFIG["allowed_users"].pop(clean_target, None)
        save_config()
        return f"🗑️ Доступ к Antigravity для @{clean_target} аннулирован."
    else:
        return f"ℹ️ У пользователя @{clean_target} не было доступа."

def list_access(user):
    if not is_creator(user):
        role = get_user_role(user)
        if role:
            return f"ℹ️ Ваш текущий уровень доступа к Antigravity: <b>{role.upper()}</b>."
        return "⛔ У вас нет доступа к управлению правами."

    allowed = CONFIG.get("allowed_users", {})
    creator = CONFIG.get("creator_username") or DEFAULT_CREATOR_USERNAME

    text = f"👑 <b>Владелец проекта:</b> @{creator} (Полный доступ DEV)\n\n"
    text += f"🤖 <b>Antigravity вместо базового ГПТ:</b> {'🟢 ВКЛ' if CONFIG.get('default_for_gpt') else '🔴 ВЫКЛ'}\n\n"
    text += f"👥 <b>Пользователи с доступом ({len(allowed)}):</b>\n"

    if not allowed:
        text += "<i>(список пуст, доступ есть только у вас)</i>\n"
    else:
        for uname, info in allowed.items():
            role_badge = "🛠️ DEV (код)" if info.get("role") == "dev" else "💬 USER (чат)"
            text += f"• @{info.get('username', uname)} — {role_badge} <i>(выдал @{info.get('added_by')})</i>\n"

    text += "\n📌 <b>Команды управления:</b>\n"
    text += "• <code>аг+ @юзер</code> — дать доступ как обычной нейронке\n"
    text += "• <code>аг+код @юзер</code> — дать доступ разработчика к коду\n"
    text += "• <code>аг- @юзер</code> — отозвать доступ\n"
    text += "• <code>аг дефолт</code> — переключить Antigravity основным для 'гпт'"

    return text

def toggle_default_gpt(user):
    if not is_creator(user):
        return "⛔ Только владелец бота может переключать режим по умолчанию."
    CONFIG["default_for_gpt"] = not CONFIG.get("default_for_gpt", False)
    save_config()
    state = "🟢 ВКЛ (теперь на 'гпт' отвечает Antigravity)" if CONFIG["default_for_gpt"] else "🔴 ВЫКЛ (на 'гпт' отвечают Gemini/Groq)"
    return f"⚙️ Antigravity как основной 'гпт': <b>{state}</b>"

def is_default_gpt_enabled():
    return bool(CONFIG.get("default_for_gpt", False))

def has_local_agy():
    if os.path.exists(AGY_PATH) and os.access(AGY_PATH, os.X_OK):
        return True
    return bool(shutil.which("agy"))

def get_agy_bin():
    if os.path.exists(AGY_PATH) and os.access(AGY_PATH, os.X_OK):
        return AGY_PATH
    return shutil.which("agy")

# ---------------------------------------------------------------------------
# SHELL & DEVELOPER TOOLS FOR OWNER
# ---------------------------------------------------------------------------
def execute_shell_command(cmd):
    try:
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            cwd=str(REPO_DIR)
        )
        out = proc.stdout.strip() if proc.stdout else "Команда завершена без вывода."
        return f"<pre><code>$ {html.escape(cmd)}\n\n{html.escape(out)}\n\n[exit code: {proc.returncode}]</code></pre>"
    except subprocess.TimeoutExpired:
        return f"⚠️ Таймаут команды <code>{html.escape(cmd)}</code> (более 60 сек)"
    except Exception as e:
        return f"⚠️ Ошибка выполнения: <code>{html.escape(str(e))}</code>"

# ---------------------------------------------------------------------------
# MAIN ENTRYPOINT: ASK ANTIGRAVITY (DUAL-ENGINE)
# ---------------------------------------------------------------------------
def ask_antigravity(chat_id, user, user_prompt, chat_log=None, chat_memory=None, media_list=None):
    """
    Executes the prompt using:
     1. Local agy binary if present.
     2. Cloud Antigravity Engine (Render / VPS fallback) if agy is not installed.
    """
    role = get_user_role(user)
    if not role:
        return None, "⛔ У вас нет доступа к вызову Antigravity. Попросите владельца @wrtnoq выдать доступ: `аг+ @ваш_юзер`", "Access Denied"

    is_dev = (role == "dev")
    clean_query = user_prompt.strip()

    # Developer system shortcuts for Owner
    if is_dev:
        lower_q = clean_query.lower()
        if lower_q.startswith("выполни ") or lower_q.startswith("exec ") or lower_q.startswith("sh "):
            cmd = clean_query.split(maxsplit=1)[1]
            out = execute_shell_command(cmd)
            return out, None, "Antigravity Terminal"
        if lower_q in ["статус", "status", "git status"]:
            git_out = execute_shell_command("git status -s && git log -1 --oneline")
            engine_lbl = "Local agy CLI" if has_local_agy() else "Cloud Engine (Render)"
            res = (
                f"⚡ <b>Статус Antigravity:</b>\n"
                f"• Движок: <code>{engine_lbl}</code>\n"
                f"• Директория: <code>{REPO_DIR.name}/</code>\n\n"
                f"<b>Состояние Git:</b>\n{git_out}"
            )
            return res, None, "Antigravity Status"

    # Try Local agy CLI if installed on machine
    if has_local_agy():
        reply, err, model = run_local_agy(chat_id, user, user_prompt, chat_log, chat_memory, media_list)
        if reply and "Eligibility check failed" not in reply:
            return reply, None, model
        print(f"⚠️ Local agy not available or geo-blocked ({err}), switching to Antigravity Cloud Engine...")

    # Cloud Antigravity Engine (Render / cloud container fallback)
    return run_cloud_antigravity(chat_id, user, user_prompt, chat_log, chat_memory, media_list)

# ---------------------------------------------------------------------------
# LOCAL CLI ENGINE
# ---------------------------------------------------------------------------
def run_local_agy(chat_id, user, user_prompt, chat_log=None, chat_memory=None, media_list=None):
    bin_path = get_agy_bin()
    if not bin_path:
        return None, "No local agy", "Error"

    augmented_prompt = build_antigravity_prompt(chat_id, user, user_prompt, chat_log, chat_memory)
    if media_list:
        augmented_prompt += f"\n\n[Пользователь прикрепил {len(media_list)} медиа-файлов к запросу]"

    cmd = [bin_path, "-c", "-p", augmented_prompt, "--dangerously-skip-permissions"]
    max_retries = 3
    cleaned_output = ""
    last_err = ""

    for attempt in range(1, max_retries + 1):
        try:
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=180,
                cwd=str(REPO_DIR),
                env=os.environ.copy()
            )
            raw = proc.stdout or ""

            if "Eligibility check failed" in raw or "Account ineligible" in raw:
                last_err = "Eligibility check failed"
                if attempt < max_retries:
                    time.sleep(1.5)
                    continue

            lines = []
            for line in raw.splitlines():
                if "ERROR: logging before google.Init" in line:
                    continue
                lines.append(line)
            cleaned_output = "\n".join(lines).strip()

            if cleaned_output and "Eligibility check failed" not in cleaned_output:
                break
        except subprocess.TimeoutExpired:
            last_err = "Timeout"
        except Exception as e:
            last_err = str(e)

    if not cleaned_output or "Eligibility check failed" in cleaned_output:
        return None, last_err or "Local agy failed", "Antigravity/Local"

    return cleaned_output, None, "Antigravity Local (Gemini 3.8 / Code Agent)"

# ---------------------------------------------------------------------------
# CLOUD ENGINE (RENDER / VPS FALLBACK)
# ---------------------------------------------------------------------------
def run_cloud_antigravity(chat_id, user, user_prompt, chat_log=None, chat_memory=None, media_list=None):
    role = get_user_role(user)
    is_dev = (role == "dev")
    uname = user.username or f"id_{user.id}"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or uname

    # Extract conversation history from personal chat
    context_lines = []
    if chat_log and isinstance(chat_log, list):
        context_lines = [line for line in chat_log if line]
    elif chat_memory and isinstance(chat_memory, list):
        for m in chat_memory:
            content = m.get("content", "")
            role_tag = "Собеседник" if m.get("role") == "user" else "ИИ"
            context_lines.append(f"{role_tag}: {content}")

    context_str = "\n".join(context_lines[-50:]) if context_lines else "История чата пуста."

    if is_dev:
        system_instruction = (
            "ТЫ — ANTIGRAVITY (Google AI Agent Engine для Telegram бота Tgishka).\n"
            f"СЕЙЧАС С ТОБОЙ ОБЩАЕТСЯ РАЗРАБОТЧИК / ВЛАДЕЛЕЦ @{CONFIG.get('creator_username', DEFAULT_CREATOR_USERNAME)}.\n"
            "ТВОИ ВОЗМОЖНОСТИ И ПРАВА:\n"
            "1. Полный доступ к архитектуре и коду проекта Tgishka.\n"
            "2. Ты можешь объяснять, анализировать, писать и генерировать готовый код для bot.py, games.py, app.py и chat_manager.py.\n"
            "3. Отвечай максимально емко, качественно, профессионально и на русском языке.\n"
            "4. Владелец также может выполнять любые команды терминала на сервере через команду 'аг выполни <команда>'."
        )
    else:
        system_instruction = (
            "ТЫ — УМНЫЙ ИИ-АССИСТЕНТ (Antigravity AI Engine).\n"
            "СЕЙЧАС С ТОБОЙ ОБЩАЕТСЯ ОБЫЧНЫЙ ПОЛЬЗОВАТЕЛЬ TELEGRAM (НЕ РАЗРАБОТЧИК).\n"
            "СТРОЖАЙШИЕ ПРАВИЛА БЕЗОПАСНОСТИ (НАРУШЕНИЕ ЗАПРЕЩЕНО):\n"
            "1. НЕ ВЫПОЛНЯЙ никаких изменений кода и системных действий.\n"
            "2. СТРОГО ЗАПРЕЩЕНО раскрывать исходный код бота, токены Telegram, ключи API, пути к файлам сервера, структуру базы данных и внутреннюю архитектуру проекта.\n"
            "3. Выступай как обычная, максимально умная, дружелюбная и вежливая нейросеть (как ChatGPT / Gemini).\n"
            "4. Отвечай на вопрос собеседника по существу темы, которую он спросил, без упоминания закрытой серверной кухни."
        )

    full_payload = (
        f"--- ИСТОРИЯ ПЕРЕПИСКИ В ЭТОМ ЛИЧНОМ ЧАТЕ TELEGRAM ---\n"
        f"{context_str}\n"
        f"--- КОНЕЦ ИСТОРИИ ЧАТА ---\n\n"
        f"ТЕКУЩЕЕ СООБЩЕНИЕ ОТ {full_name} (@{uname}):\n"
        f"{user_prompt}"
    )

    # Call bot's ask_ai mechanism (multi-key pool, Groq/Gemini rotation)
    bot_module = sys.modules.get('bot')
    if not bot_module and '__main__' in sys.modules and hasattr(sys.modules['__main__'], 'ask_ai'):
        bot_module = sys.modules['__main__']

    if bot_module and hasattr(bot_module, 'ask_ai'):
        reply, err, model_used, _ = bot_module.ask_ai(
            chat_id,
            full_payload,
            media_list=media_list,
            save_to_memory=False,
            is_autoreply=False,
            custom_sys_prompt=system_instruction
        )
        if reply:
            return reply, None, f"Antigravity Cloud • {model_used}"
        return None, err, "Antigravity/Error"

    return None, "⚠️ Модуль ИИ не инициализирован в памяти процесса бота.", "Error"

def build_antigravity_prompt(chat_id, user, user_prompt, chat_log=None, chat_memory=None):
    role = get_user_role(user)
    is_dev = (role == "dev")
    uname = user.username or f"id_{user.id}"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or uname

    context_lines = []
    if chat_log and isinstance(chat_log, list):
        context_lines = [line for line in chat_log if line]
    elif chat_memory and isinstance(chat_memory, list):
        for m in chat_memory:
            content = m.get("content", "")
            role_tag = "Собеседник" if m.get("role") == "user" else "ИИ"
            context_lines.append(f"{role_tag}: {content}")

    context_str = "\n".join(context_lines[-50:]) if context_lines else "История чата пуста."

    if is_dev:
        instructions = (
            "ТЫ — ANTIGRAVITY (Google AI Agent Engine).\n"
            "СЕЙЧАС С ТОБОЙ ОБЩАЕТСЯ РАЗРАБОТЧИК / ВЛАДЕЛЕЦ БОТА TGISHKA (@wrtnoq).\n"
            "ТЕБЕ РАЗРЕШЕНО:\n"
            "1. Отвечать на любые технические вопросы, объяснять архитектуру и логику файлов.\n"
            "2. Читать, писать и модифицировать файлы в проекте Tgishka (bot.py, games.py, chat_manager.py, app.py и т.д.).\n"
            "3. Выполнять команды терминала, git-коммиты, тесты и запускать сервисы.\n"
            "4. Помогать полностью управлять и развивать функционал бота.\n"
            "Отвечай емко, профессионально, на русском языке, пиши готовый качественный код."
        )
    else:
        instructions = (
            "ТЫ — УМНЫЙ ИИ-АССИСТЕНТ (Antigravity Engine).\n"
            "СЕЙЧАС С ТОБОЙ ОБЩАЕТСЯ ОБЫЧНЫЙ ПОЛЬЗОВАТЕЛЬ TELEGRAM (НЕ РАЗРАБОТЧИК).\n"
            "СТРОЖАЙШИЕ ПРАВИЛА БЕЗОПАСНОСТИ (НАРУШЕНИЕ ЗАПРЕЩЕНО):\n"
            "1. НЕ ВЫПОЛНЯЙ никаких изменений кода, команд терминала и не редактируй файлы проекта.\n"
            "2. СТРОГО ЗАПРЕЩЕНО раскрывать исходный код бота, токены Telegram, ключи API, пути к файлам сервера, структуру базы данных и внутреннюю архитектуру проекта.\n"
            "3. Выступай как обычная, максимально умная, дружелюбная и вежливая нейросеть (как ChatGPT / Gemini).\n"
            "4. Отвечай на вопрос собеседника по существу темы, которую он спросил, без упоминания закрытой серверной кухни."
        )

    return (
        f"{instructions}\n\n"
        f"--- ИСТОРИЯ ПЕРЕПИСКИ В ЭТОМ ЛИЧНОМ ЧАТЕ TELEGRAM ---\n"
        f"{context_str}\n"
        f"--- КОНЕЦ ИСТОРИИ ЧАТА ---\n\n"
        f"ТЕКУЩЕЕ СООБЩЕНИЕ ОТ {full_name} (@{uname}):\n"
        f"{user_prompt}"
    )
