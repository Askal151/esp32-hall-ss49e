#!/usr/bin/env python3
"""Script kalibrasi interaktif SS49E — dengan live ADC dan pengesahan stabiliti."""
import serial
import time
import sys
import glob
import threading

# ── Cari port ────────────────────────────────────────────────────────────────
def find_port():
    ports = glob.glob('/dev/ttyUSB*') + glob.glob('/dev/ttyACM*')
    return ports[0] if ports else None

# ── Thread baca serial (non-blocking) ────────────────────────────────────────
latest_adc = [0]
latest_dev = [0]
running = [True]
lines_buf = []

def reader_thread(ser):
    while running[0]:
        try:
            if ser.in_waiting:
                line = ser.readline().decode('utf-8', errors='ignore').strip()
                if line:
                    lines_buf.append(line)
                    if line.startswith('HALL|'):
                        p = line.split('|')
                        if len(p) >= 3:
                            latest_adc[0] = int(p[1])
                            latest_dev[0] = int(p[2])
        except:
            pass
        time.sleep(0.005)

def flush_lines():
    lines_buf.clear()

def wait_for_lines(keyword, timeout=6.0):
    """Tunggu line yang mengandungi keyword, return semua lines."""
    end = time.time() + timeout
    found = []
    while time.time() < end:
        for ln in list(lines_buf):
            if keyword in ln:
                found.append(ln)
        if found:
            return found
        time.sleep(0.05)
    return []

# ── Tunggu ADC stabil ─────────────────────────────────────────────────────────
def wait_stable(label, target_min=None, target_max=None, stable_count=15, tol=8):
    """
    Tunjukkan live ADC dan tunggu bacaan stabil.
    Tekan Enter untuk sahkan apabila magnet sudah diletakkan.
    """
    print(f"\n  [LIVE ADC] Tunggu bacaan stabil untuk: {label}")
    print(f"  Tekan ENTER apabila magnet sudah diletakkan dan bacaan stabil...\n")

    enter_pressed = [False]

    def wait_enter():
        input()
        enter_pressed[0] = True

    t = threading.Thread(target=wait_enter, daemon=True)
    t.start()

    history = []
    last_print = 0

    while not enter_pressed[0]:
        now = time.time()
        adc = latest_adc[0]
        dev = latest_dev[0]
        volt = adc * 3.3 / 4095

        history.append(adc)
        if len(history) > stable_count:
            history.pop(0)

        if len(history) >= stable_count:
            spread = max(history) - min(history)
            stable = spread <= tol
        else:
            stable = False
            spread = 0

        if now - last_print >= 0.2:
            status = "STABIL ✓" if stable else f"berubah ({spread})"
            print(f"\r  ADC={adc:4d}  Dev={dev:4d}  V={volt:.3f}  [{status}]       ", end='', flush=True)
            last_print = now

        time.sleep(0.05)

    t.join(timeout=0.1)
    print()

    adc = latest_adc[0]
    dev = latest_dev[0]
    volt = adc * 3.3 / 4095
    print(f"  → ADC={adc}  Deviasi={dev}  Voltase={volt:.3f}V")
    return adc, dev

# ── Main ──────────────────────────────────────────────────────────────────────
port = find_port()
if not port:
    print("[ERROR] ESP32 tidak ditemukan!")
    sys.exit(1)

print(f"\n{'='*52}")
print("   KALIBRASI SS49E HALL SENSOR")
print(f"{'='*52}")
print(f" Port  : {port}")
print(f" Jarak : Letakkan jig pada jarak tetap dari sensor")
print(f" Arah  : Pastikan semua magnet kutub SAMA menghadap sensor")
print(f"{'='*52}\n")

