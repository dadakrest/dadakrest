// Robotic Car — control platform frontend.
// Talks directly to the ESP32 firmware's HTTP API (see ../firmware/esp32_car.ino):
//   GET /forward /backward /left /right /stop
//   GET /speed?value=0-100
//   GET /status -> JSON telemetry
//
// No build step: open index.html in a browser (or serve the folder statically).

const REQUEST_TIMEOUT_MS = 2500;
const STATUS_POLL_MS = 1000;
const KEY_DIRECTIONS = {
  ArrowUp: "forward", w: "forward", W: "forward",
  ArrowDown: "backward", s: "backward", S: "backward",
  ArrowLeft: "left", a: "left", A: "left",
  ArrowRight: "right", d: "right", D: "right",
};

class Car {
  constructor(baseUrl) {
    this.setBaseUrl(baseUrl);
  }

  setBaseUrl(baseUrl) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
  }

  async _get(path) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      const res = await fetch(`${this.baseUrl}${path}`, { signal: controller.signal });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return res;
    } finally {
      clearTimeout(timeout);
    }
  }

  forward() { return this._get("/forward"); }
  backward() { return this._get("/backward"); }
  left() { return this._get("/left"); }
  right() { return this._get("/right"); }
  stop() { return this._get("/stop"); }
  setSpeed(percent) { return this._get(`/speed?value=${encodeURIComponent(percent)}`); }

  async getStatus() {
    const res = await this._get("/status");
    return res.json();
  }
}

class Dashboard {
  constructor(car, dom) {
    this.car = car;
    this.dom = dom;
    this.connected = false;
    this.pollHandle = null;
    this.activeKeyDir = null;

    this._wireConnect();
    this._wireDpad();
    this._wireKeyboard();
    this._wireSpeed();
  }

  log(message) {
    const li = document.createElement("li");
    const time = new Date().toISOString().substring(11, 19);
    li.textContent = `[${time}] ${message}`;
    this.dom.log.prepend(li);
    while (this.dom.log.children.length > 50) {
      this.dom.log.removeChild(this.dom.log.lastChild);
    }
  }

  setConnected(isConnected) {
    if (this.connected === isConnected) return;
    this.connected = isConnected;
    this.dom.connectionPill.classList.toggle("connected", isConnected);
    this.dom.connectionPill.classList.toggle("disconnected", !isConnected);
    this.dom.connectionLabel.textContent = isConnected ? "Connected" : "Disconnected";
    this.log(isConnected ? "Connected to car" : "Lost connection to car");
  }

  connect() {
    this.car.setBaseUrl(`http://${this.dom.carIp.value.trim()}`);
    if (this.pollHandle) clearInterval(this.pollHandle);
    this._pollOnce();
    this.pollHandle = setInterval(() => this._pollOnce(), STATUS_POLL_MS);
  }

  async _pollOnce() {
    try {
      const status = await this.car.getStatus();
      this.setConnected(true);
      this._renderTelemetry(status);
    } catch (err) {
      this.setConnected(false);
    }
  }

  _renderTelemetry(status) {
    this.dom.telDirection.textContent = status.direction ?? "—";
    this.dom.telSpeed.textContent = status.speed != null ? `${status.speed}%` : "—";
    this.dom.telUptime.textContent = status.uptimeMs != null ? formatUptime(status.uptimeMs) : "—";
    this.dom.telHeap.textContent = status.freeHeap != null ? `${status.freeHeap} B` : "—";
    this.dom.telClients.textContent = status.clients ?? "—";
    this.dom.telRssi.textContent = status.rssi != null ? `${status.rssi} dBm` : "—";
  }

  async _sendDirection(dir) {
    try {
      await this.car[dir]();
      this.log(`Sent: ${dir}`);
    } catch (err) {
      this.log(`Failed to send "${dir}": ${err.message}`);
      this.setConnected(false);
    }
  }

  async _sendStop() {
    try {
      await this.car.stop();
      this.log("Sent: stop");
    } catch (err) {
      this.log(`Failed to send stop: ${err.message}`);
      this.setConnected(false);
    }
  }

  _wireConnect() {
    this.dom.connectBtn.addEventListener("click", () => this.connect());
  }

  _wireDpad() {
    this.dom.dpadButtons.forEach((btn) => {
      const dir = btn.dataset.dir;
      const press = (e) => { e.preventDefault(); this._sendDirection(dir); };
      const release = (e) => { e.preventDefault(); this._sendStop(); };
      btn.addEventListener("mousedown", press);
      btn.addEventListener("touchstart", press, { passive: false });
      btn.addEventListener("mouseup", release);
      btn.addEventListener("mouseleave", release);
      btn.addEventListener("touchend", release);
    });
    this.dom.stopBtn.addEventListener("click", () => this._sendStop());
  }

  _wireKeyboard() {
    window.addEventListener("keydown", (e) => {
      if (e.code === "Space") { e.preventDefault(); this._sendStop(); return; }
      const dir = KEY_DIRECTIONS[e.key];
      if (!dir || this.activeKeyDir === dir) return;
      this.activeKeyDir = dir;
      this._sendDirection(dir);
    });
    window.addEventListener("keyup", (e) => {
      const dir = KEY_DIRECTIONS[e.key];
      if (!dir || this.activeKeyDir !== dir) return;
      this.activeKeyDir = null;
      this._sendStop();
    });
  }

  _wireSpeed() {
    this.dom.speedSlider.addEventListener("input", () => {
      this.dom.speedVal.textContent = `${this.dom.speedSlider.value}%`;
    });
    this.dom.speedSlider.addEventListener("change", async () => {
      try {
        await this.car.setSpeed(this.dom.speedSlider.value);
        this.log(`Speed set to ${this.dom.speedSlider.value}%`);
      } catch (err) {
        this.log(`Failed to set speed: ${err.message}`);
        this.setConnected(false);
      }
    });
  }
}

function formatUptime(ms) {
  const totalSeconds = Math.floor(ms / 1000);
  const h = Math.floor(totalSeconds / 3600);
  const m = Math.floor((totalSeconds % 3600) / 60);
  const s = totalSeconds % 60;
  return `${h}h ${m}m ${s}s`;
}

function init() {
  const dom = {
    carIp: document.getElementById("carIp"),
    connectBtn: document.getElementById("connectBtn"),
    connectionPill: document.getElementById("connection"),
    connectionLabel: document.getElementById("connectionLabel"),
    dpadButtons: Array.from(document.querySelectorAll(".dpad-btn")),
    stopBtn: document.getElementById("stopBtn"),
    speedSlider: document.getElementById("speedSlider"),
    speedVal: document.getElementById("speedVal"),
    telDirection: document.getElementById("telDirection"),
    telSpeed: document.getElementById("telSpeed"),
    telUptime: document.getElementById("telUptime"),
    telHeap: document.getElementById("telHeap"),
    telClients: document.getElementById("telClients"),
    telRssi: document.getElementById("telRssi"),
    log: document.getElementById("log"),
  };

  const car = new Car(`http://${dom.carIp.value.trim()}`);
  const dashboard = new Dashboard(car, dom);
  window.dashboard = dashboard; // exposed for manual/console testing
}

document.addEventListener("DOMContentLoaded", init);
