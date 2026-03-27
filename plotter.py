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
REFRESH_MS = 50
LOG_FILE   = os.path.join(os.path.dirname(__file__), 'data_log.csv')

# Konversi ADC → Voltase (12-bit, 3.3V)
def adc_to_volt(adc):
    return round(adc * 3.3 / 4095, 3)

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

print(f"[PORT] {PORT} | Baud: {BAUD}")

try:
    ser = serial.Serial(PORT, BAUD, timeout=0.1)
except Exception as e:
    print(f"[ERROR] {e}")
    sys.exit(1)

# ── Buffer data ─────────────────────────────
buf_adc  = deque([0.0] * MAX_POINTS, maxlen=MAX_POINTS)
buf_dev  = deque([0.0] * MAX_POINTS, maxlen=MAX_POINTS)
buf_led  = deque([0.0] * MAX_POINTS, maxlen=MAX_POINTS)
buf_volt = deque([0.0] * MAX_POINTS, maxlen=MAX_POINTS)

cur_adc    = 0
cur_dev    = 0
cur_led    = 0
cur_volt   = 0.0
cur_thresh = [82, 329, 720, 1049]
baseline = 2048
pkt_count = 0
last_raw  = '—'
lock      = threading.Lock()

# Format: HALL|adc|deviasi|led_count
pat        = re.compile(r'HALL\|(\d+)\|(\d+)\|(\d+)')
pat_cal    = re.compile(r'\[CAL\].*?(\d+)\s*$')
pat_thresh = re.compile(r'\[THRESH\]\s*(\d+)\|(\d+)\|(\d+)\|(\d+)')

is_logging = False
log_count  = 0
csv_writer = None
csv_file_h = None

THRESH = [82, 329, 720, 1049]

# ── Serial thread ───────────────────────────
def serial_reader():
    global cur_adc, cur_dev, cur_led, cur_volt, cur_thresh, baseline
    global pkt_count, last_raw, log_count, csv_writer
    while True:
        try:
            line = ser.readline().decode('utf-8', errors='ignore').strip()
            if not line:
                continue

            with lock:
                last_raw = line

            m_thresh = pat_thresh.search(line)
            if m_thresh:
                t = [int(m_thresh.group(i)) for i in range(1, 5)]
                with lock:
                    cur_thresh = t
                print(f"[THRESH] {t}")
                continue

            m_cal = pat_cal.search(line)
            if m_cal:
                with lock:
                    baseline = int(m_cal.group(1))
                print(f"[CAL] Baseline: {m_cal.group(1)}")
                continue

            m = pat.search(line)
            if m:
                adc  = int(m.group(1))
                dev  = int(m.group(2))
                led  = int(m.group(3))
                volt = adc_to_volt(adc)
                ts   = datetime.now().strftime('%H:%M:%S.%f')[:-3]
                with lock:
                    cur_adc  = adc
                    cur_dev  = dev
                    cur_led  = led
                    cur_volt = volt
                    pkt_count += 1
                    buf_adc.append(float(adc))
                    buf_dev.append(float(dev))
                    buf_led.append(float(led))
                    buf_volt.append(volt)
                    if is_logging and csv_writer:
                        csv_writer.writerow([ts, adc, f'{volt:.3f}', dev, led])
                        log_count += 1
            else:
                print(f"[RX] {line}")
        except Exception:
            pass

t = threading.Thread(target=serial_reader, daemon=True)
t.start()

# ── PyQtGraph setup ─────────────────────────
pg.setConfigOption('background', '#1a1a2e')
pg.setConfigOption('foreground', '#e0e0e0')

app = QtWidgets.QApplication(sys.argv)
win = QtWidgets.QWidget()
win.setWindowTitle('ESP32 SS49E/OH49E — Hall Linear Plotter')
win.resize(1280, 800)
win.setStyleSheet("background:#1a1a2e; color:#e0e0e0;")

main_layout = QtWidgets.QHBoxLayout(win)
main_layout.setContentsMargins(8, 8, 8, 8)
main_layout.setSpacing(8)

# ── 3 Panel Graf ────────────────────────────
plot_widget = pg.GraphicsLayoutWidget()
main_layout.addWidget(plot_widget, stretch=3)

x = np.arange(MAX_POINTS)

