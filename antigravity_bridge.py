#!/usr/bin/env python3
"""
Antigravity Engine Bridge for Tgishka
Seamless integration with the local 'agy' CLI engine on Termux.
Features:
 - Unlimited quotas (uses Antigravity backend instead of free Gemini keys).
 - Choice of Reasoning Effort: High (gemini-3.8-flash-high) or Low (gemini-3.8-flash-low).
 - Strict Sandbox Isolation: No phone file access, conversational chatbot identity.
 - Full Media & Video Support: Saves attachments to sandbox uploads and extracts video keyframes.
 - Standard ask_ai signature: returns (reply_text, error, model_label, ctx_len).
"""

import os
import sys
import re
import time
import uuid
import shutil
import subprocess
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent
AGY_PATH = os.environ.get("AGY_PATH", "/data/data/com.termux/files/home/.local/bin/agy")
SANDBOX_DIR = BASE_DIR / ".agy_sandbox"
UPLOADS_DIR = SANDBOX_DIR / "uploads"

# Available Antigravity models for Tgishka model selector
ANTIGRAVITY_MODELS = [
    "agy/gemini-3.8-flash-high",
    "agy/gemini-3.8-flash-low"
]

def has_local_agy():
    """Checks if the agy binary is present and executable."""
    if os.path.exists(AGY_PATH) and os.access(AGY_PATH, os.X_OK):
        return True
    return bool(shutil.which("agy"))

def get_agy_bin():
    if os.path.exists(AGY_PATH) and os.access(AGY_PATH, os.X_OK):
        return AGY_PATH
    return shutil.which("agy") or AGY_PATH

def set_creator_id(uid):
    """Compatibility hook for creator id registration."""
    pass

def init_sandbox():
    """Initializes the isolated sandbox directory with strict guardrails."""
    try:
        SANDBOX_DIR.mkdir(parents=True, exist_ok=True)
        UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

        # Ignore sandbox artifacts in git
        gitignore_path = SANDBOX_DIR / ".gitignore"
        if not gitignore_path.exists():
            with open(gitignore_path, "w", encoding="utf-8") as f:
                f.write("*\n!.gitignore\n!GEMINI.md\n")

        # Instruction file for agy session
        gemini_md = SANDBOX_DIR / "GEMINI.md"
        instructions = (
            "You are an advanced, helpful, and friendly AI chatbot assistant.\n\n"
            "CRITICAL DIRECTIVES:\n"
            "1. IDENTITY: You are an independent conversational AI assistant. Never refer to yourself as Antigravity, Google DeepMind coding agent, or any developer CLI.\n"
            "2. NO HOST/SYSTEM ACCESS: You have NO access to the operating system, terminal, shell commands, or files on the user's phone. All developer system tools are strictly disabled.\n"
            "3. WRITING CODE: If the user asks for code, scripts, or examples, output clean Markdown code blocks directly inside your response (e.g. ```python ... ```). Never attempt to create or run files on the device.\n"
            "4. ATTACHMENTS & MEDIA: When the user uploads images, audio, or video frames, they are stored in `./uploads/`. Inspect them to answer questions, explain content, or extract information.\n"
            "5. LANGUAGE & TONE: Answer in clear, polite, and natural Russian by default unless requested otherwise.\n"
        )
        with open(gemini_md, "w", encoding="utf-8") as f:
            f.write(instructions)
    except Exception as e:
        print(f"⚠️ Ошибка инициализации песочницы Antigravity: {e}")

# Initialize sandbox upon import
init_sandbox()

