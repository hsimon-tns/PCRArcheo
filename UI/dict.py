from PySide6.QtWidgets import \
    QWidget, QMainWindow, QFormLayout, QStackedLayout, QPushButton, \
    QApplication, QLabel, QFrame, QVBoxLayout, QSizePolicy
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon
import sys


ITERABLES = (list, set, tuple)
TEXT_ALIGNMENT = Qt.AlignHCenter | Qt.AlignVCenter
EXP = QSizePolicy.Policy.Expanding

class Form(QFormLayout):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFormAlignment(TEXT_ALIGNMENT)
        self.setLabelAlignment(TEXT_ALIGNMENT)

    def addRow(self, s, w):
        super().addRow(s, w)
        self.labelForField(w).setSizePolicy(EXP, EXP)

class DataLabel(QFrame):
    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.label = QLabel(text)
        self.label.setAlignment(TEXT_ALIGNMENT)
        self.frameLayout = QVBoxLayout()
        self.frameLayout.addWidget(self.label)
        self.frame = QFrame(frameShape=QFrame.WinPanel)
        self.frame.setLayout(self.frameLayout)
        self.lay = QVBoxLayout()
        self.lay.addWidget(self.frame)
        self.setLayout(self.lay)

class DictExplorer(QWidget):
    def __init__(self, data: dict, parent=None):
        super().__init__(parent)
        self.data = data
        self.build()

    def build(self) -> None:
        self.stack = QStackedLayout()
        self.root = self.buildDict(self.data)
        self.stack.insertWidget(0, self.root)
        self.stack.setCurrentIndex(0)
        self.setLayout(self.stack)

    def buildDict(self, data) -> QWidget:
        ret = QWidget()
        layout = Form()
        ret.setLayout(layout)
        dType = type(data)
        if dType in ITERABLES:
            for value in data:
                elt = self.buildElement(value)
                layout.addWidget(elt)
        elif dType is dict:
            for key, value in data.items():
                elt = self.buildElement(value)
                layout.addRow(str(key), elt)
        else:
            return QWidget()
        return ret

    def buildElement(self, value) -> QWidget:
        if self.needRecurse(value):
            retBtn = QPushButton()
            retBtn.setIcon(QIcon.fromTheme(QIcon.ThemeIcon.GoPrevious))
            retBtn.clicked.connect(lambda _:
                self.stack.setCurrentIndex(self.stack.currentIndex()-1)
            )
            widget = self.buildDict(value)
            widget.layout().addWidget(retBtn)


            self.stack.insertWidget(-1, widget)
            btn = QPushButton("View")
            btn.clicked.connect(
                lambda _, w=widget: self.stack.setCurrentWidget(w)
            )
            return btn
        else:
            print("No need recurse..")
            return DataLabel(str(value))

    def needRecurse(self, value) -> bool:
        if type(value) is dict:
            return True
        elif type(value) in ITERABLES:
            if len(value) == 0:
                return False
            else:
                dType = type(value[0])
                if dType in (int, float, str):
                    return False
                else:
                    return True
        else: #TODO
            return False

    def navigate(self):
        pass

if __name__ == "__main__":
    print("Running dict explorer test")
    app = QApplication([])
    data = {
        "toto": 5,
        "titi": [1,2,3,4],
        "tutu": -2.5,
        "tata": {
            "hello": "world"
        }
    }
    explorer = DictExplorer(data)
    window = QMainWindow()
    window.setCentralWidget(explorer)
    window.show()
    sys.exit(app.exec())