# Panel 1 – ADC (auto range supaya perubahan kecil tetap kelihatan)
p1 = plot_widget.addPlot(row=0, col=0)
p1.setTitle("<b>Nilai ADC (Mentah)</b>")
p1.showGrid(x=True, y=True, alpha=0.3)
p1.setXRange(0, MAX_POINTS, padding=0)
p1.enableAutoRange(axis='y', enable=True)
p1.setLabel('left', 'ADC')
p1.addLegend(offset=(-10, 10))

curve_adc     = p1.plot(pen=pg.mkPen('#00d4ff', width=2), name='ADC')
line_baseline = pg.InfiniteLine(pos=2048, angle=0,
    pen=pg.mkPen('#ffffff', width=1, style=QtCore.Qt.DashLine))
p1.addItem(line_baseline)

# Panel 2 – Deviasi
plot_widget.nextRow()
p2 = plot_widget.addPlot(row=1, col=0)
p2.setTitle("<b>Deviasi dari Baseline + Zon Threshold</b>")
p2.showGrid(x=True, y=True, alpha=0.2)
p2.setXRange(0, MAX_POINTS, padding=0)
p2.setYRange(0, 1300, padding=0)
p2.setLabel('left', 'Deviasi ADC')

THRESH_COLORS = ['#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']
ZONE_ALPHA    = 30   # kecerahan zon (0-255)

# Zon berwarna antara threshold (LinearRegionItem)
zone_regions = []
zone_bounds = [(0, THRESH[0]), (THRESH[0], THRESH[1]),
               (THRESH[1], THRESH[2]), (THRESH[2], THRESH[3]),
               (THRESH[3], 1400)]
zone_colors  = ['#ffffff', '#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']
for i, (lo, hi) in enumerate(zone_bounds):
    region = pg.LinearRegionItem(
        values=[lo, hi], orientation='horizontal',
        brush=pg.mkBrush(zone_colors[i] + f'{ZONE_ALPHA:02x}'),
        movable=False, pen=pg.mkPen(None))
    p2.addItem(region)
    zone_regions.append(region)

# Kurva deviasi (di atas zon)
curve_dev = p2.plot(pen=pg.mkPen('#ff9f43', width=2.5))
fill_dev  = pg.FillBetweenItem(
    curve_dev,
    p2.plot(x, np.zeros(MAX_POINTS), pen=None),
    brush=pg.mkBrush('#ff9f4330')
)
p2.addItem(fill_dev)

# Garis threshold dinamik (boleh update)
thresh_lines = []
for i, th in enumerate(THRESH):
    ln = pg.InfiniteLine(
        pos=th, angle=0,
        pen=pg.mkPen(THRESH_COLORS[i], width=1.5, style=QtCore.Qt.DashLine),
        label=f' L{i+1}={th}',
        labelOpts={'color': THRESH_COLORS[i], 'position': 0.98,
                   'fill': pg.mkBrush('#1a1a2e'), 'border': pg.mkPen(THRESH_COLORS[i])})
    p2.addItem(ln)
    thresh_lines.append(ln)

# Marker deviasi semasa (garis horizontal bergerak)
cur_dev_line = pg.InfiniteLine(
    pos=0, angle=0,
    pen=pg.mkPen('#ffffff', width=2, style=QtCore.Qt.SolidLine))
p2.addItem(cur_dev_line)
_last_thresh = THRESH[:]

# Panel 3 – LED count
plot_widget.nextRow()
p3 = plot_widget.addPlot(row=2, col=0)
p3.setTitle("<b>Jumlah LED Aktif (0–4)</b>")
p3.showGrid(x=True, y=True, alpha=0.3)
p3.setXRange(0, MAX_POINTS, padding=0)
p3.setYRange(-0.1, 4.3, padding=0)
p3.setLabel('left', 'LED')
p3.setLabel('bottom', 'Sampel')

curve_led = p3.plot(pen=pg.mkPen('#9b59b6', width=2.5), name='LED Aktif')
fill_led  = pg.FillBetweenItem(
    curve_led,
    p3.plot(x, np.zeros(MAX_POINTS), pen=None),
    brush=pg.mkBrush('#9b59b630')
)
p3.addItem(fill_led)

# ── Panel kanan ─────────────────────────────
right = QtWidgets.QVBoxLayout()
main_layout.addLayout(right, stretch=1)

def mlbl(text, size=10, color='#e0e0e0', bold=False):
    l = QtWidgets.QLabel(text)
    l.setStyleSheet(
        f"font-size:{size}pt; color:{color};"
        f"font-weight:{'bold' if bold else 'normal'};"
    )
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
            f"QPushButton:hover{{background:{c}99;}}"
            f"QPushButton:disabled{{background:#333;color:#666;}}")

