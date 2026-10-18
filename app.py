import os
import threading
import time
from flask import Flask

app = Flask(__name__)

@app.route('/')
def home():
    return "ok", 200

def run_bot():
    while True:
        print("--> БОТ ЗАГРУЖАЕТСЯ...", flush=True)
        try:
            import bot
        except Exception as e:
            print(f"--> БОТ СЛОМАЛСЯ: {e}. Перезапуск через 5 секунд...", flush=True)
            time.sleep(5)

if __name__ == '__main__':
    print("--> ЗАПУСК СЕРВЕРА RENDER...", flush=True)
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)