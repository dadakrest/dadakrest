# Robotic Car

An ESP32-based WiFi robotic car, plus a standalone web dashboard to drive it.

## Layout

- `firmware/esp32_car.ino` — ESP32 Arduino sketch. Drives two DC motors via an
  L298N-style dual H-bridge and exposes an HTTP API over WiFi:
  - `GET /forward`, `/backward`, `/left`, `/right`, `/stop`
  - `GET /speed?value=0-100`
  - `GET /status` → JSON telemetry: `direction`, `speed`, `uptimeMs`,
    `freeHeap`, `clients`, `rssi`
  - `GET /` — a minimal built-in control page (fallback if the dashboard
    below isn't available)
- `dashboard/` — a dependency-free static web app (`index.html` + `app.js` +
  `style.css`) that talks to the car's HTTP API directly from the browser.
  It adds press-and-hold + keyboard (arrow keys/WASD/space) controls, a speed
  slider, a live telemetry panel, and an activity log — a richer client than
  the tiny page baked into the firmware.

## Hardware wiring (defaults in the sketch)

- Left motor: IN1→GPIO27, IN2→GPIO26, ENA(PWM)→GPIO14
- Right motor: IN3→GPIO25, IN4→GPIO33, ENB(PWM)→GPIO32
- L298N GND ↔ ESP32 GND; motor driver powered from the drive battery; ESP32
  powered from the driver's onboard 5V regulator or its own supply.

Adjust the pin constants at the top of `esp32_car.ino` to match your wiring.

## Flashing the firmware

1. Arduino IDE with the ESP32 board package installed (core 3.x).
2. Open `firmware/esp32_car.ino`, select your ESP32 board/port, upload.
3. No external libraries needed — `WiFi.h` and `WebServer.h` ship with the
   ESP32 core.
4. On boot the car starts a WiFi access point named `ESP32-Car`
   (password `drive1234`) and serves its API at `192.168.4.1`.

To have the car join your home WiFi instead of running its own AP, replace
`WiFi.softAP(...)` in `setup()` with `WiFi.begin(ssid, password)` and read the
assigned IP from `WiFi.localIP()` (printed to Serial).

## Running the dashboard

The dashboard is static files — no build step, no backend of its own:

```bash
cd robotic-car/dashboard
python3 -m http.server 8080   # or any static file server
```

Then, with your computer/phone connected to the car's WiFi (or the same
network the car joined), open `http://localhost:8080/index.html`, enter the
car's IP (`192.168.4.1` by default) in the "Car IP address" field, and click
**Connect**. Cross-origin requests from the dashboard to the car are allowed
via `Access-Control-Allow-Origin: *` set by the firmware.

### Controls

- **Mouse/touch**: press and hold a direction button; release to stop.
- **Keyboard**: arrow keys or WASD to drive, release to stop, Space bar for
  an immediate stop from any state.
- **Speed slider**: sets drive speed (0–100%) applied to all subsequent
  movement commands.
- **Telemetry panel**: polls `/status` once per second and shows direction,
  speed, uptime, free heap, connected WiFi clients, and RSSI.

## Testing status

The dashboard's control logic (connect/disconnect detection, press-and-hold
and keyboard drive commands, speed updates, telemetry rendering) was
exercised end-to-end with a headless browser against a mock HTTP server that
mimics the firmware's API — see the activity log and connection indicator
behavior described above. This verifies the *client-side* logic; the
firmware itself has not been run on physical hardware (none is attached to
this environment), so motor wiring, PWM behavior, and real WiFi range/latency
still need a pass on an actual ESP32 + motor driver before trusting it for
real driving.
