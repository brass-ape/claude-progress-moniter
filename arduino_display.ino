/*
  arduino_lcd_display.ino

  Renders Claude 5-hour usage on a 16x2 HD44780 LCD (4-bit mode).
  Receives a single compact line over USB serial from the Pi:

      STATE,PERCENT,RESET\n

  e.g.  OK,37,16:59   WARN,83,16:59   STALE,37,16:59   OFF,0,--:--

  Deliberately uses fixed-size char buffers everywhere -- no Arduino
  String class -- to avoid heap fragmentation on the Uno's 2KB of
  RAM over long uptimes. A watchdog timer is also enabled as a
  backstop: if loop() ever stalls for 4s straight (this bug or any
  other), the chip resets itself instead of needing a physical
  unplug.

  Wiring:
    RS -> 7   EN -> 8   D4 -> 9   D5 -> 10   D6 -> 11   D7 -> 12
    RW -> GND (write-only)
*/

#include <LiquidCrystal.h>
#include <avr/wdt.h>

LiquidCrystal lcd(7, 8, 9, 10, 11, 12);

// --- Custom characters for a smooth progress bar ---
// Level N has N of 5 columns filled (level 5 = solid block).
byte barLevel1[8] = {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10};
byte barLevel2[8] = {0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18};
byte barLevel3[8] = {0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C};
byte barLevel4[8] = {0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E};
byte barLevel5[8] = {0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F};

const int BAR_COL = 8;
const int BAR_ROW = 0;
const int BAR_WIDTH = 7;        // 7 cells x 5 sub-units = 35 steps of resolution
const int INDICATOR_COL = 15;
const int INDICATOR_ROW = 0;

// --- Incoming line buffer (fixed size, no String) ---
#define LINE_BUF_SIZE 40
char lineBuf[LINE_BUF_SIZE];
uint8_t lineLen = 0;

enum State { ST_WAITING, ST_OK, ST_WARN, ST_STALE, ST_OFF };

// Last values received from the Pi
int currentPercent = -1;
char currentReset[8] = "--:--";
State currentState = ST_WAITING;

// Last values actually drawn to the LCD (for change detection)
State lastDrawnState = ST_WAITING;
int lastDrawnPercent = -2;
char lastDrawnReset[8] = "";

bool blinkOn = false;
unsigned long lastBlinkToggle = 0;
const unsigned long BLINK_INTERVAL_MS = 500;

void setup() {
  Serial.begin(115200);

  lcd.begin(16, 2);
  lcd.createChar(1, barLevel1);
  lcd.createChar(2, barLevel2);
  lcd.createChar(3, barLevel3);
  lcd.createChar(4, barLevel4);
  lcd.createChar(5, barLevel5);

  lcd.print("Waiting for");
  lcd.setCursor(0, 1);
  lcd.print("Pi data...");

  wdt_enable(WDTO_4S); // reset us automatically if loop() ever stalls
}

void loop() {
  wdt_reset(); // pet the watchdog every iteration
  readSerial();
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
        lineLen = 0; // overflow guard: discard the malformed line
      }
    }
  }
}

void parseLine(char *line) {
  // Expected format: STATE,PERCENT,RESET   e.g.  WARN,83,16:59
  char *comma1 = strchr(line, ',');
  if (!comma1) return;
  *comma1 = '\0';
  char *stateTok = line;

  char *rest = comma1 + 1;
  char *comma2 = strchr(rest, ',');
  if (!comma2) return;
  *comma2 = '\0';
  char *percentTok = rest;
  char *resetTok = comma2 + 1;

  currentPercent = atoi(percentTok);
  strncpy(currentReset, resetTok, sizeof(currentReset) - 1);
  currentReset[sizeof(currentReset) - 1] = '\0';

  if (strcmp(stateTok, "WARN") == 0) currentState = ST_WARN;
  else if (strcmp(stateTok, "STALE") == 0) currentState = ST_STALE;
  else if (stateTok[0] == 'O' && stateTok[1] == 'F') currentState = ST_OFF; // "OFF"
  else currentState = ST_OK;

  redrawIfChanged();
}

void redrawIfChanged() {
  bool dataChanged = (currentPercent != lastDrawnPercent) ||
                      (strcmp(currentReset, lastDrawnReset) != 0) ||
                      (currentState != lastDrawnState);
  if (!dataChanged) return;

  if (currentState == ST_OFF) {
    lcd.noDisplay();
    lastDrawnPercent = currentPercent;
    strncpy(lastDrawnReset, currentReset, sizeof(lastDrawnReset));
    lastDrawnState = currentState;
    return;
  }

  if (lastDrawnState == ST_OFF) {
    lcd.display(); // waking back up from OFF
  }

  // Line 1: "5H XX% " + smooth bar
  char percentField[5];
  snprintf(percentField, sizeof(percentField), "%3d", currentPercent);

  lcd.setCursor(0, 0);
  lcd.print("5H ");
  lcd.print(percentField);
  lcd.print("% ");
  drawBar(BAR_COL, BAR_ROW, currentPercent, BAR_WIDTH);

  // Line 2: "Reset HH:MM" (+ " OLD" if stale), padded/truncated to 16 chars
  char line2[17];
  if (currentState == ST_STALE) {
    snprintf(line2, sizeof(line2), "Reset %-5s OLD", currentReset);
  } else {
    snprintf(line2, sizeof(line2), "Reset %-9s", currentReset);
  }
  char padded[17];
  snprintf(padded, sizeof(padded), "%-16.16s", line2);

  lcd.setCursor(0, 1);
  lcd.print(padded);

  // Indicator column: WARN is owned by updateBlink() from here on,
  // everything else just needs a one-off draw.
  if (currentState != ST_WARN) {
    lcd.setCursor(INDICATOR_COL, INDICATOR_ROW);
    lcd.write(currentState == ST_STALE ? '?' : ' ');
  }

  lastDrawnPercent = currentPercent;
  strncpy(lastDrawnReset, currentReset, sizeof(lastDrawnReset));
  lastDrawnReset[sizeof(lastDrawnReset) - 1] = '\0';
  lastDrawnState = currentState;
}

void drawBar(int col, int row, int percent, int widthCells) {
  percent = constrain(percent, 0, 100);
  int totalSub = widthCells * 5;
  int filledSub = (int)(((long)percent * totalSub + 50) / 100); // rounded

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
  if (currentState != ST_WARN) return; // only the warning state blinks

  unsigned long now = millis();
  if (now - lastBlinkToggle < BLINK_INTERVAL_MS) return;
  lastBlinkToggle = now;
  blinkOn = !blinkOn;

  lcd.setCursor(INDICATOR_COL, INDICATOR_ROW);
  lcd.write(blinkOn ? '!' : ' ');
}