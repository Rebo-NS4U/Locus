# SPDX-License-Identifier: GPL-3.0-or-later
"""Map bridge, departure time control and status label."""
from PySide6.QtCore import QObject, Signal, Slot, QTime
from PySide6.QtWidgets import QWidget, QHBoxLayout, QLabel, QComboBox


class MapBridge(QObject):
    picked = Signal(float, float)
    stop_moved = Signal(int, float, float)
    segment_selected = Signal(int)

    @Slot(float, float)
    def pick(self, latitude, longitude):
        self.picked.emit(latitude, longitude)

    @Slot(int, float, float)
    def moveStop(self, index, latitude, longitude):
        self.stop_moved.emit(index, latitude, longitude)

    @Slot(int)
    def selectLeg(self, index):
        self.segment_selected.emit(index)


class DepartureTimePicker(QWidget):
    """Twelve-hour selection using the application's themed dropdowns."""
    timeChanged = Signal(QTime)

    def __init__(self, initial, parent=None):
        super().__init__(parent)
        self.updating = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.hour, self.minute, self.period = QComboBox(), QComboBox(), QComboBox()
        for hour in range(1, 13):
            self.hour.addItem(str(hour), hour)
        for minute in range(60):
            self.minute.addItem(f'{minute:02d}', minute)
        self.period.addItems(['AM', 'PM'])
        for widget, label in ((self.hour, 'Departure hour'), (self.minute, 'Departure minute'), (self.period, 'AM or PM')):
            widget.setAccessibleName(label)
            widget.setToolTip(label)
            widget.setMaxVisibleItems(10)
            widget.setMinimumWidth(64)
            widget.currentIndexChanged.connect(self.selection_changed)
        layout.addWidget(self.hour, 1)
        layout.addWidget(QLabel(':'))
        layout.addWidget(self.minute, 1)
        layout.addWidget(self.period, 1)
        self.setTime(initial)

    def selection_changed(self, *unused):
        if not self.updating:
            self.timeChanged.emit(self.time())

    def time(self):
        hour = self.hour.currentData() % 12 + (12 if self.period.currentText() == 'PM' else 0)
        return QTime(hour, self.minute.currentData())

    def setTime(self, value):
        if not value.isValid():
            return
        previous = self.time()
        self.updating = True
        self.hour.setCurrentIndex(self.hour.findData(value.hour() % 12 or 12))
        self.minute.setCurrentIndex(self.minute.findData(value.minute()))
        self.period.setCurrentIndex(1 if value.hour() >= 12 else 0)
        self.updating = False
        if previous != self.time():
            self.timeChanged.emit(self.time())

    def text(self):
        return self.time().toString('h:mm AP')


class StatusLabel(QLabel):
    def setText(self, message):
        self.setToolTip(message)
        super().setText(message)
