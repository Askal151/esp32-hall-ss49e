import sys
import re
import serial
import serial.tools.list_ports
import threading
import csv
import os
from collections import deque
from datetime import datetime

import pyqtgraph as pg
from pyqtgraph.Qt import QtWidgets, QtCore
import numpy as np

# ── Konfigurasi ─────────────────────────────
BAUD       = 115200
MAX_POINTS = 300
REFRESH_MS = 30
LOG_FILE   = os.path.join(os.path.dirname(__file__), 'data_log.csv')

# ── Auto detect port ────────────────────────
def find_port():
    for p in serial.tools.list_ports.comports():
        if any(c in p.description for c in ['CP210', 'CH340', 'CH341', 'FTDI']):
            return p.device
    ports = serial.tools.list_ports.comports()
    return ports[0].device if ports else None

PORT = find_port()
if not PORT:
    print("[ERROR] ESP32 tidak ditemukan.")
    sys.exit(1)

print(f"[PORT] {PORT} | Baud: {BAUD} | Refresh: {1000//REFRESH_MS}fps")

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.1)
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)

# ── Buffer data ─────────────────────────────
# Format: HALL|adc|deviasi|led_count
buf_adc  = deque([2048.0] * MAX_POINTS, maxlen=MAX_POINTS)
buf_dev  = deque([0.0]    * MAX_POINTS, maxlen=MAX_POINTS)
buf_led  = deque([0.0]    * MAX_POINTS, maxlen=MAX_POINTS)

cur_adc  = 2048
cur_dev  = 0
cur_led  = 0
baseline = 2048
lock     = threading.Lock()

pat = re.compile(r'HALL\|(\d+)\|(\d+)\|(\d+)')
pat_cal = re.compile(r'\[CAL\].*?(\d+)$')

is_logging = False
log_count  = 0
csv_writer = None
csv_file_h = None

# Threshold sama seperti firmware
THRESH = [150, 350, 600, 900]
LED_COLORS = ['#555', '#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']

# ── Serial thread ───────────────────────────
def serial_reader():
    global cur_adc, cur_dev, cur_led, baseline, log_count, csv_writer
    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()

            # Tangkap update baseline dari kalibrasi
            m_cal = pat_cal.search(line)
            if m_cal:
                with lock:
                    baseline = int(m_cal.group(1))
                continue

            m = pat.search(line)
            if m:
                adc = int(m.group(1))
                dev = int(m.group(2))
                led = int(m.group(3))
                ts  = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                with lock:
                    cur_adc = adc
                    cur_dev = dev
                    cur_led = led
                    buf_adc.append(float(adc))
                    buf_dev.append(float(dev))
                    buf_led.append(float(led))
                    if is_logging and csv_writer:
                        csv_writer.writerow([ts, adc, dev, led])
                        log_count += 1
        except Exception:
            pass

t = threading.Thread(target=serial_reader, daemon=True)
t.start()

# ── PyQtGraph setup ─────────────────────────
pg.setConfigOption('background', '#1a1a2e')
pg.setConfigOption('foreground', '#e0e0e0')

app = QtWidgets.QApplication(sys.argv)
win = QtWidgets.QWidget()
win.setWindowTitle('ESP32 Hall Linear SS49E/OH49E — Real-Time Plotter')
win.resize(1280, 780)
win.setStyleSheet("background:#1a1a2e; color:#e0e0e0;")

main_layout = QtWidgets.QHBoxLayout(win)
main_layout.setContentsMargins(8, 8, 8, 8)
main_layout.setSpacing(8)

# ── Graf ────────────────────────────────────
plot_widget = pg.GraphicsLayoutWidget()
main_layout.addWidget(plot_widget, stretch=3)

x = np.arange(MAX_POINTS)

# Panel 1 – Nilai ADC mentah
p1 = plot_widget.addPlot(row=0, col=0)
p1.setTitle("<b>Nilai ADC Sensor (0–4095)</b>")
p1.showGrid(x=True, y=True, alpha=0.25)
p1.setXRange(0, MAX_POINTS)
p1.setYRange(0, 4095)
p1.setLabel('left', 'ADC')
p1.addLegend(offset=(-10, 10))

curve_adc      = p1.plot(pen=pg.mkPen('#00d4ff', width=2), name='ADC')
line_baseline  = pg.InfiniteLine(pos=2048, angle=0,
    pen=pg.mkPen('#ffffff', width=1, style=QtCore.Qt.DashLine),
    label='Baseline', labelOpts={'color': '#aaa', 'position': 0.05})
p1.addItem(line_baseline)

