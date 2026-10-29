from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtWidgets import QWidget, QLineEdit, QVBoxLayout, QHBoxLayout, QTextEdit, QPushButton
from PySide6.QtGui import QFontDatabase, QFontMetricsF, QKeyEvent, QTextCursor


class CommandHistory(QObject):
    def __init__(self):
        super().__init__()
        self.history = []
        self.current = 0

    def __len__(self):
        return len(self.history)

    def append(self, cmd: str):
        self.history.append(cmd)

    def reset(self):
        self.current = len(self)

    def up(self):
        self.current = max(self.current - 1, 0)
        if (len(self) > 0) and (self.current >= 0):
            return self.history[self.current]
        else:
            return ""

    def down(self):
        self.current = min(self.current + 1, len(self))
        if (len(self) > 0) and (self.current >= 0) and (self.current < len(self)):
            return self.history[self.current]
        else:
            return ""

class Terminal(QWidget):
    def __init__(self, ctx: dict, parent=None):
        super().__init__(parent)

        self.history = CommandHistory()
        self.savedEntry = ""

        # Namespace persistant : les variables definies par une commande
        # restent visibles dans les commandes suivantes.
        self.execNamespace = dict(globals() | locals() | ctx)
        self.execNamespace["self"] = self
        self.execNamespace["help"] = self.showHelp
        self.execNamespace |= self.execNamespace

        self.inputTerm = QLineEdit(placeholderText="custom commands (type help())")
        self.form = QVBoxLayout()
        self.setLayout(self.form)

        font = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        font.setPointSize(11)
        metrics = QFontMetricsF(font)
        self.textZone = QTextEdit(self)
        self.textZone.setFont(font)
        self.textZone.setTabStopDistance(4*metrics.horizontalAdvance(' '))
        self.textZone.setReadOnly(True)
        self.textZone.setAcceptRichText(True)

        self.clsBtn = QPushButton("Clear")
        self.clsBtn.clicked.connect(self.textZone.clear)

        self.bottomZone = QHBoxLayout()
        self.bottomZone.addWidget(self.clsBtn)
        self.bottomZone.addWidget(self.inputTerm)

        self.form.addWidget(self.textZone)
        self.form.addLayout(self.bottomZone)

        self.setupInput()

        #TODO: Remove this, it's debug code
        self.lastClearEvent = None

    def appendRaw(self, text: str):
        """Insere du texte brut en fin de zone, sans le saut de paragraphe
        que QTextEdit.append() force a chaque appel : stdout ecrit le
        texte puis son "\\n" de fin en deux write() separes, donc
        append() y ajouterait une ligne vide entre chaque print()."""
        cursor = self.textZone.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        self.textZone.setTextCursor(cursor)
        self.textZone.ensureCursorVisible()

    def setupInput(self):
        self.inputTerm.returnPressed.connect(self.onInput)
        self.inputTerm.installEventFilter(self)

    def eventFilter(self, obj, event: QEvent):
        if event.type() == QEvent.Type.KeyPress:
            keyEvent = QKeyEvent(event)
            key = keyEvent.key()
            if key == Qt.Key.Key_Up:
                # Save current command in progress
                if self.history.current == len(self.history):
                    self.savedEntry = self.inputTerm.text()
                text = self.history.up()
                self.inputTerm.setText(text)
                event.accept()
            elif key == Qt.Key.Key_Down:
                text = self.history.down()
                if self.history.current == len(self.history):
                    self.inputTerm.setText(self.savedEntry)
                else:
                    self.inputTerm.setText(text)
                event.accept()
            elif keyEvent.modifiers() & Qt.KeyboardModifier.ControlModifier:
                if key == Qt.Key.Key_L:
                    self.lastClearEvent = keyEvent
                    self.textZone.clear()

        return super().eventFilter(obj,event)

    def showHelp(self):
        print(
            "Interactive terminal.\n"
            "Variables : \n"
            "\tself (this terminal widget) \n"
            "\tutils (this terminal widget) \n"
            "Variables disponibles : self (widget Terminal), utils, requests, threads.\n"
            "Les variables definies persistent d'une commande a l'autre."
        )

    def onInput(self):
        command = self.inputTerm.text()
        self.inputTerm.clear()
        try:
            exec(f"print({command})", self.execNamespace)
        except (TypeError, SyntaxError) as e:
            exec(command, self.execNamespace)
        except Exception as e:
            print(f"{type(e).__name__}: {e}")
        self.history.append(command)
        self.history.reset()

    def addContext(self, key, data):
        self.execNamespace[key] = data