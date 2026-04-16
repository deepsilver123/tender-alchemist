from PySide6.QtCore import QObject, Signal, Slot
from datetime import datetime
import time
import os
import json
import re
from typing import Callable, Optional

from core.config import (
    LOG_DIR,
    MINISTRAL_API_KEY,
    MINISTRAL_TEMPERATURE,
    MINISTRAL_NUM_CTX,
    MINISTRAL_NUM_PREDICT,
    MINISTRAL_PROMPT,
)


class AnalysisWorker(QObject):
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

            # If a custom extract_text_func was provided, keep sequential behaviour
            if callable(self.extract_text_func):
                for index, fp in enumerate(self.file_paths, start=1):
                    if self._is_cancel_requested():
                        self.finished.emit()
                        return
                    file_name = os.path.basename(fp)
                    self.log.emit(f"[{index}/{len(self.file_paths)}] Читаю {file_name}...")
                    file_step_start = time.time()
                    content = self.extract_text_func(fp, self.docling_base, self._is_cancel_requested)
                    if self._is_cancel_requested():
                        self.finished.emit()
                        return
                    all_html += f"\n\n--- Файл: {os.path.basename(fp)} ---\n\n{content}\n"
                    file_step_time = time.time() - file_step_start
                    self.log.emit(f"✅ {file_name}: {file_step_time:.2f} сек, символов={len(content)}")
            else:
                # Use parallel extraction helper for built-in reader
                from core.file_reader import extract_texts_from_files

                def _progress_cb(fp: str, content: str, elapsed: float) -> None:
                    try:
                        name = os.path.basename(fp)
                        self.log.emit(f"✅ {name}: extracted, символов={len(content)}")
                    except Exception:
                        pass

                try:
                    texts = extract_texts_from_files(
                        self.file_paths,
                        docling_base_url=self.docling_base,
                        cancel_checker=self._is_cancel_requested,
                        max_workers=4,
                        progress_cb=_progress_cb,
                    )
                except Exception as e:
                    self.log.emit(f"❌ Ошибка при чтении файлов: {e}")
                    self.finished.emit()
                    return

                for fp, content in zip(self.file_paths, texts):
                    if self._is_cancel_requested():
                        self.finished.emit()
                        return
                    all_html += f"\n\n--- Файл: {os.path.basename(fp)} ---\n\n{content}\n"
            read_time = time.time() - read_start
            self.log.emit(f"✅ Этап 1/5 завершён: {read_time:.2f} сек")

            LOG_DIR.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M")
            with open(LOG_DIR / f"original_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(all_html)
            self.log.emit(f"📁 Сохранён исходный объединённый HTML: {str(LOG_DIR / f'original_{timestamp}.html')}")

            self.log.emit("📌 Этап 2/5: детерминированный поиск кандидатов товаров")
            from core.html_cleaner import extract_candidate_products

            candidate_products = extract_candidate_products(all_html)
            self.log.emit(f"✅ Этап 2/5 завершён: кандидатов={len(candidate_products)}")
            if candidate_products:
                preview = "; ".join(candidate_products[:5])
                self.log.emit(f"🔎 Превью кандидатов: {preview}")

            self.log.emit("📌 Этап 3/5: сборка итогового prompt")
            # Provide a sensible fallback if caller didn't pass a build_prompt
            if not callable(self.build_prompt):
                def _default_build_prompt(all_html, candidate_products):
                    cand_preview = "; ".join(candidate_products[:10]) if candidate_products else ""
                    return f"{MINISTRAL_PROMPT}\n\nПредварительные кандидаты: {cand_preview}\n\n{all_html}"
                self.build_prompt = _default_build_prompt

            try:
                full_prompt = self.build_prompt(all_html, candidate_products)
            except Exception as e:
                self.log.emit(f"❌ Ошибка при сборке prompt: {e}")
                self.finished.emit()
                return
            self.log.emit(f"✅ Этап 3/5 завершён: длина prompt={len(full_prompt)} символов")

            with open(LOG_DIR / f"prompt_{timestamp}.html", "w", encoding="utf-8") as f:
                f.write(full_prompt)
            self.log.emit(f"📁 Полный prompt сохранён до отправки API: {str(LOG_DIR / f'prompt_{timestamp}.html')}")

            if self._is_cancel_requested():
                self.finished.emit()
                return

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
                from core.ministral_client import call_ministral

                response = call_ministral(
                    prompt=full_prompt,
                    model=self.ministral_model,
                    api_key=MINISTRAL_API_KEY,
                    base_url=self.ministral_url,
                    temperature=MINISTRAL_TEMPERATURE,
                    num_ctx=MINISTRAL_NUM_CTX,
                    num_predict=MINISTRAL_NUM_PREDICT,
                )

            if self._is_cancel_requested():
                self.finished.emit()
                return
            ai_time = time.time() - ai_start
            if response is None:
                self.log.emit(f"❌ Этап 4/5: AI анализ не дал ответа ({ai_time:.2f} сек)")
                json_str = None
            else:
                self.log.emit(f"✅ Этап 4/5 завершён: ответ получен за {ai_time:.2f} сек")
                try:
                    raw_path = LOG_DIR / f"raw_response_{timestamp}.txt"
                    with open(raw_path, "w", encoding="utf-8") as rf:
                        rf.write(response)
                    self.log.emit(f"📁 Сырой ответ сохранён в {str(raw_path)}")
                except Exception:
                    pass

                fenced_match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', response, re.DOTALL)
                candidate = fenced_match.group(1) if fenced_match else response
                parsed = json.loads(candidate)
                json_str = json.dumps(parsed, ensure_ascii=False, indent=2)

            if self._is_cancel_requested():
                self.finished.emit()
                return

            if not json_str:
                self.log.emit("❌ Ошибка при обращении к Ministral.")
                self.finished.emit()
                return

            if isinstance(json_str, str):
                try:
                    parsed_obj = json.loads(json_str)
                    from core.name_normalizer import normalize_products

                    parsed_obj = normalize_products(parsed_obj)
                    json_str = json.dumps(parsed_obj, ensure_ascii=False, indent=2)
                except Exception as ex:
                    self.log.emit(f"⚠️ Нормализация product_name не выполнена: {ex}")

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