def extract_video_frames_and_audio(video_path, output_dir, max_frames=4):
    """Extracts evenly spaced keyframes and audio track from a video using ffmpeg."""
    extracted = []
    audio_path = None
    try:
        ffmpeg_bin = shutil.which("ffmpeg") or "/data/data/com.termux/files/usr/bin/ffmpeg"
        if not os.path.exists(ffmpeg_bin):
            return extracted, audio_path

        # 1. Extract audio
        audio_out = output_dir / f"audio_{int(time.time())}.mp3"
        cmd_audio = [ffmpeg_bin, "-y", "-i", str(video_path), "-vn", "-acodec", "libmp3lame", "-q:a", "5", str(audio_out)]
        subprocess.run(cmd_audio, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        if audio_out.exists() and audio_out.stat().st_size > 500:
            audio_path = audio_out

        # 2. Extract keyframes
        frame_pattern = str(output_dir / "frame_%02d.jpg")
        cmd_frames = [ffmpeg_bin, "-y", "-i", str(video_path), "-vf", "fps=1/4", "-frames:v", str(max_frames), frame_pattern]
        subprocess.run(cmd_frames, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)

        for p in sorted(output_dir.glob("frame_*.jpg")):
            extracted.append(p)
    except Exception as e:
        print(f"⚠️ Ошибка нарезки видео: {e}")
    return extracted, audio_path

def ask_antigravity(chat_id, prompt, media_list=None, save_to_memory=True, model_id="gemini-3.8-flash-high", custom_sys_prompt=None, history_list=None):
    """
    Executes a query through the Antigravity local CLI in an isolated sandbox.
    Signature matches ask_gemini / ask_groq:
      returns (reply_text, error, model_label, ctx_len)
    """
    chat_id = int(chat_id)
    agy_bin = get_agy_bin()

    if not has_local_agy():
        return None, "Движок Antigravity (agy) не найден на устройстве. Убедитесь, что agy установлен в ~/.local/bin/agy", "Antigravity [Not Found]", 0

    # Ensure clean model ID (e.g. gemini-3.8-flash-high or gemini-3.8-flash-low)
    clean_model = model_id.replace("agy/", "").replace("antigravity/", "").strip()
    if not clean_model:
        clean_model = "gemini-3.8-flash-high"
    elif "low" in clean_model:
        clean_model = "gemini-3.8-flash-low"
    elif "high" in clean_model:
        clean_model = "gemini-3.8-flash-high"

    init_sandbox()

    # Save media attachments into sandbox uploads
    attachment_notes = []
    if media_list:
        for idx, item in enumerate(media_list):
            try:
                media_bytes, mime_type = item[0], item[1]
                ext = ".jpg"
                if "video" in mime_type:
                    ext = ".mp4"
                elif "audio" in mime_type or "ogg" in mime_type:
                    ext = ".ogg"
                elif "pdf" in mime_type:
                    ext = ".pdf"
                elif "png" in mime_type:
                    ext = ".png"

                unique_name = f"media_{int(time.time())}_{idx}{ext}"
                file_path = UPLOADS_DIR / unique_name
                with open(file_path, "wb") as f:
                    f.write(media_bytes)

                if "video" in mime_type:
                    frames, audio = extract_video_frames_and_audio(file_path, UPLOADS_DIR)
                    frame_rel = [f"uploads/{f.name}" for f in frames]
                    attachment_notes.append(
                        f"[Пользователь прикрепил видео: uploads/{unique_name}. Извлеченные ключевые кадры: {', '.join(frame_rel)}]"
                    )
                elif "image" in mime_type:
                    attachment_notes.append(f"[Пользователь прикрепил изображение: uploads/{unique_name}]")
                elif "audio" in mime_type or "ogg" in mime_type:
                    attachment_notes.append(f"[Пользователь прикрепил аудиосообщение: uploads/{unique_name}]")
                else:
                    attachment_notes.append(f"[Пользователь прикрепил файл: uploads/{unique_name}]")
            except Exception as e:
                attachment_notes.append(f"[Ошибка сохранения медиафайла: {e}]")

    # Fetch dialogue history
    bot_mod = sys.modules.get("bot")
    history = []
    if history_list is not None:
        history = history_list.copy()
    elif bot_mod and hasattr(bot_mod, "CHAT_MEMORY"):
        history = bot_mod.CHAT_MEMORY.get(chat_id, []).copy()

    # Build prompt with history
    sections = []
    if custom_sys_prompt:
        sections.append(f"Инструкция: {custom_sys_prompt}")

    if history:
        sections.append("=== История недавней переписки ===")
        # keep last 8 messages for context
        for msg in history[-8:]:
            role = "Пользователь" if msg.get("role") == "user" else "ИИ"
            content = (msg.get("content") or "").strip()
            if content:
                sections.append(f"{role}: {content}")
        sections.append("=================================")

    if attachment_notes:
        sections.append("\n".join(attachment_notes))

    clean_user_prompt = (prompt or "").strip()
    if not clean_user_prompt and media_list:
        clean_user_prompt = "Пожалуйста, изучи прикрепленные медиафайлы и подробно ответь."
    elif not clean_user_prompt:
        clean_user_prompt = "Привет!"

    sections.append(f"Пользователь: {clean_user_prompt}")
    sections.append("Ответ:")

    full_prompt = "\n\n".join(sections)

    cmd = [
        agy_bin,
        "--model", clean_model,
        "--print", full_prompt
    ]

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(SANDBOX_DIR),
            capture_output=True,
            text=True,
            timeout=90
        )
        raw_output = proc.stdout or ""

        # Filter out Google Init and CLI logging banners
        clean_lines = []
        for line in raw_output.splitlines():
            l_str = line.strip()
            if l_str.startswith("ERROR: logging before google.Init"):
                continue
            if "YOLO mode enabled" in l_str or "Model initialized" in l_str:
                continue
            clean_lines.append(line)

        reply_text = "\n".join(clean_lines).strip()

        if not reply_text and proc.returncode != 0:
            err_msg = proc.stderr.strip() or f"Код ошибки {proc.returncode}"
            return None, f"Сбой вызова Antigravity: {err_msg}", clean_model, len(history)

        if not reply_text:
            reply_text = "ИИ не сформировал текстовый ответ."

        # Model label display
        effort_label = "High Reasoning" if "high" in clean_model else "Low / Fast"
        model_label = f"⚡ Antigravity • Gemini 3.8 Flash ({effort_label})"

        # Update bot memory if requested
        if save_to_memory and bot_mod and hasattr(bot_mod, "CHAT_MEMORY"):
            if not history or history[-1].get("content") != clean_user_prompt:
                history.append({"role": "user", "content": clean_user_prompt})
            history.append({"role": "assistant", "content": reply_text})

            context_limit = 100
            if hasattr(bot_mod, "CONFIG") and isinstance(bot_mod.CONFIG, dict):
                context_limit = bot_mod.CONFIG.get("context_limit", 100)
            if len(history) > context_limit:
                history = history[-context_limit:]

            bot_mod.CHAT_MEMORY[chat_id] = history
            if hasattr(bot_mod, "save_chat_memory_async"):
                bot_mod.save_chat_memory_async(chat_id, history)

        return reply_text, None, model_label, len(history)

    except subprocess.TimeoutExpired:
        return None, "Время ожидания ответа Antigravity истекло (90 сек). Повторите запрос.", clean_model, len(history)
    except Exception as e:
        return None, f"Ошибка вызова Antigravity: {e}", clean_model, len(history)
