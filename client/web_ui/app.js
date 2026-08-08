const app = document.getElementById("app");
const DEFAULT_LOGO_CANDIDATES = [
  "../assets/secret-grounds-logo.png",
  "../../assets/secret-grounds-logo.png"
];

let currentState = {
  view: "lock",
  pc_name: "PC-01",
  shop_display_name: "Secret Grounds",
  headline: "Available soon.",
  instruction: "Please ask the cashier to begin your session.",
  connection_state: "offline",
  session_type: "Open Time",
  customer_name: "Guest",
  remaining_seconds: null,
  elapsed_seconds: 0,
  used_seconds: 0,
  login_message: "",
  is_login_processing: false,
  can_logout: false,
  countdown: null,
  accent: "#22D3EE",
  logo_url: ""
};
let lockDraft = { username: "", pin: "", pinVisible: false };
let displayClock = {
  timerId: null,
  view: "lock",
  mode: null,
  key: "",
  baseSeconds: null,
  baseStamp: 0
};

function icon(name, cls) {
  return `<svg class="${cls || ""}"><use href="#icon-${name}"></use></svg>`;
}

function emitAction(action, payload = {}) {
  if (window.pywebview && window.pywebview.api && window.pywebview.api.emitAction) {
    window.pywebview.api.emitAction(action, payload);
  }
}

window.SecretGroundsUI = {
  setState(nextState) {
    currentState = { ...currentState, ...nextState };
    syncDisplayClock();
    render();
  },
  handleLogoError(img) {
    useNextLogoCandidate(img);
  },
  focusLock() {
    const active = document.activeElement;
    if (active && (active.tagName === "INPUT" || active.tagName === "BUTTON")) return;
    const username = document.getElementById("username-input");
    if (username && !username.disabled) username.focus();
  }
};

function logoCandidates() {
  const configured = currentState.logo_url || window.SECRET_GROUNDS_LOGO_URL || "";
  return [configured, ...DEFAULT_LOGO_CANDIDATES].filter((value, index, values) => value && values.indexOf(value) === index);
}

function logoUrlAt(index) {
  return logoCandidates()[index] || "";
}

function logoImage(className = "") {
  const classAttr = className ? ` class="${className}"` : "";
  return `<img${classAttr} data-logo-index="0" src="${escapeHtml(logoUrlAt(0))}" alt="" onerror="window.SecretGroundsUI.handleLogoError(this)" />`;
}

function useNextLogoCandidate(img) {
  const nextIndex = Number(img.dataset.logoIndex || 0) + 1;
  const nextUrl = logoUrlAt(nextIndex);
  if (nextUrl) {
    img.dataset.logoIndex = String(nextIndex);
    img.src = nextUrl;
    return;
  }
  img.classList.add("logo-missing");
  img.removeAttribute("src");
}

