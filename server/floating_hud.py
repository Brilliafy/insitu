#!/usr/bin/env python3
"""
InSitu Floating Capsule HUD
A minimalist, topmost, frameless capsule/pill bar matching the user's design.
Features:
- Plus button with context menu
- Text input with auto-send to terminal
- Mode selector dropdown (Mode 1, Mode 2, Mode 3)
- 1:1 Microphone button with local Moonshine ONNX transcription
- Stays on top, draggable anywhere
"""

import sys
import os
import json
import urllib.request
import subprocess
from PyQt6.QtCore import Qt, QPoint, QThread, pyqtSignal, QTimer
from PyQt6.QtWidgets import (
    QApplication, QWidget, QHBoxLayout, QLineEdit,
    QPushButton, QMenu, QGraphicsDropShadowEffect
)
from PyQt6.QtGui import QColor, QFont, QAction, QIcon, QPainter, QCursor

BRIDGE_URL = "http://127.0.0.1:6419"
PYTHON_BIN = "/home/Aelfwif/anaconda3/envs/socratic-tutor/bin/python"
STT_SCRIPT = "/home/Aelfwif/insitu/server/stt_transcriber.py"
TTS_SCRIPT = "/home/Aelfwif/insitu/server/tts_player.py"

class STTWorker(QThread):
    finished = pyqtSignal(str, str)  # (text, error)

    def run(self):
        try:
            proc = subprocess.run(
                [PYTHON_BIN, STT_SCRIPT, "--mic", "4.0"],
                capture_output=True,
                text=True,
                timeout=9.0
            )
            if proc.returncode == 0:
                data = json.loads(proc.stdout.strip())
                self.finished.emit(data.get("text", ""), "")
            else:
                self.finished.emit("", proc.stderr.strip() or "Recording failed")
        except Exception as e:
            self.finished.emit("", str(e))

