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
from pathlib import Path

# Ensure `src` is on sys.path so imports like `import config` work when
# running this file directly (e.g. `python src/client_app/gui.py`).
_THIS_DIR = Path(__file__).resolve().parent
_SRC_DIR = str(_THIS_DIR.parent)
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

if TYPE_CHECKING:
    QSizePolicy: Any
    Qt: Any
    QDockWidget: Any
    QFrame: Any
    QCoreApplication: Any

from core.config import (
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
        self.qt_app = cast(QApplication, QApplication.instance() or QApplication(sys.argv))
        self.window = QMainWindow()
        self.window.setWindowTitle("Tender Alchemist")
        self.window.resize(1100, 760)
        self.window.setMinimumSize(920, 660)

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
        self._setup_logging()

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

        self.json_tree = QTreeWidget()
        self.json_tree.setObjectName("JsonTree")
        self.json_tree.setHeaderHidden(True)
        self.json_tree.setVisible(False)
        json_panel_layout.addWidget(self.json_tree)

        view_layout.addWidget(json_panel)

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
        self.debug_dock.setFeatures(QDockWidget.DockWidgetClosable)
        self.debug_dock.setWidget(log_panel)
        self.debug_dock.visibilityChanged.connect(self._on_debug_dock_visibility_changed)
        self.window.addDockWidget(Qt.RightDockWidgetArea, self.debug_dock)
        self.debug_dock.hide()

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
        fmt = logging.Formatter("%(asctime)s %(levelname)s: %(message)s")

        # Rotating file handler (if not present)
        if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in logger.handlers):
            fh = logging.handlers.RotatingFileHandler(
                LOG_DIR / "app.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8"
            )
            fh.setFormatter(fmt)
            logger.addHandler(fh)

        # Stream handler to print to terminal
        import sys
        if not any(isinstance(h, logging.StreamHandler) for h in logger.handlers):
            sh = logging.StreamHandler(sys.stdout)
            sh.setFormatter(fmt)
            logger.addHandler(sh)

        # Qt handler to forward logs into GUI (add if missing)
        if not any(getattr(h, 'emit_cb', None) is not None for h in logger.handlers):
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
        try:
            exists = style_path.exists()
            msg = f"Applying theme: {style_path} exists={exists}"
            # Log to file/handlers and also print to stdout so tests/terminals see it
            try:
                if self.logger:
                    self.logger.info(msg)
            except Exception:
                pass
            try:
                print(msg)
            except Exception:
                pass

            if exists:
                with open(style_path, "r", encoding="utf-8") as f:
                    style = f.read()
                self.qt_app.setStyleSheet(style.replace("__CHECK_ICON__", check_icon_qss))
        except Exception as e:
            try:
                if self.logger:
                    self.logger.warning(f"Failed to apply theme: {e}")
                else:
                    print(f"Failed to apply theme: {e}")
            except Exception:
                pass

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

    def mainloop(self) -> int:
        """Show the main window and run the Qt event loop."""
        try:
            self._apply_theme()
        except Exception:
            pass
        try:
            self.window.show()
            return int(self.qt_app.exec())
        except Exception:
            return 1

    # --- UI handlers (minimal implementations) ---
    def _append_log(self, message: str) -> None:
        try:
            if self.log_output:
                self.log_output.appendPlainText(message)
        except Exception:
            pass
        try:
            # Also print to stdout so terminal shows the same logs
            print(message)
        except Exception:
            pass

    def _set_json_text(self, text: str) -> None:
        try:
            self.last_json = text
            if self.json_output:
                self.json_output.setPlainText(text)
        except Exception:
            pass

    def _set_status(self, text: str) -> None:
        try:
            if hasattr(self, "status_bar") and self.status_bar:
                self.status_bar.showMessage(text)
            else:
                print("STATUS:", text)
        except Exception:
            pass

    def _on_finished(self) -> None:
        # basic cleanup after worker finishes
        self._analysis_running = False
        try:
            if hasattr(self, "btn_analyze"):
                self.btn_analyze.setEnabled(True)
            if hasattr(self, "btn_cancel"):
                self.btn_cancel.setEnabled(False)
            if hasattr(self, "btn_add"):
                self.btn_add.setEnabled(True)
            if hasattr(self, "btn_remove"):
                self.btn_remove.setEnabled(True)
            if hasattr(self, "btn_settings_menu"):
                self.btn_settings_menu.setEnabled(True)
            if hasattr(self, "btn_debug_menu"):
                self.btn_debug_menu.setEnabled(True)
            if hasattr(self, "settings_ministral_url"):
                self.settings_ministral_url.setEnabled(True)
            if hasattr(self, "settings_model"):
                self.settings_model.setEnabled(True)
            if hasattr(self, "settings_docling_url"):
                self.settings_docling_url.setEnabled(True)
        except Exception:
            pass
        if self._analysis_canceled:
            self._set_status("Анализ отменён")
        else:
            self._set_status("Анализ завершён")

    def add_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self.window, "Выберите файлы", self._last_open_dir)
        if not paths:
            return
        for p in paths:
            self.file_list.addItem(p)
            self.file_paths.append(p)
        self._last_open_dir = os.path.dirname(paths[0])
        try:
            self._settings_store.setValue("last_open_dir", self._last_open_dir)
        except Exception:
            pass

    def remove_file(self) -> None:
        items = self.file_list.selectedItems()
        for it in items:
            row = self.file_list.row(it)
            try:
                text = it.text()
            except Exception:
                text = None
            self.file_list.takeItem(row)
            if text and text in self.file_paths:
                try:
                    self.file_paths.remove(text)
                except Exception:
                    pass

    def start_analysis(self) -> None:
        if self._analysis_running:
            return
        # collect files
        files = list(self.file_paths)
        if not files:
            QMessageBox.information(self.window, "Файлы", "Не выбраны файлы для анализа.")
            return

        # disable UI
        self._analysis_running = True
        self._analysis_canceled = False
        self._cancel_event.clear()
        self._set_ui_enabled(False)
        self.btn_cancel.setEnabled(True)
        self._set_status("Анализ документов...")

        # current settings
        self._current_ministral_url = self.settings_ministral_url.text().strip() or MINISTRAL_URL
        self._current_model = self.settings_model.text().strip() or MINISTRAL_MODEL
        self._current_docling_url = self.settings_docling_url.text().strip() or "http://localhost:5001"

        # start AnalysisWorker in QThread
        try:
            from client_app.analysis_worker import AnalysisWorker
        except Exception:
            try:
                from analysis_worker import AnalysisWorker
            except Exception:
                QMessageBox.warning(self.window, "Ошибка", "Не удалось найти AnalysisWorker module.")
                self._on_finished()
                return

        self._analysis_thread = QThread()
        self._analysis_worker = AnalysisWorker(
            file_paths=files,
            ministral_url=self._current_ministral_url,
            ministral_model=self._current_model,
            docling_base=self._current_docling_url,
            cancel_event=self._cancel_event,
            build_prompt=None,
        )
        self._analysis_worker.moveToThread(self._analysis_thread)
        self._analysis_thread.started.connect(self._analysis_worker.start)

        # wire signals
        self._analysis_worker.log.connect(self.signals.log)
        self._analysis_worker.json_ready.connect(self.signals.json_ready)
        self._analysis_worker.status.connect(self.signals.status)
        self._analysis_worker.error.connect(lambda m: self.signals.log.emit(f"❌ {m}"))
        self._analysis_worker.finished.connect(self.signals.finished)
        self._analysis_worker.finished.connect(self._analysis_thread.quit)
        self._analysis_thread.finished.connect(self._on_worker_finished)
        self._analysis_thread.finished.connect(self._analysis_thread.deleteLater)

        self._analysis_thread.start()

    def cancel_analysis(self) -> None:
        if not self._analysis_running:
            return
        self._analysis_canceled = True
        try:
            self._cancel_event.set()
        except Exception:
            pass
        try:
            self.btn_cancel.setEnabled(False)
        except Exception:
            pass
        self._set_status("Отмена анализа...")
        self._append_log("⏹ Запрошена отмена анализа. Ожидаем завершения текущего шага...")

    def _on_worker_finished(self) -> None:
        # cleanup references
        try:
            self._analysis_worker = None
            self._analysis_thread = None
        except Exception:
            pass

    def _toggle_settings_menu(self) -> None:
        try:
            # Accept optional boolean from clicked signal
            # (clicked can emit a checked boolean in some Qt bindings)
            import inspect
            sig = inspect.signature(self._toggle_settings_menu)
        except Exception:
            pass
        try:
            if self.settings_dock.isVisible():
                self.settings_dock.hide()
            else:
                self.settings_dock.show()
            # keep button checked state in sync
            try:
                self.btn_settings_menu.setChecked(self.settings_dock.isVisible())
            except Exception:
                pass
            # log action
            try:
                if self.logger:
                    self.logger.info(f"Settings dock visible={self.settings_dock.isVisible()}")
            except Exception:
                print(f"Settings dock visible={self.settings_dock.isVisible()}")
        except Exception:
            pass

    def _toggle_debug_menu(self) -> None:
        try:
            if self.debug_dock.isVisible():
                self.debug_dock.hide()
            else:
                self.debug_dock.show()
            try:
                self.btn_debug_menu.setChecked(self.debug_dock.isVisible())
            except Exception:
                pass
            try:
                if self.logger:
                    self.logger.info(f"Debug dock visible={self.debug_dock.isVisible()}")
            except Exception:
                print(f"Debug dock visible={self.debug_dock.isVisible()}")
        except Exception:
            pass

    def _copy_json_to_clipboard(self) -> None:
        text = self.last_json or (self.json_output.toPlainText() if self.json_output else "")
        if not text:
            QMessageBox.information(self.window, "Копирование JSON", "JSON ещё не получен.")
            return
        QApplication.clipboard().setText(text)
        self._append_log("📋 JSON скопирован в буфер обмена")

    def _copy_log_to_clipboard(self) -> None:
        text = self.log_output.toPlainText() if self.log_output else ""
        if not text:
            QMessageBox.information(self.window, "Копирование логов", "Логи пусты.")
            return
        QApplication.clipboard().setText(text)
        self._append_log("📋 Логи скопированы в буфер обмена")

    def _save_log_to_file(self) -> None:
        default = str(LOG_DIR / "analysis_log.txt")
        path, _ = QFileDialog.getSaveFileName(self.window, "Сохранить логи как", default, "Text Files (*.txt);;All Files (*)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.log_output.toPlainText() if self.log_output else "")
            self._append_log(f"📁 Логи сохранены в {path}")
        except Exception as e:
            QMessageBox.warning(self.window, "Ошибка", f"Не удалось сохранить логи: {e}")

    def _on_debug_dock_visibility_changed(self, visible: bool) -> None:
        try:
            self.btn_debug_menu.setChecked(bool(visible))
        except Exception:
            pass

    def _on_settings_dock_visibility_changed(self, visible: bool) -> None:
        try:
            self.btn_settings_menu.setChecked(bool(visible))
        except Exception:
            pass

    def _populate_json_tree(self, text: str) -> None:
        # Basic JSON -> QTreeWidget population (shallow)
        self.json_tree.clear()
        try:
            parsed = json.loads(text)
        except Exception:
            item = QTreeWidgetItem(["Ошибка", "Ответ не является валидным JSON"])
            self.json_tree.addTopLevelItem(item)
            return

        root = QTreeWidgetItem(["root", self._json_node_type(parsed)])
        self.json_tree.addTopLevelItem(root)
        self._add_tree_node(root, parsed)
        self.json_tree.expandToDepth(1)

    def _add_tree_node(self, parent: QTreeWidgetItem, value) -> None:
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


def main() -> int:
    app = TenderAnalyzerApp()
    try:
        return app.mainloop()
    except Exception:
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
