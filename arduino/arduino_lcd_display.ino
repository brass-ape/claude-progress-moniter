/*
  arduino_lcd_display.ino

  16x2 HD44780 LCD renderer for the Claude Usage Monitor.

  Versioned serial protocol from the Raspberry Pi:
    V1,STATE,MODE,FIVE_PERCENT,FIVE_LEFT,WEEK_PERCENT,WEEK_LEFT,TIME,DATE\n
    S1,LINE0,LINE1\n
  Example:
    V1,OK,AUTO,42,2h13m,18,4d12h,13:42:09,Mon 6 Jul
    S1,CPU,42%

  STATE: OK, WARN, CACHE, ERR, OFF
  MODE:  AUTO, FIVE, WEEK, CLOCK, STATUS, SYS

  S1 carries the two pre-formatted text lines for the SYS (system info)
  screen — CPU/RAM/GPU/Disk/Network readings. The Pi decides which metric
  is currently due and formats units/GB/MB-per-second math; the Uno just
  prints whatever two lines it was last sent, exactly like it already does
  for the CLOCK screen's time/date strings.

  Layout note: position (15, 1) — bottom-right corner — is reserved for the
  status indicator glyph. All other content is kept within columns 0–14 on
  row 1 so the indicator is never clobbered by normal content writes.

  Status indicator:
    OK    -> custom tick  (char slot 6)
    WARN  -> '!' blinking
    CACHE -> '*'
    ERR   -> custom cross (char slot 7)

  The STATUS screen is selectable manually but no longer appears in the AUTO
  rotation; the corner indicator carries that information instead.

  The Uno only parses and renders. Networking, OAuth, timezones, history,
  and usage calculations stay on the Pi. This sketch deliberately uses
  fixed-size buffers and no Arduino String objects.
*/

#include <LiquidCrystal.h>
#include <avr/wdt.h>

LiquidCrystal lcd(7, 8, 9, 10, 11, 12);

// Bar-fill levels (slots 1-5, unchanged)
byte barLevel1[8] = {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x10};
byte barLevel2[8] = {0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18, 0x18};
byte barLevel3[8] = {0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C, 0x1C};
byte barLevel4[8] = {0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E, 0x1E};
byte barLevel5[8] = {0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F, 0x1F};

// Tick checkmark - slot 6
byte glyphTick[8]  = {0x00, 0x00, 0x01, 0x02, 0x14, 0x08, 0x00, 0x00};

// Cross X - slot 7
byte glyphCross[8] = {0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x00, 0x00};

#define LINE_BUF_SIZE 96
char lineBuf[LINE_BUF_SIZE];
uint8_t lineLen = 0;

enum State  { ST_WAITING, ST_OK, ST_WARN, ST_CACHE, ST_ERR, ST_OFF };
enum Mode   { MODE_AUTO, MODE_FIVE, MODE_WEEK, MODE_CLOCK, MODE_STATUS, MODE_SYS };
enum Screen { SCREEN_FIVE, SCREEN_WEEK, SCREEN_CLOCK, SCREEN_STATUS, SCREEN_SYS };

State  currentState  = ST_WAITING;
Mode   currentMode   = MODE_AUTO;
Screen currentScreen = SCREEN_FIVE;

int  fivePercent  = 0;
int  weekPercent  = 0;
char fiveLeft[9]  = "--";
char weekLeft[9]  = "--";
char clockTime[9] = "--:--:--";
char clockDate[11] = "--";
char sysLine0[17]  = "System";
char sysLine1[16]  = "";

char lastLine0[17]     = "";
char lastLine1[17]     = "";
Screen lastScreen      = SCREEN_FIVE;
State  lastState       = ST_WAITING;
bool   displaySleeping = false;
bool   forceDraw       = true;

unsigned long lastRotate      = 0;
const unsigned long ROTATE_MS = 4000;

bool blinkOn = false;
unsigned long lastBlinkToggle = 0;
const unsigned long BLINK_MS  = 500;

// ---------------------------------------------------------------- setup / loop

void setup() {
  Serial.begin(115200);
  lcd.begin(16, 2);
  lcd.createChar(1, barLevel1);
  lcd.createChar(2, barLevel2);
  lcd.createChar(3, barLevel3);
  lcd.createChar(4, barLevel4);
  lcd.createChar(5, barLevel5);
  lcd.createChar(6, glyphTick);
  lcd.createChar(7, glyphCross);
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

// ---------------------------------------------------------------- serial input

void readSerial() {
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      lineBuf[lineLen] = '\0';
      dispatchLine(lineBuf);
      lineLen = 0;
    } else if (c != '\r') {
      if (lineLen < LINE_BUF_SIZE - 1) lineBuf[lineLen++] = c;
      else lineLen = 0;
    }
  }
}

void dispatchLine(char *line) {
  if (line[0] == 'V' && line[1] == '1') parseLine(line);
  else if (line[0] == 'S' && line[1] == '1') parseSysLine(line);
}

