"""Full process list with search, sorting, and control actions
(end process / end tree / set priority).

The list refreshes every few seconds — but only while the tab is visible,
and scans run on the thread pool, never the GUI thread.
"""
from __future__ import annotations

import logging
import time

import psutil
from PySide6.QtCore import Qt, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget,
)

from .. import optimizer
from ..procsnap import ProcessScanner
from ..util import human_bytes
from .workers import Worker

log = logging.getLogger(__name__)

REFRESH_MS = 3000
COLS = ("Name", "PID", "User", "CPU %", "Memory", "Threads", "Priority", "Started")


class _NumItem(QTableWidgetItem):
    """Sorts by the numeric value stored in UserRole, not by display text."""

    def __lt__(self, other):
        return (self.data(Qt.UserRole) or 0) < (other.data(Qt.UserRole) or 0)


def _scan_processes(scanner, user_cache: dict):
    """Full process scan from one kernel snapshot (see app/procsnap.py).

    Everything the table shows except the owner comes straight out of that
    single call. The owner is the one field the snapshot has no room for,
    so it is looked up once per process and cached for its lifetime —
    a process cannot change owner, so re-asking every 3 s was pure waste.

    This replaces a psutil scan that cost 553 ms per refresh, 484 ms of it
    inside num_threads() alone.
    """
    rows = []
    for r in scanner.scan():
        key = (r.pid, r.create_ts)
        user = user_cache.get(key)
        if user is None:
            user = _owner(r.pid)
            user_cache[key] = user
        rows.append({"pid": r.pid, "name": r.name, "user": user,
                     "cpu": r.cpu, "rss": r.rss, "threads": r.threads,
                     "prio": r.priority_label, "started": r.create_ts})
    if len(user_cache) > 4096:                # exited processes, eventually
        live = {(r["pid"], r["started"]) for r in rows}
        for key in [k for k in user_cache if k not in live]:
            del user_cache[key]
    return rows


def _owner(pid: int) -> str:
    """Account name for a process, or "" when Windows will not say."""
    try:
        return (psutil.Process(pid).username() or "").split("\\")[-1]
    except psutil.Error:
        return ""


