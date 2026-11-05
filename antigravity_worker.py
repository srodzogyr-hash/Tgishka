#!/usr/bin/env python3
"""
Antigravity Worker Daemon for Termux
Listens to Firebase Realtime Database queue and executes tasks directly
using the active Google Antigravity Agent Session (Agent API) on Termux.

Key Features:
 - Zero Eligibility Check errors (attaches to active session at localhost:43675).
 - Zero usage of user's GEMINI_KEYS pool.
 - Extracts authentic model thinking and tool actions.
 - Real-time cancellation monitor.
 - Strict per-chat context isolation.
"""

import os
import sys
import time
import json
import urllib.request
import subprocess
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_DIR))

import antigravity_bridge

FIREBASE_URL = os.environ.get("FIREBASE_URL", "https://tgbotik-d479c-default-rtdb.firebaseio.com/secret_845891")
ACTIVE_SESSION_FILE = Path("/data/data/com.termux/files/home/.gemini/antigravity-cli/active_session.json")
AGY_BIN = "/data/data/com.termux/files/home/.local/bin/agy"

class MockUser:
    def __init__(self, uid, uname, first_name=""):
        self.id = uid
        self.username = uname
        self.first_name = first_name
        self.last_name = ""

def get_session_env():
    env = os.environ.copy()
    env["PATH"] = f"/data/data/com.termux/files/home/.local/bin:{env.get('PATH', '')}"
    if ACTIVE_SESSION_FILE.exists():
        try:
            with open(ACTIVE_SESSION_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("ls_address"):
                    env["ANTIGRAVITY_LS_ADDRESS"] = data["ls_address"]
                if data.get("csrf_token"):
                    env["ANTIGRAVITY_CSRF_TOKEN"] = data["csrf_token"]
        except Exception as e:
            print(f"⚠️ Error reading active_session.json: {e}")
    return env

def execute_via_agentapi(full_prompt, task_url, timeout=90):
    """
    Executes prompt using active Antigravity Agent Session.
    Bypasses any geo-blocking or Eligibility check errors.
    """
    env = get_session_env()
    cmd = [AGY_BIN, "agentapi", "new-conversation", full_prompt]

    try:
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, timeout=20)
        out = proc.stdout.strip()
        json_start = out.find('{')
        if json_start == -1:
            return None, f"agentapi did not return JSON: {out}"

        data = json.loads(out[json_start:])
        conv_id = data.get("response", {}).get("newConversation", {}).get("conversationId")
        if not conv_id:
            return None, f"No conversationId in response: {data}"

        print(f"⚡ [AgentAPI] Conversation spawned: {conv_id}")

        # Watch transcript for model response
        transcript_path = Path(f"/data/data/com.termux/files/home/.gemini/antigravity-cli/brain/{conv_id}/.system_generated/logs/transcript.jsonl")
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Check user cancellation in Firebase
            try:
                curr = json.loads(urllib.request.urlopen(task_url, timeout=2).read().decode("utf-8") or "{}")
                if curr.get("status") == "cancelled":
                    print(f"🛑 [AgentAPI] Task cancelled by user!")
                    return {"status": "cancelled", "response": "Прервано пользователем."}, None
            except Exception:
                pass

            if transcript_path.exists():
                try:
                    with open(transcript_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    for line in lines:
                        entry = json.loads(line)
                        if entry.get("type") == "PLANNER_RESPONSE" and entry.get("status") == "DONE":
                            thought = entry.get("thinking", "")
                            content = entry.get("content", "")
                            return {
                                "status": "completed",
                                "response": content,
                                "thought": thought,
                                "code_action": "",
                                "model": "Gemini 3.8 Flash (High) [Antigravity Engine]"
                            }, None
                except Exception:
                    pass

            time.sleep(1.0)

        return None, "Таймаут ожидания ответа Antigravity (90 сек)."

    except Exception as e:
        return None, f"Ошибка AgentAPI: {e}"

def process_single_task(task_id, task_data):
    chat_id = task_data.get("chat_id")
    user_id = task_data.get("user_id", 0)
    username = task_data.get("username", "")
    first_name = task_data.get("first_name", "")
    prompt = task_data.get("prompt", "")

    user = MockUser(user_id, username, first_name)
    task_url = f"{FIREBASE_URL.rstrip('/')}/antigravity_queue/{task_id}.json"

    # Mark as processing
    try:
        patch_req = urllib.request.Request(
            task_url,
            data=json.dumps({"status": "processing", "started_at": time.time()}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        urllib.request.urlopen(patch_req, timeout=4)
    except Exception as e:
        print(f"⚠️ Error marking task processing: {e}")

    # Build prompt strictly using context for THIS chat_id
    full_prompt = antigravity_bridge.build_prompt_with_context(chat_id, user, prompt, firebase_url=FIREBASE_URL)

    # 1. Primary: execute via AgentAPI (always works, zero eligibility errors)
    result_dict, err = execute_via_agentapi(full_prompt, task_url)

    # 2. Fallback: if AgentAPI returned error, try direct agy CLI
    if not result_dict and "agentapi" in (err or ""):
        print(f"⚠️ AgentAPI failed ({err}), falling back to agy CLI...")
        cmd = [AGY_BIN, "-c", "-p", full_prompt, "--dangerously-skip-permissions"]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(REPO_DIR), timeout=60)
            raw = proc.stdout or ""
            parsed = antigravity_bridge.parse_reasoning_and_output(raw)
            result_dict = {
                "status": "completed",
                "response": parsed["text"],
                "thought": parsed["thought"],
                "code_action": parsed["code_action"],
                "model": "Gemini 3.8 Flash (High) [Antigravity Engine]"
            }
        except Exception as e2:
            err = str(e2)

    if not result_dict:
        result_dict = {
            "status": "completed",
            "response": f"⚠️ Ошибка выполнения: {err or 'Неизвестная ошибка'}",
            "thought": "",
            "code_action": "",
            "model": "Error"
        }

    # If task was cancelled, don't overwrite as completed
    if result_dict.get("status") == "cancelled":
        return

    # Post result back to Firebase queue
    try:
        req = urllib.request.Request(
            task_url,
            data=json.dumps(result_dict).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        urllib.request.urlopen(req, timeout=5)
        print(f"✅ [Task {task_id}] completed and posted to Firebase.")
    except Exception as e:
        print(f"⚠️ Error uploading task result: {e}")

def run_worker():
    print("=" * 60)
    print("🚀 Antigravity Worker Daemon started on Termux (AgentAPI Mode)")
    print(f"📡 Queue Endpoint: {FIREBASE_URL}/antigravity_queue")
    print(f"⚡ Agent Binary: {AGY_BIN}")
    print(f"🧠 Model: Gemini 3.8 Flash (High) [Antigravity Engine]")
    print("=" * 60)

    while True:
        try:
            queue_url = f"{FIREBASE_URL.rstrip('/')}/antigravity_queue.json"
            req = urllib.request.Request(queue_url, headers={"User-Agent": "AntigravityWorker/2.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")

            if isinstance(data, dict):
                for task_id, task in data.items():
                    if isinstance(task, dict) and task.get("status") == "pending":
                        uname = task.get("username") or f"id_{task.get('user_id')}"
                        preview = (task.get("prompt") or "")[:40].replace("\n", " ")
                        print(f"⚡ [Incoming Task] {task_id} from @{uname}: '{preview}'")
                        process_single_task(task_id, task)

        except Exception:
            pass

        time.sleep(1.0)

if __name__ == "__main__":
    run_worker()
