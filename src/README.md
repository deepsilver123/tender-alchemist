# tender-alchemist
Проект для анализа тендерной документации и поиска подходящих предложений


Установка и запуск WebUI

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

python run_webui.py
# или задать хост/порт через env:
WEBUI_HOST=0.0.0.0 WEBUI_PORT=8000 python run_webui.py


## Merlion price lists

Short helper scripts were added to convert Merlion XML price lists into the
e2e4_flat CSV layout used by `scripts/import_catalog.py`.

- Convert a Merlion XML to CSV:

```bash
python scripts/merlion_to_csv.py path/to/merlion.xml --supplier "Merlion" --out data/catalogs/merlion_flat.csv
```

- Convert and import into Elasticsearch (wrapper):

```bash
python scripts/import_merlion.py path/to/merlion.xml --out data/catalogs/merlion_flat.csv --batch 1000 --clear
```

- Quick smoke test (verifies output has rows and >=9 columns):

```bash
python scripts/test_merlion_conversion.py path/to/merlion.xml
```

The converter is lenient with XML tag names and attempts to extract `id`,
`url`, `name`, `price`, `quantity` and common `param`/`property` tags and
concatenate them into the `full_name` field.
