const $ = (id) => document.getElementById(id);
const modes = ["AUTO", "FIVE", "WEEK", "CLOCK", "STATUS"];
let currentStatus = null;

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
    $("error").textContent = "";
  } catch (error) {
    $("error").textContent = error.message;
  }
}

function render(data) {
  currentStatus = data;
  const usage = data.usage || {};
  const five = Number(usage.five_hour_percent);
  const week = Number(usage.weekly_percent);

  $("summary").textContent = data.api_status === "ok"
    ? `5-hour ${fmtPercent(five)} · week ${fmtPercent(week)}`
    : `API ${data.api_status || "unknown"}`;

  $("fivePercent").textContent = fmtPercent(five);
  $("weekPercent").textContent = fmtPercent(week);
  $("fiveBar").style.width = `${Math.max(0, Math.min(100, five || 0))}%`;
  $("weekBar").style.width = `${Math.max(0, Math.min(100, week || 0))}%`;
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

function drawChart(canvas, points) {
  const ctx = canvas.getContext("2d");
  const width = canvas.width;
  const height = canvas.height;
  ctx.clearRect(0, 0, width, height);
  ctx.strokeStyle = "#343a40";
  ctx.lineWidth = 1;
  for (let y = 0; y <= 4; y += 1) {
    const lineY = 12 + y * ((height - 24) / 4);
    ctx.beginPath();
    ctx.moveTo(0, lineY);
    ctx.lineTo(width, lineY);
    ctx.stroke();
  }
  if (points.length < 2) return;
  ctx.strokeStyle = "#5bb7d7";
  ctx.lineWidth = 3;
  ctx.beginPath();
  points.forEach((point, index) => {
    const x = (index / (points.length - 1)) * width;
    const y = height - 12 - (Number(point.five_hour_percent) / 100) * (height - 24);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

$("refreshButton").addEventListener("click", async () => {
  $("refreshButton").disabled = true;
  try { render(await post("/api/refresh")); }
  finally { $("refreshButton").disabled = false; }
});

$("powerButton").addEventListener("click", async () => {
  const path = currentStatus?.display_on ? "/api/display/off" : "/api/display/on";
  render(await post(path));
});

modes.forEach((mode) => {
  document.querySelector(`[data-mode="${mode}"]`).addEventListener("click", async () => {
    render(await post("/api/display/mode", { mode }));
  });
});

refresh();
setInterval(refresh, 5000);
