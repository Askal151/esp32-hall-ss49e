#include "Arduino.h"

// ── Sensor SS49E / OH49E (Analog Hall Linear) ──────────
// Wiring:
//   VCC  → 3.3V
//   GND  → GND
//   OUT  → GPIO 34 (ADC1_CH6, input-only)
//
// Output sensor (tanpa magnet) ≈ 1.65V → ADC ≈ 2048
// Magnet kutub S mendekat: tegangan naik  → ADC > 2048
// Magnet kutub N mendekat: tegangan turun → ADC < 2048

#define HALL_PIN    34   // ADC1_CH6

// ── 4x LED bar ─────────────────────────────────────────
// LED1 = paling lemah, LED4 = paling kuat
#define LED1_PIN    32
#define LED2_PIN    33
#define LED3_PIN    18
#define LED4_PIN    19

// ── Kalibrasi ──────────────────────────────────────────
// Baseline: rata-rata ADC saat tidak ada magnet (±2048 untuk 3.3V)
// Bisa di-update lewat serial command 'c'
int  baseline    = 2048;
bool calibrating = false;

// Threshold deviasi ADC untuk tiap LED menyala
// (nilai absolut dari baseline)
// Sesuaikan setelah tes dengan magnet Anda
const int THRESH[4] = {150, 350, 600, 900};

// ── Variabel global ────────────────────────────────────
const uint8_t LED_PINS[4] = {LED1_PIN, LED2_PIN, LED3_PIN, LED4_PIN};
int  adc_val   = 0;
int  deviation = 0;
int  led_count = 0;

// ── Fungsi helper ──────────────────────────────────────
int read_adc_avg(int samples = 16) {
    long sum = 0;
    for (int i = 0; i < samples; i++) sum += analogRead(HALL_PIN);
    return (int)(sum / samples);
}

void set_leds(int count) {
    for (int i = 0; i < 4; i++)
        digitalWrite(LED_PINS[i], i < count ? HIGH : LOW);
}

void calibrate() {
    Serial.println("[CAL] Jauhkan magnet, kalibrasi baseline...");
    long sum = 0;
    for (int i = 0; i < 64; i++) {
        sum += analogRead(HALL_PIN);
        delay(5);
    }
    baseline = (int)(sum / 64);
    Serial.print("[CAL] Baseline baru: ");
    Serial.println(baseline);
}

// ── Setup ──────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    analogReadResolution(12);       // 12-bit ADC (0–4095)
    analogSetAttenuation(ADC_11db); // range 0–3.3V

    pinMode(HALL_PIN, INPUT);
    for (int i = 0; i < 4; i++) {
        pinMode(LED_PINS[i], OUTPUT);
        digitalWrite(LED_PINS[i], LOW);
    }

    Serial.println("=== ESP32 Hall Linear SS49E/OH49E → LED Bar ===");
    Serial.println("Sensor OUT → GPIO 34 | LED → GPIO 32,33,18,19");
    Serial.println("Perintah: 'c' = kalibrasi ulang baseline");
    Serial.println("Format output: HALL|adc|dev|leds");
    Serial.println("-----------------------------------------------");

    // Kalibrasi awal
    calibrate();
}

// ── Loop ───────────────────────────────────────────────
void loop() {
    // Terima perintah serial
    if (Serial.available()) {
        char cmd = Serial.read();
        if (cmd == 'c' || cmd == 'C') calibrate();
        if (cmd == 's' || cmd == 'S') {
            Serial.print("[STATUS] ADC="); Serial.print(adc_val);
            Serial.print(" | Baseline="); Serial.print(baseline);
            Serial.print(" | Dev="); Serial.print(deviation);
            Serial.print(" | LED="); Serial.println(led_count);
        }
    }

    // Baca sensor
    adc_val   = read_adc_avg(16);
    deviation = abs(adc_val - baseline);

    // Hitung berapa LED yang nyala
    led_count = 0;
    for (int i = 0; i < 4; i++) {
        if (deviation >= THRESH[i]) led_count = i + 1;
    }

    set_leds(led_count);

    // Output untuk serial monitor & plotter (setiap 100ms)
    static uint32_t last_print = 0;
    if (millis() - last_print >= 100) {
        last_print = millis();
        Serial.print("HALL|");
        Serial.print(adc_val);   Serial.print("|");
        Serial.print(deviation); Serial.print("|");
        Serial.println(led_count);
    }

    delay(5);
}
