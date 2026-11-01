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

  // ---- Footer year ----
  document.querySelectorAll("[data-year]").forEach((el) => { el.textContent = String(new Date().getFullYear()); });
})();
