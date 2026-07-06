const $ = (id) => document.getElementById(id);
const modes = ["AUTO", "FIVE", "WEEK", "CLOCK", "STATUS"];
let currentStatus = null;
let pollInterval = null;

// Matches warning_threshold default in scheduler.py
const WARN_THRESHOLD = 80;

function fmtPercent(value) {
  return Number.isFinite(value) ? `${value}%` : "--%";
}

function fmtDuration(seconds) {
  seconds = Math.max(0, Number(seconds) || 0);
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days) return `${days}d ${hours}h`;
  if (hours) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function fmtDate(value) {
  if (!value) return "--";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "--";
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

async function post(path, body) {
  const options = { method: "POST" };
  if (body) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) throw new Error(`${path} failed`);
  return response.json();
}

async function refresh() {
  try {
    const response = await fetch("/api/status", { cache: "no-store" });
    if (!response.ok) throw new Error("status request failed");
    render(await response.json());
  } catch (error) {
    $("error").textContent = error.message;
  }
}

function startPolling() {
  if (!pollInterval) pollInterval = setInterval(refresh, 5000);
}

function stopPolling() {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = null;
  }
}

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopPolling();
  } else {
    refresh();
    startPolling();
  }
});

function setBarLevel(barEl, percent) {
  barEl.style.width = `${Math.max(0, Math.min(100, percent || 0))}%`;
  barEl.classList.toggle("warn", percent >= WARN_THRESHOLD && percent < 95);
  barEl.classList.toggle("danger", percent >= 95);
}

function render(data) {
  currentStatus = data;
  const usage = data.usage || {};
  const five = Number(usage.five_hour_percent);
  const week = Number(usage.weekly_percent);

  // Clear any previous error on a successful render
  $("error").textContent = data.rate_limit_seconds > 0
    ? `Rate limited — next fetch in ${fmtDuration(data.rate_limit_seconds)}`
    : "";

  $("summary").textContent = data.api_status === "ok"
    ? `5-hour ${fmtPercent(five)} · week ${fmtPercent(week)}`
    : `API ${data.api_status || "unknown"}`;

  $("fivePercent").textContent = fmtPercent(five);
  $("weekPercent").textContent = fmtPercent(week);
  setBarLevel($("fiveBar"), five);
  setBarLevel($("weekBar"), week);
  $("fiveLeft").textContent = `${usage.five_hour_remaining || "--"} left · resets ${usage.five_hour_reset_label || "--:--"}`;
  $("weekLeft").textContent = `${usage.weekly_remaining || "--"} left · resets ${usage.weekly_reset_label || "--:--"}`;

  $("lastRefresh").textContent = fmtDate(data.last_success);
  $("latency").textContent = usage.api_latency_ms ? `${usage.api_latency_ms}ms` : "--";
  $("uptime").textContent = fmtDuration(data.uptime_seconds);
  $("arduino").textContent = data.arduino_connected ? "connected" : "offline";
  $("oauth").textContent = data.oauth_status || "unknown";
  $("internet").textContent = data.internet_status || "unknown";
  $("lcdState").textContent = `${data.lcd_state || "--"} / ${data.display_mode || "AUTO"}`;
  $("trend").textContent = data.history?.trend || "--";
  $("peak").textContent = `Peak ${data.history?.peak_utilization ?? "--"}%`;
  $("average").textContent = `Avg ${data.history?.average_daily_usage ?? "--"}%`;

  $("powerButton").textContent = data.display_on ? "Turn off" : "Turn on";
  $("powerButton").classList.toggle("danger", data.display_on);

  document.querySelectorAll("#modeControls button").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === data.display_mode);
  });

  drawChart($("chart24"), data.history?.points_24h || []);
  drawChart($("chart7"), data.history?.points_7d || []);
}

function drawLine(ctx, points, key, color, width, height) {
  if (points.length < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (index / (points.length - 1)) * width;
    const y = height - 12 - (Math.min(100, Math.max(0, Number(point[key]))) / 100) * (height - 24);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawChart(canvas, points) {
  // Sync pixel width to CSS width for crisp rendering on all screen densities
  const cssWidth = canvas.offsetWidth || 640;
  if (canvas.width !== cssWidth) canvas.width = cssWidth;

  const ctx = canvas.getContext("2d");
  const { width, height } = canvas;

  ctx.clearRect(0, 0, width, height);

  // Grid lines
  ctx.strokeStyle = "#343a40";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const y = 12 + i * ((height - 24) / 4);
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }

  if (points.length < 2) return;

  // Weekly percent line (dimmer, drawn first so 5-hour sits on top)
  drawLine(ctx, points, "weekly_percent", "#3d6b7a", 2, width, height);
  // 5-hour percent line
  drawLine(ctx, points, "five_hour_percent", "#5bb7d7", 2, width, height);
}

$("refreshButton").addEventListener("click", async () => {
  $("refreshButton").disabled = true;
  try { render(await post("/api/refresh")); }
  catch (err) { $("error").textContent = err.message; }
  finally { $("refreshButton").disabled = false; }
});

$("powerButton").addEventListener("click", async () => {
  const path = currentStatus?.display_on ? "/api/display/off" : "/api/display/on";
  try { render(await post(path)); }
  catch (err) { $("error").textContent = err.message; }
});

modes.forEach((mode) => {
  document.querySelector(`[data-mode="${mode}"]`).addEventListener("click", async () => {
    try { render(await post("/api/display/mode", { mode })); }
    catch (err) { $("error").textContent = err.message; }
  });
});

refresh();
startPolling();
