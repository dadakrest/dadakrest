// ESP32 Robotic Car — WiFi remote control firmware
// Board: any ESP32 dev board, Arduino core 3.x
// Motor driver: L298N (or similar dual H-bridge)
//
// Exposes two things over WiFi:
//   1. A minimal built-in control page at "/"
//   2. A small JSON/plain-text HTTP API, used by the standalone
//      dashboard in ../dashboard/, and by anything else that wants
//      to drive the car (curl, another script, etc.)

#include <WiFi.h>
#include <WebServer.h>

// ---- WiFi AP credentials ----
const char *AP_SSID = "ESP32-Car";
const char *AP_PASSWORD = "drive1234"; // must be 8+ chars

// ---- Motor pins ----
const int LEFT_IN1 = 27;
const int LEFT_IN2 = 26;
const int LEFT_EN  = 14; // PWM

const int RIGHT_IN1 = 25;
const int RIGHT_IN2 = 33;
const int RIGHT_EN  = 32; // PWM

const int PWM_FREQ = 5000;
const int PWM_RES  = 8; // 0-255

int speedPercent = 70;         // 0-100, set via /speed
String currentDirection = "stop"; // stop | forward | backward | left | right

WebServer server(80);

void setMotor(int in1, int in2, int enPin, int pwmValue) {
  // pwmValue: -255..255, sign = direction, magnitude = speed
  if (pwmValue > 0) {
    digitalWrite(in1, HIGH);
    digitalWrite(in2, LOW);
  } else if (pwmValue < 0) {
    digitalWrite(in1, LOW);
    digitalWrite(in2, HIGH);
  } else {
    digitalWrite(in1, LOW);
    digitalWrite(in2, LOW);
  }
  ledcWrite(enPin, abs(pwmValue));
}

void stopMotors() {
  setMotor(LEFT_IN1, LEFT_IN2, LEFT_EN, 0);
  setMotor(RIGHT_IN1, RIGHT_IN2, RIGHT_EN, 0);
  currentDirection = "stop";
}

int speedToPWM() {
  return map(constrain(speedPercent, 0, 100), 0, 100, 0, 255);
}

void driveForward() {
  int p = speedToPWM();
  setMotor(LEFT_IN1, LEFT_IN2, LEFT_EN, p);
  setMotor(RIGHT_IN1, RIGHT_IN2, RIGHT_EN, p);
  currentDirection = "forward";
}

void driveBackward() {
  int p = speedToPWM();
  setMotor(LEFT_IN1, LEFT_IN2, LEFT_EN, -p);
  setMotor(RIGHT_IN1, RIGHT_IN2, RIGHT_EN, -p);
  currentDirection = "backward";
}

void turnLeft() {
  int p = speedToPWM();
  setMotor(LEFT_IN1, LEFT_IN2, LEFT_EN, -p);
  setMotor(RIGHT_IN1, RIGHT_IN2, RIGHT_EN, p);
  currentDirection = "left";
}

void turnRight() {
  int p = speedToPWM();
  setMotor(LEFT_IN1, LEFT_IN2, LEFT_EN, p);
  setMotor(RIGHT_IN1, RIGHT_IN2, RIGHT_EN, -p);
  currentDirection = "right";
}

