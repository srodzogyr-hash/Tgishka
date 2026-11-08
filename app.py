import os
import sys
import threading
import time
import importlib
from flask import Flask, render_template, request, jsonify, Response
import chat_manager
import durak_engine

app = Flask(__name__)

# --- ДУРАК ОНЛАЙН (WEB APP & API) ---
@app.route('/durak')
@app.route('/durak/<room_id>')
def durak_page(room_id='lobby'):
    try:
        return render_template('durak.html', room_id=room_id)
    except Exception as e:
        return f"Error loading Durak template: {e}", 500

@app.route('/api/durak/<room_id>/state', methods=['GET'])
def api_durak_state(room_id):
    uid = request.args.get('uid')
    room = durak_engine.get_or_create_room(room_id)
    return jsonify(room.get_state(uid))

@app.route('/api/durak/<room_id>/join', methods=['POST'])
def api_durak_join(room_id):
    data = request.get_json(silent=True) or {}
    uid = str(data.get('user_id') or 'guest')
    name = (data.get('name') or 'Игрок').strip()
    avatar = data.get('avatar')
    room = durak_engine.get_or_create_room(room_id, creator_id=uid, creator_name=name)
    ok, msg = room.add_player(uid, name, avatar=avatar)
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/durak/<room_id>/start', methods=['POST'])
def api_durak_start(room_id):
    room = durak_engine.get_or_create_room(room_id)
    ok, msg = room.start_game()
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/durak/<room_id>/add_bot', methods=['POST'])
def api_durak_add_bot(room_id):
    room = durak_engine.get_or_create_room(room_id)
    ok, msg = room.add_bot()
    return jsonify({'ok': ok, 'message': msg})

@app.route('/api/durak/<room_id>/action', methods=['POST'])
def api_durak_action(room_id):
    data = request.get_json(silent=True) or {}
    uid = str(data.get('user_id') or '')
    action = data.get('action')
    room = durak_engine.get_or_create_room(room_id)

    if action == 'play_card':
        card = data.get('card')
        target_idx = data.get('target_index')
        ok, msg = room.play_card(uid, card, target_attack_idx=target_idx)
        return jsonify({'ok': ok, 'message': msg})

    elif action == 'pass':
        ok, msg = room.pass_turn(uid)
        return jsonify({'ok': ok, 'message': msg})

    elif action == 'take':
        ok, msg = room.take_cards(uid)
        return jsonify({'ok': ok, 'message': msg})

    elif action == 'chat':
        text = (data.get('text') or '').strip()
        sender_name = (data.get('sender_name') or 'Игрок').strip()
        if text:
            room.send_chat_message(uid, sender_name, text)
        return jsonify({'ok': True})

    return jsonify({'ok': False, 'message': 'Неизвестное действие'}), 400

@app.route('/api/durak/<room_id>/set_model', methods=['POST'])
def api_durak_set_model(room_id):
    data = request.get_json(silent=True) or {}
    bot_model = data.get('bot_model')
    if bot_model:
        room = durak_engine.get_or_create_room(room_id)
        room.bot_model = bot_model
        for p in room.players:
            if p.is_bot:
                p.bot_model = bot_model
        return jsonify({'ok': True, 'bot_model': bot_model})
    return jsonify({'ok': False}), 400

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

@app.route('/manifest.json')
def manifest():
    return app.send_static_file('manifest.json')

@app.route('/sw.js')
def service_worker():
    response = app.send_static_file('sw.js')
    response.headers['Service-Worker-Allowed'] = '/'
    return response

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
