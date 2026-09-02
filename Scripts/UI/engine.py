from PySide6.QtWidgets import QApplication, QMainWindow
import threads
import sys
import time

counter = 0

def Work(id: int, t: int):
    print(f"[{id}] Starting work")
    time.sleep(t)
    print(f"[{id}] Finished work")

def Task(node: threads.Node):
    global counter
    Work(counter, 3)
    counter += 1

if __name__ == "__main__":
    app = QApplication()
    window = QMainWindow()
    window.show()
    manager = threads.ThreadManager()
    manager.run(Task)
    manager.run(Task)
    manager.run(Task)
    manager.run(Task)
    sys.exit(app.exec())