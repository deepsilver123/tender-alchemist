Core package for Tender Alchemist

Expose `analyze_files`, `normalize_products`, and `extract_json_from_text` for UI layers.

Install for development:

pip install -e src/core
# tender_alchemist_core

Thin adapter package exposing core analysis primitives for local development.

Quickstart (developer):

1. From project root, install editable:

```bash
pip install -e src/core
```

2. In Python, import core APIs:

```py
from core import analyze_files, normalize_products

# analyze_files(file_paths, log_callable)
```

Notes:
- This adapter currently re-exports functions from the repository.
- Later we will move implementation files into `src/core` and make this a standalone package.