# Header
right.addWidget(mlbl('SS49E / OH49E', 12, '#00d4ff', bold=True))
right.addWidget(mlbl('Linear Hall Sensor', 9, '#555'))
right.addWidget(msep())

# Status koneksi
lbl_conn = mlbl(f'● {PORT}', 9, '#2ecc71')
lbl_pkt  = mlbl('Paket diterima: 0', 9, '#888')
lbl_raw  = mlbl('—', 8, '#444')
for w in [lbl_conn, lbl_pkt, lbl_raw]: right.addWidget(w)
right.addWidget(msep())

# Nilai real-time
lbl_adc  = mlbl('ADC: —', 16, '#00d4ff', bold=True)
lbl_volt = mlbl('0.000 V', 20, '#2ecc71', bold=True)
lbl_bl   = mlbl('Baseline: 2048', 9, '#666')
lbl_dev  = mlbl('Deviasi: —', 14, '#ff9f43', bold=True)
for w in [lbl_adc, lbl_volt, lbl_bl, lbl_dev]: right.addWidget(w)
right.addWidget(msep())

# LED bar
right.addWidget(mlbl('LED BAR', 10, '#888', bold=True))
led_row = QtWidgets.QHBoxLayout()
led_row.setSpacing(4)
lbl_leds = []
LED_CLR  = ['#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']
for i in range(4):
    lbl = QtWidgets.QLabel(f'L{i+1}')
    lbl.setAlignment(QtCore.Qt.AlignCenter)
    lbl.setStyleSheet(
        "font-size:11pt; font-weight:bold; color:#555;"
        "background:#16213e; border:2px solid #333;"
        "border-radius:6px; padding:8px;"
    )
    lbl.setMinimumHeight(50)
    led_row.addWidget(lbl)
    lbl_leds.append(lbl)
right.addLayout(led_row)
lbl_led_count = mlbl('0 / 4 LED', 18, '#9b59b6', bold=True)
right.addWidget(lbl_led_count)
right.addWidget(msep())

# ── Kalibrasi ───────────────────────────────
right.addWidget(mlbl('KALIBRASI', 10, '#888', bold=True))

btn_cal = QtWidgets.QPushButton('⟳  Baseline (tanpa magnet)')
btn_cal.setStyleSheet(bstyle('#2980b9'))
right.addWidget(btn_cal)

