import sys
import threading
import json
import os
from pathlib import Path
from datetime import datetime
import time
import re

from PySide6.QtCore import QObject, Signal, Qt, QSettings
from PySide6.QtGui import QAction, QFont, QColor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QFrame,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QSizePolicy,
    QGraphicsDropShadowEffect,
)

from typing import TYPE_CHECKING, Any, cast

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
    MINISTRAL_API_KEY,
    MINISTRAL_URL,
    MINISTRAL_TEMPERATURE,
    MINISTRAL_NUM_CTX,
    MINISTRAL_NUM_PREDICT,
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

    def _apply_theme(self):
        self.qt_app.setStyle("Fusion")
        check_icon = DATA_DIR / "checkmark_orange.svg"
        check_icon_qss = check_icon.as_posix()

        style_sheet = """
            QWidget {
                background: #ffffff;
                color: #111111;
                font-family: 'Segoe UI';
                font-size: 10pt;
            }
            QFrame#HeaderCard, QFrame#ControlCard {
                background: #ffffff;
                border: none;
                border-radius: 12px;
            }
            QFrame#ControlCard {
                background: #ffffff;
            }
            QWidget#OutputCard {
                border-left: 1px solid #e6e7ea;
                border-right: 1px solid #e6e7ea;
                border-radius: 8px;
                background: #ffffff;
            }
            QLabel#TitleLabel {
                color: #111111;
                font-weight: 700;
            }
            QLabel#SubtitleLabel {
                color: #111111;
            }
            QLabel#SectionLabel {
                color: #111111;
                font-weight: 600;
            }
            QPushButton {
                border: none;
                border-radius: 8px;
                padding: 8px 14px;
                font-family: 'Segoe UI Semibold';
            }
            QPushButton#PrimaryButton {
                background: #f97316;
                color: #ffffff;
            }
            QPushButton#PrimaryButton:hover {
                background: #ea580c;
            }
            QPushButton#SecondaryButton {
                background: #ffffff;
                color: #111111;
                border: 1px solid #e6e7ea;
            }
            QPushButton#SecondaryButton:hover {
                background: #f9fafb;
            }
            QPushButton#DangerButton {
                background: #fff1f2;
                color: #be123c;
                border: 1px solid #ffdce0;
            }
            QPushButton#DangerButton:hover {
                background: #ffe4e6;
            }
            QListWidget#FileList, QPlainTextEdit#OutputText, QTreeWidget#JsonTree {
                background: #ffffff;
                border: 1px solid #d1d5db;
                border-radius: 8px;
                color: #111111;
                padding: 8px;
                selection-background-color: #e5e7eb;
                selection-color: #111111;
                outline: 0;
            }
            QListWidget#FileList:focus, QPlainTextEdit#OutputText:focus, QTreeWidget#JsonTree:focus {
                border: 1px solid #9ca3af;
            }
            QTabWidget#OutputTabs::pane {
                border: none;
                border-radius: 8px;
                background: #ffffff;
                top: -1px;
            }
            QTabBar::tab {
                background: #f9fafb;
                color: #374151;
                border: none;
                min-width: 100px;
                padding: 8px 14px;
                margin-right: 6px;
                border-top-left-radius: 10px;
                border-top-right-radius: 10px;
                font-family: 'Segoe UI Semibold';
            }
            QTabBar::tab:selected {
                background: #ffffff;
                color: #111111;
            }
            QTabBar::tab:hover:!selected {
                background: #f3f4f6;
                color: #111111;
            }
            QListWidget#FileList::item:selected {
                background: #f3f4f6;
                color: #111111;
                border-radius: 4px;
            }
            QCheckBox#RawCheck {
                color: #111111;
            }
            QCheckBox#RawCheck::indicator {
                width: 16px;
                height: 16px;
                border: 2px solid #9ca3af;
                border-radius: 4px;
                background: #ffffff;
            }
            QCheckBox#RawCheck::indicator:hover {
                border-color: #6b7280;
                background: #f9fafb;
            }
            QCheckBox#RawCheck::indicator:checked {
                border-color: #6b7280;
                background: #ffffff;
                image: url(__CHECK_ICON__);
            }
            QStatusBar {
                background: #ffffff;
                color: #111111;
                border-top: none;
            }
            QScrollBar:vertical {
                background: #f3f4f6;
                width: 12px;
                margin: 0;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                min-height: 22px;
                border-radius: 6px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QScrollBar:horizontal {
                background: #f3f4f6;
                height: 12px;
                margin: 0;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal {
                background: #cbd5e1;
                min-width: 22px;
                border-radius: 6px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
                width: 0px;
            }
            QLineEdit {
                background: #ffffff;
                border: 1px solid #e6e7ea;
                border-radius: 8px;
                color: #111111;
                padding: 6px 10px;
                selection-background-color: #e5e7eb;
                selection-color: #111111;
            }
            QLineEdit:focus {
                border: 1px solid #9ca3af;
                outline: 0;
            }
            /* Ensure inputs in docks stand out slightly from white background */
            QDockWidget#SettingsDock QLineEdit, QDockWidget#DebugDock QLineEdit {
                border: 1px solid #d1d5db;
                background: #ffffff;
            }
            QLabel#SettingsGroupLabel {
                color: #374151;
                font-weight: 600;
                font-size: 10pt;
                padding-top: 4px;
            }
            QFrame#SettingsSeparator {
                color: #e5e7eb;
                background-color: #e5e7eb;
                border: none;
                min-height: 1px;
                max-height: 1px;
            }
            QDockWidget#SettingsDock {
                border: none;
                titlebar-close-icon: url(none);
                titlebar-normal-icon: url(none);
            }
            QDockWidget#SettingsDock::title {
                background: #f9fafb;
                color: #374151;
                padding: 8px 10px;
                border-bottom: none;
                text-align: left;
                font-family: 'Segoe UI Semibold';
            }
            /* Make docks stand out slightly from the white main area */
            QDockWidget#SettingsDock, QDockWidget#DebugDock {
                background: #fbfdff;
                border-left: 1px solid #eef0f2;
                border-top: none;
                border-bottom: none;
            }
            QDockWidget#SettingsDock QWidget, QDockWidget#DebugDock QWidget {
                background: transparent;
            }
            """
        self.qt_app.setStyleSheet(style_sheet.replace("__CHECK_ICON__", check_icon_qss))

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

        thread = threading.Thread(target=self._run_analysis, daemon=True)
        thread.start()

    def cancel_analysis(self):
        if not self._analysis_running:
            return
        self._cancel_event.set()
        self.btn_cancel.setEnabled(False)
        self._set_status("Отмена анализа...")
        self._log_json("⏹ Запрошена отмена анализа. Ожидаем завершения текущего шага...")

    def _is_cancel_requested(self) -> bool:
        if self._cancel_event.is_set():
            if not self._analysis_canceled:
                self._analysis_canceled = True
                self._log_json("⏹ Анализ отменён пользователем.")
            return True
        return False

    def _run_analysis(self):
        start_time = time.time()
        ministral_url = self._current_ministral_url
        ministral_model = self._current_model
        docling_base = self._current_docling_url
        # Heavy parser deps (pandas/bs4) are loaded only when analysis actually starts.
        from file_reader import extract_text_from_file

        self._log_json(f"🚀 Начало анализа: {datetime.now().strftime('%H:%M:%S')}")
        self._log_json(f"📌 Этап 1/5: чтение {len(self.file_paths)} файлов")

        all_html = ""
        read_start = time.time()
        for index, fp in enumerate(self.file_paths, start=1):
            if self._is_cancel_requested():
                self.signals.finished.emit()
                return
            file_name = os.path.basename(fp)
            self._log_json(f"[{index}/{len(self.file_paths)}] Читаю {file_name}...")
            file_step_start = time.time()
            content = extract_text_from_file(fp, docling_base, self._is_cancel_requested)
            if self._is_cancel_requested():
                self.signals.finished.emit()
                return
            all_html += f"\n\n--- Файл: {os.path.basename(fp)} ---\n\n{content}\n"
            file_step_time = time.time() - file_step_start
            self._log_json(f"✅ {file_name}: {file_step_time:.2f} сек, символов={len(content)}")
        read_time = time.time() - read_start
        self._log_json(f"✅ Этап 1/5 завершён: {read_time:.2f} сек")

        log_dir = LOG_DIR
        log_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%d-%m-%Y-%H-%M")
        with open(log_dir / f"original_{timestamp}.html", "w", encoding="utf-8") as f:
            f.write(all_html)
        self._log_json(f"📁 Сохранён исходный объединённый HTML: {str(log_dir / f'original_{timestamp}.html')}")

        self._log_json("📌 Этап 2/5: детерминированный поиск кандидатов товаров")
        # lazy import: heavy HTML parsing (bs4 etc.) only when needed
        try:
            from html_cleaner import extract_candidate_products
        except Exception:
            self._log_json("⚠️ Не удалось импортировать html_cleaner; поиск кандидатов пропущен")
            candidate_products = []
        else:
            candidate_products = extract_candidate_products(all_html)
        self._log_json(f"✅ Этап 2/5 завершён: кандидатов={len(candidate_products)}")
        if candidate_products:
            preview = "; ".join(candidate_products[:5])
            self._log_json(f"🔎 Превью кандидатов: {preview}")

        self._log_json("📌 Этап 3/5: сборка итогового prompt")
        full_prompt = self._build_analysis_prompt(all_html, candidate_products)
        self._log_json(f"✅ Этап 3/5 завершён: длина prompt={len(full_prompt)} символов")

        prompt_path = log_dir / f"prompt_{timestamp}.html"
        with open(prompt_path, "w", encoding="utf-8") as f:
            f.write(full_prompt)
        self._log_json(f"📁 Полный prompt сохранён до отправки API: {str(prompt_path)}")

        if self._is_cancel_requested():
            self.signals.finished.emit()
            return
        self._log_json("📌 Этап 4/5: отправка prompt в Ministral API")
        self._log_json(f"🧠 Модель: {ministral_model}; URL: {ministral_url}")
        ai_start = time.time()
        # lazy import: network client only when calling the model
        try:
            from ministral_client import call_ministral
        except Exception:
            self._log_json("❌ Не удалось импортировать клиент Ministral/Ollama; пропуск AI шага")
            response = None
        else:
            response = call_ministral(
                prompt=full_prompt,
                model=ministral_model,
                api_key=MINISTRAL_API_KEY,
                base_url=ministral_url,
                temperature=MINISTRAL_TEMPERATURE,
                num_ctx=MINISTRAL_NUM_CTX,
                num_predict=MINISTRAL_NUM_PREDICT,
            )
        if self._is_cancel_requested():
            self.signals.finished.emit()
            return
        ai_time = time.time() - ai_start
        if response is None:
            self._log_json(f"❌ Этап 4/5: AI анализ не дал ответа ({ai_time:.2f} сек)")
            json_str = None
        else:
            self._log_json(f"✅ Этап 4/5 завершён: ответ получен за {ai_time:.2f} сек")
            json_str = self._extract_json_response(response)

        if self._is_cancel_requested():
            self.signals.finished.emit()
            return

        if not json_str:
            self._log_json("❌ Ошибка при обращении к Ministral.")
            self.signals.finished.emit()
            return

        if self.show_raw_checkbox.isChecked():
            self._log_json("=== СЫРОЙ ОТВЕТ МОДЕЛИ ===")
            self._log_json(json_str[:2000] if len(json_str) > 2000 else json_str)
            self._log_json("=== КОНЕЦ СЫРОГО ОТВЕТА ===")

        self._log_json("📌 Этап 5/5: публикация и сохранение JSON")
        self._log_json("✅ JSON получен")
        self.signals.json_ready.emit(json_str)
        self.last_json = json_str

        result_path = os.path.join(log_dir, f"result_{timestamp}.json")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(json_str)
        self._log_json(f"📁 Результат сохранён в {result_path}")
        self._log_json("✅ Этап 5/5 завершён")

        total_time = time.time() - start_time
        self._log_json(f"🎉 Анализ завершён за {total_time:.2f} сек")
        self.signals.finished.emit()

    def _log_json(self, message: str):
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
        fenced_match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', response, re.DOTALL)
        if fenced_match:
            return fenced_match.group(1)
        return response

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

    def mainloop(self):
        # Apply theme here to avoid blocking import/initialization time
        try:
            self._apply_theme()
        except Exception:
            # Ensure app still runs even if theme application fails
            self._log_json("⚠️ Применение темы не удалось — продолжаем без неё")
        self.window.show()
        return self.qt_app.exec()
