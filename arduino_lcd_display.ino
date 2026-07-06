/*
  arduino_lcd_display.ino

  16x2 HD44780 LCD renderer for the Claude Usage Monitor.

  Versioned serial protocol from the Raspberry Pi:
    V1,STATE,MODE,FIVE_PERCENT,FIVE_LEFT,WEEK_PERCENT,WEEK_LEFT,TIME,DATE\n
  Example:
    V1,OK,AUTO,42,2h13m,18,4d12h,13:42:09,Mon 6 Jul

  STATE: OK, WARN, CACHE, ERR, OFF
  MODE:  AUTO, FIVE, WEEK, CLOCK, STATUS

  The Uno only parses and renders. Networking, OAuth, timezones, history,
  and usage calculations stay on the Pi. This sketch deliberately uses
  fixed-size buffers and no Arduino String objects.
*/

#include <LiquidCrystal.h>
#include <avr/wdt.h>

LiquidCrystal lcd(7, 8, 9, 10, 11, 12);

byte barLevel1[8] = {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10};
byte barLevel2[8] = {0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18};
byte barLevel3[8] = {0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C};
byte barLevel4[8] = {0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E};
byte barLevel5[8] = {0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F};

#define LINE_BUF_SIZE 96
char lineBuf[LINE_BUF_SIZE];
uint8_t lineLen = 0;

enum State { ST_WAITING, ST_OK, ST_WARN, ST_CACHE, ST_ERR, ST_OFF };
enum Mode { MODE_AUTO, MODE_FIVE, MODE_WEEK, MODE_CLOCK, MODE_STATUS };
enum Screen { SCREEN_FIVE, SCREEN_WEEK, SCREEN_CLOCK, SCREEN_STATUS };

State currentState = ST_WAITING;
Mode currentMode = MODE_AUTO;
Screen currentScreen = SCREEN_STATUS;

int fivePercent = 0;
int weekPercent = 0;
char fiveLeft[9] = "--";
char weekLeft[9] = "--";
char clockTime[9] = "--:--:--";
char clockDate[11] = "--";

char lastLine0[17] = "";
char lastLine1[17] = "";
Screen lastScreen = SCREEN_STATUS;
State lastState = ST_WAITING;
bool displaySleeping = false;
bool forceDraw = true;

unsigned long lastRotate = 0;
const unsigned long ROTATE_MS = 4000;

bool blinkOn = false;
unsigned long lastBlinkToggle = 0;
const unsigned long BLINK_MS = 500;

void setup() {
  Serial.begin(115200);
  lcd.begin(16, 2);
  lcd.createChar(1, barLevel1);
  lcd.createChar(2, barLevel2);
  lcd.createChar(3, barLevel3);
  lcd.createChar(4, barLevel4);
  lcd.createChar(5, barLevel5);
  drawText("Waiting for", "Pi data...");
  wdt_enable(WDTO_4S);
}

void loop() {
  wdt_reset();
  readSerial();
  updateAutoScreen();
  render();
  updateBlink();
}

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      parseLine(lineBuf);
      lineLen = 0;
    } else if (c != '\r') {
      if (lineLen < LINE_BUF_SIZE - 1) {
        lineBuf[lineLen++] = c;
      } else {
        lineLen = 0;
      }
    }
  }
}

void parseLine(char *line) {
  char *version = strtok(line, ",");
  if (!version || strcmp(version, "V1") != 0) return;

  char *stateTok = strtok(NULL, ",");
  char *modeTok = strtok(NULL, ",");
  char *fiveTok = strtok(NULL, ",");
  char *fiveLeftTok = strtok(NULL, ",");
  char *weekTok = strtok(NULL, ",");
  char *weekLeftTok = strtok(NULL, ",");
  char *timeTok = strtok(NULL, ",");
  char *dateTok = strtok(NULL, "");

  if (!stateTok || !modeTok || !fiveTok || !fiveLeftTok || !weekTok || !weekLeftTok || !timeTok || !dateTok) return;

  currentState = parseState(stateTok);
  currentMode = parseMode(modeTok);
  fivePercent = constrain(atoi(fiveTok), 0, 100);
  weekPercent = constrain(atoi(weekTok), 0, 100);
  copyField(fiveLeft, sizeof(fiveLeft), fiveLeftTok);
  copyField(weekLeft, sizeof(weekLeft), weekLeftTok);
  copyField(clockTime, sizeof(clockTime), timeTok);
  copyField(clockDate, sizeof(clockDate), dateTok);

  if (displaySleeping && currentState != ST_OFF) {
    lcd.display();
    displaySleeping = false;
  }

  forceDraw = true;
}

State parseState(const char *token) {
  if (strcmp(token, "WARN") == 0) return ST_WARN;
  if (strcmp(token, "CACHE") == 0) return ST_CACHE;
  if (strcmp(token, "ERR") == 0) return ST_ERR;
  if (strcmp(token, "OFF") == 0) return ST_OFF;
  return ST_OK;
}

