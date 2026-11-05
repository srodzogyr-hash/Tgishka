#!/usr/bin/env python3
"""
Antigravity Bridge for Tgishka Telegram Bot
Integrates Antigravity Agent Engine (agy) with Role-Based Access Control:
 - Command 'аг' / 'ag'
 - Full chat context injection from personal Telegram conversations
 - Code modification & system control for Creator / Devs
 - Strict Guardrails for regular authorized users (no code changes, no code/secret leaks)
 - Permissions management: 'аг+ @user', 'аг+код @user', 'аг- @user', 'аг список'
"""

import os
import sys
import json
import time
import html
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
    """
    user: The user performing the action (must be creator)
    target_str: '@username' or 'username'
    role: 'user' or 'dev'
    """
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

    role_desc = "🛠️ <b>Разработчик</b> (изменение кода и управление ботом)" if role == "dev" else "💬 <b>Обычный ИИ</b> (только общение, без доступа к коду)"
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

# ---------------------------------------------------------------------------
# CONTEXT INJECTION & PROMPT BUILDER
# ---------------------------------------------------------------------------
def build_antigravity_prompt(chat_id, user, user_prompt, chat_log=None, chat_memory=None):
    """
    Builds the augmented prompt with full chat context and role-based guardrails.
    """
    role = get_user_role(user)
    is_dev = (role == "dev")
    uname = user.username or f"id_{user.id}"
    full_name = f"{user.first_name or ''} {user.last_name or ''}".strip() or uname

    # Build context lines
    context_lines = []
    if chat_log and isinstance(chat_log, list):
        context_lines = [line for line in chat_log if line]
    elif chat_memory and isinstance(chat_memory, list):
        for m in chat_memory:
            content = m.get("content", "")
            role_tag = "Собеседник" if m.get("role") == "user" else "ИИ"
            context_lines.append(f"{role_tag}: {content}")

    context_str = "\n".join(context_lines[-50:]) if context_lines else "История чата пуста."

    # Guardrails based on role
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

    full_payload = (
        f"{instructions}\n\n"
        f"--- ИСТОРИЯ ПЕРЕПИСКИ В ЭТОМ ЛИЧНОМ ЧАТЕ TELEGRAM ---\n"
        f"{context_str}\n"
        f"--- КОНЕЦ ИСТОРИИ ЧАТА ---\n\n"
        f"ТЕКУЩЕЕ СООБЩЕНИЕ ОТ {full_name} (@{uname}):\n"
        f"{user_prompt}"
    )

    return full_payload

# ---------------------------------------------------------------------------
# AGY EXECUTION ENGINE WITH AUTO-RETRY
# ---------------------------------------------------------------------------
def ask_antigravity(chat_id, user, user_prompt, chat_log=None, chat_memory=None, media_list=None):
    """
    Executes the prompt via 'agy -c -p ...' with automatic retries for VPN/geo flaps.
    Returns: (reply_text, error_message, model_used)
    """
    role = get_user_role(user)
    if not role:
        return None, "⛔ У вас нет доступа к вызову Antigravity. Попросите владельца выдать доступ: `аг+ @ваш_юзер`", "Access Denied"

    augmented_prompt = build_antigravity_prompt(chat_id, user, user_prompt, chat_log, chat_memory)

    # If user provided image, mention path or context
    if media_list:
        augmented_prompt += f"\n\n[Пользователь также прикрепил {len(media_list)} медиа-файлов/изображений к запросу]"

    # Check if agy executable exists
    actual_agy = AGY_PATH
    if not os.path.exists(actual_agy):
        # Try finding in PATH
        which_agy = shutil_which("agy")
        if which_agy:
            actual_agy = which_agy
        else:
            return None, "⚠️ Бинарник Antigravity CLI ('agy') не найден в окружении хоста.", "Error"

    cmd = [
        actual_agy,
        "-c",
        "-p", augmented_prompt,
        "--dangerously-skip-permissions"
    ]

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
                last_err = "Eligibility check failed (гео-блокировка VPN)"
                if attempt < max_retries:
                    time.sleep(1.5)
                    continue

            # Strip noisy Android/Google init logs
            lines = []
            for line in raw.splitlines():
                if "ERROR: logging before google.Init" in line:
                    continue
                lines.append(line)
            cleaned_output = "\n".join(lines).strip()

            if cleaned_output and "Eligibility check failed" not in cleaned_output:
                break

        except subprocess.TimeoutExpired:
            last_err = "Таймаут ожидания ответа Antigravity (более 3 мин.)"
        except Exception as e:
            last_err = str(e)

    if not cleaned_output:
        if "Eligibility" in last_err:
            return None, "⚠️ Google временно отклонил IP (сбой геолокации VPN). Переподключите VPN на сервере/телефоне.", "Antigravity/GeoBlock"
        return None, f"Ошибка вызова Antigravity: {last_err or 'Пустой ответ'}", "Antigravity/Error"

    return cleaned_output, None, "Antigravity (Gemini 3.8 / Code Agent)"

def shutil_which(cmd):
    import shutil
    return shutil.which(cmd)

# ---------------------------------------------------------------------------
# TELEGRAM FORMATTING & CHUNKING
# ---------------------------------------------------------------------------
def format_for_telegram(text):
    """
    Converts markdown code blocks to clean Telegram-compatible HTML tags.
    """
    if not text:
        return ""
    
    # We do a safe replacement of ```code``` -> <pre><code>code</code></pre>
    def code_replacer(match):
        lang = match.group(1) or ""
        code_content = html.escape(match.group(2))
        return f"\n<pre><code>{code_content}</code></pre>\n"

    # Replace fenced code blocks
    import re
    res = re.sub(r'```([a-zA-Z0-9_-]*)\n([\s\S]*?)```', code_replacer, text)
    
    # Inline code `code`
    res = re.sub(r'`([^`\n]+)`', lambda m: f"<code>{html.escape(m.group(1))}</code>", res)

    return res

def split_message_chunks(text, max_len=4000):
    """
    Splits long messages by line/paragraph to respect Telegram's 4096 char limit.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    lines = text.split("\n")
    current = ""

    for line in lines:
        if len(current) + len(line) + 1 > max_len:
            if current:
                chunks.append(current)
                current = ""
            if len(line) > max_len:
                # hard slice if single line exceeds limit
                for i in range(0, len(line), max_len):
                    chunks.append(line[i:i+max_len])
            else:
                current = line
        else:
            current = (current + "\n" + line) if current else line

    if current:
        chunks.append(current)

    return chunks