try:
    ser = serial.Serial(port, 115200, timeout=0.1)
    time.sleep(2)
    ser.reset_input_buffer()
    flush_lines()

    # Mulakan reader thread
    rt = threading.Thread(target=reader_thread, args=(ser,), daemon=True)
    rt.start()
    time.sleep(1)

    # Reset threshold ke default dulu
    print("[INFO] Reset threshold ke default...")
    flush_lines()
    ser.write(b'r')
    time.sleep(1)

    # ── LANGKAH 1: Kalibrasi Baseline ────────────────────────────────────────
    print(f"\n{'─'*52}")
    print(" LANGKAH 1: KALIBRASI BASELINE (tanpa magnet)")
    print(" Jauhkan SEMUA magnet dari sensor.")

    wait_stable("Tiada magnet (baseline)")

    flush_lines()
    ser.write(b'c')
    result = wait_for_lines('[CAL] Baseline:', timeout=6)
    if result:
        print(f"  ✓ {result[0]}")
    else:
        print("  [WARN] Tiada respons dari firmware")
    time.sleep(0.5)

    # ── LANGKAH 2-5: Kalibrasi tiap magnet ────────────────────────────────────
    thresholds = {}

    for n in range(1, 5):
        print(f"\n{'─'*52}")
        print(f" LANGKAH {n+1}: KALIBRASI {n} MAGNET")
        if n == 1:
            print(f" Letak 1 magnet dalam jig (kutub betul menghadap sensor).")
        else:
            print(f" Letak {n} magnet bertindih dalam jig (kutub semua sama arah).")

        ok = False
        attempts = 0

        while not ok and attempts < 3:
            attempts += 1
            adc, dev = wait_stable(f"{n} magnet dalam jig")

            if dev < 30:
                print(f"  [!] Deviasi terlalu kecil ({dev}). Pastikan magnet betul-betul dalam jig.")
                print(f"      Cuba lagi...")
                continue

            flush_lines()
            ser.write(str(n).encode())

            # Tunggu respons
            time.sleep(0.5)
            result_lines = []
            end = time.time() + 5.0
            while time.time() < end:
                for ln in list(lines_buf):
                    if '[CAL]' in ln and ln not in result_lines:
                        result_lines.append(ln)
                        print(f"  >> {ln}")
                if any('Threshold=' in ln for ln in result_lines):
                    ok = True
                    break
                if any('ERROR' in ln for ln in result_lines):
                    print(f"  [!] Kalibrasi gagal. Cuba lagi...")
                    break
                time.sleep(0.1)

            if not ok and attempts < 3:
                print(f"  [RETRY] Cuba letak magnet lebih rapat ke sensor...")

        if ok:
            print(f"  ✓ Threshold {n} magnet berjaya dikalibrasi.")
            thresholds[n] = dev
        else:
            print(f"  [WARN] Kalibrasi {n} magnet tidak berjaya. Semak magnet dan jig.")

    # ── Kira dan hantar threshold optimum (titik tengah) ─────────────────────
    if len(thresholds) == 4:
        devs = [0] + [thresholds[n] for n in range(1, 5)]
        opt = [(devs[i] + devs[i+1]) // 2 for i in range(4)]

        print(f"\n{'─'*52}")
        print(" PENGOPTIMUMAN THRESHOLD (titik tengah):")
        print(f"   {'Magnet':<10} {'Deviasi':>8}   {'Threshold':>10}")
        print(f"   {'──────':<10} {'───────':>8}   {'─────────':>10}")
        for i in range(4):
            n = i + 1
            lo = devs[i]
            hi = devs[i+1]
            print(f"   {n} magnet   {hi:>8}   L{n}={opt[i]:>6}  (tengah {lo}–{hi})")

        # Hantar ke firmware
        cmd = f"T {opt[0]} {opt[1]} {opt[2]} {opt[3]}\n"
        flush_lines()
        ser.write(cmd.encode())
        time.sleep(1)

        ok_lines = [ln for ln in lines_buf if '[OK]' in ln or '[THRESH]' in ln]
        if ok_lines:
            for ln in ok_lines:
                print(f"  >> {ln}")
            print(f"  ✓ Threshold optimum disimpan ke NVS.")
        else:
            print(f"  [WARN] Tiada respons. Pastikan firmware sudah diupload semula.")
    else:
        print(f"\n[WARN] Tidak semua level dikalibrasi, threshold tidak dioptimumkan.")

    # ── Status akhir ──────────────────────────────────────────────────────────
    print(f"\n{'─'*52}")
    print(" STATUS AKHIR:")
    flush_lines()
    ser.write(b's')
    time.sleep(2)
    for ln in lines_buf:
        if '===' in ln or 'Baseline' in ln or 'Threshold' in ln:
            print(f"  {ln}")

    print(f"\n{'='*52}")
    print(" KALIBRASI SELESAI!")
    print()
    if thresholds:
        print(" Ringkasan deviasi per magnet:")
        for k, v in sorted(thresholds.items()):
            bar = '█' * (v // 50)
            print(f"   {k} magnet: Dev={v:4d}  {bar}")
    print()
    print(" Sila run: make plot")
    print(f"{'='*52}\n")

    running[0] = False
    ser.close()

except serial.SerialException as e:
    print(f"[ERROR] {e}")
    running[0] = False
    sys.exit(1)
except KeyboardInterrupt:
    print("\n[BATAL] Kalibrasi dibatalkan.")
    running[0] = False
    try:
        ser.close()
    except:
        pass
