// Shared behaviour for every page: theme, navigation, scroll progress, back to top, UTM tracking.
(function () {
  const root = document.documentElement;
  const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");

  // ---- Theme toggle (the choice is remembered; otherwise the system setting applies) ----
  const themeToggle = document.querySelector("[data-theme-toggle]");

  function currentTheme() {
    return root.dataset.theme || (prefersDark.matches ? "dark" : "light");
  }

  function labelThemeToggle() {
    if (!themeToggle) return;
    const dark = currentTheme() === "dark";
    themeToggle.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    themeToggle.title = dark ? "Light theme" : "Dark theme";
  }

  if (themeToggle) {
    themeToggle.addEventListener("click", () => {
      const next = currentTheme() === "dark" ? "light" : "dark";
      root.dataset.theme = next;
      try { localStorage.setItem("theme", next); } catch (e) { /* storage blocked */ }
      labelThemeToggle();
      document.dispatchEvent(new CustomEvent("themechange"));
    });
  }
  prefersDark.addEventListener("change", () => {
    labelThemeToggle();
    document.dispatchEvent(new CustomEvent("themechange"));
  });
  labelThemeToggle();

  // ---- Mobile navigation ----
  const navToggle = document.querySelector("[data-nav-toggle]");
  const nav = document.getElementById("site-nav");

  function closeNav() {
    if (!nav || !navToggle) return;
    nav.classList.remove("open");
    navToggle.setAttribute("aria-expanded", "false");
  }

  if (navToggle && nav) {
    navToggle.addEventListener("click", () => {
      const open = nav.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", String(open));
    });
    nav.addEventListener("click", (e) => { if (e.target.closest("a")) closeNav(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeNav(); });
    window.addEventListener("resize", () => { if (window.innerWidth > 860) closeNav(); });
  }

  // ---- Scroll progress bar and back-to-top button ----
  const progress = document.querySelector(".scroll-progress span");
  const backToTop = document.querySelector("[data-back-to-top]");

  function onScroll() {
    const max = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = max > 0 ? Math.min(window.scrollY / max, 1) : 0;
    if (progress) progress.style.transform = "scaleX(" + ratio + ")";
    if (backToTop) backToTop.classList.toggle("visible", window.scrollY > 600);
  }
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("resize", onScroll);
  onScroll();

  if (backToTop) {
    backToTop.addEventListener("click", () => {
      window.scrollTo({ top: 0, behavior: reduceMotion.matches ? "auto" : "smooth" });
      const skip = document.querySelector(".skip-link");
      if (skip) skip.focus({ preventScroll: true });
    });
  }

  // ---- UTM tracking: remembered for the browser tab, sent with anonymous usage events ----
  const UTM_KEYS = ["utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"];
  let utm = {};
  try { utm = JSON.parse(sessionStorage.getItem("utm") || "{}"); } catch (e) { utm = {}; }

  const params = new URLSearchParams(window.location.search);
  const fresh = {};
  UTM_KEYS.forEach((key) => {
    const value = params.get(key);
    if (value) fresh[key] = value.slice(0, 100);
  });
  if (Object.keys(fresh).length) {
    utm = fresh;
    try { sessionStorage.setItem("utm", JSON.stringify(utm)); } catch (e) { /* storage blocked */ }
  }

  function referrerHost() {
    try {
      const host = document.referrer ? new URL(document.referrer).hostname : "";
      return host === window.location.hostname ? "" : host;
    } catch (e) {
      return "";
    }
  }

  // Events carry no location, address or personal data: only the event name, page and campaign.
  window.track = function (event, data) {
    const body = JSON.stringify({
      event: event,
      path: window.location.pathname,
      referrer: referrerHost(),
      utm: utm,
      data: data || {},
    });
    try {
      const sent = navigator.sendBeacon && navigator.sendBeacon("/api/track", new Blob([body], { type: "application/json" }));
      if (!sent) {
        fetch("/api/track", { method: "POST", headers: { "Content-Type": "application/json" }, body: body, keepalive: true })
          .catch(() => {});
      }
    } catch (e) { /* tracking must never break the page */ }
  };
  window.track("page_view");

  // ---- Pop-up notifications ----
  // notify(key, type, title, body, actions, autoHideMs): a newer message with the same key replaces
  // the older one. Success and info messages close themselves; errors stay until dismissed.
  const AUTO_HIDE = { success: 6000, info: 6000, warning: 10000, error: 0 };
  const MAX_TOASTS = 3;
  let region = null;
  const live = new Map();

  function toastRegion() {
    if (!region) {
      region = document.createElement("div");
      region.className = "toast-region";
      region.setAttribute("aria-live", "polite");
      document.body.append(region);
    }
    return region;
  }

  function dismiss(key) {
    const entry = live.get(key);
    if (!entry) return;
    clearTimeout(entry.timer);
    live.delete(key);
    entry.el.classList.add("leaving");
    setTimeout(() => entry.el.remove(), reduceMotion.matches ? 0 : 180);
  }

  window.notify = function (key, type, title, body, actions, autoHide) {
    dismiss(key);
    if (!title) return;
    const el = document.createElement("div");
    el.className = "toast alert alert-" + type;
    el.setAttribute("role", type === "error" ? "alert" : "status");

    const text = document.createElement("p");
    const strong = document.createElement("strong");
    strong.textContent = title;
    text.append(strong);
    if (body) text.append(document.createTextNode(body));
    el.append(text);

    if (actions && actions.length) {
      const row = document.createElement("div");
      row.className = "alert-actions";
      actions.forEach((action) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "btn btn-secondary btn-small";
        button.textContent = action.label;
        button.addEventListener("click", () => { dismiss(key); action.onClick(); });
        row.append(button);
      });
      el.append(row);
    }

    const close = document.createElement("button");
    close.type = "button";
    close.className = "alert-close";
    close.setAttribute("aria-label", "Dismiss message");
    close.textContent = "×";
    close.addEventListener("click", () => dismiss(key));
    el.append(close);

    toastRegion().prepend(el);
    const ms = autoHide === undefined || autoHide === null ? AUTO_HIDE[type] : autoHide;
    const entry = { el: el, timer: null };
    const arm = () => { if (ms) entry.timer = setTimeout(() => dismiss(key), ms); };
    // Pause the countdown while the visitor is reading or using the message
    el.addEventListener("mouseenter", () => clearTimeout(entry.timer));
    el.addEventListener("mouseleave", arm);
    el.addEventListener("focusin", () => clearTimeout(entry.timer));
    live.set(key, entry);
    arm();

    while (live.size > MAX_TOASTS) dismiss(live.keys().next().value);
  };
  window.notify.clear = dismiss;

  // ---- Footer year ----
  document.querySelectorAll("[data-year]").forEach((el) => { el.textContent = String(new Date().getFullYear()); });
})();
