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
// Deliberately separate from lockDraft: the unlock PIN field must always
// start empty, never pre-filled from whatever a member (this one or a
// previous one) last typed into the unrelated MEMBER LOGIN form.
let unlockDraft = { pin: "", pinVisible: false };
let overlayLockConfirmVisible = false;
// True only while a "Lock PC" request is in flight. Kept separate from
// overlayLockConfirmVisible so a routine per-second state push (the session
// timer refreshes the overlay every second regardless of this flow) can't
// clobber the confirm panel back to the plain active view before the
// backend has actually responded -- see the confirm button handler below.
let lockPcPending = false;
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
    // Wipe both credential drafts on a genuine transition INTO any lock
    // screen (fresh lock, expiration/logout grace, shutdown, member Lock
    // PC) -- a new person sitting down, or the same one after a logout,
    // must never see the previous occupant's typed username/PIN. Gated on
    // an actual mode change so a WebSocket reconnect blip mid-typing while
    // already on the same screen can't clobber what was just typed.
    const LOCK_RESET_MODES = new Set([
      "available", "member_locked", "grace", "shutting_down", "shutdown_failed"
    ]);
    if (LOCK_RESET_MODES.has(nextState.mode) && nextState.mode !== currentState.mode) {
      lockDraft.username = "";
      lockDraft.pin = "";
      lockDraft.pinVisible = false;
      unlockDraft.pin = "";
      unlockDraft.pinVisible = false;
    }
    // A notice arriving while a lock request is pending is the definitive
    // "the backend responded and it failed" signal (success instead hides
    // this whole window, so there's nothing left here to reset). Only then
    // do we leave the confirm panel -- never just because time passed.
    if (lockPcPending && nextState.notice) {
      lockPcPending = false;
      overlayLockConfirmVisible = false;
    }
    // presentation_token only changes when the overlay transitions from
    // hidden to visible again (see customer_overlay.py's show_session) --
    // e.g. a successful lock hides this window, and unlocking later shows
    // it again as what is, to the member, a brand new screen. Any leftover
    // local UI flow (a Lock PC confirmation that was mid-flight when the
    // window was hidden) must not resurface on that fresh presentation.
    if (
      currentState.presentation_token !== undefined &&
      nextState.presentation_token !== undefined &&
      nextState.presentation_token !== currentState.presentation_token
    ) {
      lockPcPending = false;
      overlayLockConfirmVisible = false;
    }
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

