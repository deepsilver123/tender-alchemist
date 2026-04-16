# tender-alchemist
Проект для анализа тендерной документации и поиска подходящих предложений


Установка и запуск WebUI

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python run_webui.py
# или задать хост/порт через env:
WEBUI_HOST=0.0.0.0 WEBUI_PORT=8000 python run_webui.py