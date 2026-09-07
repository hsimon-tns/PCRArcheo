from PySide6.QtCore import \
    QObject, QThread, Signal, QRunnable, QThreadPool, Slot
from collections.abc import Callable
from typing import assert_type

'''
    Used to communicate between threads
'''

class Node(QObject):
    started = Signal()
    finished = Signal(object)
    progress = Signal(float)
    error = Signal(str)
    def __init__(self, parent=None):
        super().__init__(parent)

Callback = Callable[[Node], None]

class Worker(QRunnable):
    def __init__(self, callback: Callback):
        assert_type(callback, Callback)
        super().__init__()
        self.node = Node()
        self.callback = callback

    @Slot()
    def run(self):
        self.callback(self.node)

class ThreadManager(QObject):
    def __init__(self, parent):
        super().__init__(parent)
        self.threadPool = QThreadPool()
        self.workers = []

    def run(self, callback: Callback, 
            onStarted: Callback = None, 
            onFinished: Callback = None,
            onProgress: Callback = None,
            onError: Callback = None):
        w = Worker(callback)
        n = w.node
        if onStarted is not None:
            n.started.connect(onStarted)
        if onFinished is not None:
            n.finished.connect(onFinished)
        if onProgress is not None:
            n.progress.connect(onProgress)
        if onError is not None:
            n.error.connect(onError)
        self.workers.append(w) 
        self.threadPool.start(w)
