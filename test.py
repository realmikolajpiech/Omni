# Source - https://stackoverflow.com/a
# Posted by Pedro, modified by community. See post 'Timeline' for change history
# Retrieved 2026-01-29, License - CC BY-SA 4.0

import sys
from PySide6.QtWidgets import *
from PySide6.QtCore import *
from PySide6.QtGui import *
from BlurWindow.blurWindow import blur



class MainWindow(QWidget):
    def __init__(self):
        super(MainWindow, self).__init__()
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.resize(500, 400)

        blur(self.winId())

        self.setStyleSheet("background-color: rgba(0, 0, 0, 0)")



if __name__ == '__main__':
    app = QApplication(sys.argv)
    mw = MainWindow()
    mw.show()
    sys.exit(app.exec_())