function formatTimer(totalSeconds, elapsed = false) {
  if (totalSeconds === null || totalSeconds === undefined) return "--:--";
  const safe = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  if (elapsed) {
    return hours ? `${pad(hours)}:${pad(minutes)}:${pad(seconds)}` : `${minutes}:${pad(seconds)}`;
  }
  return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}`;
}

function authoritativeTimer() {
  if (currentState.view !== "overlay") return null;
  const isOpen = currentState.session_type === "Open Time";
  const seconds = isOpen ? currentState.elapsed_seconds : currentState.remaining_seconds;
  if (seconds === null || seconds === undefined) return null;
  return {
    mode: isOpen ? "elapsed" : "countdown",
    key: `${currentState.session_type || ""}|${currentState.customer_name || ""}|${currentState.pc_name || ""}`,
    seconds: Math.max(0, Math.floor(Number(seconds) || 0))
  };
}

function syncDisplayClock() {
  const timer = authoritativeTimer();
  if (!timer) {
    stopDisplayClock();
    displayClock.view = currentState.view;
    return;
  }
  displayClock.view = "overlay";
  displayClock.mode = timer.mode;
  displayClock.key = timer.key;
  displayClock.baseSeconds = timer.seconds;
  displayClock.baseStamp = performance.now();
  if (!displayClock.timerId) {
    displayClock.timerId = window.setInterval(updateDisplayedTimer, 1000);
  }
}

function stopDisplayClock() {
  if (displayClock.timerId) {
    window.clearInterval(displayClock.timerId);
    displayClock.timerId = null;
  }
  displayClock.mode = null;
  displayClock.key = "";
  displayClock.baseSeconds = null;
  displayClock.baseStamp = 0;
}

function displayedTimerSeconds() {
  if (displayClock.baseSeconds === null || displayClock.mode === null) {
    const isOpen = currentState.session_type === "Open Time";
    return isOpen ? currentState.elapsed_seconds : currentState.remaining_seconds;
  }
  const elapsed = Math.floor((performance.now() - displayClock.baseStamp) / 1000);
  if (displayClock.mode === "elapsed") return displayClock.baseSeconds + elapsed;
  return Math.max(0, displayClock.baseSeconds - elapsed);
}

function updateDisplayedTimer() {
  if (currentState.view !== "overlay") {
    stopDisplayClock();
    return;
  }
  const timer = document.querySelector(".overlay-timer");
  if (!timer) return;
  const isOpen = currentState.session_type === "Open Time";
  timer.textContent = formatTimer(displayedTimerSeconds(), isOpen);
}

function formatUsedTime(totalSeconds) {
  const safe = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(safe / 3600);
  const minutes = Math.floor((safe % 3600) / 60);
  const seconds = safe % 60;
  if (hours) return `${hours}h ${pad(minutes)}m`;
  if (minutes) return `${minutes} min ${seconds} sec`;
  return `${seconds} sec`;
}

function pad(value) {
  return String(value).padStart(2, "0");
}

function lockVolumetricRays() {
  return `
    <svg class="lock-rays" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="rayGradA" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#3B82F6" stop-opacity="0.32"/>
          <stop offset="100%" stop-color="#3B82F6" stop-opacity="0"/>
        </linearGradient>
        <linearGradient id="rayGradB" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="#22D3EE" stop-opacity="0.28"/>
          <stop offset="100%" stop-color="#22D3EE" stop-opacity="0"/>
        </linearGradient>
        <filter id="rayBlur" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="46"/>
        </filter>
      </defs>
      <polygon points="-120,-60 380,-60 760,880 220,880" fill="url(#rayGradA)" filter="url(#rayBlur)"/>
      <polygon points="1580,-60 2040,-60 1880,820 1360,820" fill="url(#rayGradB)" filter="url(#rayBlur)"/>
    </svg>`;
}

function lockFlowStreaks() {
  const pathA = "M -190,120 C 120,145 205,420 430,555 C 650,685 850,610 1045,500 C 1240,390 1400,475 1535,645 C 1690,840 1880,870 2110,875";
  const pathB = "M 1390,175 C 1510,300 1475,430 1360,520";
  return `
    <svg class="lock-flow" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="streakA" x1="-220" y1="150" x2="980" y2="500" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stop-color="#22D3EE" stop-opacity="0.78"/>
          <stop offset="18%" stop-color="#1AC9FF" stop-opacity="0.98"/>
          <stop offset="48%" stop-color="#168BFF" stop-opacity="0.55"/>
          <stop offset="100%" stop-color="#0A55CE" stop-opacity="0.12"/>
        </linearGradient>
        <linearGradient id="streakB" x1="2140" y1="940" x2="1000" y2="370" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stop-color="#168BFF" stop-opacity="0.15"/>
          <stop offset="45%" stop-color="#22D3EE" stop-opacity="0.5"/>
          <stop offset="100%" stop-color="#1266D8" stop-opacity="0"/>
        </linearGradient>
        <filter id="streakGlow" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="18"/>
        </filter>
        <filter id="streakCore" x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="2.2"/>
        </filter>
      </defs>
      <path d="${pathA}" transform="translate(0,-24)" fill="none" stroke="url(#streakA)" stroke-width="82" stroke-opacity="0.32" stroke-linecap="round" filter="url(#streakGlow)"/>
      <path d="${pathA}" fill="none" stroke="url(#streakA)" stroke-width="42" stroke-opacity="0.55" stroke-linecap="round" filter="url(#streakGlow)"/>
      <path d="${pathA}" transform="translate(0,34)" fill="none" stroke="url(#streakA)" stroke-width="16" stroke-opacity="0.35" stroke-linecap="round" filter="url(#streakGlow)"/>
      <path d="${pathA}" fill="none" stroke="url(#streakA)" stroke-width="3.5" stroke-linecap="round" filter="url(#streakCore)"/>
      <path d="${pathB}" fill="none" stroke="url(#streakB)" stroke-width="34" stroke-linecap="round" filter="url(#streakGlow)"/>
      <path d="${pathB}" fill="none" stroke="url(#streakB)" stroke-width="2" stroke-linecap="round" filter="url(#streakCore)"/>
    </svg>`;
}

function particleDots(count) {
  let html = "";
  const seeded = [];
  let seed = 42;
  for (let index = 0; index < count * 3; index += 1) {
    seed = (seed * 9301 + 49297) % 233280;
    seeded.push(seed / 233280);
  }
  for (let index = 0; index < count; index += 1) {
    const x = (seeded[index] * 100).toFixed(1);
    const y = (seeded[index + count] * 100).toFixed(1);
    const size = (0.8 + seeded[index + count * 2] * 1.8).toFixed(1);
    const opacity = (0.15 + seeded[index + count * 2] * 0.45).toFixed(2);
    html += `<i style="left:${x}%; top:${y}%; width:${size}px; height:${size}px; opacity:${opacity};"></i>`;
  }
  return html;
}

function renderLock() {
  const currentUsername = document.getElementById("username-input");
  const currentPin = document.getElementById("pin-input");
  if (currentUsername) lockDraft.username = currentUsername.value;
  if (currentPin) {
    lockDraft.pin = currentPin.value;
    lockDraft.pinVisible = currentPin.type === "text";
  }
  const previousFocus = document.activeElement?.id || "";
  const offline = currentState.connection_state !== "online";
  const disabled = currentState.is_login_processing ? "disabled" : "";
  const countdown = currentState.countdown
    ? `<div class="lock-countdown" style="color:${escapeHtml(currentState.accent || "#22D3EE")}">${escapeHtml(currentState.countdown)}</div>`
    : "";
  const loginMessage = currentState.login_message
    ? `<div class="lock-login-message">${escapeHtml(currentState.login_message)}</div>`
    : "";
  app.innerHTML = `
    <section class="lock-screen ${offline ? "is-offline" : ""}">
      <div class="lock-fog"></div>
      ${lockVolumetricRays()}
      ${lockFlowStreaks()}
      <div class="lock-branding-bloom"></div>
      <div class="lock-watermark">${logoImage()}</div>
      <div class="lock-particles">${particleDots(72)}</div>
      <div class="lock-vignette"></div>
      <button class="lock-gear" type="button" data-action="open_setup">${icon("gear")}</button>
      <div class="lock-content">
        ${logoImage("lock-logo")}
        <h1 class="lock-title">${escapeHtml((currentState.shop_display_name || "Secret Grounds").toUpperCase())}</h1>
        <p class="lock-headline">${escapeHtml(currentState.headline || "Available soon.")}</p>
        <p class="lock-instruction">${escapeHtml(currentState.instruction || "")}</p>
        ${countdown}
        <form class="login-card" id="login-form">
          <div class="login-title">MEMBER LOGIN</div>
          <div class="field-group">
            <label class="field-label" for="username-input">username</label>
            <div class="field">
              ${icon("user")}
              <input id="username-input" name="username" type="text" autocomplete="username" placeholder="enter username" ${disabled} />
            </div>
          </div>
          <div class="field-group">
            <label class="field-label" for="pin-input">pin</label>
            <div class="field">
              ${icon("lock")}
              <input id="pin-input" name="pin" type="password" autocomplete="off" placeholder="enter pin" ${disabled} />
              <button class="eye-toggle" id="eye-toggle" type="button" ${disabled}>${icon("eye")}</button>
            </div>
          </div>
          <button class="btn-gradient" type="submit" ${disabled}>${currentState.is_login_processing ? "LOGGING IN..." : "LOG IN"}</button>
          ${loginMessage}
        </form>
        <div class="lock-pc-row">${icon("computer")}<span>${escapeHtml(currentState.pc_name || "PC-01")}</span></div>
        <div class="lock-connection-warning">Waiting for cashier connection...</div>
      </div>
    </section>`;
  wireLock();
  const username = document.getElementById("username-input");
  const pin = document.getElementById("pin-input");
  const eye = document.getElementById("eye-toggle");
  if (username) username.value = lockDraft.username;
  if (pin) {
    pin.value = lockDraft.pin;
    pin.type = lockDraft.pinVisible ? "text" : "password";
  }
  if (eye && lockDraft.pinVisible) {
    eye.classList.add("revealed");
    eye.innerHTML = icon("eye-off");
  }
  if (!currentState.is_login_processing) {
    const previous = document.getElementById(previousFocus);
    if (previous) previous.focus();
  }
}