const char PAGE[] PROGMEM = R"HTML(
<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32 Car</title>
<style>
  body { font-family: sans-serif; text-align: center; background:#111; color:#eee; }
  .row { margin: 8px; }
  button {
    width: 90px; height: 90px; font-size: 20px; margin: 4px;
    border-radius: 12px; border: none; background:#2a7; color:#fff;
  }
  button:active { background:#164; }
  #stopBtn { background:#c33; }
  #stopBtn:active { background:#822; }
  input[type=range] { width: 250px; }
</style>
</head>
<body>
  <h2>ESP32 Car Control</h2>
  <p style="opacity:.6">Full dashboard: see ../dashboard/index.html</p>
  <div class="row">
    <button ontouchstart="cmd('forward')" ontouchend="cmd('stop')"
            onmousedown="cmd('forward')" onmouseup="cmd('stop')">&#8593;</button>
  </div>
  <div class="row">
    <button ontouchstart="cmd('left')" ontouchend="cmd('stop')"
            onmousedown="cmd('left')" onmouseup="cmd('stop')">&#8592;</button>
    <button id="stopBtn" onclick="cmd('stop')">STOP</button>
    <button ontouchstart="cmd('right')" ontouchend="cmd('stop')"
            onmousedown="cmd('right')" onmouseup="cmd('stop')">&#8594;</button>
  </div>
  <div class="row">
    <button ontouchstart="cmd('backward')" ontouchend="cmd('stop')"
            onmousedown="cmd('backward')" onmouseup="cmd('stop')">&#8595;</button>
  </div>
  <div class="row">
    <label>Speed: <span id="speedVal">70</span>%</label><br>
    <input type="range" min="0" max="100" value="70" id="speedSlider"
           oninput="setSpeed(this.value)">
  </div>
<script>
function cmd(action) {
  fetch('/' + action).catch(()=>{});
}
function setSpeed(v) {
  document.getElementById('speedVal').innerText = v;
  fetch('/speed?value=' + v).catch(()=>{});
}
</script>
</body>
</html>
)HTML";

void sendCors() {
  // Allows the standalone dashboard (served from a different origin,
  // e.g. a file:// page or a dev machine) to call this API directly.
  server.sendHeader("Access-Control-Allow-Origin", "*");
}

void handleRoot() { sendCors(); server.send_P(200, "text/html", PAGE); }
void handleForward() { sendCors(); driveForward(); server.send(200, "text/plain", "OK"); }
void handleBackward() { sendCors(); driveBackward(); server.send(200, "text/plain", "OK"); }
void handleLeft() { sendCors(); turnLeft(); server.send(200, "text/plain", "OK"); }
void handleRight() { sendCors(); turnRight(); server.send(200, "text/plain", "OK"); }
void handleStop() { sendCors(); stopMotors(); server.send(200, "text/plain", "OK"); }

void handleSpeed() {
  sendCors();
  if (server.hasArg("value")) {
    speedPercent = constrain(server.arg("value").toInt(), 0, 100);
  }
  server.send(200, "text/plain", "OK");
}

void handleStatus() {
  sendCors();
  String json = "{";
  json += "\"direction\":\"" + currentDirection + "\",";
  json += "\"speed\":" + String(speedPercent) + ",";
  json += "\"uptimeMs\":" + String(millis()) + ",";
  json += "\"freeHeap\":" + String(ESP.getFreeHeap()) + ",";
  json += "\"clients\":" + String(WiFi.softAPgetStationNum()) + ",";
  json += "\"rssi\":" + String(WiFi.RSSI());
  json += "}";
  server.send(200, "application/json", json);
}

void handleOptions() {
  // Preflight support for browsers that send OPTIONS before cross-origin GETs.
  sendCors();
  server.sendHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  server.send(204);
}

void setup() {
  Serial.begin(115200);

  pinMode(LEFT_IN1, OUTPUT);
  pinMode(LEFT_IN2, OUTPUT);
  pinMode(RIGHT_IN1, OUTPUT);
  pinMode(RIGHT_IN2, OUTPUT);

  ledcAttach(LEFT_EN, PWM_FREQ, PWM_RES);
  ledcAttach(RIGHT_EN, PWM_FREQ, PWM_RES);

  stopMotors();

  WiFi.softAP(AP_SSID, AP_PASSWORD);
  Serial.print("AP started. Connect to WiFi \"");
  Serial.print(AP_SSID);
  Serial.println("\" and browse to 192.168.4.1");

  server.on("/", handleRoot);
  server.on("/forward", handleForward);
  server.on("/backward", handleBackward);
  server.on("/left", handleLeft);
  server.on("/right", handleRight);
  server.on("/stop", handleStop);
  server.on("/speed", handleSpeed);
  server.on("/status", handleStatus);
  server.onNotFound([]() {
    if (server.method() == HTTP_OPTIONS) { handleOptions(); }
    else { sendCors(); server.send(404, "text/plain", "Not found"); }
  });
  server.begin();
}

void loop() {
  server.handleClient();
}