Mode parseMode(const char *token) {
  if (strcmp(token, "FIVE") == 0) return MODE_FIVE;
  if (strcmp(token, "WEEK") == 0) return MODE_WEEK;
  if (strcmp(token, "CLOCK") == 0) return MODE_CLOCK;
  if (strcmp(token, "STATUS") == 0) return MODE_STATUS;
  return MODE_AUTO;
}

void copyField(char *dest, size_t destSize, const char *src) {
  strncpy(dest, src, destSize - 1);
  dest[destSize - 1] = '\0';
}

void updateAutoScreen() {
  if (currentState == ST_OFF) return;

  if (currentMode == MODE_FIVE) currentScreen = SCREEN_FIVE;
  else if (currentMode == MODE_WEEK) currentScreen = SCREEN_WEEK;
  else if (currentMode == MODE_CLOCK) currentScreen = SCREEN_CLOCK;
  else if (currentMode == MODE_STATUS) currentScreen = SCREEN_STATUS;
  else {
    if (currentState == ST_CACHE || currentState == ST_ERR) {
      currentScreen = SCREEN_STATUS;
      return;
    }
    unsigned long now = millis();
    if (now - lastRotate >= ROTATE_MS) {
      lastRotate = now;
      if (currentScreen == SCREEN_FIVE) currentScreen = SCREEN_WEEK;
      else if (currentScreen == SCREEN_WEEK) currentScreen = SCREEN_CLOCK;
      else currentScreen = SCREEN_FIVE;
      forceDraw = true;
    }
  }
}

void render() {
  if (currentState == ST_OFF) {
    if (!displaySleeping) {
      lcd.noDisplay();
      displaySleeping = true;
    }
    return;
  }

  if (currentScreen == SCREEN_FIVE) drawFive();
  else if (currentScreen == SCREEN_WEEK) drawWeek();
  else if (currentScreen == SCREEN_CLOCK) drawClock();
  else drawStatus();

  lastScreen = currentScreen;
  lastState = currentState;
  forceDraw = false;
}

void drawFive() {
  char line0[17];
  char line1[17];
  snprintf(line0, sizeof(line0), "5H %3d%%", fivePercent);
  snprintf(line1, sizeof(line1), "%-8s left", fiveLeft);
  drawText(line0, line1);
  drawBar(8, 0, fivePercent, 7);
}

void drawWeek() {
  char line0[17];
  char line1[17];
  snprintf(line0, sizeof(line0), "Week %3d%%", weekPercent);
  snprintf(line1, sizeof(line1), "%-8s left", weekLeft);
  drawText(line0, line1);
}

void drawClock() {
  char line0[17];
  char line1[17];
  snprintf(line0, sizeof(line0), "%-16s", clockTime);
  snprintf(line1, sizeof(line1), "%-16s", clockDate);
  drawText(line0, line1);
}

void drawStatus() {
  char line0[17];
  char line1[17];
  if (currentState == ST_ERR) {
    snprintf(line0, sizeof(line0), "API Offline");
    snprintf(line1, sizeof(line1), "Check network");
  } else if (currentState == ST_CACHE) {
    snprintf(line0, sizeof(line0), "Using Cache");
    snprintf(line1, sizeof(line1), "Last good data");
  } else if (currentState == ST_WARN) {
    snprintf(line0, sizeof(line0), "Usage High");
    snprintf(line1, sizeof(line1), "5H %3d%%", fivePercent);
  } else {
    snprintf(line0, sizeof(line0), "API Online");
    snprintf(line1, sizeof(line1), "LCD ready");
  }
  drawText(line0, line1);
}

void drawText(const char *line0, const char *line1) {
  char padded0[17];
  char padded1[17];
  snprintf(padded0, sizeof(padded0), "%-16.16s", line0);
  snprintf(padded1, sizeof(padded1), "%-16.16s", line1);

  bool screenChanged = forceDraw || currentScreen != lastScreen || currentState != lastState;
  if (screenChanged || strcmp(padded0, lastLine0) != 0) {
    lcd.setCursor(0, 0);
    lcd.print(padded0);
    copyField(lastLine0, sizeof(lastLine0), padded0);
  }
  if (screenChanged || strcmp(padded1, lastLine1) != 0) {
    lcd.setCursor(0, 1);
    lcd.print(padded1);
    copyField(lastLine1, sizeof(lastLine1), padded1);
  }
}

void drawBar(int col, int row, int percent, int widthCells) {
  int totalSub = widthCells * 5;
  int filledSub = (int)(((long)percent * totalSub + 50) / 100);
  lcd.setCursor(col, row);
  for (int i = 0; i < widthCells; i++) {
    int cellFilled;
    if (filledSub >= 5) { cellFilled = 5; filledSub -= 5; }
    else if (filledSub > 0) { cellFilled = filledSub; filledSub = 0; }
    else cellFilled = 0;

    if (cellFilled == 0) lcd.write(' ');
    else lcd.write((byte)cellFilled);
  }
}

void updateBlink() {
  if (currentState != ST_WARN || currentScreen != SCREEN_FIVE) return;

  unsigned long now = millis();
  if (now - lastBlinkToggle < BLINK_MS) return;
  lastBlinkToggle = now;
  blinkOn = !blinkOn;
  lcd.setCursor(15, 0);
  lcd.write(blinkOn ? '!' : ' ');
}
