#!/usr/bin/env python3
"""
Antigravity Worker Daemon for Termux
Processes Telegram bot requests queued in Firebase Realtime Database.
Executes local Google Antigravity CLI ('agy') agent on phone with full tool access,
code modification capabilities, and strict chat context isolation.
NEVER touches the user's Gemini keys pool.
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

class MockUser:
    def __init__(self, uid, uname, first_name=""):
        self.id = uid
        self.username = uname
        self.first_name = first_name
        self.last_name = ""

def process_single_task(task_id, task_data):
    chat_id = task_data.get("chat_id")
    user_id = task_data.get("user_id", 0)
    username = task_data.get("username", "")
    first_name = task_data.get("first_name", "")
    role = task_data.get("role", "user")
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

    # Build prompt with isolated chat context strictly for this chat_id
    full_prompt = antigravity_bridge.build_prompt_with_context(chat_id, user, prompt, firebase_url=FIREBASE_URL)

    bin_path = antigravity_bridge.get_agy_bin()
    if not bin_path:
        err_res = {
            "status": "completed",
            "response": "⚠️ Бинарник agy не найден в окружении Termux.",
            "thought": "",
            "code_action": "",
            "model": "Error"
        }
        try:
            req = urllib.request.Request(task_url, data=json.dumps(err_res).encode(), headers={"Content-Type": "application/json"}, method="PATCH")
            urllib.request.urlopen(req, timeout=3)
        except Exception:
            pass
        return

    cmd = [bin_path, "-c", "-p", full_prompt, "--dangerously-skip-permissions"]

    # Start subprocess
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=str(REPO_DIR),
        env=os.environ.copy()
    )

    # Monitor loop for user cancellation
    cancelled = False
    start_t = time.time()
    while proc.poll() is None:
        try:
            curr = json.loads(urllib.request.urlopen(task_url, timeout=2).read().decode("utf-8") or "{}")
            if curr.get("status") == "cancelled":
                proc.terminate()
                proc.kill()
                cancelled = True
                print(f"🛑 Task {task_id} cancelled by user!")
                break
        except Exception:
            pass
        if time.time() - start_t > 180:
            proc.terminate()
            proc.kill()
            break
        time.sleep(0.5)

    if cancelled:
        return

    stdout, _ = proc.communicate()
    raw_output = stdout or ""

    # Parse thoughts, code, and response
    parsed = antigravity_bridge.parse_reasoning_and_output(raw_output)
    active_model = antigravity_bridge.get_active_model_name()

    res_payload = {
        "status": "completed",
        "response": parsed["text"],
        "thought": parsed["thought"],
        "code_action": parsed["code_action"],
        "model": active_model,
        "completed_at": time.time()
    }

    try:
        req = urllib.request.Request(
            task_url,
            data=json.dumps(res_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="PATCH"
        )
        urllib.request.urlopen(req, timeout=5)
        print(f"✅ Task {task_id} completed successfully.")
    except Exception as e:
        print(f"⚠️ Error uploading task result: {e}")

def run_worker():
    print("=" * 60)
    print("🚀 Antigravity Worker Daemon started on Termux")
    print(f"📡 Queue Endpoint: {FIREBASE_URL}/antigravity_queue")
    print(f"⚡ Local agy binary: {antigravity_bridge.get_agy_bin()}")
    print(f"🧠 Model: {antigravity_bridge.get_active_model_name()}")
    print("=" * 60)

    while True:
        try:
            queue_url = f"{FIREBASE_URL.rstrip('/')}/antigravity_queue.json"
            req = urllib.request.Request(queue_url, headers={"User-Agent": "AntigravityWorker/1.0"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")

            if isinstance(data, dict):
                for task_id, task in data.items():
                    if isinstance(task, dict) and task.get("status") == "pending":
                        uname = task.get("username") or f"id_{task.get('user_id')}"
                        preview = (task.get("prompt") or "")[:40].replace("\n", " ")
                        print(f"⚡ [Task {task_id}] from @{uname}: '{preview}'")
                        process_single_task(task_id, task)

        except Exception as e:
            # Silent retry
            pass

        time.sleep(1.0)

if __name__ == "__main__":
    run_worker()
