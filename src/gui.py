import sys
import threading
import json
import os

from PySide6.QtCore import QObject, Signal, Qt, QSettings, QThread
from PySide6.QtGui import QAction, QFont, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QStatusBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QGraphicsDropShadowEffect,
)

from typing import TYPE_CHECKING, Any, cast, Optional
import logging
import logging.handlers

if TYPE_CHECKING:
    # Provide Any-typed aliases so Pylance won't complain about attributes
    QSizePolicy: Any
    Qt: Any
    QDockWidget: Any
    QFrame: Any
    QCoreApplication: Any

# lazy imports: heavy modules (html_cleaner, ministral_client) are loaded only when analysis runs
# project-level `data/` and `logs/` are defined in config.py
from config import (
    MINISTRAL_PROMPT,
    MINISTRAL_MODEL,
    MINISTRAL_URL,
    DATA_DIR,
    LOG_DIR,
)


class UiSignals(QObject):
    log = Signal(str)
    json_ready = Signal(str)
    status = Signal(str)
    finished = Signal()


class TenderAnalyzerApp:
    def __init__(self):
        # Ensure the type is recognized as QApplication (not QCoreApplication)
        self.qt_app = cast(QApplication, QApplication.instance() or QApplication(sys.argv))
        self.window = QMainWindow()
        self.window.setWindowTitle("Tender Alchemist")
        self.window.resize(1100, 760)
        self.window.setMinimumSize(920, 660)

        # Typed attributes (help static analysis)
        self.json_output: Optional[QPlainTextEdit] = None
        self.log_output: Optional[QPlainTextEdit] = None
        self.json_tree: Optional[QTreeWidget] = None
        self.file_list: Optional[QListWidget] = None
        self._analysis_thread: Optional[QThread] = None
        self._analysis_worker: Optional[object] = None
        self.logger: Optional[logging.Logger] = None

        self.last_json = None
        self.file_paths = []
        self._settings_store = QSettings("TenderAlchemist", "TenderAlchemistApp")
        last_open = self._settings_store.value("last_open_dir", os.path.expanduser("~"))
        # QSettings.value can return QVariant/None; ensure we have a str for the file dialog
        self._last_open_dir: str = last_open if isinstance(last_open, str) else os.path.expanduser("~")
        self._cancel_event = threading.Event()
        self._analysis_running = False
        self._analysis_canceled = False

        self.signals = UiSignals()
        self.signals.log.connect(self._append_log)
        self.signals.json_ready.connect(self._set_json_text)
        self.signals.status.connect(self._set_status)
        self.signals.finished.connect(self._on_finished)

        self._build_ui()
        # Configure logging (file + Qt handler)
        self._setup_logging()
        # defer applying theme until show() to reduce perceived startup latency
        # (applies stylesheet right before window becomes visible)

    def _build_ui(self):
        root = QWidget()
        self.window.setCentralWidget(root)

        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(14, 14, 14, 14)
        main_layout.setSpacing(10)

        controls = QFrame()
        controls.setObjectName("ControlCard")
        controls_layout = QVBoxLayout(controls)
        controls_layout.setContentsMargins(12, 10, 12, 10)
        controls_layout.setSpacing(8)

        files_title = QLabel("Источники данных")
        files_title.setObjectName("SectionLabel")
        controls_layout.addWidget(files_title)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)

        self.btn_add = QPushButton("Добавить файлы")
        self.btn_add.setObjectName("SecondaryButton")
        self.btn_add.clicked.connect(self.add_files)

        self.btn_remove = QPushButton("Удалить выбранный")
        self.btn_remove.setObjectName("DangerButton")
        self.btn_remove.clicked.connect(self.remove_file)

        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_remove)
        btn_row.addStretch(1)
        controls_layout.addLayout(btn_row)

        self.file_list = QListWidget()
        self.file_list.setObjectName("FileList")
        self.file_list.setMinimumHeight(84)
        self.file_list.setMaximumHeight(120)
        self.file_list.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        controls_layout.addWidget(self.file_list)

        self.show_raw_checkbox = QCheckBox("Показать сырой ответ модели (для отладки)")
        self.show_raw_checkbox.setObjectName("RawCheck")
        controls_layout.addWidget(self.show_raw_checkbox)

        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.btn_analyze = QPushButton("Запустить анализ")
        self.btn_analyze.setObjectName("PrimaryButton")
        self.btn_analyze.clicked.connect(self.start_analysis)
        self.btn_cancel = QPushButton("Отменить")
        self.btn_cancel.setObjectName("SecondaryButton")
        self.btn_cancel.clicked.connect(self.cancel_analysis)
        self.btn_cancel.setEnabled(False)
        self.btn_settings_menu = QPushButton("⚙ Настройки")
        self.btn_settings_menu.setObjectName("SecondaryButton")
        self.btn_settings_menu.setCheckable(True)
        self.btn_settings_menu.clicked.connect(self._toggle_settings_menu)
        self.btn_debug_menu = QPushButton("🐛 Отладка")
        self.btn_debug_menu.setObjectName("SecondaryButton")
        self.btn_debug_menu.setCheckable(True)
        self.btn_debug_menu.clicked.connect(self._toggle_debug_menu)
        action_row.addWidget(self.btn_analyze)
        action_row.addWidget(self.btn_cancel)
        action_row.addWidget(self.btn_settings_menu)
        action_row.addWidget(self.btn_debug_menu)
        action_row.addStretch(1)
        controls_layout.addLayout(action_row)

        main_layout.addWidget(controls)

        output_card = QWidget()
        output_card.setObjectName("OutputCard")
        # Add a subtle right-side shadow to visually separate the central area from docks
        shadow = QGraphicsDropShadowEffect(self.window)
        shadow.setBlurRadius(18)
        shadow.setOffset(4, 0)
        shadow.setColor(QColor(0, 0, 0, 30))
        output_card.setGraphicsEffect(shadow)
        output_layout = QVBoxLayout(output_card)
        output_layout.setContentsMargins(12, 10, 12, 10)

        view_widget = QWidget()
        view_layout = QVBoxLayout(view_widget)
        view_layout.setContentsMargins(0, 0, 0, 0)
        view_layout.setSpacing(8)

        # Simple JSON panel (single text view) and a separate fixed debug dock for logs
        json_panel = QWidget()
        json_panel_layout = QVBoxLayout(json_panel)
        json_panel_layout.setContentsMargins(0, 0, 0, 0)
        json_panel_layout.setSpacing(6)

        json_header = QHBoxLayout()
        json_header.addWidget(QLabel("JSON"))
        json_header.addStretch(1)
        self.btn_copy_json = QPushButton("Копировать JSON")
        self.btn_copy_json.setObjectName("SecondaryButton")
        self.btn_copy_json.clicked.connect(self._copy_json_to_clipboard)
        json_header.addWidget(self.btn_copy_json)
        json_panel_layout.addLayout(json_header)

        self.json_output = QPlainTextEdit()
        self.json_output.setObjectName("OutputText")
        self.json_output.setReadOnly(True)
        self.json_output.setFont(QFont("Consolas", 10))
        self.json_output.setContextMenuPolicy(Qt.CustomContextMenu)
        self.json_output.customContextMenuRequested.connect(
            lambda pos: self._show_text_context_menu(self.json_output, pos)
        )
        json_panel_layout.addWidget(self.json_output)

        # Tree view for structured JSON (created here so Pylance sees the attribute)
        self.json_tree = QTreeWidget()
        self.json_tree.setObjectName("JsonTree")
        self.json_tree.setHeaderHidden(True)
        self.json_tree.setVisible(False)
        json_panel_layout.addWidget(self.json_tree)

        view_layout.addWidget(json_panel)

        # --- Логи (будут в правом доке) ---
        log_panel = QWidget()
        log_panel_layout = QVBoxLayout(log_panel)
        log_panel_layout.setContentsMargins(12, 10, 12, 10)
        log_panel_layout.setSpacing(6)

        log_header = QHBoxLayout()
        log_header.addWidget(QLabel("Логи этапов"))
        log_header.addStretch(1)
        self.btn_copy_log = QPushButton("Копировать")
        self.btn_copy_log.setObjectName("SecondaryButton")
        self.btn_copy_log.clicked.connect(self._copy_log_to_clipboard)
        log_header.addWidget(self.btn_copy_log)
        self.btn_save_log = QPushButton("Сохранить как...")
        self.btn_save_log.setObjectName("SecondaryButton")
        self.btn_save_log.clicked.connect(self._save_log_to_file)
        log_header.addWidget(self.btn_save_log)
        log_panel_layout.addLayout(log_header)

        self.log_output = QPlainTextEdit()
        self.log_output.setObjectName("OutputText")
        self.log_output.setReadOnly(True)
        self.log_output.setFont(QFont("Consolas", 10))
        self.log_output.setContextMenuPolicy(Qt.CustomContextMenu)
        self.log_output.customContextMenuRequested.connect(
            lambda pos: self._show_text_context_menu(self.log_output, pos)
        )
        log_panel_layout.addWidget(self.log_output)

        self.debug_dock = QDockWidget("Отладка", self.window)
        self.debug_dock.setObjectName("DebugDock")
        self.debug_dock.setAllowedAreas(Qt.RightDockWidgetArea)
        # Allow resizing while preventing floating/moving: enable closable only
        self.debug_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.debug_dock.setWidget(log_panel)
        self.debug_dock.visibilityChanged.connect(self._on_debug_dock_visibility_changed)
        self.window.addDockWidget(Qt.RightDockWidgetArea, self.debug_dock)
        self.debug_dock.hide()

        # --- Настройки ---
        settings_widget = QWidget()
        settings_layout = QVBoxLayout(settings_widget)
        settings_layout.setContentsMargins(16, 14, 16, 14)
        settings_layout.setSpacing(10)

        ministral_section = QLabel("Ministral / Ollama")
        ministral_section.setObjectName("SettingsGroupLabel")
        settings_layout.addWidget(ministral_section)

        min_url_row = QHBoxLayout()
        min_url_row.setSpacing(10)
        min_url_lbl = QLabel("URL API:")
        min_url_lbl.setFixedWidth(130)
        self.settings_ministral_url = QLineEdit(MINISTRAL_URL)
        self.settings_ministral_url.setPlaceholderText("http://localhost:11434/api")
        min_url_row.addWidget(min_url_lbl)
        min_url_row.addWidget(self.settings_ministral_url)
        settings_layout.addLayout(min_url_row)

        min_model_row = QHBoxLayout()
        min_model_row.setSpacing(10)
        min_model_lbl = QLabel("Модель:")
        min_model_lbl.setFixedWidth(130)
        self.settings_model = QLineEdit(MINISTRAL_MODEL)
        self.settings_model.setPlaceholderText("ministral-3:3b")
        min_model_row.addWidget(min_model_lbl)
        min_model_row.addWidget(self.settings_model)
        settings_layout.addLayout(min_model_row)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setObjectName("SettingsSeparator")
        settings_layout.addWidget(sep)

        docling_section = QLabel("Docling (конвертация документов)")
        docling_section.setObjectName("SettingsGroupLabel")
        settings_layout.addWidget(docling_section)

        doc_url_row = QHBoxLayout()
        doc_url_row.setSpacing(10)
        doc_url_lbl = QLabel("Базовый URL:")
        doc_url_lbl.setFixedWidth(130)
        self.settings_docling_url = QLineEdit("http://localhost:5001")
        self.settings_docling_url.setPlaceholderText("http://localhost:5001")
        doc_url_row.addWidget(doc_url_lbl)
        doc_url_row.addWidget(self.settings_docling_url)
        settings_layout.addLayout(doc_url_row)

        settings_layout.addStretch(1)

        self.settings_dock = QDockWidget("Настройки", self.window)
        self.settings_dock.setObjectName("SettingsDock")
        self.settings_dock.setAllowedAreas(Qt.LeftDockWidgetArea)
        # Allow resizing while preventing floating/moving: enable closable only
        self.settings_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.settings_dock.setWidget(settings_widget)
        self.settings_dock.visibilityChanged.connect(self._on_settings_dock_visibility_changed)
        self.window.addDockWidget(Qt.LeftDockWidgetArea, self.settings_dock)
        self.settings_dock.hide()

        output_layout.addWidget(view_widget)

        main_layout.addWidget(output_card, 1)

        self.status_bar = QStatusBar()
        self.window.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Готов к работе")

    def _setup_logging(self):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        logger = logging.getLogger("tender")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            fh = logging.handlers.RotatingFileHandler(
                LOG_DIR / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
            fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")
            fh.setFormatter(fmt)
            logger.addHandler(fh)

            class QtHandler(logging.Handler):
                def __init__(self, emit_cb):
                    super().__init__()
                    self.emit_cb = emit_cb

                def emit(self, record):
                    try:
                        msg = self.format(record)
                    except Exception:
                        msg = str(record)
                    try:
                        self.emit_cb(msg)
                    except Exception:
                        pass

            qt = QtHandler(lambda m: self.signals.log.emit(m))
            qt.setFormatter(fmt)
            logger.addHandler(qt)

        self.logger = logger

    def _set_ui_enabled(self, enabled: bool):
        # Helper to enable/disable main controls during analysis
        self.btn_analyze.setEnabled(enabled)
        self.btn_add.setEnabled(enabled)
        self.btn_remove.setEnabled(enabled)
        self.btn_settings_menu.setEnabled(enabled)
        self.btn_debug_menu.setEnabled(enabled)
        self.settings_ministral_url.setEnabled(enabled)
        self.settings_model.setEnabled(enabled)
        self.settings_docling_url.setEnabled(enabled)

    def _apply_theme(self):
        self.qt_app.setStyle("Fusion")
        check_icon = DATA_DIR / "checkmark_orange.svg"
        check_icon_qss = check_icon.as_posix()

        style_path = DATA_DIR / "style.qss"
        if style_path.exists():
            with open(style_path, "r", encoding="utf-8") as f:
                style = f.read()
            self.qt_app.setStyleSheet(style.replace("__CHECK_ICON__", check_icon_qss))

    def _show_text_context_menu(self, text_widget, pos):
        menu = QMenu(self.window)
        copy_action = QAction("Копировать", self.window)
        copy_all_action = QAction("Копировать всё", self.window)
        select_all_action = QAction("Выделить всё", self.window)

        copy_action.triggered.connect(text_widget.copy)
        copy_all_action.triggered.connect(lambda: QApplication.clipboard().setText(text_widget.toPlainText()))
        select_all_action.triggered.connect(text_widget.selectAll)

        menu.addAction(copy_action)
        menu.addAction(copy_all_action)
        menu.addSeparator()
        menu.addAction(select_all_action)
        menu.exec(text_widget.mapToGlobal(pos))

    def _toggle_settings_menu(self, checked: bool):
        if checked:
            self.settings_dock.show()
            self.settings_dock.raise_()
        else:
            self.settings_dock.hide()

    def _on_settings_dock_visibility_changed(self, visible: bool):
        self.btn_settings_menu.setChecked(visible)

    def _toggle_debug_menu(self, checked: bool):
        if checked:
            self.debug_dock.show()
            self.debug_dock.raise_()
        else:
            self.debug_dock.hide()

    def _on_debug_dock_visibility_changed(self, visible: bool):
        self.btn_debug_menu.setChecked(visible)

    def _copy_log_to_clipboard(self):
        text = self.log_output.toPlainText()
        if not text:
            QMessageBox.information(self.window, "Копирование", "Лог пуст.")
            return
        QApplication.clipboard().setText(text)
        self._log_json("📋 Лог скопирован в буфер обмена")

    def _save_log_to_file(self):
        path, _ = QFileDialog.getSaveFileName(self.window, "Сохранить лог", os.path.join(os.path.expanduser('~'), 'log.txt'), "Текстовые файлы (*.txt);;Все файлы (*.*)")
        if not path:
            return
        try:
            with open(path, 'w', encoding='utf-8') as f:
                f.write(self.log_output.toPlainText())
            self._log_json(f"📁 Лог сохранён в {path}")
        except Exception as e:
            QMessageBox.critical(self.window, "Ошибка", f"Не удалось сохранить лог: {e}")

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(
            self.window,
            "Выберите файлы ТЗ и НМЦК",
            self._last_open_dir,
            "Документы (*.docx *.pdf *.txt *.xlsx *.xls);;Все файлы (*.*)",
        )
        if files:
            self._last_open_dir = os.path.dirname(files[0])
            self._settings_store.setValue("last_open_dir", self._last_open_dir)
        for file_path in files:
            if file_path not in self.file_paths:
                self.file_paths.append(file_path)
                self.file_list.addItem(os.path.basename(file_path))

    def remove_file(self):
        row = self.file_list.currentRow()
        if row >= 0:
            self.file_list.takeItem(row)
            del self.file_paths[row]

    def start_analysis(self):
        if not self.file_paths:
            QMessageBox.critical(self.window, "Ошибка", "Не выбрано ни одного файла.")
            return

        self.json_output.clear()
        self.log_output.clear()
        self.last_json = None
        self._analysis_running = True
        self._analysis_canceled = False
        self._cancel_event.clear()
        self.btn_analyze.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.btn_add.setEnabled(False)
        self.btn_remove.setEnabled(False)
        self.settings_ministral_url.setEnabled(False)
        self.settings_model.setEnabled(False)
        self.settings_docling_url.setEnabled(False)
        self._set_status("Анализ документов...")

        self._current_ministral_url = self.settings_ministral_url.text().strip() or MINISTRAL_URL
        self._current_model = self.settings_model.text().strip() or MINISTRAL_MODEL
        self._current_docling_url = self.settings_docling_url.text().strip() or "http://localhost:5001"

        # Start analysis inside a QThread using AnalysisWorker
        from analysis_worker import AnalysisWorker

        self._analysis_thread = QThread()
        self._analysis_worker = AnalysisWorker(
            file_paths=self.file_paths,
            ministral_url=self._current_ministral_url,
            ministral_model=self._current_model,
            docling_base=self._current_docling_url,
            cancel_event=self._cancel_event,
            build_prompt=self._build_analysis_prompt,
        )
        self._analysis_worker.moveToThread(self._analysis_thread)
        self._analysis_thread.started.connect(self._analysis_worker.start)

        # Forward worker signals to UI signals
        self._analysis_worker.log.connect(self.signals.log)
        self._analysis_worker.json_ready.connect(self.signals.json_ready)
        self._analysis_worker.status.connect(self.signals.status)
        self._analysis_worker.error.connect(lambda m: self._log_json(f"❌ {m}"))

        # Ensure UI on finish
        self._analysis_worker.finished.connect(self.signals.finished)
        self._analysis_worker.finished.connect(self._analysis_thread.quit)
        self._analysis_worker.finished.connect(self._on_worker_finished)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)

        self._analysis_thread.start()

    def cancel_analysis(self):
        if not self._analysis_running:
            return
        self._analysis_canceled = True
        self._cancel_event.set()
        self.btn_cancel.setEnabled(False)
        self._set_status("Отмена анализа...")
        self._log_json("⏹ Запрошена отмена анализа. Ожидаем завершения текущего шага...")

    # Legacy synchronous analysis runner removed — AnalysisWorker in QThread is used exclusively

    def _log_json(self, message: str):
        if self.logger:
            self.logger.info(message)
        else:
            self.signals.log.emit(message)

    def _build_analysis_prompt(self, all_html: str, candidate_products: list[str]) -> str:
        if not candidate_products:
            return MINISTRAL_PROMPT + "\n\n" + all_html

        candidate_block = [
            "[ПРЕДВАРИТЕЛЬНО ИЗВЛЕЧЕННЫЕ КАНДИДАТЫ ТОВАРОВ]",
            "Ниже перечислены кандидаты на наименования товаров, найденные детерминированно по таблицам документа.",
            "Используй этот список как ориентир для сопоставления данных, но не выдумывай товары и не считай список исчерпывающим.",
            "Если в документе есть более точное наименование, чем в списке, выбирай более точное наименование из самого документа.",
        ]
        candidate_block.extend(f"- {name}" for name in candidate_products)
        candidate_block.append("[/ПРЕДВАРИТЕЛЬНО ИЗВЛЕЧЕННЫЕ КАНДИДАТЫ ТОВАРОВ]")

        self._log_json(f"🔎 Предварительно найдено кандидатов товаров: {len(candidate_products)}")
        return MINISTRAL_PROMPT + "\n\n" + "\n".join(candidate_block) + "\n\n" + all_html

    def _extract_json_response(self, response: str):
        # Delegate robust extraction to helper to make testing easier
        try:
            from json_utils import extract_json_from_text

            return extract_json_from_text(response)
        except Exception:
            return None

    def _append_log(self, message: str):
        self.log_output.appendPlainText(message)

    def _set_json_text(self, text: str):
        self.json_output.setPlainText(text)

    def _populate_json_tree(self, text: str):
        self.json_tree.clear()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            item = QTreeWidgetItem(["Ошибка", "Ответ не является валидным JSON"])
            self.json_tree.addTopLevelItem(item)
            return

        root = QTreeWidgetItem(["root", self._json_node_type(parsed)])
        self.json_tree.addTopLevelItem(root)
        self._add_tree_node(root, parsed)
        self.json_tree.expandToDepth(1)

    def _add_tree_node(self, parent: QTreeWidgetItem, value):
        if isinstance(value, dict):
            for key, child_value in value.items():
                node = QTreeWidgetItem([str(key), self._json_node_preview(child_value)])
                parent.addChild(node)
                self._add_tree_node(node, child_value)
            return

        if isinstance(value, list):
            for index, child_value in enumerate(value):
                node = QTreeWidgetItem([f"[{index}]", self._json_node_preview(child_value)])
                parent.addChild(node)
                self._add_tree_node(node, child_value)
            return

    def _json_node_type(self, value) -> str:
        if isinstance(value, dict):
            return f"object ({len(value)})"
        if isinstance(value, list):
            return f"array ({len(value)})"
        return type(value).__name__

    def _json_node_preview(self, value) -> str:
        if isinstance(value, dict):
            return f"object ({len(value)})"
        if isinstance(value, list):
            return f"array ({len(value)})"
        if value is None:
            return "null"
        text = str(value)
        return text if len(text) <= 120 else text[:117] + "..."

    def _copy_json_to_clipboard(self):
        if not self.last_json:
            QMessageBox.information(self.window, "Копирование JSON", "JSON ещё не получен.")
            return
        QApplication.clipboard().setText(self.last_json)
        self._log_json("📋 JSON скопирован в буфер обмена")

    def _set_status(self, text: str):
        self.status_bar.showMessage(text)

    def _on_finished(self):
        self._analysis_running = False
        self.btn_analyze.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_add.setEnabled(True)
        self.btn_remove.setEnabled(True)
        self.settings_ministral_url.setEnabled(True)
        self.settings_model.setEnabled(True)
        self.settings_docling_url.setEnabled(True)
        if self._analysis_canceled:
            self._set_status("Анализ отменён")
        else:
            self._set_status("Анализ завершён")

    def _on_worker_finished(self):
        # Clean up thread/worker references after worker emits finished
        try:
            if hasattr(self, "_analysis_thread") and self._analysis_thread is not None:
                # Allow thread to quit; it is already asked to quit by connection
                try:
                    self._analysis_thread.wait(1000)
                except Exception:
                    pass
        finally:
            try:
                self._analysis_worker = None
                self._analysis_thread = None
            except Exception:
                pass

    def mainloop(self):
        # Apply theme here to avoid blocking import/initialization time
        self._apply_theme()
        self.window.show()
        return self.qt_app.exec()