void parseSysLine(char *line) {
  char *version   = strtok(line, ",");
  if (!version || strcmp(version, "S1") != 0) return;

  char *line0Tok = strtok(NULL, ",");
  char *line1Tok = strtok(NULL, "");
  if (!line0Tok || !line1Tok) return;

  copyField(sysLine0, sizeof(sysLine0), line0Tok);
  copyField(sysLine1, sizeof(sysLine1), line1Tok);
  forceDraw = true;
}

void parseLine(char *line) {
  char *version     = strtok(line, ",");
  if (!version || strcmp(version, "V1") != 0) return;

  char *stateTok    = strtok(NULL, ",");
  char *modeTok     = strtok(NULL, ",");
  char *fiveTok     = strtok(NULL, ",");
  char *fiveLeftTok = strtok(NULL, ",");
  char *weekTok     = strtok(NULL, ",");
  char *weekLeftTok = strtok(NULL, ",");
  char *timeTok     = strtok(NULL, ",");
  char *dateTok     = strtok(NULL, "");

  if (!stateTok || !modeTok || !fiveTok || !fiveLeftTok ||
      !weekTok  || !weekLeftTok || !timeTok || !dateTok) return;

  currentState = parseState(stateTok);
  currentMode  = parseMode(modeTok);
  fivePercent  = constrain(atoi(fiveTok),  0, 100);
  weekPercent  = constrain(atoi(weekTok),  0, 100);
  copyField(fiveLeft,  sizeof(fiveLeft),  fiveLeftTok);
  copyField(weekLeft,  sizeof(weekLeft),  weekLeftTok);
  copyField(clockTime, sizeof(clockTime), timeTok);
  copyField(clockDate, sizeof(clockDate), dateTok);

  if (displaySleeping && currentState != ST_OFF) {
    lcd.display();
    displaySleeping = false;
  }

  forceDraw = true;
}

State parseState(const char *token) {
  if (strcmp(token, "WARN")  == 0) return ST_WARN;
  if (strcmp(token, "CACHE") == 0) return ST_CACHE;
  if (strcmp(token, "ERR")   == 0) return ST_ERR;
  if (strcmp(token, "OFF")   == 0) return ST_OFF;
  return ST_OK;
}

Mode parseMode(const char *token) {
  if (strcmp(token, "FIVE")   == 0) return MODE_FIVE;
  if (strcmp(token, "WEEK")   == 0) return MODE_WEEK;
  if (strcmp(token, "CLOCK")  == 0) return MODE_CLOCK;
  if (strcmp(token, "STATUS") == 0) return MODE_STATUS;
  if (strcmp(token, "SYS")    == 0) return MODE_SYS;
  return MODE_AUTO;
}

void copyField(char *dest, size_t destSize, const char *src) {
  strncpy(dest, src, destSize - 1);
  dest[destSize - 1] = '\0';
}

// -------------------------------------------------------------- screen logic

void updateAutoScreen() {
  if (currentState == ST_OFF) return;

  if      (currentMode == MODE_FIVE)   currentScreen = SCREEN_FIVE;
  else if (currentMode == MODE_WEEK)   currentScreen = SCREEN_WEEK;
  else if (currentMode == MODE_CLOCK)  currentScreen = SCREEN_CLOCK;
  else if (currentMode == MODE_STATUS) currentScreen = SCREEN_STATUS;
  else if (currentMode == MODE_SYS)    currentScreen = SCREEN_SYS;
  else {
    // AUTO: rotate FIVE -> WEEK -> CLOCK -> SYS -> FIVE regardless of state.
    // The corner indicator shows status at all times so STATUS screen
    // is no longer forced on errors — it remains manually selectable only.
    unsigned long now = millis();
    if (now - lastRotate >= ROTATE_MS) {
      lastRotate = now;
      if      (currentScreen == SCREEN_FIVE)  currentScreen = SCREEN_WEEK;
      else if (currentScreen == SCREEN_WEEK)  currentScreen = SCREEN_CLOCK;
      else if (currentScreen == SCREEN_CLOCK) currentScreen = SCREEN_SYS;
      else                                     currentScreen = SCREEN_FIVE;
      forceDraw = true;
    }
  }
}

// --------------------------------------------------------------- rendering

void render() {
  if (currentState == ST_OFF) {
    if (!displaySleeping) {
      lcd.noDisplay();
      displaySleeping = true;
    }
    return;
  }

  if      (currentScreen == SCREEN_FIVE)   drawFive();
  else if (currentScreen == SCREEN_WEEK)   drawWeek();
  else if (currentScreen == SCREEN_CLOCK)  drawClock();
  else if (currentScreen == SCREEN_SYS)    drawSys();
  else                                      drawStatus();

  // Draw the corner indicator on all screens except STATUS (which fills col 15)
  if (currentScreen != SCREEN_STATUS) drawIndicator();

  lastScreen = currentScreen;
  lastState  = currentState;
  forceDraw  = false;
}

// -------------------------------------------------- screen-specific renderers

void drawFive() {
  char line0[17], line1[17];
  snprintf(line0, sizeof(line0), "5H  %3d%%", fivePercent);
  snprintf(line1, sizeof(line1), "%-7s left", fiveLeft);
  drawText(line0, line1);
  drawBar(9, 0, fivePercent, 6);
}

