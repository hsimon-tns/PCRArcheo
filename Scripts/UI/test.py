import sys
import os

def subDirs(dir: str):
    sys.path.append(dir)
    for path in os.listdir(dir):
        if os.path.isdir(path):
            sys.path.append(path)
            subDirs(path)

def setRoot(dir: str):
    os.chdir(dir)
    subDirs(dir)

PROJECT_PATH = os.path.abspath(__file__+"/../..")
setRoot(PROJECT_PATH)

import common
import utils