class FloatingTutorHUD(QWidget):
    def __init__(self):
        super().__init__()
        self.drag_position = QPoint()
        self.current_mode = 1
        self.auto_send = True
        self.is_recording = False
        self.worker = None

        self.init_ui()
        self.fetch_mode_from_bridge()

        # Poll bridge for mode changes or highlight updates occasionally
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_bridge)
        self.poll_timer.start(2500)

    def init_ui(self):
        # Frameless, topmost, translucent window
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.SubWindow
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.resize(600, 68)

        # Main layout
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)

        # Capsule Container
        self.container = QWidget(self)
        self.container.setObjectName("capsule")
        self.container.setStyleSheet("""
            QWidget#capsule {
                background-color: #1f1f21;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 24px;
            }
        """)

        # Drop shadow for depth
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(18)
        shadow.setColor(QColor(0, 0, 0, 120))
        shadow.setOffset(0, 4)
        self.container.setGraphicsEffect(shadow)

        # Capsule inner layout
        capsule_layout = QHBoxLayout(self.container)
        capsule_layout.setContentsMargins(14, 0, 14, 0)
        capsule_layout.setSpacing(10)

        # 1. Plus Button (+)
        self.btn_plus = QPushButton("+", self.container)
        self.btn_plus.setFixedSize(30, 30)
        self.btn_plus.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_plus.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #a0a0a5;
                font-size: 20px;
                font-weight: 300;
                border: none;
                border-radius: 15px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: rgba(255, 255, 255, 0.08);
            }
        """)
        self.btn_plus.clicked.connect(self.show_plus_menu)
        capsule_layout.addWidget(self.btn_plus)

        # 2. Text Input Field
        self.input_field = QLineEdit(self.container)
        self.input_field.setPlaceholderText("Ask Tutor or speak...")
        self.input_field.setStyleSheet("""
            QLineEdit {
                background: transparent;
                border: none;
                color: #e5e5e7;
                font-size: 13px;
                selection-background-color: #3b82f6;
            }
            QLineEdit::placeholder {
                color: #71717a;
            }
        """)
        self.input_field.returnPressed.connect(self.handle_submit)
        capsule_layout.addWidget(self.input_field, 1)

        # 3. Mode Dropdown Pill Button (e.g. "Mode 1: Section ▾")
        self.btn_mode = QPushButton("Mode 1: Section  ▾", self.container)
        self.btn_mode.setFixedHeight(28)
        self.btn_mode.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_mode.setStyleSheet("""
            QPushButton {
                background: rgba(255, 255, 255, 0.06);
                color: #d4d4d8;
                font-size: 12px;
                font-weight: 500;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
                padding: 0 12px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.12);
                color: #ffffff;
                border-color: rgba(255, 255, 255, 0.18);
            }
        """)
        self.btn_mode.clicked.connect(self.show_mode_menu)
        capsule_layout.addWidget(self.btn_mode)

        # 4. Microphone Button (1:1 SVG style)
        self.btn_mic = QPushButton(self.container)
        self.btn_mic.setFixedSize(32, 32)
        self.btn_mic.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.btn_mic.setText("🎙")
        self.btn_mic.setStyleSheet(self.get_mic_style(recording=False))
        self.btn_mic.clicked.connect(self.toggle_mic)
        capsule_layout.addWidget(self.btn_mic)

        layout.addWidget(self.container)

        # Position at top center of primary screen
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = 36
        self.move(x, y)

    def get_mic_style(self, recording: bool) -> str:
        if recording:
            return """
                QPushButton {
                    background: #dc2626;
                    color: #ffffff;
                    font-size: 16px;
                    border: 2px solid #ef4444;
                    border-radius: 16px;
                }
            """
        return """
            QPushButton {
                background: transparent;
                color: #a0a0a5;
                font-size: 16px;
                border: none;
                border-radius: 16px;
            }
            QPushButton:hover {
                color: #ffffff;
                background: rgba(255, 255, 255, 0.08);
            }
        """

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_position = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self.drag_position)
            event.accept()

    def show_plus_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self.get_menu_style())

        act_inspect = menu.addAction("🔍 Ask Doubt on Selection (Mode 3)")
        act_inspect.triggered.connect(self.inspect_editor_selection)

        act_replay = menu.addAction("🔊 Replay Last Tutor Speech")
        act_replay.triggered.connect(self.replay_speech)

        act_clear = menu.addAction("🗑️ Clear Input")
        act_clear.triggered.connect(lambda: self.input_field.clear())

        menu.addSeparator()

        auto_label = "✓ Auto-send to Agent" if self.auto_send else "  Auto-send to Agent"
        act_toggle_auto = menu.addAction(auto_label)
        act_toggle_auto.triggered.connect(self.toggle_auto_send)

        act_close = menu.addAction("✕ Hide Bar")
        act_close.triggered.connect(self.hide)

        pos = self.btn_plus.mapToGlobal(QPoint(0, self.btn_plus.height() + 6))
        menu.exec(pos)

    def show_mode_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet(self.get_menu_style())

        m1 = menu.addAction(("● " if self.current_mode == 1 else "○ ") + "Mode 1: Section Scaffold")
        m1.triggered.connect(lambda: self.set_mode(1))

        m2 = menu.addAction(("● " if self.current_mode == 2 else "○ ") + "Mode 2: Line-by-Line Step")
        m2.triggered.connect(lambda: self.set_mode(2))

        m3 = menu.addAction(("● " if self.current_mode == 3 else "○ ") + "Mode 3: Refactor & Doubt")
        m3.triggered.connect(lambda: self.set_mode(3))

        pos = self.btn_mode.mapToGlobal(QPoint(0, self.btn_mode.height() + 6))
        menu.exec(pos)

    def get_menu_style(self) -> str:
        return """
            QMenu {
                background-color: #232326;
                color: #e4e4e7;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 10px;
                padding: 6px;
                font-size: 12px;
            }
            QMenu::item {
                padding: 6px 14px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: rgba(255, 255, 255, 0.1);
                color: #ffffff;
            }
            QMenu::separator {
                height: 1px;
                background: rgba(255, 255, 255, 0.1);
                margin: 4px 0;
            }
        """

    def set_mode(self, mode: int):
        self.current_mode = mode
        labels = {
            1: "Mode 1: Section  ▾",
            2: "Mode 2: Line  ▾",
            3: "Mode 3: Refactor  ▾"
        }
        self.btn_mode.setText(labels.get(mode, "Mode 1: Section  ▾"))
        # Notify extension bridge
        try:
            req = urllib.request.Request(
                f"{BRIDGE_URL}/mode",
                data=json.dumps({"mode": mode}).encode(),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=0.8)
        except Exception:
            pass

    def fetch_mode_from_bridge(self):
        try:
            with urllib.request.urlopen(f"{BRIDGE_URL}/mode", timeout=0.8) as r:
                data = json.loads(r.read().decode())
                m = data.get("mode", 1)
                self.set_mode(m)
        except Exception:
            pass

    def poll_bridge(self):
        try:
            with urllib.request.urlopen(f"{BRIDGE_URL}/mode", timeout=0.5) as r:
                data = json.loads(r.read().decode())
                m = data.get("mode", self.current_mode)
                if m != self.current_mode:
                    self.set_mode(m)
        except Exception:
            pass

    def toggle_auto_send(self):
        self.auto_send = not self.auto_send

    def inspect_editor_selection(self):
        try:
            with urllib.request.urlopen(f"{BRIDGE_URL}/selection", timeout=1.0) as r:
                data = json.loads(r.read().decode())
                if data.get("active") and data.get("selectedText"):
                    self.set_mode(3)
                    snippet = data["selectedText"][:80].replace("\n", " ")
                    prompt = f"[Mode 3]: In {os.path.basename(data.get('file', 'code'))}:{data.get('startLine')}-{data.get('endLine')}, how should we refactor: \"{snippet}\"?"
                    self.input_field.setText(prompt)
                    if self.auto_send:
                        self.handle_submit()
                else:
                    self.input_field.setPlaceholderText("No active code selection found in IDE")
        except Exception as e:
            self.input_field.setPlaceholderText(f"IDE not responding: {e}")

    def replay_speech(self):
        subprocess.Popen([PYTHON_BIN, TTS_SCRIPT, "Replaying last tutor guidance."])

    def toggle_mic(self):
        if self.is_recording:
            return

        self.is_recording = True
        self.btn_mic.setStyleSheet(self.get_mic_style(recording=True))
        self.input_field.setPlaceholderText("Listening with Moonshine ONNX (speak now)...")

        self.worker = STTWorker()
        self.worker.finished.connect(self.on_stt_finished)
        self.worker.start()

    def on_stt_finished(self, text: str, error: str):
        self.is_recording = False
        self.btn_mic.setStyleSheet(self.get_mic_style(recording=False))

        if text and text.strip():
            self.input_field.setText(text.strip())
            self.input_field.setPlaceholderText("Ask Tutor or speak...")
            if self.auto_send:
                self.handle_submit()
        else:
            self.input_field.setPlaceholderText(error or "No speech detected. Tap mic to retry.")

    def handle_submit(self):
        text = self.input_field.text().strip()
        if not text:
            return

        # Send prompt to IDE active terminal via bridge
        try:
            req = urllib.request.Request(
                f"{BRIDGE_URL}/insert_prompt",
                data=json.dumps({"prompt": text}).encode(),
                headers={"Content-Type": "application/json"}
            )
            urllib.request.urlopen(req, timeout=1.0)
            self.input_field.clear()
            self.input_field.setPlaceholderText("Prompt sent to agent 🚀")
            QTimer.singleShot(2500, lambda: self.input_field.setPlaceholderText("Ask Tutor or speak..."))
        except Exception:
            # If bridge offline, keep prompt and notify in placeholder
            self.input_field.setPlaceholderText("IDE bridge offline (open Antigravity/VS Code)")

def main():
    app = QApplication(sys.argv)
    hud = FloatingTutorHUD()
    hud.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
