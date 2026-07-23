import sys
import os
import mss
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QSlider, QLabel, QPushButton, QLineEdit, QGroupBox, QComboBox,
    QFrame, QSystemTrayIcon, QMenu, QListWidget, QAbstractItemView, 
    QTimeEdit, QCheckBox, QMessageBox, QColorDialog, QListWidgetItem, QScrollArea
)
from PySide6.QtGui import QAction, QIcon
from PySide6.QtCore import Qt, Slot, QEvent, QTimer, QTime

from config import ConfigManager
from engine import SyncWorker, discover_bulbs

class AmbienZUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AmbienZ")
        self.setMinimumWidth(550)
        
        self.config_manager = ConfigManager()
        self.cfg = self.config_manager.config
        
        self.worker = SyncWorker(self.cfg)
        self.worker.preview_signal.connect(self._update_ui)
        self.worker.log_signal.connect(self._log_msg)
        
        self.last_scheduled_action_time = ""
        
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.NoFrame)
        
        central = QWidget()
        scroll_area.setWidget(central)
        
        self.setCentralWidget(scroll_area)
        self._root_layout = QVBoxLayout(central)
        self._root_layout.setContentsMargins(20, 20, 20, 20)
        self._root_layout.setSpacing(10)

        self._setup_ui()
        self._setup_tray()
        self._load_config_to_ui()
        
        # Apply CSS
        try:
            with open("style.qss", "r") as f:
                self.setStyleSheet(f.read())
        except Exception as e:
            print(f"Failed to load style.qss: {e}")

        # Scheduling timer (checks every 10 seconds)
        self.schedule_timer = QTimer(self)
        self.schedule_timer.timeout.connect(self._check_schedule)
        self.schedule_timer.start(10000)

    # -----------------------------------------------------------------------
    # UI CONSTRUCTION
    # -----------------------------------------------------------------------
    def _setup_ui(self):
        # ---- Preview strip ----
        self.preview_frame = QFrame()
        self.preview_frame.setFixedHeight(72)
        self.preview_frame.setObjectName("preview")
        self._root_layout.addWidget(self.preview_frame)

        # ---- Status row ----
        status_row = QHBoxLayout()
        self.status_dot = QLabel("●")
        self.status_dot.setObjectName("dot_idle")
        self.status_dot.setFixedWidth(20)
        self.status_label = QLabel("Ready")
        self.bulb_count_label = QLabel("Bulbs: 0")
        self.fps_readout = QLabel("FPS: –")
        for w in (self.status_dot, self.status_label, self.bulb_count_label, self.fps_readout):
            status_row.addWidget(w)
        status_row.addStretch()
        self._root_layout.addLayout(status_row)

        # ---- Bulbs group ----
        bulb_group = QGroupBox("Bulbs")
        bulb_layout = QVBoxLayout(bulb_group)

        self.bulb_list = QListWidget()
        self.bulb_list.setFixedHeight(70)
        self.bulb_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        bulb_layout.addWidget(self.bulb_list)

        bulb_input_row = QHBoxLayout()
        self.bulb_input = QLineEdit()
        self.bulb_input.setPlaceholderText("192.168.x.x")
        
        btn_add = QPushButton("+ Add")
        btn_add.setObjectName("smallBtn")
        btn_add.clicked.connect(self._add_bulb)
        
        btn_remove = QPushButton("− Remove")
        btn_remove.setObjectName("smallBtn")
        btn_remove.clicked.connect(self._remove_bulb)
        
        btn_scan = QPushButton("🔍 Scan (Auto)")
        btn_scan.setObjectName("smallBtn")
        btn_scan.clicked.connect(self._scan_bulbs)

        for w in (self.bulb_input, btn_add, btn_remove, btn_scan):
            bulb_input_row.addWidget(w)
        bulb_layout.addLayout(bulb_input_row)
        self._root_layout.addWidget(bulb_group, stretch=1)

        # ---- Settings group ----
        ctrl_group = QGroupBox("Settings")
        ctrl_layout = QVBoxLayout(ctrl_group)

        # Selectors Row
        selectors_layout = QHBoxLayout()
        
        # Monitor selector
        monitor_layout = QVBoxLayout()
        self.monitor_combo = QComboBox()
        with mss.MSS() as sct:
            for i in range(1, len(sct.monitors)):
                m = sct.monitors[i]
                self.monitor_combo.addItem(f"Display {i} ({m['width']}×{m['height']})", i)
        self.monitor_combo.currentIndexChanged.connect(self._sync_params)
        monitor_layout.addWidget(QLabel("Monitor:"))
        monitor_layout.addWidget(self.monitor_combo)
        selectors_layout.addLayout(monitor_layout)
        
        # Mode selector
        mode_layout = QVBoxLayout()
        
        mode_header_layout = QHBoxLayout()
        mode_header_layout.addWidget(QLabel("Extraction Mode:"))
        self.btn_pick_color = QPushButton("Pick Color")
        self.btn_pick_color.setObjectName("smallBtn")
        self.btn_pick_color.setVisible(False)
        self.btn_pick_color.clicked.connect(self._pick_color)
        mode_header_layout.addWidget(self.btn_pick_color)
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["Dominant", "Average", "Edge Weighted", "Static Color"])
        self.mode_combo.currentTextChanged.connect(self._sync_params)
        self.mode_combo.currentTextChanged.connect(self._on_mode_changed)
        
        mode_layout.addLayout(mode_header_layout)
        mode_layout.addWidget(self.mode_combo)
        selectors_layout.addLayout(mode_layout)

        # Effect selector
        effect_layout = QVBoxLayout()
        self.effect_combo = QComboBox()
        self.effect_combo.addItems(["None", "Candle Flicker", "Pulse", "Breathe", "Emergency White Flicker"])
        self.effect_combo.currentTextChanged.connect(self._sync_params)
        effect_layout.addWidget(QLabel("Dynamic Effect:"))
        effect_layout.addWidget(self.effect_combo)
        selectors_layout.addLayout(effect_layout)

        selectors_layout.setContentsMargins(0, 0, 0, 10)
        selectors_container = QWidget()
        selectors_container.setLayout(selectors_layout)
        ctrl_layout.addWidget(selectors_container)

        # Sliders
        self.fps_slider    = self._add_slider(ctrl_layout, "FPS",          10, 60,     40,    divisor=1,   suffix="")
        self.bright_slider = self._add_slider(ctrl_layout, "Brightness",   10, 100,    100,   divisor=1,   suffix="%")
        self.sat_slider    = self._add_slider(ctrl_layout, "Saturation",   10, 30,     14,    divisor=10,  suffix="×")
        self.smooth_slider = self._add_slider(ctrl_layout, "Smoothing",    0,  99,     60,    divisor=100, suffix="")
        self.gamma_slider  = self._add_slider(ctrl_layout, "Gamma",        8,  22,     10,    divisor=10,  suffix="")
        self.kelvin_slider = self._add_slider(ctrl_layout, "Color Temp",   1_000, 20_000, 6_500, divisor=1, suffix=" K")

        ctrl_layout.addStretch()
        self._root_layout.addWidget(ctrl_group, stretch=0)
        
        # ---- Scheduling group ----
        sched_group = QGroupBox("Schedules")
        sched_vlayout = QVBoxLayout(sched_group)
        
        self.sched_list = QListWidget()
        sched_vlayout.addWidget(self.sched_list)
        
        sched_ctrl_layout = QHBoxLayout()
        
        self.sched_time_edit = QTimeEdit()
        self.sched_time_edit.setDisplayFormat("HH:mm")
        self.sched_time_edit.setTime(QTime.currentTime())
        
        self.sched_action_combo = QComboBox()
        self.sched_action_combo.addItems(["Start Sync", "Stop Sync", "Trigger Alarm"])
        
        self.btn_add_sched = QPushButton("+ Add")
        self.btn_add_sched.setObjectName("smallBtn")
        self.btn_add_sched.clicked.connect(self._add_schedule)
        
        self.btn_remove_sched = QPushButton("- Remove")
        self.btn_remove_sched.setObjectName("smallBtn")
        self.btn_remove_sched.clicked.connect(self._remove_schedule)
        
        sched_ctrl_layout.addWidget(QLabel("Time:"))
        sched_ctrl_layout.addWidget(self.sched_time_edit)
        sched_ctrl_layout.addWidget(QLabel("Action:"))
        sched_ctrl_layout.addWidget(self.sched_action_combo)
        sched_ctrl_layout.addWidget(self.btn_add_sched)
        sched_ctrl_layout.addWidget(self.btn_remove_sched)
        
        sched_vlayout.addLayout(sched_ctrl_layout)
        
        self._root_layout.addWidget(sched_group, stretch=2)

        # ---- Toggle button ----
        self.btn_toggle = QPushButton("▶  START SYNC")
        self.btn_toggle.setObjectName("startBtn")
        self.btn_toggle.setCheckable(True)
        self.btn_toggle.clicked.connect(self._toggle_engine)
        self._root_layout.addWidget(self.btn_toggle)

    def _add_slider(self, layout, name: str, mn: int, mx: int, val: int, divisor: int = 1, suffix: str = "") -> QSlider:
        def fmt(v):
            return f"{v / divisor:.2g}{suffix}" if divisor != 1 else f"{v}{suffix}"

        lbl = QLabel(f"{name}: {fmt(val)}")
        slider = QSlider(Qt.Horizontal)
        slider.setRange(mn, mx)
        slider.setValue(val)

        def on_change(v):
            lbl.setText(f"{name}: {fmt(v)}")
            self._sync_params()

        slider.valueChanged.connect(on_change)
        layout.addWidget(lbl)
        layout.addWidget(slider)
        return slider

    # -----------------------------------------------------------------------
    # BULB LIST & DISCOVERY
    # -----------------------------------------------------------------------
    def _add_bulb(self):
        ip = self.bulb_input.text().strip()
        if ip and not self._bulb_exists(ip):
            self.bulb_list.addItem(ip)
            self.bulb_input.clear()
            self._sync_params()

    def _remove_bulb(self):
        row = self.bulb_list.currentRow()
        if row >= 0:
            self.bulb_list.takeItem(row)
            self._sync_params()

    def _scan_bulbs(self):
        self._set_status("syncing", "Scanning for WiZ bulbs on network...")
        QApplication.processEvents()
        
        found = discover_bulbs(timeout=2.0)
        added = 0
        for ip in found:
            if not self._bulb_exists(ip):
                self.bulb_list.addItem(ip)
                added += 1
                
        if added > 0:
            self._set_status("idle", f"Found and added {added} new bulb(s).")
            self._sync_params()
        else:
            self._set_status("idle", "No new bulbs found.")

    def _bulb_exists(self, ip: str) -> bool:
        for i in range(self.bulb_list.count()):
            if self.bulb_list.item(i).text() == ip:
                return True
        return False

    def _get_bulb_ips(self) -> list:
        return [self.bulb_list.item(i).text() for i in range(self.bulb_list.count())]

    # -----------------------------------------------------------------------
    # PARAMS SYNC
    # -----------------------------------------------------------------------
    def _sync_params(self):
        # Update config object in memory (which is passed by reference to SyncWorker)
        monitor_data = self.monitor_combo.currentData()
        
        self.cfg.bulb_ips = self._get_bulb_ips()
        self.cfg.fps = self.fps_slider.value()
        self.cfg.brightness = self.bright_slider.value()
        self.cfg.saturation = self.sat_slider.value()
        self.cfg.smoothness = self.smooth_slider.value()
        self.cfg.gamma = self.gamma_slider.value()
        self.cfg.kelvin = self.kelvin_slider.value()
        self.cfg.mode = self.mode_combo.currentText()
        self.cfg.monitor_idx = monitor_data if monitor_data is not None else 1
        
        self.cfg.effect = self.effect_combo.currentText()

        self.bulb_count_label.setText(f"Bulbs: {len(self.cfg.bulb_ips)}")

    def _load_config_to_ui(self):
        # Bulbs
        for ip in self.cfg.bulb_ips:
            if ip and not self._bulb_exists(ip):
                self.bulb_list.addItem(ip)

        # Monitor
        idx = self.monitor_combo.findData(self.cfg.monitor_idx)
        if idx >= 0:
            self.monitor_combo.setCurrentIndex(idx)

        # Sliders
        self.fps_slider.setValue(self.cfg.fps)
        self.bright_slider.setValue(self.cfg.brightness)
        self.sat_slider.setValue(self.cfg.saturation)
        self.smooth_slider.setValue(self.cfg.smoothness)
        self.gamma_slider.setValue(self.cfg.gamma)
        self.kelvin_slider.setValue(self.cfg.kelvin)

        # Combos
        self.mode_combo.setCurrentText(self.cfg.mode)
        self.effect_combo.setCurrentText(self.cfg.effect)
        
        # Schedule
        self.sched_list.clear()
        for s in self.cfg.schedules:
            item_text = f"{s['time']} - {s['action']}"
            self.sched_list.addItem(item_text)

    # -----------------------------------------------------------------------
    # ENGINE TOGGLE
    # -----------------------------------------------------------------------
    def _toggle_engine(self):
        if self.btn_toggle.isChecked():
            self._sync_params()
            self.worker.start()
            self.btn_toggle.setText("■  STOP SYNC")
            self._set_status("syncing")
        else:
            self.worker.running = False
            self.btn_toggle.setText("▶  START SYNC")
            self._set_status("idle", "Stopped.")

    # -----------------------------------------------------------------------
    # SCHEDULE LOGIC
    # -----------------------------------------------------------------------
    def _on_mode_changed(self, text):
        self.btn_pick_color.setVisible(text == "Static Color")

    def _pick_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.cfg.static_color = color.name()
            self._set_status("idle", f"Static color set to {self.cfg.static_color}")
            self.config_manager.save()

    def _add_schedule(self):
        t = self.sched_time_edit.time().toString("HH:mm")
        act = self.sched_action_combo.currentText()
        self.cfg.schedules.append({"time": t, "action": act, "enabled": True})
        self.sched_list.addItem(f"{t} - {act}")
        self.config_manager.save()
        
    def _remove_schedule(self):
        row = self.sched_list.currentRow()
        if row >= 0:
            self.sched_list.takeItem(row)
            self.cfg.schedules.pop(row)
            self.config_manager.save()

    def _check_schedule(self):
        current_time_str = QTime.currentTime().toString("HH:mm")
        
        if self.last_scheduled_action_time == current_time_str:
            return
            
        triggered = False
        for s in self.cfg.schedules:
            if not s.get("enabled", True):
                continue
                
            target_time_str = s.get("time")
            if current_time_str == target_time_str:
                action = s.get("action")
                self._set_status("idle", f"Schedule triggered: {action}")
                triggered = True
                
                if action == "Start Sync" and not self.worker.running:
                    self.btn_toggle.setChecked(True)
                    self._toggle_engine()
                elif action == "Stop Sync" and self.worker.running:
                    self.btn_toggle.setChecked(False)
                    self._toggle_engine()
                elif action == "Trigger Alarm":
                    self.effect_combo.setCurrentText("Emergency White Flicker")
                    self.bright_slider.setValue(100)
                    if not self.worker.running:
                        self.btn_toggle.setChecked(True)
                        self._toggle_engine()
                        
        if triggered:
            self.last_scheduled_action_time = current_time_str

    # -----------------------------------------------------------------------
    # STATUS & LOGGING
    # -----------------------------------------------------------------------
    def _set_status(self, state: str, msg: str = ""):
        state_cfg = {
            "idle":    ("#aaaaaa", "Idle"),
            "syncing": ("#0078d4", "Syncing"),
            "error":   ("#d83b01", "Error"),
        }
        color, label = state_cfg.get(state, ("#aaaaaa", state))
        self.status_dot.setStyleSheet(f"color: {color}; font-size: 18px;")
        self.status_label.setText(msg if msg else label)

    @Slot(str)
    def _log_msg(self, msg: str):
        if "Error" in msg:
            self._set_status("error", msg)
        else:
            self.status_label.setText(msg)

    # -----------------------------------------------------------------------
    # UI UPDATE SLOT
    # -----------------------------------------------------------------------
    @Slot(dict)
    def _update_ui(self, data):
        r, g, b = data["rgb"]
        self.preview_frame.setStyleSheet(f"background-color: rgb({r},{g},{b}); border-radius: 8px;")
        
        ms = data["time"] * 1000
        actual_fps = 1000 / ms if ms > 0 else 0
        self.fps_readout.setText(f"FPS: {actual_fps:.0f}")
        
        suffix = " (skip)" if data.get("skipped") else ""
        self.status_label.setText(f"RGB ({r}, {g}, {b})  |  {ms:.1f} ms{suffix}")
        self.status_dot.setStyleSheet("color: #0078d4; font-size: 18px;")

    # -----------------------------------------------------------------------
    # SYSTEM TRAY & APP LIFECYCLE
    # -----------------------------------------------------------------------
    def _setup_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = QIcon("Movie.ico")
        if icon.isNull():
            icon = self.style().standardIcon(self.style().StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)

        tray_menu = QMenu()
        show_act = QAction("Show Settings", self)
        show_act.triggered.connect(self.showNormal)
        quit_act = QAction("Quit AmbienZ", self)
        quit_act.triggered.connect(QApplication.instance().quit)
        
        tray_menu.addAction(show_act)
        tray_menu.addSeparator()
        tray_menu.addAction(quit_act)

        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.activated.connect(self._tray_activated)
        self.tray_icon.show()

    def _tray_activated(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange:
            if self.windowState() & Qt.WindowState.WindowMinimized:
                event.ignore()
                self.hide()
                self.tray_icon.showMessage(
                    "AmbienZ", "Running in the background.",
                    QSystemTrayIcon.MessageIcon.Information, 2000,
                )
                return
        super().changeEvent(event)

    def closeEvent(self, event):
        self._sync_params()
        self.config_manager.save()
        self.worker.running = False
        self.worker.wait()
        event.accept()

# ---------------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    app = QApplication(sys.argv)
    icon = QIcon("Movie.ico")
    if not icon.isNull():
        app.setWindowIcon(icon)

    window = AmbienZUI()
    window.show()
    sys.exit(app.exec())