class ProcessTab(QWidget):
    action_result = Signal(object)     # ActionResult -> main window log

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = QThreadPool.globalInstance()
        self._scanning = False
        self._active = False
        # Own scanner: its buffer and CPU baseline stay independent of the
        # sampler thread's, which is why no locking is needed here.
        self._scanner = ProcessScanner()
        self._user_cache = {}          # (pid, create_ts) -> account name

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 8, 4, 4)
        lay.setSpacing(8)

        # toolbar: search + actions
        bar = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("Filter by name, PID or user…")
        self._search.setClearButtonEnabled(True)
        self._search.textChanged.connect(self._apply_filter)
        bar.addWidget(self._search, 1)

        self._btn_end = QPushButton("End process")
        self._btn_tree = QPushButton("End process tree")
        self._prio_combo = QComboBox()
        self._prio_combo.addItems(list(optimizer.PRIORITY_CLASSES))
        self._prio_combo.setCurrentText("Normal")
        self._btn_prio = QPushButton("Set priority")
        self._btn_end.clicked.connect(lambda: self._end_selected(tree=False))
        self._btn_tree.clicked.connect(lambda: self._end_selected(tree=True))
        self._btn_prio.clicked.connect(self._set_priority_selected)
        for w in (self._btn_end, self._btn_tree, self._prio_combo,
                  self._btn_prio):
            bar.addWidget(w)
        lay.addLayout(bar)

        self._count_label = QLabel("")
        self._count_label.setObjectName("panelNote")
        lay.addWidget(self._count_label)

        t = QTableWidget(0, len(COLS))
        t.setHorizontalHeaderLabels(COLS)
        t.verticalHeader().setVisible(False)
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.setSelectionMode(QAbstractItemView.SingleSelection)
        t.setSortingEnabled(True)
        t.setShowGrid(False)
        header = t.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(COLS)):
            header.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        t.sortByColumn(3, Qt.DescendingOrder)     # CPU% desc by default
        self._table = t
        lay.addWidget(t, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._request_scan)

    # -- visibility-driven refresh ------------------------------------------
    def set_active(self, active: bool):
        self._active = active
        if active:
            self._request_scan()
            self._timer.start()
        else:
            self._timer.stop()

    def _request_scan(self):
        if self._scanning:
            return
        self._scanning = True
        worker = Worker(lambda: _scan_processes(self._scanner,
                                                self._user_cache))
        worker.signals.done.connect(self._scan_done)
        self._pool.start(worker)

    def _scan_done(self, rows):
        self._scanning = False
        if not isinstance(rows, list):
            log.error("Process scan failed: %s", getattr(rows, "message", rows))
            return
        self._populate(rows)

    def _populate(self, rows):
        t = self._table
        selected_pid = self.selected_pid()
        t.setSortingEnabled(False)
        t.setRowCount(len(rows))
        for r, d in enumerate(rows):
            started = (time.strftime("%m-%d %H:%M",
                                     time.localtime(d["started"]))
                       if d["started"] else "")
            cells = (
                (d["name"], None),
                (str(d["pid"]), d["pid"]),
                (d["user"], None),
                (f"{d['cpu']:.1f}", d["cpu"]),
                (human_bytes(d["rss"]), d["rss"]),
                (str(d["threads"]), d["threads"]),
                (d["prio"], None),
                (started, d["started"]),
            )
            for c, (text, num) in enumerate(cells):
                item = _NumItem() if num is not None else QTableWidgetItem()
                item.setText(text)
                if num is not None:
                    item.setData(Qt.UserRole, num)
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 0:
                    item.setData(Qt.UserRole, d["pid"])   # pid rides on col 0
                t.setItem(r, c, item)
        t.setSortingEnabled(True)
        self._apply_filter()
        if selected_pid is not None:
            self._reselect(selected_pid)
        self._count_label.setText(f"{len(rows):,} processes")

    def _apply_filter(self):
        needle = self._search.text().strip().lower()
        t = self._table
        for r in range(t.rowCount()):
            if not needle:
                t.setRowHidden(r, False)
                continue
            hay = " ".join((t.item(r, 0).text(), t.item(r, 1).text(),
                            t.item(r, 2).text())).lower()
            t.setRowHidden(r, needle not in hay)

    def _reselect(self, pid: int):
        t = self._table
        for r in range(t.rowCount()):
            if t.item(r, 0) and t.item(r, 0).data(Qt.UserRole) == pid:
                t.selectRow(r)
                return

    # -- actions ---------------------------------------------------------------
    def selected_pid(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return None
        item = self._table.item(rows[0].row(), 0)
        return item.data(Qt.UserRole) if item else None

    def _selected_name(self):
        rows = self._table.selectionModel().selectedRows()
        return self._table.item(rows[0].row(), 0).text() if rows else "?"

    def _end_selected(self, tree: bool):
        pid = self.selected_pid()
        if pid is None:
            return
        name = self._selected_name()
        what = "and all its child processes " if tree else ""
        ret = QMessageBox.warning(
            self, "End process",
            f"End {name} (PID {pid}) {what}now?\n"
            "Unsaved data in that application will be lost.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        worker = Worker(lambda: optimizer.end_process(pid, tree=tree))
        worker.signals.done.connect(self._on_action_done)
        self._pool.start(worker)

    def _set_priority_selected(self):
        pid = self.selected_pid()
        if pid is None:
            return
        level = self._prio_combo.currentText()
        worker = Worker(lambda: optimizer.set_priority(pid, level))
        worker.signals.done.connect(self._on_action_done)
        self._pool.start(worker)

    def _on_action_done(self, result):
        self.action_result.emit(result)
        self._request_scan()
