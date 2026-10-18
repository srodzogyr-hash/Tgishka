import os
import threading
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "ok", 200

def run_bot():
    print("--> БОТ ЗАГРУЖАЕТСЯ...", flush=True)
    try:
        import bot
    except Exception as e:
        print(f"--> БОТ СЛОМАЛСЯ: {e}", flush=True)

if __name__ == '__main__':
    print("--> ЗАПУСК СЕРВЕРА RENDER...", flush=True)
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)