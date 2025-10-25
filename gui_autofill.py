#!/usr/bin/env python3
"""
PyQt5 GUI front-end for the smart autofill workflow (based on Auto-Fill project).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from PyQt5 import QtCore, QtWidgets

from autofill_core import FillAction, load_json, perform_autofill


class AutofillWorker(QtCore.QObject):
    finished = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)
    message = QtCore.pyqtSignal(str)
    request_confirmation = QtCore.pyqtSignal(object, object)
    confirmation_reply = QtCore.pyqtSignal(bool)

    def __init__(self, cfg: Dict[str, Any], data: Dict[str, Any]) -> None:
        super().__init__()
        self.cfg = cfg
        self.data = data

    @QtCore.pyqtSlot()
    def run(self) -> None:
        try:
            outcome = perform_autofill(
                self.cfg,
                self.data,
                confirm_submit=self._confirm_submit,
                status_callback=self.message.emit,
            )
            self.finished.emit(outcome)
        except Exception as exc:  # defensive: should not normally happen
            self.failed.emit(str(exc))

    def _confirm_submit(self, actions: List[FillAction], screenshot_path: Path) -> bool:
        loop = QtCore.QEventLoop()
        decision = {"value": False}

        def handle_reply(value: bool) -> None:
            decision["value"] = value
            loop.quit()

        self.confirmation_reply.connect(handle_reply)
        self.request_confirmation.emit(actions, screenshot_path)
        loop.exec_()
        self.confirmation_reply.disconnect(handle_reply)
        return decision["value"]


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Auto-Fill Assistant")
        self.resize(900, 720)

        self.current_config_path: Path | None = None
        self.current_data_path: Path | None = None
        self.autofill_thread: QtCore.QThread | None = None
        self.autofill_worker: AutofillWorker | None = None

        self._build_ui()

    # --- UI setup helpers -------------------------------------------------
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget(self)
        self.setCentralWidget(central)

        main_layout = QtWidgets.QVBoxLayout(central)

        form_layout = QtWidgets.QFormLayout()
        main_layout.addLayout(form_layout)

        self.url_edit = QtWidgets.QLineEdit()
        form_layout.addRow("URL:", self.url_edit)

        self.headless_check = QtWidgets.QCheckBox("Run browser headless")
        form_layout.addRow("", self.headless_check)

        self.timeout_spin = QtWidgets.QSpinBox()
        self.timeout_spin.setRange(1, 120)
        self.timeout_spin.setValue(10)
        form_layout.addRow("Wait timeout (s):", self.timeout_spin)

        self.close_delay_spin = QtWidgets.QDoubleSpinBox()
        self.close_delay_spin.setRange(0.0, 60.0)
        self.close_delay_spin.setDecimals(1)
        self.close_delay_spin.setSingleStep(0.5)
        self.close_delay_spin.setValue(10.0)
        form_layout.addRow("Close delay (s):", self.close_delay_spin)

        self.submit_selector_edit = QtWidgets.QLineEdit("button[type='submit'], input[type='submit'], button")
        form_layout.addRow("Submit selector:", self.submit_selector_edit)

        delay_layout = QtWidgets.QHBoxLayout()
        self.delay_min_spin = QtWidgets.QDoubleSpinBox()
        self.delay_min_spin.setRange(0.0, 5.0)
        self.delay_min_spin.setDecimals(2)
        self.delay_min_spin.setSingleStep(0.1)
        self.delay_min_spin.setValue(0.4)

        self.delay_max_spin = QtWidgets.QDoubleSpinBox()
        self.delay_max_spin.setRange(0.1, 5.0)
        self.delay_max_spin.setDecimals(2)
        self.delay_max_spin.setSingleStep(0.1)
        self.delay_max_spin.setValue(1.0)

        delay_layout.addWidget(QtWidgets.QLabel("Min:"))
        delay_layout.addWidget(self.delay_min_spin)
        delay_layout.addWidget(QtWidgets.QLabel("Max:"))
        delay_layout.addWidget(self.delay_max_spin)
        form_layout.addRow("Human delay (s):", delay_layout)

        button_layout = QtWidgets.QHBoxLayout()
        main_layout.addLayout(button_layout)

        self.load_config_btn = QtWidgets.QPushButton("Load Config…")
        self.save_config_btn = QtWidgets.QPushButton("Save Config As…")
        self.load_data_btn = QtWidgets.QPushButton("Load Data…")
        self.auto_fill_btn = QtWidgets.QPushButton("Auto-Fill")

        button_layout.addWidget(self.load_config_btn)
        button_layout.addWidget(self.save_config_btn)
        button_layout.addWidget(self.load_data_btn)
        button_layout.addStretch()
        button_layout.addWidget(self.auto_fill_btn)

        main_layout.addWidget(QtWidgets.QLabel("Form data (JSON):"))
        self.data_edit = QtWidgets.QPlainTextEdit()
        self.data_edit.setPlaceholderText('{\n  "name": "Example User"\n}')
        main_layout.addWidget(self.data_edit, 1)

        main_layout.addWidget(QtWidgets.QLabel("Activity log:"))
        self.log_output = QtWidgets.QPlainTextEdit()
        self.log_output.setReadOnly(True)
        main_layout.addWidget(self.log_output, 1)

        self.status_bar = self.statusBar()

        self.load_config_btn.clicked.connect(self.load_config)
        self.save_config_btn.clicked.connect(self.save_config)
        self.load_data_btn.clicked.connect(self.load_data)
        self.auto_fill_btn.clicked.connect(self.start_autofill)

    # --- Config and data helpers ------------------------------------------
    def _collect_config(self) -> Dict[str, Any]:
        minimum = self.delay_min_spin.value()
        maximum = self.delay_max_spin.value()
        if minimum > maximum:
            minimum, maximum = maximum, minimum

        return {
            "url": self.url_edit.text().strip(),
            "headless": self.headless_check.isChecked(),
            "wait_timeout": self.timeout_spin.value(),
            "submit_selector": self.submit_selector_edit.text().strip()
            or "button[type='submit'], input[type='submit'], button",
            "human_delay_seconds": [minimum, maximum],
            "close_delay_seconds": float(self.close_delay_spin.value()),
        }

    def _apply_config(self, cfg: Dict[str, Any]) -> None:
        self.url_edit.setText(str(cfg.get("url", "")))
        self.headless_check.setChecked(bool(cfg.get("headless", False)))
        self.timeout_spin.setValue(int(cfg.get("wait_timeout", 10)))
        self.close_delay_spin.setValue(float(cfg.get("close_delay_seconds", 10)))
        self.submit_selector_edit.setText(
            cfg.get("submit_selector") or "button[type='submit'], input[type='submit'], button"
        )
        delays = cfg.get("human_delay_seconds", [0.4, 1.0])
        if isinstance(delays, (list, tuple)) and len(delays) == 2:
            self.delay_min_spin.setValue(float(delays[0]))
            self.delay_max_spin.setValue(float(delays[1]))

    def load_config(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select config JSON", str(Path.cwd()), "JSON Files (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            cfg = load_json(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Error loading config", str(exc))
            return
        self._apply_config(cfg)
        self.current_config_path = path
        self.status_bar.showMessage(f"Loaded config: {path}", 5000)

    def save_config(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save config JSON", str(self.current_config_path or Path.cwd()), "JSON Files (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        cfg = self._collect_config()
        try:
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(cfg, handle, indent=2)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Error saving config", str(exc))
            return
        self.current_config_path = path
        self.status_bar.showMessage(f"Saved config: {path}", 5000)

    def load_data(self) -> None:
        path_str, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Select data JSON", str(Path.cwd()), "JSON Files (*.json)"
        )
        if not path_str:
            return
        path = Path(path_str)
        try:
            data = load_json(path)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Error loading data", str(exc))
            return
        self.data_edit.setPlainText(json.dumps(data, indent=2))
        self.current_data_path = path
        self.status_bar.showMessage(f"Loaded data: {path}", 5000)

    # --- Logging helpers ---------------------------------------------------
    @QtCore.pyqtSlot(str)
    def append_log(self, message: str) -> None:
        cursor = self.log_output.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText(message + "\n")
        self.log_output.ensureCursorVisible()

    def _set_running(self, running: bool) -> None:
        self.load_config_btn.setEnabled(not running)
        self.save_config_btn.setEnabled(not running)
        self.load_data_btn.setEnabled(not running)
        self.auto_fill_btn.setEnabled(not running)
        if running:
            self.status_bar.showMessage("Running autofill…")
        else:
            self.status_bar.showMessage("Ready")

    # --- Worker coordination ----------------------------------------------
    def start_autofill(self) -> None:
        cfg = self._collect_config()
        if not cfg["url"]:
            QtWidgets.QMessageBox.warning(self, "Missing URL", "Please provide a URL before running autofill.")
            return
        try:
            data = json.loads(self.data_edit.toPlainText() or "{}")
        except json.JSONDecodeError as exc:
            QtWidgets.QMessageBox.critical(self, "Invalid JSON", f"Data JSON is invalid:\n{exc}")
            return

        self.append_log("Starting autofill run…")
        self._set_running(True)

        self.autofill_thread = QtCore.QThread(self)
        self.autofill_worker = AutofillWorker(cfg, data)
        self.autofill_worker.moveToThread(self.autofill_thread)

        self.autofill_thread.started.connect(self.autofill_worker.run)
        self.autofill_worker.finished.connect(self._on_autofill_finished)
        self.autofill_worker.finished.connect(lambda _: self.autofill_thread.quit())
        self.autofill_worker.failed.connect(self._on_autofill_failed)
        self.autofill_worker.failed.connect(lambda _: self.autofill_thread.quit())
        self.autofill_worker.message.connect(self.append_log)
        self.autofill_worker.request_confirmation.connect(self._on_worker_request_confirmation)
        self.autofill_thread.finished.connect(self._cleanup_worker)

        self.autofill_thread.start()

    def _cleanup_worker(self) -> None:
        self._set_running(False)
        if self.autofill_worker:
            self.autofill_worker.deleteLater()
            self.autofill_worker = None
        if self.autofill_thread:
            self.autofill_thread.deleteLater()
            self.autofill_thread = None

    def _on_autofill_finished(self, outcome: Any) -> None:
        self.append_log("Autofill run finished.")
        message_lines = [
            f"Submitted: {outcome.submitted}",
        ]
        if outcome.screenshot_path:
            message_lines.append(f"Preview screenshot: {outcome.screenshot_path}")
        if outcome.post_submit_screenshot_path:
            message_lines.append(f"Post-submit screenshot: {outcome.post_submit_screenshot_path}")
        if outcome.aborted_reason:
            message_lines.append(f"Aborted reason: {outcome.aborted_reason}")
        if outcome.error:
            message_lines.append(f"Error: {outcome.error}")

        summary = "\n".join(message_lines)
        if outcome.error:
            QtWidgets.QMessageBox.critical(self, "Autofill error", summary)
        else:
            QtWidgets.QMessageBox.information(self, "Autofill complete", summary)

    def _on_autofill_failed(self, message: str) -> None:
        self.append_log(f"Worker failed: {message}")
        QtWidgets.QMessageBox.critical(self, "Autofill error", message)

    def _on_worker_request_confirmation(self, actions_obj: object, screenshot_obj: object) -> None:
        actions: List[FillAction] = list(actions_obj)
        screenshot_path = Path(screenshot_obj)

        lines = []
        for idx, action in enumerate(actions, 1):
            label = (action.label or "<no label>").strip()
            lines.append(
                f"{idx:02d}. label='{label[:60]}' tag={action.tag} type={action.input_type} "
                f"score={action.score} mapped={action.mapped_key} filled={action.filled} "
                f"value={str(action.value)[:80]}"
            )
        details = "\n".join(lines)

        msg_box = QtWidgets.QMessageBox(self)
        msg_box.setWindowTitle("Confirm submission")
        msg_box.setIcon(QtWidgets.QMessageBox.Question)
        msg_box.setText("Review the detected fields before submitting automatically.")
        msg_box.setInformativeText(f"Screenshot saved to:\n{screenshot_path}")
        msg_box.setDetailedText(details or "No actions recorded.")
        msg_box.setStandardButtons(QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No)
        msg_box.setDefaultButton(QtWidgets.QMessageBox.No)
        result = msg_box.exec_()

        if self.autofill_worker:
            self.autofill_worker.confirmation_reply.emit(result == QtWidgets.QMessageBox.Yes)


def main() -> None:
    app = QtWidgets.QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    import sys

    main()

