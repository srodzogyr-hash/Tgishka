# -*- coding: utf-8 -*-
"""
AI Engine for Durak Online
Provides tactical moves and AI commentary/reactions using Gemini / Groq / Classic engines.
"""

import random
import time
import threading

# Bot reaction phrases (for quick offline responses or fallback)
OFFLINE_PHRASES = {
    "take": [
        "Эх, нечем крыть... Забираю! 🥲",
        "Ладно-ладно, беру себе.",
        "Ну закидали, так закидали! Забираю.",
        "Не лезет больше... Беру!",
        "Ничего, в следующий раз отыграюсь!"
    ],
    "beat_ace": [
        "Козырной на твоего Туза! 😎",
        "Туз не помог, держи козыря!",
        "Красиво побил, согласен? 🔥"
    ],
    "toss": [
        "Лови добавку! 🃏",
        "Вот тебе ещё одна в масть!",
        "На, держи подарочек!",
        "Подкидываю от души!",
        "Принимай пополнение!"
    ],
    "pass": [
        "Бито! Чисто сработано. ✋",
        "Всё, бито! Ходи дальше.",
        "Бито, погнали дальше!",
        "Сброс! Хороший кон."
    ],
    "attack_trump": [
        "С козырей заходим! 💥",
        "Держи козырного, попробуй отбейся!",
        "Тяжёлая артиллерия пошла!"
    ],
    "win": [
        "Я чист! Без карт! 👑 До свидания!",
        "Победа! Кто тут следующий в дураки? 😎",
        "Легчайшая партия!"
    ],
    "lose": [
        "Ну вот, я остался дураком... 🤡",
        "Шулерские карты у вас! В следующий раз отомщу!",
        "Эх, не зашла масть в этой раздаче."
    ]
}

def get_quick_reaction(event_type: str) -> str:
    phrases = OFFLINE_PHRASES.get(event_type, OFFLINE_PHRASES["pass"])
    return random.choice(phrases)

def generate_ai_commentary_async(room, bot_player, event_type: str, card_info: str = ""):
    """Spawns a thread to generate in-character AI commentary without blocking the game."""
    def _worker():
        model = bot_player.bot_model or "gemini-3.8-flash"
        # 40% chance to say something so chat doesn't get flooded
        if random.random() > 0.45 and event_type not in ("lose", "win"):
            return

        # If model is classic, use rich offline Russian phrases
        if model == "classic":
            phrase = get_quick_reaction(event_type)
            room.send_chat_message(bot_player.id, bot_player.name, phrase)
            return

        # Attempt to call bot.py's ask_gemini / ask_groq
        try:
            import sys
            main = sys.modules.get('bot')
            if not main or not hasattr(main, 'ask_gemini'):
                phrase = get_quick_reaction(event_type)
                room.send_chat_message(bot_player.id, bot_player.name, phrase)
                return

            prompt = (
                f"Ты — живой игрок в карточной игре 'Дурак Онлайн'. Твоё имя: {bot_player.name}.\n"
                f"Событие за столом: {event_type} {card_info}.\n"
                f"Козырь партии: {room.trump_suit}.\n"
                "Напиши ОДНУ очень короткую (максимум 5-8 слов) живую, эмоциональную, слегка дерзкую или забавную фразу в чат стола на русском языке. "
                "Без кавычек, без приветствий, только сама реплика."
            )

            reply = None
            if "groq" in model.lower():
                reply, err, _, _ = main.ask_groq(9999999, prompt, save_to_memory=False, chosen_groq_model=model)
            else:
                reply, err, _, _ = main.ask_gemini(9999999, prompt, save_to_memory=False, preferred_model=model)

            if reply and reply.strip():
                clean_text = reply.strip().strip('"').strip("'")
                room.send_chat_message(bot_player.id, bot_player.name, clean_text)
            else:
                phrase = get_quick_reaction(event_type)
                room.send_chat_message(bot_player.id, bot_player.name, phrase)
        except Exception:
            phrase = get_quick_reaction(event_type)
            room.send_chat_message(bot_player.id, bot_player.name, phrase)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
