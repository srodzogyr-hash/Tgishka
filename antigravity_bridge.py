#!/usr/bin/env python3
"""
Antigravity Bridge for Tgishka Telegram Bot
Dual-Engine & Queue Architecture:
 1. Local CLI Engine ('agy') when available on host (e.g. Termux on Android).
 2. Firebase Queue Bridge (Render / Cloud container fallback) connected to Termux Worker.
Strict Requirements:
 - Zero consumption of user's GEMINI_KEYS pool.
 - Strict Chat Isolation: context fetched strictly for the requested chat_id from Firebase RTDB.
 - Interactive Cancellation: task_id tracking with abort/kill capability.
 - Reasoning & Code Visibility: separates thoughts, code execution, and final response.
 - Active Model Display: Gemini 3.8 Flash (High) [Antigravity Engine].
 - Role-Based Access Control:
     * Owner/Dev: Code modifications, terminal exec, repository inspection.
     * Regular Users: Safe conversational AI only (strict anti-leak & anti-modification guardrails).
"""

import os
import sys
import re
import json
import time
import html
import shutil
import urllib.request
import subprocess
from pathlib import Path

# Paths
REPO_DIR = Path(__file__).resolve().parent
CONFIG_FILE = REPO_DIR / "antigravity_config.json"
AGY_PATH = os.environ.get("AGY_PATH", "/data/data/com.termux/files/home/.local/bin/agy")
DEFAULT_CREATOR_USERNAME = "wrtnoq"
DEFAULT_FIREBASE_URL = "https://tgbotik-d479c-default-rtdb.firebaseio.com/secret_845891"

# Active tasks registry: {task_id: {"user_id": int, "chat_id": int, "proc": subprocess.Popen, "status": str, "start_time": float}}
ACTIVE_TASKS = {}

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
    uname = (getattr(user, "username", "") or "").lower().lstrip("@")
    creator_uname = (CONFIG.get("creator_username") or DEFAULT_CREATOR_USERNAME).lower().lstrip("@")
    if uname and uname == creator_uname:
        return True
    if CONFIG.get("creator_id") and getattr(user, "id", None) == CONFIG.get("creator_id"):
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
    
    uname = (getattr(user, "username", "") or "").lower().lstrip("@")
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
        "added_by": getattr(user, "username", "") or str(getattr(user, "id", "")),
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
    state = "🟢 ВКЛ (теперь на 'гпт' отвечает Antigravity)" if CONFIG["default_for_gpt"] else "🔴 ВЫКЛ (на 'гпт' отвечают базовые модели)"
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

