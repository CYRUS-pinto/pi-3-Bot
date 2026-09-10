/*
  TARS MK-IV — Physical Servo Interaction Firmware
  ------------------------------------------------
  Hardware:
    - Arduino Uno / Nano / Mega / ESP32
    - Pan Servo (Eye / Head Horizontal): Pin 9
    - Tilt Servo (Eye / Head Vertical) : Pin 10
    - Left Arm Servo                   : Pin 5
    - Right Arm Servo                  : Pin 6

  Baud Rate: 115200 (newline terminated ASCII commands)

  Protocol Commands:
    PAN:<deg>          e.g. PAN:85
    TILT:<deg>         e.g. TILT:100
    ARMS:<l_deg>,<r_deg> e.g. ARMS:90,90
    GESTURE:<name>     e.g. GESTURE:WAVE, GESTURE:SWIPE_LEFT, GESTURE:SWIPE_RIGHT
    STANDBY            Smoothly relaxes servos to prevent motor buzzing
    SYNC               Handshake command
*/

#include <Servo.h>

Servo servoPan;
Servo servoTilt;
Servo servoArmL;
Servo servoArmR;

// Pin assignments
const int PIN_PAN   = 9;
const int PIN_TILT  = 10;
const int PIN_ARM_L = 5;
const int PIN_ARM_R = 6;

// Target and current angles for smooth kinematic interpolation
float curPan = 90.0, targetPan = 90.0;
float curTilt = 90.0, targetTilt = 90.0;
float curArmL = 90.0, targetArmL = 90.0;
float curArmR = 90.0, targetArmR = 90.0;

bool inStandby = false;

// Serial reception buffer
String inputBuffer = "";

void setup() {
  Serial.begin(115200);
  while (!Serial && millis() < 2000); // Wait for serial on USB-native boards

  servoPan.attach(PIN_PAN);
  servoTilt.attach(PIN_TILT);
  servoArmL.attach(PIN_ARM_L);
  servoArmR.attach(PIN_ARM_R);

  // Home positions
  servoPan.write(90);
  servoTilt.write(90);
  servoArmL.write(90);
  servoArmR.write(90);

  Serial.println("[TARS-ARDUINO] Physical Servo Firmware Online. Ready.");
}

void loop() {
  // 1. Read incoming serial commands
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputBuffer.length() > 0) {
        parseCommand(inputBuffer);
        inputBuffer = "";
      }
    } else {
      if (inputBuffer.length() < 64) {
        inputBuffer += c;
      }
    }
  }

  // 2. Smooth exponential kinematic motion filter (prevents violent twitching)
  if (!inStandby) {
    curPan  += (targetPan  - curPan)  * 0.25;
    curTilt += (targetTilt - curTilt) * 0.25;
    curArmL += (targetArmL - curArmL) * 0.25;
    curArmR += (targetArmR - curArmR) * 0.25;

    servoPan.write((int)curPan);
    servoTilt.write((int)curTilt);
    servoArmL.write((int)curArmL);
    servoArmR.write((int)curArmR);
  }

  delay(15);
}

void parseCommand(String cmd) {
  cmd.trim();
  if (cmd.length() == 0) return;

  if (cmd.startsWith("PAN:")) {
    inStandby = false;
    targetPan = constrain(cmd.substring(4).toInt(), 20, 160);
  }
  else if (cmd.startsWith("TILT:")) {
    inStandby = false;
    targetTilt = constrain(cmd.substring(5).toInt(), 30, 150);
  }
  else if (cmd.startsWith("ARMS:")) {
    inStandby = false;
    int comma = cmd.indexOf(',');
    if (comma > 5) {
      targetArmL = constrain(cmd.substring(5, comma).toInt(), 10, 170);
      targetArmR = constrain(cmd.substring(comma + 1).toInt(), 10, 170);
    }
  }
  else if (cmd.startsWith("GESTURE:")) {
    inStandby = false;
    String g = cmd.substring(8);
    executeGesture(g);
  }
  else if (cmd == "STANDBY") {
    inStandby = true;
    targetPan = 90;
    targetTilt = 90;
    targetArmL = 90;
    targetArmR = 90;
    servoPan.write(90);
    servoTilt.write(90);
    servoArmL.write(90);
    servoArmR.write(90);
  }
  else if (cmd == "SYNC") {
    Serial.println("ACK:TARS_MK4");
  }
}

void executeGesture(String g) {
  if (g == "SWIPE_LEFT") {
    // Left mechanical arm sweeps across to indicate left slide change
    servoArmL.write(145);
    servoArmR.write(90);
    delay(220);
    servoArmL.write(45);
    delay(200);
    targetArmL = 90;
  }
  else if (g == "SWIPE_RIGHT") {
    // Right mechanical arm sweeps across to indicate right slide change
    servoArmR.write(35);
    servoArmL.write(90);
    delay(220);
    servoArmR.write(135);
    delay(200);
    targetArmR = 90;
  }
  else if (g == "SWIPE_UP") {
    // Celebratory raise both arms
    servoArmL.write(150);
    servoArmR.write(30);
    delay(400);
    targetArmL = 90;
    targetArmR = 90;
  }
  else if (g == "SWIPE_DOWN") {
    // Lower arms to resting side pose
    servoArmL.write(45);
    servoArmR.write(135);
    delay(400);
    targetArmL = 90;
    targetArmR = 90;
  }
  else if (g == "WAVE") {
    // Friendly wave with right arm
    for (int w = 0; w < 3; w++) {
      servoArmR.write(140);
      delay(180);
      servoArmR.write(70);
      delay(180);
    }
    targetArmR = 90;
  }
  else if (g == "POINT") {
    // Point arm forward towards target
    servoArmR.write(160);
    delay(500);
    targetArmR = 90;
  }
}
