PIO    := $(shell which pio 2>/dev/null || echo /home/manticore/.platformio/penv/bin/pio)
PYTHON := $(shell test -f /home/manticore/.platformio/penv/bin/python3 && echo /home/manticore/.platformio/penv/bin/python3 || echo python3)

# Auto detect ESP32 port
ESP_PORT := $(shell $(PIO) device list 2>/dev/null | grep -B5 -E 'CP210|CH340|CH341|FTDI' | grep -E '^/dev/tty(USB|ACM)' | head -1)
ifeq ($(ESP_PORT),)
	ESP_PORT := $(shell $(PIO) device list 2>/dev/null | grep -E '^/dev/tty(USB|ACM)' | head -1)
endif

.PHONY: help build upload monitor um clean ports kill plot

help:
	@echo ""
	@echo " ╔══════════════════════════════════════════╗"
	@echo " ║   ESP32 SS49E/OH49E Hall Linear → LED   ║"
	@echo " ╚══════════════════════════════════════════╝"
	@echo ""
	@echo "  build    - Compile sahaja"
	@echo "  upload   - Compile + upload ke ESP32"
	@echo "  monitor  - Buka Serial Monitor (115200)"
	@echo "  um       - Upload + terus buka monitor"
	@echo "  clean    - Hapus hasil build"
	@echo "  ports    - Daftar port tersedia"
	@echo "  plot     - Buka real-time plotter SS49E"
	@echo "  kill     - Matikan proses yang memakai port"
	@echo ""

build:
	$(PIO) run

upload: _check_port
	$(PIO) run --target upload --upload-port $(ESP_PORT)

monitor: _check_port
	$(PIO) device monitor --port $(ESP_PORT) --baud 115200

um: _check_port
	$(PIO) run --target upload --upload-port $(ESP_PORT) && \
	$(PIO) device monitor --port $(ESP_PORT) --baud 115200

plot:
	$(PYTHON) plotter.py

clean:
	$(PIO) run --target clean

ports:
	$(PIO) device list

kill:
	-pkill -f "pio device monitor" || true
	-pkill -f "miniterm"           || true
	@echo "[KILL] Selesai."

_check_port:
	@if [ -z "$(ESP_PORT)" ]; then \
		echo "[ERROR] ESP32 tidak ditemukan. Pastikan sudah terhubung."; \
		exit 1; \
	fi
	@echo "[PORT] ESP32 ditemukan di: $(ESP_PORT)"