# Garis threshold
THRESH_COLORS = ['#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']
for i, th in enumerate(THRESH):
    for sign in [1, -1]:
        ln = pg.InfiniteLine(pos=2048 + sign * th, angle=0,
            pen=pg.mkPen(THRESH_COLORS[i], width=1, style=QtCore.Qt.DotLine))
        p1.addItem(ln)

# Panel 2 – Deviasi & LED count
plot_widget.nextRow()
p2 = plot_widget.addPlot(row=1, col=0)
p2.setTitle("<b>Deviasi & Jumlah LED Aktif</b>")
p2.showGrid(x=True, y=True, alpha=0.25)
p2.setXRange(0, MAX_POINTS)
p2.setYRange(-0.2, 4.3)
p2.setLabel('bottom', 'Sampel')
p2.setLabel('left', 'LED (0–4)')
p2.addLegend(offset=(-10, 10))

curve_led = p2.plot(pen=pg.mkPen('#9b59b6', width=2.5), name='LED Aktif')
fill_led  = pg.FillBetweenItem(curve_led,
    p2.plot([0, MAX_POINTS], [0, 0], pen=None),
    brush=pg.mkBrush('#9b59b620'))
p2.addItem(fill_led)

# Axis kanan — deviasi (skala berbeda)
p2r = pg.ViewBox()
p2.showAxis('right')
p2.scene().addItem(p2r)
p2.getAxis('right').linkToView(p2r)
p2r.setXLink(p2)
p2.getAxis('right').setLabel('Deviasi ADC', color='#ff9f43')

def update_views():
    p2r.setGeometry(p2.vb.sceneBoundingRect())
    p2r.linkedViewChanged(p2.vb, p2r.XAxis)

p2.vb.sigResized.connect(update_views)
curve_dev = pg.PlotCurveItem(pen=pg.mkPen('#ff9f43', width=1.5))
p2r.addItem(curve_dev)
p2r.setYRange(0, 1200)

# ── Panel kanan ─────────────────────────────
right = QtWidgets.QVBoxLayout()
main_layout.addLayout(right, stretch=1)

def mlbl(text, size=10, color='#e0e0e0', bold=False):
    l = QtWidgets.QLabel(text)
    l.setStyleSheet(f"font-size:{size}pt; color:{color}; font-weight:{'bold' if bold else 'normal'};")
    l.setAlignment(QtCore.Qt.AlignCenter)
    l.setWordWrap(True)
    return l

def msep():
    f = QtWidgets.QFrame()
    f.setFrameShape(QtWidgets.QFrame.HLine)
    f.setStyleSheet("color:#333;")
    return f

def bstyle(c):
    return (f"QPushButton{{background:{c};color:white;border:none;padding:6px;"
            f"border-radius:4px;font-weight:bold;font-size:10pt;}}"
            f"QPushButton:hover{{background:{c}bb;}}"
            f"QPushButton:disabled{{background:#333;color:#666;}}")

# Header
right.addWidget(mlbl('SS49E / OH49E', 12, '#00d4ff', bold=True))
right.addWidget(mlbl('Linear Hall Sensor', 9, '#666'))
right.addWidget(msep())

# Nilai real-time
lbl_adc      = mlbl('ADC: 2048', 14, '#00d4ff', bold=True)
lbl_baseline = mlbl('Baseline: 2048', 9, '#888')
lbl_dev      = mlbl('Deviasi: 0', 13, '#ff9f43', bold=True)
right.addWidget(lbl_adc)
right.addWidget(lbl_baseline)
right.addWidget(lbl_dev)
right.addWidget(msep())

# LED bar visual
right.addWidget(mlbl('LED BAR', 10, '#888', bold=True))
led_grid = QtWidgets.QHBoxLayout()
led_grid.setSpacing(4)
lbl_leds = []
for i in range(4):
    lbl = QtWidgets.QLabel(f'L{i+1}')
    lbl.setAlignment(QtCore.Qt.AlignCenter)
    lbl.setStyleSheet(
        "font-size:11pt; font-weight:bold; color:#555;"
        "background:#16213e; border:2px solid #333;"
        "border-radius:6px; padding:8px;"
    )
    lbl.setMinimumHeight(50)
    led_grid.addWidget(lbl)
    lbl_leds.append(lbl)

right.addLayout(led_grid)
lbl_led_count = mlbl('0 / 4 LED', 16, '#9b59b6', bold=True)
right.addWidget(lbl_led_count)
right.addWidget(msep())

# Tombol kalibrasi
btn_cal = QtWidgets.QPushButton('⟳  Kalibrasi Baseline')
btn_cal.setStyleSheet(bstyle('#2980b9'))
right.addWidget(btn_cal)

def do_calibrate():
    try:
        ser.write(b'c')
    except Exception:
        pass

