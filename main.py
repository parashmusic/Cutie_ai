import sys
import os
import platform
import subprocess
import webbrowser
import json
from datetime import datetime
import threading

import psutil
import pyautogui
from llama_cpp import Llama
import qtawesome as qta
import speech_recognition as sr
import pyttsx3

from PyQt5.QtCore import (
    Qt, QThread, pyqtSignal, QEvent, QTimer, QPropertyAnimation,
    QRect, QPoint
)
from PyQt5.QtGui import QFont, QColor, QIcon, QPixmap
from PyQt5.QtWidgets import (
    QApplication, QWidget, QLabel, QTextEdit, QPushButton,
    QVBoxLayout, QHBoxLayout, QScrollArea, QFrame,
    QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QGraphicsBlurEffect
)


ICON_PATH = os.path.join(os.path.dirname(__file__), 'cutie_icon.png')


class FloatingIcon(QWidget):
    showChat = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        pix = QPixmap(ICON_PATH).scaled(64, 64, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.label = QLabel(self)
        self.label.setPixmap(pix)
        self.resize(pix.size())
        self._drag = None
        self._press = None

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._press = e.globalPos()
            self._drag = e.globalPos() - self.frameGeometry().topLeft()
        e.accept()

    def mouseMoveEvent(self, e):
        if self._drag:
            self.move(e.globalPos() - self._drag)
        e.accept()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._press:
            if (e.globalPos() - self._press).manhattanLength() < 10:
                self.showChat.emit()
        self._drag = None
        self._press = None
        e.accept()

# Worker thread for AI commands
class CommandWorker(QThread):
    responseReady = pyqtSignal(str)

    def __init__(self, llm, commands):
        super().__init__()
        self.llm = llm
        self.commands = commands
        self.text = None

    def set_input(self, t):
        self.text = t

    def run(self):
        txt = self.text or ""
        for cmd, act in self.commands.items():
            if cmd in txt.lower():
                try:
                    res = act()
                except Exception as e:
                    res = f"Error: {e}"
                self.responseReady.emit(res)
                return
        out = self.llm.create_chat_completion(
            messages=[{"role": "user", "content": txt}],
            max_tokens=256
        )
        msg = out['choices'][0]['message']['content']
        self.responseReady.emit(msg.strip())

# Main overlay chat window with synced dragging and custom icon
class cutieOverlay(QWidget):
    addMessage = pyqtSignal(str, str, bool)

    def __init__(self):
        super().__init__()
        self.recognizer = sr.Recognizer()
        self.tts = pyttsx3.init()
        self.tts.setProperty('rate', 150)

        # Use cutie_icon.png as the window icon
        self.setWindowIcon(QIcon(ICON_PATH))
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)

        self._setup_ui()
        self._init_animations()
        self._init_llm()
        self._setup_cmds()

        self.worker = CommandWorker(self.llm, self.cmds)
        self.worker.responseReady.connect(self._handle_ai_response)
        self.addMessage.connect(self._display_message)
        self._greet()
        self._drag_pos = None

    def _setup_ui(self):
        self.resize(500, 600)
        self.container = QFrame(self)
        self.container.setObjectName("container")
        self.container.setGeometry(0, 0, 500, 600)
        self.container.setStyleSheet("#container { background: rgba(15,15,26,200); border-radius: 12px; }")
        blur = QGraphicsBlurEffect(self)
        blur.setBlurRadius(20)
        self.container.setGraphicsEffect(blur)
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(30)
        shadow.setOffset(0)
        shadow.setColor(QColor(0, 170, 255, 120))
        self.container.setGraphicsEffect(shadow)

        layout = QVBoxLayout(self.container)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)

        # Title Bar
        tb = QHBoxLayout()
        title = QLabel("cutie")
        title.setFont(QFont("Orbitron", 20, QFont.Bold))
        title.setStyleSheet("color: #00ffff;")
        tb.addWidget(title)
        tb.addStretch()
        btn_min = QPushButton()
        btn_min.setIcon(qta.icon('fa5s.window-minimize', color='#888'))
        btn_min.setFixedSize(24, 24)
        btn_min.setStyleSheet("background:transparent;")
        btn_min.clicked.connect(self.hide_with_animation)
        tb.addWidget(btn_min)
        btn_close = QPushButton()
        btn_close.setIcon(qta.icon('fa5s.times', color='#888'))
        btn_close.setFixedSize(24, 24)
        btn_close.setStyleSheet("background:transparent;")
        btn_close.clicked.connect(QApplication.quit)
        tb.addWidget(btn_close)
        layout.addLayout(tb)

        # Chat Area
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setStyleSheet("border: none; background: transparent;")
        self.content = QFrame()
        self.layout_c = QVBoxLayout(self.content)
        self.layout_c.setAlignment(Qt.AlignTop)
        self.area.setWidget(self.content)
        layout.addWidget(self.area)

        # Input + Mic + Send
        ib = QHBoxLayout()
        self.input = QTextEdit()
        self.input.setFixedHeight(60)
        self.input.setPlaceholderText("Speak to cutie...")
        self.input.setStyleSheet("background: #1e1e2f; color: #ccc; border: 1px solid #333; border-radius: 6px; padding: 8px;")
        ib.addWidget(self.input)
        self.mic_btn = QPushButton()
        self.mic_btn.setIcon(qta.icon('fa5s.microphone', color='#00ffff'))
        self.mic_btn.setFixedSize(48, 48)
        self.mic_btn.setStyleSheet("background: transparent;")
        self.mic_btn.clicked.connect(self._record)
        ib.addWidget(self.mic_btn)
        send = QPushButton()
        send.setIcon(qta.icon('fa5s.paper-plane', color='#00ffff'))
        send.setFixedSize(48, 48)
        send.setStyleSheet("QPushButton{border-radius:24px;} QPushButton:hover{background:#002a4e;}")
        send.clicked.connect(self._send)
        ib.addWidget(send)
        layout.addLayout(ib)

    def hide_with_animation(self):
        self.fade_out.start()

    def _init_animations(self):
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.container.setGraphicsEffect(self.opacity_effect)
        self.fade_in = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_in.setDuration(300)
        self.fade_in.setStartValue(0.0)
        self.fade_in.setEndValue(1.0)
        self.fade_out = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_out.setDuration(300)
        self.fade_out.setStartValue(1.0)
        self.fade_out.setEndValue(0.0)
        self.fade_out.finished.connect(self._after_fade_out)
        geom = self.geometry()
        self.slide_in = QPropertyAnimation(self, b"geometry")
        self.slide_in.setDuration(300)
        self.slide_in.setStartValue(QRect(geom.x(), geom.y() - 100, geom.width(), geom.height()))
        self.slide_in.setEndValue(geom)

    def showEvent(self, e):
        super().showEvent(e)
        if hasattr(self, 'icon_widget'):
            self.icon_offset = self.icon_widget.pos() - self.pos()
        self.fade_in.start()
        self.slide_in.start()

    def _after_fade_out(self):
        super().hide()
        if hasattr(self, 'icon_widget'):
            self.icon_widget.show()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._drag_pos = event.globalPos() - self.frameGeometry().topLeft()
        event.accept()

    def mouseMoveEvent(self, event):
        if self._drag_pos:
            new_pos = event.globalPos() - self._drag_pos
            self.move(new_pos)
            if hasattr(self, 'icon_widget'):
                self.icon_widget.move(new_pos + self.icon_offset)
        event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_pos = None
        event.accept()

    def _init_llm(self):
        try:
            self.llm = Llama(model_path="./models/mistral-7b-instruct-v0.1.Q4_K_M.gguf", n_ctx=32768)
        except Exception as e:
            self.addMessage.emit("Error", f"LLM load failed: {e}", True)
            QApplication.quit()

    def _setup_cmds(self):
        self.cmds = {"open file manager": self._open_fm, "open browser": lambda: (webbrowser.open("https://google.com"), "Opened")[1], "take screenshot": self._shot, "show system info": self._info}

    def _greet(self):
        self.addMessage.emit("cutie", "How can I assist you today?", True)

    def _display_message(self, sender, text, is_ai):
        bubble = QFrame()
        bubble.setStyleSheet("QFrame{background:rgba(20,20,40,0.8);border-radius:8px;}")
        vlay = QVBoxLayout(bubble)
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#aaffff;" if is_ai else "color:#ddffdd;")
        vlay.addWidget(lbl)
        ts = QLabel(datetime.now().strftime("%H:%M"))
        ts.setStyleSheet("color:#555;font-size:8px;")
        vlay.addWidget(ts, alignment=Qt.AlignRight)
        hlay = QHBoxLayout()
        if is_ai:
            hlay.addWidget(bubble)
            hlay.addStretch()
        else:
            hlay.addStretch()
            hlay.addWidget(bubble)
        self.layout_c.addLayout(hlay)
        self.area.verticalScrollBar().setValue(self.area.verticalScrollBar().maximum())
        if is_ai:
            threading.Thread(target=self._speak, args=(text,), daemon=True).start()

    def _send(self):
        txt = self.input.toPlainText().strip()
        if not txt:
            return
        self.addMessage.emit("You", txt, False)
        self.input.clear()
        self.worker.set_input(txt)
        self.worker.start()

    def _handle_ai_response(self, resp):
        self.addMessage.emit("cutie", resp, True)

    def _record(self):
        threading.Thread(target=self._listen, daemon=True).start()

    def _listen(self):
        with sr.Microphone() as src:
            audio = self.recognizer.listen(src)
        try:
            text = self.recognizer.recognize_google(audio)
            self.addMessage.emit("You", text, False)
            self.worker.set_input(text)
            self.worker.start()
        except sr.UnknownValueError:
            self.addMessage.emit("cutie", "Sorry, I didn't catch that.", True)
        except sr.RequestError:
            self.addMessage.emit("cutie", "Speech service unavailable.", True)

    def _speak(self, txt):
        self.tts.say(txt)
        self.tts.runAndWait()

    def _open_fm(self):
        if platform.system() == 'Windows': os.startfile(os.environ['USERPROFILE'])
        elif platform.system() == 'Darwin': subprocess.Popen(['open', os.path.expanduser('~')])
        else: subprocess.Popen(['xdg-open', os.path.expanduser('~')])
        return 'File Manager opened.'

    def _shot(self):
        fn = f"screenshot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png"
        pyautogui.screenshot(fn)
        return f"Saved: {fn}"

    def _info(self):
        return f"CPU: {psutil.cpu_percent()}% RAM: {psutil.virtual_memory().percent}%"

    def closeEvent(self, e):
        e.ignore()
        self.hide_with_animation()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    overlay = cutieOverlay()
    floater = FloatingIcon()
    overlay.icon_widget = floater
    floater.showChat.connect(lambda: (overlay.show(), floater.hide()))
    floater.show()
    sys.exit(app.exec_())