function renderOverlay() {
  document.body.dataset.webviewSurface = "overlay";
  const isOpen = currentState.session_type === "Open Time";
  const isMember = currentState.session_type === "Member Time";
  const primarySeconds = displayedTimerSeconds();
  const timerLabel = isOpen ? "Elapsed Time" : "Remaining Time";
  const identityName = isMember ? (currentState.customer_name || "").toLowerCase() : "Guest";
  const identityCaption = isMember ? "Member" : (isOpen ? "Open Time" : "Package");
  const connection = currentState.connection_state !== "online"
    ? `<div class="overlay-connection-warning">Reconnecting...</div>`
    : "";
  const used = !isOpen
    ? `<div class="overlay-stat-row">${icon("clock")}<div><div class="overlay-stat-caption">Used Time</div><div class="overlay-stat-value">${formatUsedTime(currentState.used_seconds)}</div></div></div>`
    : "";
  const logout = currentState.can_logout
    ? `<div class="overlay-footer-divider"></div><div class="overlay-actions"><button class="btn-outline" type="button" data-action="logout">Log Out</button></div>`
    : "";
  app.innerHTML = `
    <section class="web-overlay-root">
      <div class="overlay-card ${isOpen ? "is-open" : ""}">
        ${overlayHeader()}
        <div class="overlay-timer">${formatTimer(primarySeconds, isOpen)}</div>
        <div class="overlay-timer-caption">${timerLabel}</div>
        <div class="overlay-divider"></div>
        ${used}
        <div class="overlay-stat-row identity">
          ${icon("user")}
          <div><div class="overlay-identity-name ${isMember ? "" : "guest"}">${escapeHtml(identityName || "Guest")}</div><div class="overlay-identity-caption">${identityCaption}</div></div>
        </div>
        <div class="overlay-pc-row">${icon("computer")}<span>${escapeHtml(currentState.pc_name || "PC-01")}</span></div>
        ${connection}
        ${logout}
      </div>
    </section>`;
  wireActions();
  requestResize();
}

