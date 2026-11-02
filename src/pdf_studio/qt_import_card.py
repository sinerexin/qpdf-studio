"""Local-file import surface; delegates PDF reading to the existing window."""
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QVBoxLayout


def dropped_pdf_path(urls):
    """Accept one local PDF; never fetch remote URLs or choose from a batch."""
    if len(urls) != 1 or not urls[0].isLocalFile():
        return None
    path = Path(urls[0].toLocalFile())
    return path if path.suffix.lower() == '.pdf' else None


class PdfImportCard(QFrame):
    browse_requested = Signal()
    pdf_dropped = Signal(object)
    rejected = Signal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName('PdfImportCard')
        self.setAcceptDrops(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAccessibleName('导入原稿 PDF，点击或拖入一个本地 PDF 文件')
        self.setMinimumHeight(210)
        self.setStyleSheet('''
            QFrame#PdfImportCard { background: #f5f5f7; border: 1px solid #e5e5ea; border-radius: 16px; }
            QFrame#PdfImportCard[dragActive="true"] { background: #e8f2fc; border: 1px solid #0071e3; }
            QFrame#PdfImportCard:focus { border: 1px solid #0071e3; }
            QLabel { border: none; background: transparent; color: #1d1d1f; }
            QLabel#PdfImportIcon { color: #0071e3; font-size: 22px; font-weight: 600; }
            QPushButton { background: #0071e3; color: white; border: 0; padding: 8px 22px; border-radius: 980px; font-weight: 600; }
            QPushButton:hover { background: #0077ed; }
        ''')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 22)
        for text, name in (
            ('↓  PDF', 'PdfImportIcon'),
            ('01 · 导入原稿', 'Heading'),
            ('将 PDF 拖到这里，或点击选择文件', 'WorkflowHint'),
        ):
            label = QLabel(text)
            label.setObjectName(name)
            label.setAlignment(Qt.AlignCenter)
            label.setWordWrap(True)
            label.setAttribute(Qt.WA_TransparentForMouseEvents)
            layout.addWidget(label)
        button = QPushButton('选择 PDF 文件…')
        button.clicked.connect(lambda _checked=False: self.browse_requested.emit())
        layout.addWidget(button, 0, Qt.AlignHCenter)
        note = QLabel('本机导入 · 保留原稿 · 不上传文件')
        note.setObjectName('WorkflowHint')
        note.setAlignment(Qt.AlignCenter)
        note.setAttribute(Qt.WA_TransparentForMouseEvents)
        layout.addWidget(note)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.rect().contains(event.position().toPoint()):
            self.browse_requested.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space):
            self.browse_requested.emit()
            event.accept()
        else:
            super().keyPressEvent(event)

    def _highlight(self, active):
        self.setProperty('dragActive', active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.update()

    def dragEnterEvent(self, event):
        if dropped_pdf_path(event.mimeData().urls()) is not None:
            self._highlight(True)
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if dropped_pdf_path(event.mimeData().urls()) is not None:
            event.setDropAction(Qt.CopyAction)
            event.accept()
        else:
            event.ignore()

    def dragLeaveEvent(self, event):
        self._highlight(False)
        event.accept()

    def dropEvent(self, event):
        self._highlight(False)
        path = dropped_pdf_path(event.mimeData().urls())
        if path is None:
            self.rejected.emit('请一次拖入一个本地 PDF 文件。')
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self.pdf_dropped.emit(path)
