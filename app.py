import os
import sys
import threading
import time
import importlib
from flask import Flask, render_template, request, jsonify, Response
import chat_manager

app = Flask(__name__)

# --- WEB UI & HEALTH ROUTES ---
@app.route('/')
@app.route('/chats')
def chats_page():
    # If Render or monitoring tool requests a lightweight healthcheck
    if request.args.get('health') == '1' or request.headers.get('User-Agent', '').startswith('Render/'):
        return "ok", 200
    try:
        return render_template('chats.html')
    except Exception as e:
        return f"Error loading template: {e}", 500

@app.route('/healthz')
def healthz():
    return "ok", 200

# --- REST API ДЛЯ ПАНЕЛИ ЧАТОВ ---
@app.route('/api/chats', methods=['GET'])
def api_get_chats():
    return jsonify(chat_manager.get_chats_list())

@app.route('/api/chats/<uid>', methods=['GET'])
def api_get_chat_history(uid):
    history = chat_manager.get_chat_history(uid)
    if not history:
        return jsonify({'error': 'Chat not found', 'uid': uid, 'messages': []}), 404
    return jsonify(history)

@app.route('/api/chats/<uid>/send', methods=['POST'])
def api_send_message(uid):
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'ok': False, 'error': 'Пустой текст сообщения'}), 400

    ok, err = chat_manager.send_reply(uid, text)
    if ok:
        return jsonify({'ok': True})
    return jsonify({'ok': False, 'error': err or 'Ошибка отправки'}), 500

@app.route('/api/chats/<uid>/clear', methods=['POST'])
def api_clear_chat(uid):
    chat_manager.clear_chat_history(uid)
    return jsonify({'ok': True})

@app.route('/api/avatar/<uid>', methods=['GET'])
def api_get_avatar(uid):
    img_bytes, mime = chat_manager.get_user_avatar(uid)
    return Response(img_bytes, mimetype=mime)

@app.route('/api/status', methods=['GET'])
def api_status():
    bot = chat_manager.get_bot_instance()
    bot_active = bool(bot)
    return jsonify({
        'status': 'online' if bot_active else 'starting',
        'chats_count': len(chat_manager.CHATS),
        'port': os.environ.get('PORT', 8080)
    })

# --- ЗАПУСК БОТА В ФОНЕ С ПЕРЕЗАПУСКОМ ---
def run_bot():
    while True:
        print("--> [SERVER] ЗАГРУЗКА И СТАРТ TELEGRAM-БОТА...", flush=True)
        try:
            if 'bot' in sys.modules:
                bot_module = importlib.reload(sys.modules['bot'])
            else:
                import bot as bot_module

            if hasattr(bot_module, 'bot'):
                chat_manager.set_bot_instance(bot_module.bot)

        except Exception as e:
            print(f"--> [SERVER] ОШИБКА БОТА: {e}. Перезапуск через 5 сек...", flush=True)
            time.sleep(5)

if __name__ == '__main__':
    print("--> ЗАПУСК СЕРВЕРА RENDER & ПАНЕЛИ ЧАТОВ...", flush=True)
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)
