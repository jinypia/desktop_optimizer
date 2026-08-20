"""One-click optimize actions, grouped by category, with the action log.

Every action runs only on click; the runner callable (provided by the main
window) executes it on a worker thread and reports the result to the log.
"""
from __future__ import annotations

from PySide6.QtWidgets import (
    QGridLayout, QGroupBox, QHBoxLayout, QLabel, QMessageBox, QPlainTextEdit,
    QPushButton, QScrollArea, QVBoxLayout, QWidget,
)

from .. import optimizer


class OptimizeTab(QWidget):
    """runner(button, fn, refresh_reclaimable=False) is supplied by MainWindow."""

    def __init__(self, runner, request_quit_for_elevation, parent=None):
        super().__init__(parent)
        self._runner = runner
        self._request_quit_for_elevation = request_quit_for_elevation

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 8, 4, 4)
        outer.setSpacing(8)

        head = QHBoxLayout()
        note = QLabel("Actions run only when you click — nothing is automatic.")
        note.setObjectName("panelNote")
        self._btn_all = QPushButton("▶ Run all safe cleanups")
        self._btn_all.setToolTip(
            "Temp files, Recycle Bin, browser caches, crash reports, "
            "DNS cache, and a working-set trim.")
        self._btn_all.clicked.connect(self._run_all_safe)
        head.addWidget(note, 1)
        head.addWidget(self._btn_all)
        outer.addLayout(head)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        body = QWidget()
        scroll.setWidget(body)
        grid = QGridLayout(body)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(10)
        outer.addWidget(scroll, 1)

        # -- disk ------------------------------------------------------------
        disk = self._group("Disk cleanup")
        self.btn_temp = self._action(
            disk, "Clear temp files", optimizer.clear_temp,
            "Deletes temp files untouched for 24+ hours; in-use files are skipped.",
            refresh=True)
        self.btn_bin = self._action(
            disk, "Empty Recycle Bin", optimizer.empty_recycle_bin,
            "Permanently removes everything in the Recycle Bin.", refresh=True)
        self.btn_browser = self._action(
            disk, "Clear browser caches", optimizer.clear_browser_caches,
            "Chrome / Edge / Firefox HTTP caches. Close browsers for a full clean.")
        self.btn_thumbs = self._action(
            disk, "Clear thumbnail cache", optimizer.clear_thumbnail_cache,
            "Explorer thumbnail/icon caches; fixes wrong or stale thumbnails.")
        self.btn_crash = self._action(
            disk, "Clear crash dumps && error reports", optimizer.clear_crash_reports,
            "Old crash dumps and Windows Error Reporting queues (user profile).")
        grid.addWidget(disk, 0, 0)

        # -- memory ------------------------------------------------------------
        mem = self._group("Memory")
        self.btn_trim = self._action(
            mem, "Trim process memory", optimizer.trim_working_sets,
            "Asks Windows to trim process working sets; pages return on demand.")
        admin = optimizer.is_admin()
        self.btn_standby = self._action(
            mem, "Purge standby memory" + ("" if admin else "  (admin)"),
            optimizer.purge_standby_list,
            "Drops cached standby pages so they become immediately free. "
            + ("Ready (running as administrator)." if admin
               else "Requires administrator — use 'Restart as administrator'."))
        self.btn_standby.setEnabled(admin)
        grid.addWidget(mem, 0, 1)

        # -- network ------------------------------------------------------------
        net = self._group("Network")
        self.btn_dns = self._action(
            net, "Flush DNS cache", optimizer.flush_dns,
            "Clears the DNS resolver cache; helps after VPN or network changes.")
        grid.addWidget(net, 1, 0)

        # -- system ------------------------------------------------------------
        system = self._group("System")
        self.btn_power = self._action(
            system, "Switch power plan", optimizer.toggle_power_plan,
            "Toggles between Balanced and High performance.", confirm=None)
        self.btn_gfx = self._action(
            system, "Restart graphics driver", optimizer.restart_graphics_driver,
            "Sends Win+Ctrl+Shift+B; fixes display glitches. Screen flashes briefly.")
        self.btn_explorer = self._action(
            system, "Restart Explorer", optimizer.restart_explorer,
            "Restarts the shell (taskbar, folder windows). They reopen empty.",
            confirm=("Restart Explorer",
                     "This closes and restarts Windows Explorer — the taskbar "
                     "and any open folder windows will briefly disappear. "
                     "Continue?"))
        if not admin:
            self.btn_elevate = QPushButton("Restart as administrator…")
            self.btn_elevate.clicked.connect(self._elevate)
            system.layout().addWidget(self.btn_elevate)
            elab = QLabel("Relaunches the app elevated (UAC prompt) to enable "
                          "admin-only actions.")
            elab.setObjectName("panelNote")
            elab.setWordWrap(True)
            system.layout().addWidget(elab)
        grid.addWidget(system, 1, 1)
        grid.setRowStretch(2, 1)

        # -- log ------------------------------------------------------------
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(400)
        self.log.setFixedHeight(110)
        self.log.setPlaceholderText("Action results appear here.")
        outer.addWidget(self.log)

    # -- builders ------------------------------------------------------------
    @staticmethod
    def _group(title: str) -> QGroupBox:
        box = QGroupBox(title)
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(4)
        return box

    def _action(self, group, label, fn, description, refresh=False,
                confirm=None) -> QPushButton:
        btn = QPushButton(label)

        def run():
            if confirm:
                title, text = confirm
                ret = QMessageBox.warning(self, title, text,
                                          QMessageBox.Yes | QMessageBox.No,
                                          QMessageBox.No)
                if ret != QMessageBox.Yes:
                    return
            self._runner(btn, fn, refresh)

        btn.clicked.connect(run)
        group.layout().addWidget(btn)
        desc = QLabel(description)
        desc.setObjectName("panelNote")
        desc.setWordWrap(True)
        group.layout().addWidget(desc)
        return btn

    # -- composite actions -----------------------------------------------------
    def _run_all_safe(self):
        self._runner(self.btn_temp, optimizer.clear_temp, True)
        self._runner(self.btn_bin, optimizer.empty_recycle_bin, True)
        self._runner(self.btn_browser, optimizer.clear_browser_caches)
        self._runner(self.btn_crash, optimizer.clear_crash_reports)
        self._runner(self.btn_dns, optimizer.flush_dns)
        self._runner(self.btn_trim, optimizer.trim_working_sets)

    def _elevate(self):
        ret = QMessageBox.question(
            self, "Restart as administrator",
            "The app will close and reopen with administrator rights "
            "(UAC prompt). Continue?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        if optimizer.relaunch_as_admin():
            self._request_quit_for_elevation()
        else:
            QMessageBox.warning(self, "Restart as administrator",
                                "Elevation was cancelled or failed.")