void drawWeek() {
  char line0[17], line1[17];
  snprintf(line0, sizeof(line0), "Wk  %3d%%", weekPercent);
  snprintf(line1, sizeof(line1), "%-7s left", weekLeft);
  drawText(line0, line1);
  drawBar(9, 0, weekPercent, 6);
}

void drawClock() {
  char line0[17], line1[17];
  snprintf(line0, sizeof(line0), "%-16s", clockTime);
  snprintf(line1, sizeof(line1), "%-15s", clockDate);  // 15 chars; col 15 = indicator
  drawText(line0, line1);
}

void drawSys() {
  drawText(sysLine0, sysLine1);
}

void drawStatus() {
  char line0[17], line1[17];
  if (currentState == ST_ERR) {
    snprintf(line0, sizeof(line0), "API Offline");
    snprintf(line1, sizeof(line1), "Check network   ");
  } else if (currentState == ST_CACHE) {
    snprintf(line0, sizeof(line0), "Using Cache");
    snprintf(line1, sizeof(line1), "Last good data  ");
  } else if (currentState == ST_WARN) {
    snprintf(line0, sizeof(line0), "Usage High");
    snprintf(line1, sizeof(line1), "5H%3d%% Wk%3d%%  ", fivePercent, weekPercent);
  } else {
    snprintf(line0, sizeof(line0), "API Online");
    snprintf(line1, sizeof(line1), "All good        ");
  }
  // STATUS uses full 16 chars on both rows; clears col 15 itself
  drawTextFull(line0, line1);
}

// ------------------------------------------------------------ status indicator

void drawIndicator() {
  lcd.setCursor(15, 1);
  switch (currentState) {
    case ST_OK:    lcd.write((byte)6); break;  // tick
    case ST_WARN:  lcd.write('!');     break;  // blinking handled in updateBlink()
    case ST_CACHE: lcd.write('*');     break;
    case ST_ERR:   lcd.write((byte)7); break;  // cross
    default:       lcd.write(' ');     break;
  }
}

// ---------------------------------------------------------------- text helpers

/*
 * drawText — row 0 full width, row 1 limited to 15 chars so col 15 is free
 * for the corner indicator. Only rewrites a row when its content changes.
 */
void drawText(const char *line0, const char *line1) {
  char padded0[17], padded1[16];  // padded1 is 15 chars + NUL
  snprintf(padded0, sizeof(padded0), "%-16.16s", line0);
  snprintf(padded1, sizeof(padded1), "%-15.15s", line1);

  bool changed = forceDraw || currentScreen != lastScreen || currentState != lastState;

  if (changed || strcmp(padded0, lastLine0) != 0) {
    lcd.setCursor(0, 0);
    lcd.print(padded0);
    copyField(lastLine0, sizeof(lastLine0), padded0);
  }
  if (changed || strcmp(padded1, lastLine1) != 0) {
    lcd.setCursor(0, 1);
    lcd.print(padded1);
    copyField(lastLine1, sizeof(lastLine1), padded1);
  }
}

/*
 * drawTextFull — writes full 16 chars on both rows. Used by drawStatus()
 * which manages col 15 of row 1 itself via its own padding.
 */
void drawTextFull(const char *line0, const char *line1) {
  char padded0[17], padded1[17];
  snprintf(padded0, sizeof(padded0), "%-16.16s", line0);
  snprintf(padded1, sizeof(padded1), "%-16.16s", line1);

  bool changed = forceDraw || currentScreen != lastScreen || currentState != lastState;

  if (changed || strcmp(padded0, lastLine0) != 0) {
    lcd.setCursor(0, 0);
    lcd.print(padded0);
    copyField(lastLine0, sizeof(lastLine0), padded0);
  }
  if (changed || strcmp(padded1, lastLine1) != 0) {
    lcd.setCursor(0, 1);
    lcd.print(padded1);
    copyField(lastLine1, sizeof(lastLine1), padded1);
  }
}

// -------------------------------------------------------------- bar renderer

void drawBar(int col, int row, int percent, int widthCells) {
  int totalSub  = widthCells * 5;
  int filledSub = (int)(((long)percent * totalSub + 50) / 100);
  lcd.setCursor(col, row);
  for (int i = 0; i < widthCells; i++) {
    int cellFilled;
    if      (filledSub >= 5) { cellFilled = 5; filledSub -= 5; }
    else if (filledSub > 0)  { cellFilled = filledSub; filledSub = 0; }
    else                       cellFilled = 0;

    if (cellFilled == 0) lcd.write(' ');
    else                 lcd.write((byte)cellFilled);
  }
}

// --------------------------------------------------------- indicator blink

/*
 * Blink the '!' at position (15, 1) when state is WARN and the indicator
 * is visible (i.e. not on the STATUS screen).
 */
void updateBlink() {
  if (currentState != ST_WARN || currentScreen == SCREEN_STATUS) return;

  unsigned long now = millis();
  if (now - lastBlinkToggle < BLINK_MS) return;
  lastBlinkToggle = now;
  blinkOn = !blinkOn;
  lcd.setCursor(15, 1);
  lcd.write(blinkOn ? '!' : ' ');
}