# Tombol kalibrasi per magnet
cal_grid = QtWidgets.QGridLayout()
cal_grid.setSpacing(4)
CAL_CLR = ['#2ecc71', '#f39c12', '#e74c3c', '#9b59b6']
btn_mags = []
for i in range(4):
    btn = QtWidgets.QPushButton(f'🧲 {i+1} Magnet')
    btn.setStyleSheet(bstyle(CAL_CLR[i]))
    btn.setToolTip(f'Tempelkan {i+1} magnet lalu klik')
    cal_grid.addWidget(btn, i // 2, i % 2)
    btn_mags.append(btn)
right.addLayout(cal_grid)

lbl_cal_info = mlbl('Kalibrasi: —', 9, '#888')
right.addWidget(lbl_cal_info)
right.addWidget(msep())

# ── Threshold live (dari firmware) ──────────
right.addWidget(mlbl('THRESHOLD AKTIF', 10, '#888', bold=True))
lbl_thresh = []
for i in range(4):
    l = mlbl(f'L{i+1}: —', 9, CAL_CLR[i])
    right.addWidget(l)
    lbl_thresh.append(l)
right.addWidget(msep())

def do_calibrate():
    try:
        ser.write(b'c')
        lbl_cal_info.setText('Kalibrasi baseline...')
    except Exception:
        pass

def make_cal_magnet(n):
    def fn():
        try:
            ser.write(str(n).encode())
            lbl_cal_info.setText(f'Kalibrasi {n} magnet...')
        except Exception:
            pass
    return fn

btn_cal.clicked.connect(do_calibrate)
for i, btn in enumerate(btn_mags):
    btn.clicked.connect(make_cal_magnet(i + 1))

# Log
right.addWidget(mlbl('DATA LOGGING', 10, '#888', bold=True))
lbl_log_status = mlbl('● Idle', 10, '#888')
lbl_log_count  = mlbl('0 rekaman', 10, '#aaa')
right.addWidget(lbl_log_status)
right.addWidget(lbl_log_count)

btn_start = QtWidgets.QPushButton('⏺  Mulai Log')
btn_stop  = QtWidgets.QPushButton('⏹  Stop Log')
btn_clear = QtWidgets.QPushButton('🗑  Hapus CSV')
btn_open  = QtWidgets.QPushButton('📂  Buka CSV')
for btn, c in [(btn_start,'#2ecc71'),(btn_stop,'#e74c3c'),
               (btn_clear,'#e67e22'),(btn_open,'#3498db')]:
    btn.setStyleSheet(bstyle(c))
    right.addWidget(btn)
btn_stop.setEnabled(False)
right.addStretch()

# ── Log actions ─────────────────────────────
def start_logging():
    global is_logging, csv_writer, csv_file_h, log_count
    if is_logging: return
    csv_file_h = open(LOG_FILE, 'a', newline='')
    csv_writer  = csv.writer(csv_file_h)
    if os.path.getsize(LOG_FILE) == 0:
        csv_writer.writerow(['Waktu', 'ADC', 'Voltase_V', 'Deviasi', 'LED'])
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
        adc    = cur_adc
        dev    = cur_dev
        led    = cur_led
        volt   = cur_volt
        bl     = baseline
        thresh = cur_thresh[:]
        count  = log_count
        pkts   = pkt_count
        raw    = last_raw
        d_adc  = np.array(buf_adc)
        d_dev  = np.array(buf_dev)
        d_led  = np.array(buf_led)

    # Update semua kurva
    curve_adc.setData(x, d_adc)
    curve_dev.setData(x, d_dev)
    curve_led.setData(x, d_led)

    # Update garis baseline
    line_baseline.setValue(bl)

    # Update marker deviasi semasa
    cur_dev_line.setValue(dev)

    # Update threshold lines + zon jika threshold berubah
    global _last_thresh
    if thresh != _last_thresh:
        _last_thresh = thresh[:]
        new_bounds = [(0, thresh[0]), (thresh[0], thresh[1]),
                      (thresh[1], thresh[2]), (thresh[2], thresh[3]),
                      (thresh[3], 1400)]
        for i, (lo, hi) in enumerate(new_bounds):
            zone_regions[i].setRegion([lo, hi])
        for i, ln in enumerate(thresh_lines):
            ln.setValue(thresh[i])
            ln.label.setFormat(f' L{i+1}={thresh[i]}')
        p2.setYRange(0, max(thresh[3] * 1.15, 1300), padding=0)

    # Update label
    lbl_adc.setText(f'ADC: {adc}')
    lbl_volt.setText(f'{volt:.3f} V')
    lbl_bl.setText(f'Baseline: {bl}  ({adc_to_volt(bl):.3f}V)')
    # Tunjuk deviasi + zon aktif
    zone_names = ['—', '●1 Magnet', '●●2 Magnet', '●●●3 Magnet', '●●●●4 Magnet']
    lbl_dev.setText(f'Deviasi: {dev}  {zone_names[led]}')
    lbl_led_count.setText(f'{led} / 4 LED')
    lbl_pkt.setText(f'Paket diterima: {pkts}')
    lbl_raw.setText(str(raw)[:28])

    # Update LED bar
    for i in range(4):
        if i < led:
            lbl_leds[i].setStyleSheet(
                f"font-size:11pt; font-weight:bold; color:#1a1a2e;"
                f"background:{LED_CLR[i]}; border:2px solid {LED_CLR[i]};"
                f"border-radius:6px; padding:8px;"
            )
        else:
            lbl_leds[i].setStyleSheet(
                "font-size:11pt; font-weight:bold; color:#555;"
                "background:#16213e; border:2px solid #333;"
                "border-radius:6px; padding:8px;"
            )

    lbl_log_count.setText(f'{count} rekaman')
    lbl_pkt.setText(f'Paket diterima: {pkts}')
    lbl_raw.setText(str(raw)[:28])

    # Update label threshold
    for i in range(4):
        lbl_thresh[i].setText(f'L{i+1}: ≥ {thresh[i]}  ({thresh[i]*3.3/4095:.3f}V)')

timer = QtCore.QTimer()
timer.timeout.connect(update)
timer.start(REFRESH_MS)

win.show()
print("[PLOTTER] Berjalan. Format: HALL|adc|deviasi|led")

try:
    app.exec_()
finally:
    stop_logging()
    ser.close()
    print("[PLOTTER] Selesai.")