btn_cal.clicked.connect(do_calibrate)
right.addWidget(msep())

# Log controls
right.addWidget(mlbl('DATA LOGGING', 10, '#888', bold=True))
lbl_log_status = mlbl('● Idle', 10, '#888')
lbl_log_count  = mlbl('0 rekaman', 10, '#aaa')
right.addWidget(lbl_log_status)
right.addWidget(lbl_log_count)

btn_start = QtWidgets.QPushButton('⏺  Mulai Log')
btn_stop  = QtWidgets.QPushButton('⏹  Stop Log')
btn_clear = QtWidgets.QPushButton('🗑  Hapus')
btn_open  = QtWidgets.QPushButton('📂  Buka CSV')
btn_start.setStyleSheet(bstyle('#2ecc71'))
btn_stop.setStyleSheet(bstyle('#e74c3c'))
btn_clear.setStyleSheet(bstyle('#e67e22'))
btn_open.setStyleSheet(bstyle('#3498db'))
btn_stop.setEnabled(False)
for w in [btn_start, btn_stop, btn_clear, btn_open]:
    right.addWidget(w)

right.addStretch()

# ── Log actions ─────────────────────────────
def start_logging():
    global is_logging, csv_writer, csv_file_h, log_count
    if is_logging: return
    csv_file_h = open(LOG_FILE, 'a', newline='')
    csv_writer  = csv.writer(csv_file_h)
    if os.path.getsize(LOG_FILE) == 0:
        csv_writer.writerow(['Waktu', 'ADC', 'Deviasi', 'LED'])
    is_logging = True
    btn_start.setEnabled(False); btn_stop.setEnabled(True)
    lbl_log_status.setText('● Merekam...')
    lbl_log_status.setStyleSheet("font-size:10pt; color:#2ecc71; font-weight:bold;")

def stop_logging():
    global is_logging, csv_writer, csv_file_h
    if not is_logging: return
    is_logging = False
    if csv_file_h: csv_file_h.close()
    csv_writer = None
    btn_start.setEnabled(True); btn_stop.setEnabled(False)
    lbl_log_status.setText('● Berhenti')
    lbl_log_status.setStyleSheet("font-size:10pt; color:#e74c3c;")

def clear_log():
    global log_count
    stop_logging()
    if os.path.exists(LOG_FILE): os.remove(LOG_FILE)
    log_count = 0
    lbl_log_count.setText('0 rekaman')
    lbl_log_status.setText('● Idle')
    lbl_log_status.setStyleSheet("font-size:10pt; color:#888;")

def open_csv():
    if os.path.exists(LOG_FILE):
        import subprocess
        subprocess.Popen(['xdg-open', LOG_FILE])

btn_start.clicked.connect(start_logging)
btn_stop.clicked.connect(stop_logging)
btn_clear.clicked.connect(clear_log)
btn_open.clicked.connect(open_csv)

# ── Update loop ──────────────────────────────
def update():
    with lock:
        adc   = cur_adc
        dev   = cur_dev
        led   = cur_led
        bl    = baseline
        count = log_count
        d_adc = np.array(buf_adc)
        d_dev = np.array(buf_dev)
        d_led = np.array(buf_led)

    # Update graf
    curve_adc.setData(x, d_adc)
    curve_dev.setData(x, d_dev)
    curve_led.setData(x, d_led)

    # Update garis baseline
    line_baseline.setValue(bl)

    # Update label
    lbl_adc.setText(f'ADC: {adc}')
    lbl_baseline.setText(f'Baseline: {bl}')
    lbl_dev.setText(f'Deviasi: {dev}')
    lbl_led_count.setText(f'{led} / 4 LED')

    # Update LED bar
    colors = ['#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']
    for i in range(4):
        on = i < led
        if on:
            lbl_leds[i].setStyleSheet(
                f"font-size:11pt; font-weight:bold; color:#1a1a2e;"
                f"background:{colors[i]}; border:2px solid {colors[i]};"
                f"border-radius:6px; padding:8px;"
            )
        else:
            lbl_leds[i].setStyleSheet(
                "font-size:11pt; font-weight:bold; color:#555;"
                "background:#16213e; border:2px solid #333;"
                "border-radius:6px; padding:8px;"
            )

    lbl_log_count.setText(f'{count} rekaman')

timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(REFRESH_MS)

win.show()
update_views()
print(f"[PLOTTER] SS49E/OH49E | Format: HALL|adc|deviasi|led | ~{1000//REFRESH_MS}fps")

try:
    app.exec_()
finally:
    stop_logging()
    ser.close()
    print("[PLOTTER] Selesai.")
