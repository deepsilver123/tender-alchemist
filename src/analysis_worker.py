from PySide6.QtCore import QObject, Signal, Slot
from datetime import datetime
import time
import os
import json
import re
from pathlib import Path
from typing import Callable, Optional

from config import (
    LOG_DIR,
    MINISTRAL_API_KEY,
    MINISTRAL_TEMPERATURE,
    MINISTRAL_NUM_CTX,
    MINISTRAL_NUM_PREDICT,
)


class AnalysisWorker(QObject):
    """Worker that runs the analysis pipeline inside a QThread.

    Signals:
        log(str): textual log lines
        json_ready(str): final JSON result
        status(str): current status message
        finished(): emitted when worker finishes (success or cancel)
        error(str): emitted on unexpected exceptions
    """

    log = Signal(str)
    json_ready = Signal(str)
    status = Signal(str)
    finished = Signal()
    error = Signal(str)

    def __init__(
        self,
        file_paths: list[str],
        ministral_url: str,
        ministral_model: str,
        docling_base: str,
        cancel_event,
        build_prompt: Optional[Callable[[str, list[str]], str]] = None,
        call_ministral_func: Optional[Callable] = None,
        extract_text_func: Optional[Callable] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.file_paths = list(file_paths)
        self.ministral_url = ministral_url
        self.ministral_model = ministral_model
        self.docling_base = docling_base
        self.cancel_event = cancel_event
        self.build_prompt = build_prompt
        self.call_ministral_func = call_ministral_func
        self.extract_text_func = extract_text_func

    def _is_cancel_requested(self) -> bool:
        if self.cancel_event is not None and self.cancel_event.is_set():
            self.log.emit("⏹ Анализ отменён пользователем.")
            return True
        return False

    @Slot()
    def start(self):
        try:
            start_time = time.time()
            self.log.emit(f"🚀 Начало анализа: {datetime.now().strftime('%H:%M:%S')}")
            self.log.emit(f"📌 Этап 1/5: чтение {len(self.file_paths)} файлов")

            all_html = ""
            read_start = time.time()
            for index, fp in enumerate(self.file_paths, start=1):
                if self._is_cancel_requested():
                    self.finished.emit()
                    return
                file_name = os.path.basename(fp)
                self.log.emit(f"[{index}/{len(self.file_paths)}] Читаю {file_name}...")
                file_step_start = time.time()
                if self.extract_text_func:
                    content = self.extract_text_func(fp, self.docling_base, self._is_cancel_requested)
                else:
                    # lazy import to avoid heavy deps at module import time
                    from file_reader import extract_text_from_file

                    content = extract_text_from_file(fp, self.docling_base, self._is_cancel_requested)
                if self._is_cancel_requested():
                    self.finished.emit()
                    return
                all_html += f"\n\n--- Файл: {os.path.basename(fp)} ---\n\n{content}\n"
                file_step_time = time.time() - file_step_start
                self.log.emit(f"✅ {file_name}: {file_step_time:.2f} сек, символов={len(content)}")
            read_time = time.time() - read_start
            self.log.emit(f"✅ Этап 1/5 завершён: {read_time:.2f} сек")

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M")
            with open(LOG_DIR / f"original_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(all_html)
            self.log.emit(f"📁 Сохранён исходный объединённый HTML: {str(LOG_DIR / f'original_{timestamp}.html')}")

            # Candidates
            self.log.emit("📌 Этап 2/5: детерминированный поиск кандидатов товаров")
            try:
                from html_cleaner import extract_candidate_products

                candidate_products = extract_candidate_products(all_html)
            except Exception:
                self.log.emit("⚠️ Не удалось импортировать html_cleaner; поиск кандидатов пропущен")
                candidate_products = []
            self.log.emit(f"✅ Этап 2/5 завершён: кандидатов={len(candidate_products)}")
            if candidate_products:
                preview = "; ".join(candidate_products[:5])
                self.log.emit(f"🔎 Превью кандидатов: {preview}")

            # Prompt
            self.log.emit("📌 Этап 3/5: сборка итогового prompt")
            if self.build_prompt:
                full_prompt = self.build_prompt(all_html, candidate_products)
            else:
                # fallback minimal prompt
                full_prompt = all_html
            self.log.emit(f"✅ Этап 3/5 завершён: длина prompt={len(full_prompt)} символов")

            with open(LOG_DIR / f"prompt_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(full_prompt)
            self.log.emit(f"📁 Полный prompt сохранён до отправки API: {str(LOG_DIR / f'prompt_{timestamp}.html')}")

            if self._is_cancel_requested():
                self.finished.emit()
                return

            # Call model
            self.log.emit("📌 Этап 4/5: отправка prompt в Ministral API")
            self.log.emit(f"🧠 Модель: {self.ministral_model}; URL: {self.ministral_url}")
            ai_start = time.time()
            if self.call_ministral_func:
                response = self.call_ministral_func(
                    prompt=full_prompt,
                    model=self.ministral_model,
                    api_key=MINISTRAL_API_KEY,
                    base_url=self.ministral_url,
                    temperature=MINISTRAL_TEMPERATURE,
                    num_ctx=MINISTRAL_NUM_CTX,
                    num_predict=MINISTRAL_NUM_PREDICT,
                )
            else:
                try:
                    from ministral_client import call_ministral

                    response = call_ministral(
                        prompt=full_prompt,
                        model=self.ministral_model,
                        api_key=MINISTRAL_API_KEY,
                        base_url=self.ministral_url,
                        temperature=MINISTRAL_TEMPERATURE,
                        num_ctx=MINISTRAL_NUM_CTX,
                        num_predict=MINISTRAL_NUM_PREDICT,
                    )
                except Exception:
                    self.log.emit("❌ Не удалось импортировать клиент Ministral/Ollama; пропуск AI шага")
                    response = None

            if self._is_cancel_requested():
                self.finished.emit()
                return
            ai_time = time.time() - ai_start
            if response is None:
                self.log.emit(f"❌ Этап 4/5: AI анализ не дал ответа ({ai_time:.2f} сек)")
                json_str = None
            else:
                self.log.emit(f"✅ Этап 4/5 завершён: ответ получен за {ai_time:.2f} сек")
                # Try to robustly extract JSON-like payload
                fenced_match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', response, re.DOTALL)
                candidate = fenced_match.group(1) if fenced_match else response
                # Try JSON decode
                try:
                    parsed = json.loads(candidate)
                    json_str = json.dumps(parsed, ensure_ascii=False)
                except Exception:
                    # fallback: find any JSON-like substring
                    match = re.search(r'(\{(?:.|\n)*\}|\[(?:.|\n)*\])', response, re.DOTALL)
                    json_str = match.group(1) if match else response

            if self._is_cancel_requested():
                self.finished.emit()
                return

            if not json_str:
                self.log.emit("❌ Ошибка при обращении к Ministral.")
                self.finished.emit()
                return

            # Save and emit
            if isinstance(json_str, str):
                self.json_ready.emit(json_str)
                result_path = LOG_DIR / f"result_{timestamp}.json"
                with open(result_path, "w", encoding="utf-8") as f:
                    f.write(json_str)
                self.log.emit(f"📁 Результат сохранён в {str(result_path)}")
                self.log.emit("✅ Этап 5/5 завершён")

            total_time = time.time() - start_time
            self.log.emit(f"🎉 Анализ завершён за {total_time:.2f} сек")
            self.finished.emit()
        except Exception as e:
            try:
                import traceback

                tb = traceback.format_exc()
            except Exception:
                tb = ""
            self.error.emit(f"{e}\n{tb}")
            self.finished.emit()