function overlayHeader() {
  return `
    <div class="overlay-header">
      ${logoImage()}
      <div class="overlay-eyebrow">SECRET GROUNDS</div>
      <button class="overlay-icon-btn" type="button" data-action="collapse_overlay">${icon("chevron-down")}</button>
    </div>`;
}

function renderPill() {
  document.body.dataset.webviewSurface = "pill";
  app.innerHTML = `
    <section class="web-overlay-root">
      <div class="overlay-pill" data-action="restore_overlay">
        ${logoImage()}
        <span>SECRET GROUNDS</span>
      </div>
    </section>`;
  wireActions();
  requestResize();
}

function wireLock() {
  wireActions();
  const form = document.getElementById("login-form");
  if (form) {
    form.addEventListener("submit", event => {
      event.preventDefault();
      emitAction("member_login", {
        username: document.getElementById("username-input").value,
        pin: document.getElementById("pin-input").value
      });
    });
  }
  const eye = document.getElementById("eye-toggle");
  const pin = document.getElementById("pin-input");
  if (eye && pin) {
    eye.addEventListener("click", () => {
      const reveal = pin.type === "password";
      pin.type = reveal ? "text" : "password";
      eye.classList.toggle("revealed", reveal);
      eye.innerHTML = icon(reveal ? "eye-off" : "eye");
      emitAction("toggle_pin", { revealed: reveal });
    });
  }
}

function wireActions() {
  document.querySelectorAll("[data-action]").forEach(element => {
    element.addEventListener("click", () => emitAction(element.dataset.action));
  });
}

function requestResize() {
  window.requestAnimationFrame(() => {
    const target = document.querySelector(".overlay-card, .overlay-pill") || document.body;
    const rect = target.getBoundingClientRect();
    emitAction("content_size", {
      width: Math.ceil(rect.width),
      height: Math.ceil(rect.height)
    });
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function render() {
  if (currentState.view === "pill") {
    renderPill();
  } else if (currentState.view === "overlay") {
    renderOverlay();
  } else {
    renderLock();
  }
}

document.addEventListener("keydown", event => {
  if (event.ctrlKey && event.shiftKey && event.key === "F12") {
    event.preventDefault();
    emitAction("development_escape");
  }
});

window.addEventListener("error", event => {
  emitAction("ui_error", {
    message: event.message || "Unknown JavaScript error",
    source: event.filename || "",
    line: event.lineno || 0,
    column: event.colno || 0
  });
});

window.addEventListener("unhandledrejection", event => {
  emitAction("ui_error", {
    message: String(event.reason || "Unhandled promise rejection"),
    source: "promise"
  });
});

render();