def get_active_model_name():
    """Reads configured model name from settings.json or returns default."""
    settings_file = Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/settings.json")
    if settings_file.exists():
        try:
            with open(settings_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("model"):
                    return f"{data['model']} [Antigravity]"
        except Exception:
            pass
    return "Gemini 3.8 Flash (High) [Antigravity]"

# ---------------------------------------------------------------------------
# TASK REGISTRATION & CANCELLATION
# ---------------------------------------------------------------------------
def register_task(task_id, user_id, chat_id, message_id=None, proc=None):
    ACTIVE_TASKS[task_id] = {
        "user_id": user_id,
        "chat_id": chat_id,
        "message_id": message_id,
        "proc": proc,
        "status": "running",
        "start_time": time.time()
    }

def unregister_task(task_id):
    ACTIVE_TASKS.pop(task_id, None)

def cancel_task(task_id, user=None, firebase_url=None):
    """
    Cancels an active task either locally (terminating process)
    or via Firebase queue (patching status to 'cancelled').
    """
    fb_url = firebase_url or os.environ.get("FIREBASE_URL") or DEFAULT_FIREBASE_URL
    task_info = ACTIVE_TASKS.get(task_id)

    # Permission check: must be owner or task creator
    if user and task_info:
        uid = getattr(user, "id", None)
        if not is_creator(user) and uid != task_info.get("user_id"):
            return False, "⛔ Только автор запроса или владелец бота может прервать ответ."

    # Cancel local subprocess if running
    if task_info:
        task_info["status"] = "cancelled"
        proc = task_info.get("proc")
        if proc:
            try:
                proc.terminate()
                proc.kill()
            except Exception:
                pass

    # Cancel in Firebase queue (for worker)
    if fb_url and task_id:
        try:
            task_url = f"{fb_url.rstrip('/')}/antigravity_queue/{task_id}.json"
            req = urllib.request.Request(
                task_url,
                data=json.dumps({"status": "cancelled"}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="PATCH"
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass

    return True, "⏹ Генерация Antigravity прервана."

# ---------------------------------------------------------------------------
# CONTEXT ISOLATION: STRICTLY PER CHAT_ID
# ---------------------------------------------------------------------------
def get_isolated_chat_context(chat_id, firebase_url=None, max_messages=40):
    """
    STRICT CHAT ISOLATION:
    Retrieves message history strictly for the requested `chat_id`.
    Never combines, touches, or leaks history from any other chat.
    """
    messages = []
    fb_url = firebase_url or os.environ.get("FIREBASE_URL") or DEFAULT_FIREBASE_URL

    # 1. Fetch from Firebase RTDB for this specific chat_id
    if fb_url:
        try:
            url = f"{fb_url.rstrip('/')}/chat_memory/{chat_id}.json"
            req = urllib.request.Request(url, headers={"User-Agent": "Antigravity/1.5"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if isinstance(data, list):
                        messages = data
        except Exception:
            pass

    # 2. In-memory fallback if bot.py is loaded in same process
    if not messages:
        bot_mod = sys.modules.get('bot')
        if bot_mod and hasattr(bot_mod, 'CHAT_MEMORY'):
            messages = bot_mod.CHAT_MEMORY.get(int(chat_id), [])

    # Format isolated dialogue lines
    formatted = []
    for m in messages[-max_messages:]:
        if isinstance(m, dict):
            role_raw = m.get("role", "user")
            content = (m.get("content") or "").strip()
            if not content:
                continue
            role_label = "Собеседник" if role_raw == "user" else "ИИ"
            formatted.append(f"{role_label}: {content}")
        elif isinstance(m, str):
            formatted.append(m)

    return formatted

def build_prompt_with_context(chat_id, user, user_prompt, firebase_url=None):
    """
    Builds the system prompt with strict isolated context and role guardrails.
    """
    role = get_user_role(user)
    is_dev = (role == "dev")
    uname = getattr(user, "username", "") or f"id_{getattr(user, 'id', '')}"
    full_name = f"{getattr(user, 'first_name', '') or ''} {getattr(user, 'last_name', '') or ''}".strip() or uname

    history_lines = get_isolated_chat_context(chat_id, firebase_url=firebase_url)
    history_text = "\n".join(history_lines) if history_lines else "История этого чата пуста."

    if is_dev:
        instructions = (
            "ТЫ — ANTIGRAVITY (Google AI Agent Engine & Pair Programmer для проекта Tgishka).\n"
            f"СЕЙЧАС С ТОБОЙ ОБЩАЕТСЯ РАЗРАБОТЧИК / ВЛАДЕЛЕЦ @{CONFIG.get('creator_username', DEFAULT_CREATOR_USERNAME)}.\n"
            "ТВОИ ВОЗМОЖНОСТИ И ПРАВА:\n"
            "1. Полный доступ к архитектуре и коду проекта (bot.py, games.py, app.py, antigravity_bridge.py, antigravity_worker.py).\n"
            "2. Ты можешь объяснять, анализировать, писать и генерировать готовый качественный код, находить баги и предлагать улучшения.\n"
            "3. Ты можешь выполнять любые команды терминала, git-коммиты и системные операции.\n"
            "4. Структурируй ответы:\n"
            "   - Если есть рассуждения, выноси их в блок '### Рассуждения'.\n"
            "   - Готовый код выноси в блок '### Готовый код'.\n"
            "   - Отвечай емко, по делу и на русском языке."
        )
    else:
        instructions = (
            "ТЫ — УМНЫЙ ИИ-АССИСТЕНТ (Antigravity AI Engine).\n"
            "СЕЙЧАС С ТОБОЙ ОБЩАЕТСЯ ОБЫЧНЫЙ ПОЛЬЗОВАТЕЛЬ TELEGRAM (НЕ РАЗРАБОТЧИК).\n"
            "СТРОЖАЙШИЕ ПРАВИЛА БЕЗОПАСНОСТИ (НАРУШЕНИЕ КАТЕГОРИЧЕСКИ ЗАПРЕЩЕНО):\n"
            "1. НЕ ВЫПОЛНЯЙ никаких изменений кода, системных команд или модификаций файлов проекта.\n"
            "2. СТРОГО ЗАПРЕЩЕНО раскрывать исходный код бота, токены Telegram, ключи API, пути к файлам сервера, структуру базы данных и внутреннюю архитектуру проекта.\n"
            "3. Выступай как обычная, максимально умная, дружелюбная и вежливая нейросеть (как ChatGPT / Gemini).\n"
            "4. Отвечай на вопрос собеседника по существу темы, которую он спросил, без упоминания закрытой серверной кухни."
        )

    return (
        f"{instructions}\n\n"
        f"--- ИЗОЛИРОВАННАЯ ИСТОРИЯ ЭТОГО ЧАТА (CHAT ID: {chat_id}) ---\n"
        f"{history_text}\n"
        f"--- КОНЕЦ ИСТОРИИ ЧАТА ---\n\n"
        f"ТЕКУЩЕЕ СООБЩЕНИЕ ОТ {full_name} (@{uname}):\n"
        f"{user_prompt}"
    )

# ---------------------------------------------------------------------------
# REASONING & OUTPUT EXTRACTION
# ---------------------------------------------------------------------------
def parse_reasoning_and_output(raw_text):
    """
    Extracts thoughts/reasoning and separates them from final answer and code.
    """
    if not raw_text:
        return {"thought": "", "text": "", "code_action": ""}

    cleaned = []
    for line in raw_text.splitlines():
        if "ERROR: logging before google.Init" in line:
            continue
        cleaned.append(line)
    raw_text = "\n".join(cleaned).strip()

    thought = ""
    code_action = ""
    final_text = raw_text

    # 1. <think> or <thought> tags
    think_match = re.search(r'<(?:think|thought)>(.*?)</(?:think|thought)>', raw_text, flags=re.DOTALL | re.IGNORECASE)
    if think_match:
        thought = think_match.group(1).strip()
        final_text = (raw_text[:think_match.start()] + raw_text[think_match.end():]).strip()

    # 2. Markdown headers like '### Рассуждения' or '### Reasoning'
    elif "### Рассуждения" in raw_text or "### Reasoning" in raw_text or "### План" in raw_text:
        parts = re.split(r'###\s*(?:Рассуждения|Reasoning|План)', raw_text, maxsplit=1)
        prefix = parts[0].strip()
        after = parts[1].strip() if len(parts) > 1 else ""
        next_head = re.search(r'###\s*(?:Ответ|Готовый код|Код|Реализация|Результат)', after)
        if next_head:
            thought = after[:next_head.start()].strip()
            final_text = (prefix + "\n\n" + after[next_head.start():]).strip()
        else:
            thought = after[:600].strip()
            final_text = after.strip()

    # 3. Detect code blocks if any
    code_matches = re.findall(r'```(?:[\w+-]+)?\n(.*?)```', raw_text, flags=re.DOTALL)
    if code_matches:
        code_action = "\n\n".join(c.strip() for c in code_matches[:2])

    return {
        "thought": thought,
        "text": final_text or raw_text,
        "code_action": code_action
    }

# ---------------------------------------------------------------------------
# SHELL COMMANDS (FOR DEV ONLY)
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
# MAIN ENTRYPOINT: ASK ANTIGRAVITY (LOCAL OR QUEUE)
# ---------------------------------------------------------------------------
def ask_antigravity(chat_id, user, user_prompt, task_id=None, firebase_url=None, media_list=None):
    """
    Executes prompt using:
     1. Local agy CLI if present on host.
     2. Firebase Queue Bridge to Termux worker if running on Render / Cloud.
    Returns:
     (result_dict, error_string)
     result_dict: {"status": "completed"|"cancelled", "text": str, "thought": str, "code_action": str, "model": str}
    """
    role = get_user_role(user)
    if not role:
        return None, "⛔ У вас нет доступа к вызову Antigravity. Попросите владельца @wrtnoq выдать доступ: `аг+ @ваш_юзер`"

    if not task_id:
        task_id = f"ag_{int(time.time()*1000)}_{chat_id}"

    register_task(task_id, getattr(user, "id", 0), chat_id)

    is_dev = (role == "dev")
    clean_query = user_prompt.strip()

    # Developer shortcuts for Owner
    if is_dev:
        lower_q = clean_query.lower()
        if lower_q.startswith("выполни ") or lower_q.startswith("exec ") or lower_q.startswith("sh "):
            cmd = clean_query.split(maxsplit=1)[1]
            out = execute_shell_command(cmd)
            unregister_task(task_id)
            return {
                "status": "completed",
                "text": out,
                "thought": "Выполнение команды терминала на хосте",
                "code_action": cmd,
                "model": "Antigravity Terminal"
            }, None

        if lower_q in ["статус", "status", "git status"]:
            git_out = execute_shell_command("git status -s && git log -1 --oneline")
            engine_lbl = "Local agy CLI" if has_local_agy() else "Firebase Queue Bridge (Termux Worker)"
            active_model = get_active_model_name()
            res = (
                f"⚡ <b>Статус Antigravity:</b>\n"
                f"• Движок: <code>{engine_lbl}</code>\n"
                f"• Активная модель: <code>{active_model}</code>\n"
                f"• Директория: <code>{REPO_DIR.name}/</code>\n\n"
                f"<b>Состояние Git:</b>\n{git_out}"
            )
            unregister_task(task_id)
            return {
                "status": "completed",
                "text": res,
                "thought": "Проверка статуса проекта и состояния репозитория",
                "code_action": "git status -s && git log -1 --oneline",
                "model": "Antigravity Status"
            }, None

    # Option 1: Local agy CLI (Termux / host with binary)
    if has_local_agy():
        res_dict, err = run_local_agy(chat_id, user, user_prompt, task_id, firebase_url, media_list)
        unregister_task(task_id)
        if res_dict:
            return res_dict, None
        print(f"⚠️ Local agy execution failed ({err}), trying Queue fallback...")

    # Option 2: Firebase Queue Bridge (Render / Cloud container connecting to Termux Worker)
    res_dict, err = run_queue_antigravity(chat_id, user, user_prompt, task_id, firebase_url, media_list)
    unregister_task(task_id)
    return res_dict, err

# ---------------------------------------------------------------------------
# LOCAL CLI EXECUTION
# ---------------------------------------------------------------------------
def run_local_agy(chat_id, user, user_prompt, task_id, firebase_url=None, media_list=None):
    bin_path = get_agy_bin()
    if not bin_path:
        return None, "Бинарник agy не найден на хосте."

    full_prompt = build_prompt_with_context(chat_id, user, user_prompt, firebase_url=firebase_url)
    if media_list:
        full_prompt += f"\n\n[Пользователь прикрепил {len(media_list)} медиа-файлов к запросу]"

    cmd = [bin_path, "-c", "-p", full_prompt, "--dangerously-skip-permissions"]
    active_model = get_active_model_name()

    max_retries = 3
    for attempt in range(1, max_retries + 1):
        if ACTIVE_TASKS.get(task_id, {}).get("status") == "cancelled":
            return {"status": "cancelled", "text": "Прервано пользователем."}, None

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(REPO_DIR),
                env=os.environ.copy()
            )
            if task_id in ACTIVE_TASKS:
                ACTIVE_TASKS[task_id]["proc"] = proc

            # Monitor loop with timeout
            start_t = time.time()
            cancelled = False
            while proc.poll() is None:
                if ACTIVE_TASKS.get(task_id, {}).get("status") == "cancelled":
                    proc.terminate()
                    proc.kill()
                    cancelled = True
                    break
                if time.time() - start_t > 180:
                    proc.terminate()
                    proc.kill()
                    return None, "Таймаут выполнения agy (180 сек)."
                time.sleep(0.3)

            if cancelled:
                return {"status": "cancelled", "text": "Прервано пользователем."}, None

            stdout, _ = proc.communicate()
            raw = stdout or ""

            if "Eligibility check failed" in raw or "Account ineligible" in raw:
                if attempt < max_retries:
                    time.sleep(1.5)
                    continue

            parsed = parse_reasoning_and_output(raw)
            if parsed["text"] and "Eligibility check failed" not in parsed["text"]:
                return {
                    "status": "completed",
                    "text": parsed["text"],
                    "thought": parsed["thought"],
                    "code_action": parsed["code_action"],
                    "model": active_model
                }, None

        except Exception as e:
            if attempt == max_retries:
                return None, f"Ошибка вызова agy: {e}"
            time.sleep(1.0)

    return None, "Сбой локального запуска Antigravity agy."

# ---------------------------------------------------------------------------
# FIREBASE QUEUE EXECUTION (RENDER -> TERMUX WORKER)
# ---------------------------------------------------------------------------
def run_queue_antigravity(chat_id, user, user_prompt, task_id, firebase_url=None, media_list=None):
    """
    Places task in Firebase RTDB queue and waits for Termux worker to process it.
    NEVER touches user's GEMINI_KEYS pool.
    """
    fb_url = firebase_url or os.environ.get("FIREBASE_URL") or DEFAULT_FIREBASE_URL
    if not fb_url:
        return None, "FIREBASE_URL не задан на сервере."

    role = get_user_role(user)
    payload = {
        "task_id": task_id,
        "chat_id": chat_id,
        "user_id": getattr(user, "id", 0),
        "username": getattr(user, "username", "") or "",
        "first_name": getattr(user, "first_name", "") or "",
        "role": role,
        "prompt": user_prompt,
        "status": "pending",
        "created_at": time.time()
    }

    task_url = f"{fb_url.rstrip('/')}/antigravity_queue/{task_id}.json"

    # Put task in queue
    try:
        req = urllib.request.Request(
            task_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PUT"
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        return None, f"Не удалось отправить задачу в Firebase queue: {e}"

    # Poll Firebase queue for worker response
    start_time = time.time()
    timeout = 75

    while time.time() - start_time < timeout:
        # Check local cancellation
        if ACTIVE_TASKS.get(task_id, {}).get("status") == "cancelled":
            return {"status": "cancelled", "text": "Прервано пользователем."}, None

        try:
            res_data = json.loads(urllib.request.urlopen(task_url, timeout=3).read().decode("utf-8") or "{}")
        except Exception:
            res_data = None

        if not res_data or not isinstance(res_data, dict):
            time.sleep(1.0)
            continue

        status = res_data.get("status")
        if status == "cancelled":
            return {"status": "cancelled", "text": "Прервано пользователем."}, None

        if status == "completed":
            # Clean up task
            try:
                del_req = urllib.request.Request(task_url, method="DELETE")
                urllib.request.urlopen(del_req, timeout=3)
            except Exception:
                pass

            resp_text = res_data.get("response") or res_data.get("text") or ""
            return {
                "status": "completed",
                "text": resp_text,
                "response": resp_text,
                "thought": res_data.get("thought", ""),
                "code_action": res_data.get("code_action", ""),
                "model": res_data.get("model", "Gemini 3.8 Flash (High) [Antigravity Thinking]")
            }, None

        time.sleep(1.2)

    return None, (
        "⚠️ Antigravity Worker на Termux не ответил вовремя (35 сек).\n"
        "Убедитесь, что воркер запущен на телефоне: <code>python3 antigravity_worker.py</code>\n"
        "<i>(Ваш пул ключей Gemini НЕ использовался и квота цела)</i>"
    )