function lockAuroraRibbon() {
  // One low, gentle wave well below the logo/card, plus a fainter wisp up
  // and to the right for balance. Pure layered blur -- no crisp core
  // stroke, so it reads as a glow, not a line.
  // A single travelling filament with a real crest -- no long flat run
  // (that read as a slab). Thin strokes + tight halo so it glows like a
  // light ribbon rather than a band.
  const ribbon = "M -180,736 C 300,810 520,530 940,582 C 1320,630 1560,830 2100,676";
  const wisp = "M 1240,330 C 1470,386 1560,500 1470,618";
  return `
    <svg class="lock-aurora" viewBox="0 0 1920 1080" preserveAspectRatio="xMidYMid slice">
      <defs>
        <linearGradient id="auroraFade" x1="0" y1="0" x2="1920" y2="0" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stop-color="#22D3EE" stop-opacity="0"/>
          <stop offset="24%" stop-color="#2EDBF5" stop-opacity="0.9"/>
          <stop offset="44%" stop-color="#8FF1FF" stop-opacity="1"/>
          <stop offset="52%" stop-color="#DFFCFF" stop-opacity="1"/>
          <stop offset="60%" stop-color="#8FF1FF" stop-opacity="1"/>
          <stop offset="78%" stop-color="#22D3EE" stop-opacity="0.7"/>
          <stop offset="100%" stop-color="#1266D8" stop-opacity="0"/>
        </linearGradient>
        <linearGradient id="auroraWisp" x1="1210" y1="330" x2="1500" y2="630" gradientUnits="userSpaceOnUse">
          <stop offset="0%" stop-color="#7DECFF" stop-opacity="0"/>
          <stop offset="50%" stop-color="#22D3EE" stop-opacity="0.4"/>
          <stop offset="100%" stop-color="#1266D8" stop-opacity="0"/>
        </linearGradient>
        <filter id="auroraHalo" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="26"/></filter>
        <filter id="auroraMid" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="12"/></filter>
        <filter id="auroraInner" x="-40%" y="-40%" width="180%" height="180%"><feGaussianBlur stdDeviation="4"/></filter>
      </defs>
      <path d="${ribbon}" fill="none" stroke="url(#auroraFade)" stroke-width="96" stroke-opacity="0.3" stroke-linecap="round" filter="url(#auroraHalo)"/>
      <path d="${ribbon}" fill="none" stroke="url(#auroraFade)" stroke-width="36" stroke-opacity="0.55" stroke-linecap="round" filter="url(#auroraMid)"/>
      <path d="${ribbon}" fill="none" stroke="url(#auroraFade)" stroke-width="13" stroke-opacity="0.85" stroke-linecap="round" filter="url(#auroraInner)"/>
      <path d="${ribbon}" fill="none" stroke="url(#auroraFade)" stroke-width="3.5" stroke-opacity="1" stroke-linecap="round" filter="url(#auroraInner)"/>
      <path d="${wisp}" fill="none" stroke="url(#auroraWisp)" stroke-width="34" stroke-opacity="0.68" stroke-linecap="round" filter="url(#auroraMid)"/>
      <path d="${wisp}" fill="none" stroke="url(#auroraWisp)" stroke-width="8" stroke-opacity="0.7" stroke-linecap="round" filter="url(#auroraInner)"/>
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
      ${lockAuroraRibbon()}
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

function renderMemberLocked() {
  // Distinct from the idle "Available soon" lock screen: no username field
  // (the active session already identifies the member), a danger-toned
  // headline/card accent, and an UNLOCK button instead of LOG IN. Reuses
  // the exact same fullscreen/topmost lock window and PIN field markup
  // (including the custom eye toggle), so all of that screen's existing
  // input-blocking behavior and PIN-field styling applies unchanged.
  const currentPin = document.getElementById("unlock-pin-input");
  if (currentPin) {
    unlockDraft.pin = currentPin.value;
    unlockDraft.pinVisible = currentPin.type === "text";
  }
  const previousFocus = document.activeElement?.id || "";
  const offline = currentState.connection_state !== "online";
  const disabled = currentState.is_login_processing ? "disabled" : "";
  const memberName = currentState.locked_member_name || "this member";
  const loginMessage = currentState.login_message
    ? `<div class="lock-login-message">${escapeHtml(currentState.login_message)}</div>`
    : "";
  app.innerHTML = `
    <section class="lock-screen lock-screen-member-locked ${offline ? "is-offline" : ""}">
      <div class="lock-fog"></div>
      ${lockVolumetricRays()}
      ${lockAuroraRibbon()}
      <div class="lock-branding-bloom"></div>
      <div class="lock-watermark">${logoImage()}</div>
      <div class="lock-particles">${particleDots(72)}</div>
      <div class="lock-vignette"></div>
      <div class="lock-content">
        ${logoImage("lock-logo")}
        <h1 class="lock-title">${escapeHtml((currentState.shop_display_name || "Secret Grounds").toUpperCase())}</h1>
        <p class="lock-headline lock-headline-danger">SESSION LOCKED</p>
        <p class="lock-instruction">This PC is reserved for ${escapeHtml(memberName)}.\nYour session is still running. Enter your PIN to continue.</p>
        <form class="login-card is-unlock" id="unlock-form">
          <div class="login-title">UNLOCK</div>
          <div class="field-group">
            <label class="field-label" for="unlock-pin-input">pin</label>
            <div class="field">
              ${icon("lock")}
              <input id="unlock-pin-input" name="pin" type="password" autocomplete="off" placeholder="enter pin" ${disabled} />
              <button class="eye-toggle" id="unlock-eye-toggle" type="button" ${disabled}>${icon("eye")}</button>
            </div>
          </div>
          <button class="btn-gradient" type="submit" ${disabled}>${currentState.is_login_processing ? "UNLOCKING..." : "UNLOCK"}</button>
          ${loginMessage}
        </form>
        <div class="lock-pc-row">${icon("computer")}<span>${escapeHtml(currentState.pc_name || "PC-01")}</span></div>
        <div class="lock-connection-warning">Waiting for cashier connection...</div>
      </div>
    </section>`;
  wireMemberLocked();
  const pin = document.getElementById("unlock-pin-input");
  const eye = document.getElementById("unlock-eye-toggle");
  if (pin) {
    pin.value = unlockDraft.pin;
    pin.type = unlockDraft.pinVisible ? "text" : "password";
  }
  if (eye && unlockDraft.pinVisible) {
    eye.classList.add("revealed");
    eye.innerHTML = icon("eye-off");
  }
  if (!currentState.is_login_processing) {
    const previous = document.getElementById(previousFocus);
    if (previous) previous.focus();
    else if (pin) pin.focus();
  }
}

function wireMemberLocked() {
  const form = document.getElementById("unlock-form");
  if (form) {
    form.addEventListener("submit", event => {
      event.preventDefault();
      emitAction("unlock_session", { pin: document.getElementById("unlock-pin-input").value });
    });
  }
  const eye = document.getElementById("unlock-eye-toggle");
  const pin = document.getElementById("unlock-pin-input");
  if (eye && pin) {
    eye.addEventListener("click", () => {
      const reveal = pin.type === "password";
      pin.type = reveal ? "text" : "password";
      eye.classList.toggle("revealed", reveal);
      eye.innerHTML = icon(reveal ? "eye-off" : "eye");
    });
  }
}

function renderOverlay() {
  document.body.dataset.webviewSurface = "overlay";
  if (overlayLockConfirmVisible) {
    renderLockConfirmPanel();
    return;
  }
  const isOpen = currentState.session_type === "Open Time";
  const isMember = currentState.session_type === "Member Time";
  const primarySeconds = displayedTimerSeconds();
  const timerLabel = isOpen ? "Elapsed Time" : "Remaining Time";
  const identityName = isMember ? (currentState.customer_name || "").toLowerCase() : "Guest";
  const identityCaption = isMember ? "Member" : (isOpen ? "Open Time" : "Package");
  const connection = currentState.connection_state !== "online"
    ? `<div class="overlay-connection-warning">Reconnecting...</div>`
    : "";
  const notice = currentState.notice
    ? `<div class="overlay-notice">${escapeHtml(currentState.notice)}</div>`
    : "";
  const used = !isOpen
    ? `<div class="overlay-stat-row">${icon("clock")}<div><div class="overlay-stat-caption">Used Time</div><div class="overlay-stat-value">${formatUsedTime(currentState.used_seconds)}</div></div></div>`
    : "";
  // Lock PC is a secondary action alongside Log Out -- never styled larger
  // or more prominent than the timer/session info above it.
  const actionButtons = [];
  if (currentState.can_lock_pc) {
    actionButtons.push(`<button class="btn-outline" type="button" id="lock-pc-open">Lock PC</button>`);
  }
  if (currentState.can_logout) {
    actionButtons.push(`<button class="btn-outline" type="button" data-action="logout">Log Out</button>`);
  }
  const actions = actionButtons.length
    ? `<div class="overlay-footer-divider"></div><div class="overlay-actions">${actionButtons.join("")}</div>`
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
        ${notice}
        ${actions}
      </div>
    </section>`;
  wireActions();
  const lockOpenButton = document.getElementById("lock-pc-open");
  if (lockOpenButton) {
    lockOpenButton.addEventListener("click", () => {
      overlayLockConfirmVisible = true;
      renderOverlay();
    });
  }
  requestResize();
}

