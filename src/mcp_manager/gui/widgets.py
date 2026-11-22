"""Small presentational building blocks. No business logic lives here."""
from __future__ import annotations
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget,
)


def label(text='', name=None, wrap=False):
    widget = QLabel(text)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def pill(text, tone='neutral'):
    widget = QLabel(text)
    widget.setProperty('pill', tone)
    widget.setAlignment(Qt.AlignmentFlag.AlignCenter)
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return widget


def divider():
    line = QFrame()
    line.setObjectName('divider')
    return line


class Card(QFrame):
    """Rounded surface with an optional header (title, hint, trailing widgets)."""

    def __init__(self, title=None, hint=None, padding=20, spacing=12):
        super().__init__()
        self.setObjectName('card')
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(padding, padding - 2, padding, padding)
        self.body.setSpacing(spacing)
        self.header = None
        if title:
            self.header = QHBoxLayout()
            self.header.setSpacing(8)
            text = QVBoxLayout()
            text.setSpacing(2)
            text.addWidget(label(title, 'cardTitle'))
            if hint:
                text.addWidget(label(hint, 'cardHint', wrap=True))
            self.header.addLayout(text, 1)
            self.body.addLayout(self.header)

    def add(self, widget, stretch=0):
        self.body.addWidget(widget, stretch)
        return widget

    def add_layout(self, layout, stretch=0):
        self.body.addLayout(layout, stretch)
        return layout

    def trailing(self, widget):
        if self.header is not None:
            self.header.addWidget(widget, 0, Qt.AlignmentFlag.AlignVCenter)
        return widget


def field(title, widget, hint=None):
    """Vertical label + control block used by the redesigned forms."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(6)
    layout.addWidget(label(title, 'fieldLabel'))
    layout.addWidget(widget)
    if hint:
        layout.addWidget(label(hint, 'muted', wrap=True))
    return box


class Segmented(QWidget):
    """Segmented buttons that mirror a (hidden) QComboBox, keeping the combo as the source of truth."""
    changed = Signal(str)

    def __init__(self, combo: QComboBox, labels: dict | None = None):
        super().__init__()
        self.combo = combo
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons = []
        count = combo.count()
        for index in range(count):
            value = combo.itemText(index)
            button = QPushButton((labels or {}).get(value, value))
            button.setObjectName('segment')
            button.setCheckable(True)
            button.setProperty('first', index == 0)
            button.setProperty('last', index == count - 1)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            self.group.addButton(button, index)
            layout.addWidget(button)
            self.buttons.append(button)
        layout.addStretch()
        self.group.idClicked.connect(combo.setCurrentIndex)
        combo.currentIndexChanged.connect(self._sync)
        self._sync(combo.currentIndex())

    def _sync(self, index):
        if 0 <= index < len(self.buttons):
            self.buttons[index].setChecked(True)
            self.changed.emit(self.combo.itemText(index))


class EmptyState(QWidget):
    def __init__(self, icon_label: QLabel, title, text, action: QPushButton | None = None):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 40, 24, 40)
        layout.setSpacing(8)
        layout.addStretch()
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignHCenter)
        heading = label(title, 'emptyTitle')
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(heading)
        body = label(text, 'muted', wrap=True)
        body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(body)
        if action is not None:
            layout.addSpacing(6)
            layout.addWidget(action, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