function renderLockConfirmPanel() {
  // A focused confirm-only card, not an addition stacked onto the normal
  // session card -- deliberately has no collapse control, so this always
  // resolves via Cancel/Lock PC before anything else can happen.
  app.innerHTML = `
    <section class="web-overlay-root">
      <div class="overlay-card">
        ${overlayHeader(false)}
        <div class="overlay-confirm-title">Lock this computer?</div>
        <div class="overlay-confirm-body">Your session will remain active and your time will continue running while this PC is locked.</div>
        <div class="overlay-confirm-actions">
          <button class="btn-outline" type="button" id="lock-pc-cancel">Cancel</button>
          <button class="btn-gradient" type="button" id="lock-pc-confirm" ${lockPcPending ? "disabled" : ""}>${lockPcPending ? "Locking..." : "Lock PC"}</button>
        </div>
      </div>
    </section>`;
  document.getElementById("lock-pc-cancel").addEventListener("click", () => {
    overlayLockConfirmVisible = false;
    lockPcPending = false;
    renderOverlay();
  });
  document.getElementById("lock-pc-confirm").addEventListener("click", () => {
    // Deliberately does NOT clear overlayLockConfirmVisible here. The
    // routine per-second state push (the session timer refreshes the
    // overlay every second regardless of this flow) used to arrive before
    // the backend responded, and because that flag was already cleared,
    // renderOverlay() fell through to the plain active view -- silently
    // discarding the confirm panel (and the still-in-flight request's
    // eventual result) before the member ever saw an outcome. Now the
    // confirm panel stays up, showing a pending state, until setState()
    // sees a definitive result: a notice (failure) or the whole window
    // being hidden by the server (success).
    if (lockPcPending) return;
    lockPcPending = true;
    emitAction("lock_pc");
    renderLockConfirmPanel();
  });
  requestResize();
}

function overlayHeader(showCollapse = true) {
  const collapse = showCollapse
    ? `<button class="overlay-icon-btn" type="button" data-action="collapse_overlay">${icon("chevron-down")}</button>`
    : "";
  return `
    <div class="overlay-header">
      ${logoImage()}
      <div class="overlay-eyebrow">SECRET GROUNDS</div>
      ${collapse}
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
  if (currentState.view === "hud") {
    renderHud();
  } else if (currentState.view === "pill") {
    renderPill();
  } else if (currentState.view === "overlay") {
    renderOverlay();
  } else if (currentState.mode === "member_locked") {
    renderMemberLocked();
  } else {
    renderLock();
  }
}

// Remaining-time reminder HUD: a one-line "[logo] 5 minutes left" strip that
// briefly appears over gameplay and fades itself out. Its window is
// click-through; it never blocks input. Each new reminder (a new hud_token)
// restarts the entrance/exit animation.
function renderHud() {
  document.body.dataset.webviewSurface = "hud";
  const text = currentState.hud_text || "";
  const token = currentState.hud_token;
  app.innerHTML = `
    <section class="web-overlay-root hud-root">
      <div class="hud-strip" id="hud-strip">
        ${logoImage("hud-logo")}
        <span class="hud-text">${escapeHtml(text)}</span>
      </div>
    </section>`;
  const strip = document.getElementById("hud-strip");
  if (!strip) return;
  strip.style.animation = "none";
  void strip.offsetWidth;
  strip.style.animation = "";
  strip.addEventListener(
    "animationend",
    () => emitAction("hud_done", { token }),
    { once: true }
  );
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
