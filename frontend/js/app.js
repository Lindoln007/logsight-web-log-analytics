/**
 * LogSight application shell.
 *
 * One working dataset is chosen in the sidebar and every page reads from it.
 * Each page ("view") has a loader that fetches what it needs, writes the
 * finding as a sentence, then draws the supporting evidence.
 */
(function () {
  "use strict";

  var API = window.LogSightAPI;
  var Charts = window.LogSightCharts;
  var ROLE = window.LOGSIGHT_USER_ROLE || "viewer";
  var CAN_EDIT = ROLE === "admin" || ROLE === "analyst";
  var REDUCED = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  var ROUTES = ["overview", "datasets", "explore", "forecast", "anomalies", "security", "compare", "reports"];
  var ALIASES = { dashboard: "overview", upload: "datasets", viewer: "explore", search: "explore" };
  var STORE = { active: "logsight.activeDataset", hidden: "logsight.hiddenSections", saved: "logsight.savedFilters" };

  var state = {
    datasets: [],
    active: null,          // normalised dataset object
    summary: null,         // summary for the active dataset
    route: "overview",
    runs: {},              // per-view counters that discard stale responses
    loaded: {},            // per-view signature of what is currently rendered
    range: { from: "", to: "" },
    interval: null,
    hidden: [],
    ex: { q: "", status: "all", method: "", ip: "", endpoint: "", from: "", to: "", page: 1 },
    fc: { method: "ml", periods: 24 },
    an: { method: "isolation_forest", sensitivity: "medium", filter: "all", data: null },
    polling: null,
  };

  // ---------------------------------------------------------------------------
  // Small helpers
  // ---------------------------------------------------------------------------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $$(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function esc(value) {
    return String(value == null ? "" : value)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }
  function icon(name, cls) { return '<svg class="icon ' + (cls || "") + '" aria-hidden="true"><use href="#i-' + name + '"/></svg>'; }
  function num(value) { return value == null || isNaN(value) ? "—" : Math.round(Number(value)).toLocaleString("en"); }
  function pct(value, digits) { return value == null || isNaN(value) ? "—" : Number(value).toFixed(digits == null ? 1 : digits) + "%"; }
  function plural(n, one, many) { return Number(n) === 1 ? one : (many || one + "s"); }

  function bytes(value) {
    if (value == null || isNaN(value)) return "—";
    var v = Number(value), units = ["B", "KB", "MB", "GB"], i = 0;
    while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
    return (i === 0 ? Math.round(v) : v.toFixed(v < 10 ? 1 : 0)) + " " + units[i];
  }

  // All log timestamps are shown in UTC so they match the server-side labels.
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function pad(n) { return String(n).padStart(2, "0"); }
  function utc(iso, style) {
    if (!iso) return "—";
    var d = new Date(iso);
    if (isNaN(d)) return "—";
    var day = MONTHS[d.getUTCMonth()] + " " + d.getUTCDate();
    var time = pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes());
    if (style === "day") return day;
    if (style === "date") return day + ", " + d.getUTCFullYear();
    if (style === "seconds") return day + " " + time + ":" + pad(d.getUTCSeconds());
    return day + " " + time;
  }
  function period(range) {
    if (!range || !range.start) return "";
    var a = new Date(range.start), b = new Date(range.end || range.start);
    var sameYear = a.getUTCFullYear() === b.getUTCFullYear();
    if (utc(range.start, "date") === utc(range.end, "date")) return utc(range.start, "date");
    return (sameYear ? utc(range.start, "day") : utc(range.start, "date")) + " – " + utc(range.end, "date");
  }
  function localDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleDateString("en", { day: "numeric", month: "short", year: "numeric" }); } catch (_) { return "—"; }
  }
  function ago(iso) {
    var s = (Date.now() - new Date(iso).getTime()) / 1000;
    if (isNaN(s)) return "";
    if (s < 60) return "just now";
    if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago";
    return localDate(iso);
  }

  function load(key, fallback) { try { var v = JSON.parse(localStorage.getItem(key)); return v == null ? fallback : v; } catch (_) { return fallback; } }
  function save(key, value) { try { localStorage.setItem(key, JSON.stringify(value)); } catch (_) {} }

  /** Returns a function that is true only while this is the newest run of *view*. */
  function guard(view) {
    var run = (state.runs[view] = (state.runs[view] || 0) + 1);
    var id = state.active && state.active.id;
    return function () { return state.runs[view] === run && (state.active && state.active.id) === id; };
  }

  function skeleton(lines, cls) {
    var widths = ["96%", "88%", "62%", "80%"], html = "";
    for (var i = 0; i < lines; i++) html += '<span class="skeleton ' + (cls || "skeleton-line") + '" style="width:' + widths[i % widths.length] + '">&nbsp;</span>';
    return html;
  }

  /** Animate a number from zero so the eye lands on it as the page settles. */
  function countUp(el, value, format) {
    if (!el) return;
    format = format || num;
    if (value == null || isNaN(value)) { el.textContent = "—"; return; }
    if (REDUCED || document.hidden) { el.innerHTML = format(value); return; }
    var start = performance.now(), duration = 800;
    (function frame(now) {
      var t = Math.min(1, (now - start) / duration);
      el.innerHTML = format(value * (1 - Math.pow(2, -10 * t)) / (1 - Math.pow(2, -10)));
      if (t < 1) requestAnimationFrame(frame); else el.innerHTML = format(value);
    })(start);
  }

  // ---------------------------------------------------------------------------
  // Feedback: toasts and the route progress line
  // ---------------------------------------------------------------------------

  function toast(message, kind, html) {
    var host = $("#toasts");
    if (!host) return;
    var el = document.createElement("div");
    el.className = "toast" + (kind ? " toast-" + kind : "");
    el.innerHTML = icon(kind === "bad" ? "alert" : kind === "good" ? "check" : "info") +
      "<span>" + (html ? message : esc(message)) + "</span>" +
      '<button type="button" aria-label="Dismiss">' + icon("x", "icon-sm") + "</button>";
    var close = function () {
      if (el.classList.contains("is-leaving")) return;
      el.classList.add("is-leaving");
      setTimeout(function () { el.remove(); }, 240);
    };
    $("button", el).addEventListener("click", close);
    host.appendChild(el);
    setTimeout(close, kind === "bad" ? 8000 : 5000);
  }

  var busyCount = 0;
  function busy(promise) {
    var bar = $("#route-progress");
    if (++busyCount === 1 && bar) { bar.className = "route-progress"; void bar.offsetWidth; bar.classList.add("is-running"); }
    var done = function () {
      if (--busyCount === 0 && bar) { bar.classList.remove("is-running"); bar.classList.add("is-done"); }
    };
    promise.then(done, done);
    return promise;
  }

  // ---------------------------------------------------------------------------
  // Segmented controls and popovers
  // ---------------------------------------------------------------------------

  function syncSegmented(el) {
    var on = $('button[aria-pressed="true"]', el);
    if (!on || !el.offsetParent) return;
    el.style.setProperty("--thumb-x", on.offsetLeft + "px");
    el.style.setProperty("--thumb-w", on.offsetWidth + "px");
  }
  function setSegmented(el, value) {
    $$("button", el).forEach(function (b) { b.setAttribute("aria-pressed", String(b.dataset.value === String(value))); });
    syncSegmented(el);
  }
  function initSegmented(id, onChange) {
    var el = $("#" + id);
    if (!el) return;
    el.addEventListener("click", function (e) {
      var b = e.target.closest("button");
      if (!b || b.getAttribute("aria-pressed") === "true") return;
      setSegmented(el, b.dataset.value);
      onChange(b.dataset.value);
    });
  }
  function syncAllSegmented() { $$(".segmented").forEach(syncSegmented); }

  var openPopover = null;
  function closePopover() {
    if (!openPopover) return;
    openPopover.pop.hidden = true;
    openPopover.btn.setAttribute("aria-expanded", "false");
    openPopover = null;
  }
  function bindPopover(btn, pop, onOpen) {
    if (!btn || !pop) return;
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var wasOpen = openPopover && openPopover.pop === pop;
      closePopover();
      if (wasOpen) return;
      pop.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      openPopover = { btn: btn, pop: pop };
      if (onOpen) onOpen();
    });
    pop.addEventListener("click", function (e) { e.stopPropagation(); });
  }
  document.addEventListener("click", closePopover);
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") {
      if (openPopover) { var b = openPopover.btn; closePopover(); b.focus(); }
      $("#shell").classList.remove("nav-open");
    }
  });

  // ---------------------------------------------------------------------------
  // Routing
  // ---------------------------------------------------------------------------

  function parseHash() {
    var name = (location.hash || "").replace(/^#\/?/, "").split(/[/?]/)[0];
    name = ALIASES[name] || name;
    return ROUTES.indexOf(name) >= 0 ? name : "overview";
  }
  function go(route) {
    if (parseHash() === route && state.route === route) { showRoute(route); return; }
    location.hash = "#/" + route;
  }

  function showRoute(route) {
    state.route = route;
    var target = $('[data-view="' + route + '"]');
    var needsDataset = target.hasAttribute("data-needs-dataset") && !state.active;
    var shown = needsDataset ? $("#no-dataset") : target;

    var swap = function () {
      $$(".view[data-view], #no-dataset").forEach(function (v) { v.hidden = v !== shown; });
      shown.classList.remove("is-entering");
      $$(":scope > *", shown).forEach(function (child, i) { child.style.setProperty("--i", i); });
      void shown.offsetWidth;
      shown.classList.add("is-entering");
      window.scrollTo(0, 0);
      // Thumbs can only be measured once the view is on screen.
      syncAllSegmented();
    };
    if (document.startViewTransition && !REDUCED && !document.hidden) {
      // A transition can be skipped (tab hidden, another one starting); the swap still runs.
      var transition = document.startViewTransition(swap);
      transition.ready.catch(function () {});
      transition.finished.catch(function () {});
    } else swap();

    // Frame: breadcrumb, nav state, document title.
    var section = target.dataset.section || "";
    var title = target.dataset.title || "";
    $("#crumb-section").textContent = section || title;
    $("#crumb-page").textContent = section ? title : "";
    $("#crumb-sep").hidden = !section;
    document.title = title + " · LogSight";
    $$(".nav-link").forEach(function (a) {
      if (a.dataset.route === route) a.setAttribute("aria-current", "page"); else a.removeAttribute("aria-current");
    });
    moveNavMarker();
    $("#shell").classList.remove("nav-open");
    $("#menu-btn").setAttribute("aria-expanded", "false");
    requestAnimationFrame(syncAllSegmented);

    if (needsDataset) return;
    // A view keeps its rendered result while hidden.  Coming back to it with
    // the same dataset and settings shows that result at once, with no reload.
    if (state.loaded[route] === viewKey(route)) return;
    reload(route);
  }

  /** Everything a view's content depends on, as one comparable string. */
  function viewKey(route) {
    var id = state.active ? state.active.id : "";
    switch (route) {
      case "overview": return [id, state.range.from, state.range.to].join("|");
      case "explore": return id + "|" + JSON.stringify(state.ex);
      case "forecast": return [id, state.fc.method, state.fc.periods].join("|");
      case "anomalies": return [id, state.an.method, state.an.sensitivity].join("|");
      case "datasets": return "";          // always refreshed: statuses change
      case "compare": return completed().map(function (d) { return d.id; }).join(",") + "|" + id;
      default: return id;
    }
  }

  /** Run a view's loader and remember what it rendered. */
  function reload(route) {
    var loader = LOADERS[route];
    if (!loader) return Promise.resolve();
    var key = viewKey(route);
    return busy(Promise.resolve().then(loader).then(function () {
      state.loaded[route] = route === "datasets" ? null : key;
    }).catch(function (err) {
      state.loaded[route] = null;
      toast(err && err.message ? err.message : "Something went wrong loading this page.", "bad");
    }));
  }

  /** Forget every rendered view (after data changes on the server). */
  function forgetViews() { state.loaded = {}; }

  function moveNavMarker() {
    var nav = $("#nav"), link = $('.nav-link[aria-current="page"]', nav);
    if (!link) { nav.style.setProperty("--marker-o", 0); return; }
    $("#nav-marker").style.height = link.offsetHeight + "px";
    nav.style.setProperty("--marker-y", link.offsetTop + "px");
    nav.style.setProperty("--marker-o", 1);
  }

  window.addEventListener("hashchange", function () { showRoute(parseHash()); });
  window.addEventListener("resize", function () { moveNavMarker(); syncAllSegmented(); });

  // ---------------------------------------------------------------------------
  // Working dataset
  // ---------------------------------------------------------------------------

  function completed() { return state.datasets.filter(function (d) { return d.status === "completed"; }); }

  async function refreshDatasets(force) {
    if (force) API.invalidate("datasets");
    state.datasets = await API.listDatasets();
    var ready = completed();
    $("#nav-datasets-count").textContent = state.datasets.length ? state.datasets.length : "";

    // Keep the working dataset valid; fall back to the most recent ready one.
    var wanted = state.active ? state.active.id : load(STORE.active, null);
    var match = ready.filter(function (d) { return d.id === String(wanted); })[0] || ready[0] || null;
    if ((match && match.id) !== (state.active && state.active.id)) setActive(match, true);
    else if (match) { state.active = match; renderSwitcher(); }
    else renderSwitcher();
    return state.datasets;
  }

  function setActive(dataset, silent) {
    state.active = dataset || null;
    state.summary = null;
    state.range = { from: "", to: "" };
    state.interval = null;
    state.ex = { q: "", status: "all", method: "", ip: "", endpoint: "", from: "", to: "", page: 1 };
    API.setCurrentDataset(dataset ? dataset.id : null);
    save(STORE.active, dataset ? dataset.id : null);
    renderSwitcher();
    if (!silent) showRoute(state.route);
  }

  function renderSwitcher() {
    var btn = $("#ds-button"), d = state.active;
    btn.classList.toggle("is-empty", !d);
    $("#ds-name").textContent = d ? d.name : "No dataset open";
    $("#ds-meta").textContent = d ? num(d.totalEntries) + " requests" : (state.datasets.length ? "Choose one to begin" : "Upload a log to begin");

    var list = $("#ds-list"), ready = completed();
    if (!ready.length) {
      list.innerHTML = '<p class="faint" style="padding:var(--space-sm);font-size:var(--text-sm)">No finished datasets yet.</p>';
      return;
    }
    list.innerHTML = ready.map(function (item) {
      var on = d && d.id === item.id;
      return '<button type="button" class="ds-option" role="option" aria-selected="' + on + '" data-id="' + esc(item.id) + '">' +
        '<span><span class="ds-name truncate">' + esc(item.name) + '</span><span class="ds-meta">' + num(item.totalEntries) + " requests · " + esc(localDate(item.uploadedAt)) + "</span></span>" +
        (on ? icon("check") : "") + "</button>";
    }).join("");
  }

  async function activeSummary() {
    if (!state.active) return null;
    if (state.summary && state.summary.datasetId === state.active.id) return state.summary;
    var s = await API.getDashboardSummary(state.active.id);
    if (s.available) state.summary = s;
    return s;
  }

  /** The line under every page title that says which data is on screen. */
  async function contextLine(id, extra) {
    var el = $("#" + id);
    if (!el || !state.active) return null;
    el.innerHTML = "<b>" + esc(state.active.name) + "</b>";
    var s = await activeSummary();
    if (!state.active) return s;
    var parts = ["<b>" + esc(state.active.name) + "</b>"];
    if (s && s.available) {
      parts.push(num(s.totalRequests) + " requests");
      if (s.dateRange && s.dateRange.start) parts.push(esc(period(s.dateRange)));
    }
    if (extra) parts.push(extra);
    el.innerHTML = parts.join('<span class="dot" aria-hidden="true"></span>');
    return s;
  }

  // ---------------------------------------------------------------------------
  // Overview
  // ---------------------------------------------------------------------------

  function verdict(rate) {
    if (rate == null) return { cls: "", text: "" };
    if (rate < 5) return { cls: "hl-good", text: "a healthy error rate" };
    if (rate < 15) return { cls: "hl-warn", text: "more than a healthy site should show" };
    return { cls: "hl-bad", text: "a serious share of the traffic" };
  }

  async function loadOverview() {
    var current = guard("overview");
    var id = state.active.id;
    var range = state.range, ranged = !!(range.from || range.to);
    var opts = { dateFrom: range.from, dateTo: range.to };

    $("#ov-lede").innerHTML = skeleton(3, "skeleton-lede");
    $("#ov-range-clear").hidden = !ranged;
    applyHiddenSections();

    var full = await contextLine("ov-context", ranged ? "filtered by date" : "");
    if (!current()) return;
    if (!full || !full.available) {
      $("#ov-lede").innerHTML = "The analytics for this dataset are not available yet. Try recalculating them, or check the dataset's status on the Datasets page.";
      return;
    }

    // Date pickers are limited to the days the log actually covers.
    var dr = full.dateRange || {};
    ["ov-from", "ov-to"].forEach(function (key) {
      var input = $("#" + key);
      input.min = (dr.start || "").slice(0, 10);
      input.max = (dr.end || "").slice(0, 10);
      input.value = key === "ov-from" ? range.from : range.to;
    });

    var fig = { total: full.totalRequests, errors: full.totalErrors, rate: full.errorRate, visitors: full.uniqueVisitors, size: full.avgResponseSize, e4: full.errors4xx, e5: full.errors5xx };
    if (ranged) {
      try {
        var f = await API.getFilteredSummary(id, range.from ? range.from + "T00:00:00Z" : "", range.to ? range.to + "T23:59:59Z" : "");
        fig = { total: f.total_requests, errors: f.total_errors, rate: f.error_rate, visitors: f.unique_visitors, size: f.avg_response_size, e4: null, e5: null };
      } catch (_) { toast("Could not apply the date range; showing the whole log.", "bad"); }
      if (!current()) return;
    }

    // The finding.
    var v = verdict(fig.rate);
    var when = ranged
      ? "Between <b>" + esc(range.from || (dr.start || "").slice(0, 10)) + "</b> and <b>" + esc(range.to || (dr.end || "").slice(0, 10)) + "</b>"
      : (dr.start ? "Over <b>" + esc(period(dr)) + "</b>" : "In this log");
    $("#ov-lede").innerHTML = fig.total
      ? when + ', this site answered <span class="hl">' + num(fig.total) + " " + plural(fig.total, "request") + "</span> from <b>" + num(fig.visitors) +
        " " + plural(fig.visitors, "visitor") + '</b>. <span class="' + v.cls + '">' + pct(fig.rate) + " failed</span>, " + v.text + "."
      : when + ", no requests were recorded.";

    countUp($("#fig-requests"), fig.total);
    $("#fig-requests-note").textContent = full.durationHours && !ranged ? "about " + num(fig.total / Math.max(full.durationHours, 1)) + " an hour" : " ";
    countUp($("#fig-errors"), fig.rate, function (x) { return x.toFixed(1) + "<small>%</small>"; });
    $("#fig-errors-note").textContent = fig.e4 != null ? num(fig.e4) + " client · " + num(fig.e5) + " server" : num(fig.errors) + " failed requests";
    countUp($("#fig-visitors"), fig.visitors);
    $("#fig-visitors-note").textContent = fig.visitors ? (fig.total / fig.visitors).toFixed(1) + " requests each" : " ";
    $("#fig-size").textContent = bytes(fig.size);

    // Default to daily buckets for long logs so the whole period is visible.
    if (!state.interval) state.interval = (full.durationHours || 0) > 24 * 14 ? "day" : "hour";
    setSegmented($("#ov-interval"), state.interval);

    loadSignals(current);
    loadTraffic(current, opts);
    loadStatus(current, opts);
    loadHours(current, opts);
    loadEndpoints(current, opts, fig.total);
  }

  function signalRow(href, tagHtml, title, text) {
    return '<a class="signal reveal" href="' + href + '"><span>' + tagHtml + '</span><span><span class="signal-title">' + esc(title) +
      '</span><br /><span class="signal-text">' + text + "</span></span>" + icon("arrow-right") + "</a>";
  }

  function loadSignals(current) {
    var host = $("#ov-signals"), id = state.active.id;
    host.innerHTML = ["an", "se", "fc"].map(function (k) {
      return '<div class="signal" id="sig-' + k + '"><span>' + skeleton(1) + "</span><span>" + skeleton(2) + "</span><span></span></div>";
    }).join("");
    var put = function (key, html) { var el = $("#sig-" + key); if (el && current()) el.outerHTML = html; };
    var failed = function (key, title, href) {
      put(key, signalRow(href, '<span class="tag tag-low">Unavailable</span>', title, "This analysis could not be run. Open the page to try again."));
    };

    // The three analyses run one after another: started together they compete
    // for the server and all arrive late, in sequence each row fills in sooner.
    var anomalies = function () { return API.getAnomalies(id, { method: "isolation_forest", sensitivity: "medium" }).then(function (d) {
      if (!d.available) return failed("an", "Unusual hours", "#/anomalies");
      var s = d.summary || {}, total = s.total_anomalies || 0, hours = (d.historical_series || []).length;
      var tag = total === 0 ? '<span class="tag tag-good">Clear</span>'
        : s.high_severity ? '<span class="tag tag-high">' + s.high_severity + " critical</span>"
        : '<span class="tag tag-medium">' + total + " flagged</span>";
      var worst = worstAnomaly(d.anomalies || []);
      put("an", signalRow("#/anomalies", tag, "Unusual hours",
        total ? "<b>" + total + "</b> of " + num(hours) + " hours stand out" + (worst ? ", most of all " + esc(worst.label) + "." : ".")
              : "All " + num(hours) + " hours look consistent with one another."));
    }).catch(function () { failed("an", "Unusual hours", "#/anomalies"); }); };

    var security = function () { return API.getSecurityInsights(id).then(function (d) {
      var score = d.risk_score || 0, n = (d.findings || []).length, level = riskLevel(score);
      var named = (d.findings || []).filter(function (f) { return f.source === "ai"; }).slice(0, 2).map(function (f) { return esc(f.title.replace(/ detected$/i, "").toLowerCase()); });
      put("se", signalRow("#/security", '<span class="tag ' + level.tag + '">' + level.word + "</span>", "Security",
        n ? "Risk score <b>" + score + "/100</b> from " + n + " " + plural(n, "finding") + (named.length ? ", including " + named.join(" and ") + "." : ".")
          : "No attack patterns or suspicious client behaviour were found."));
    }).catch(function () { failed("se", "Security", "#/security"); }); };

    var forecast = function () { return API.getTrafficForecast(id, { periods: 24, method: "ml" }).then(function (d) {
      if (!d.available || !(d.forecast || []).length) return failed("fc", "Next 24 hours", "#/forecast");
      var total = d.forecast.reduce(function (a, p) { return a + p.value; }, 0);
      var word = { increasing: "Rising", decreasing: "Falling", stable: "Steady" }[d.trend] || "Steady";
      put("fc", signalRow("#/forecast", '<span class="tag tag-accent">' + word + "</span>", "Next 24 hours",
        "About <b>" + num(total) + "</b> requests expected after the log ends, roughly " + num(total / d.forecast.length) + " an hour."));
    }).catch(function () { failed("fc", "Next 24 hours", "#/forecast"); }); };

    var step = function (next) { return function () { return current() ? next() : null; }; };
    anomalies().then(step(forecast)).then(step(security));
  }

  async function loadTraffic(current, opts) {
    var interval = state.interval || "hour";
    var data = await API.getRequestFrequency(state.active.id, { interval: interval, limit: interval === "hour" ? 336 : 366, dateFrom: opts.dateFrom, dateTo: opts.dateTo });
    if (!current()) return;
    var values = data.available ? data.datasets[0].data : [];
    var stamps = data.timestamps || [];
    var labels = stamps.map(function (t) { return utc(t, interval === "day" ? "day" : "time"); });
    Charts.traffic("chart-traffic", labels, values);
    var cap = $("#ov-traffic-caption");
    if (!values.length) { cap.textContent = "No requests fall inside this period."; return; }
    var peak = values.indexOf(Math.max.apply(null, values));
    var unit = interval === "day" ? "day" : "hour";
    cap.innerHTML = "The busiest " + unit + " was <b>" + esc(labels[peak]) + "</b> with " + num(values[peak]) + " requests. Showing " +
      (values.length >= (interval === "hour" ? 336 : 366) ? "the most recent " : "all ") + num(values.length) + " " + plural(values.length, unit) + ", in UTC.";
  }

  var STATUS_NAMES = { "1xx": "Informational", "2xx": "Success", "3xx": "Redirect", "4xx": "Client error", "5xx": "Server error", other: "Other" };

  async function loadStatus(current, opts) {
    var id = state.active.id;
    var results = await Promise.all([API.getStatusDistribution(id, opts), API.getErrorBreakdown(id)]);
    if (!current()) return;
    var dist = results[0], errors = results[1];
    var cats = dist.by_category || {}, keys = Object.keys(cats).filter(function (k) { return cats[k] > 0; });
    var total = keys.reduce(function (a, k) { return a + cats[k]; }, 0);

    $("#ov-stackbar").innerHTML = keys.map(function (k) { return '<span class="share-' + k + '" style="flex:' + cats[k] + ' 0 0" title="' + k + ": " + num(cats[k]) + '"></span>'; }).join("");
    $("#ov-status-body").innerHTML = keys.length ? keys.map(function (k) {
      return '<tr><td><i class="key share-' + k + '"></i><span class="mono">' + k + "</span> " + STATUS_NAMES[k] + '</td><td class="r">' + num(cats[k]) + '</td><td class="r">' + pct(cats[k] / total * 100) + "</td></tr>";
    }).join("") : '<tr><td colspan="3" class="faint">No responses in this period.</td></tr>';

    var urls = (errors.top_error_endpoints || []).slice(0, 5), max = urls.length ? urls[0].error_count : 1;
    $("#ov-error-urls").innerHTML = urls.length ? urls.map(function (u, i) {
      return '<li style="--i:' + i + '"><a class="rank-label truncate" href="#/explore" data-explore=\'' + esc(JSON.stringify({ endpoint: u.endpoint, status: "errors" })) + "'>" + esc(u.endpoint) +
        '</a><span class="rank-value"><b>' + num(u.error_count) + '</b> errors</span><span class="rank-bar is-bad"><i style="width:' + (u.error_count / max * 100) + '%"></i></span></li>';
    }).join("") : '<li class="faint" style="display:block">No failed requests in this log.</li>';
  }

  async function loadHours(current, opts) {
    var data = await API.getTrafficPattern(state.active.id, opts);
    if (!current()) return;
    var values = data.available ? data.datasets[0].data : [];
    Charts.hours("chart-hours", data.labels || [], values, data.peak_hour);
    var cap = $("#ov-hours-caption");
    if (!values.some(Boolean)) { cap.textContent = "No requests fall inside this period."; return; }
    cap.innerHTML = "Traffic peaks around <b>" + pad(data.peak_hour) + ":00</b> and is quietest around <b>" + pad(data.quiet_hour) + ":00</b> (UTC), adding every day in the log together.";
  }

  async function loadEndpoints(current, opts, totalRequests) {
    var data = await API.getTopEndpoints(state.active.id, 10, opts);
    if (!current()) return;
    var items = data.items || [], max = items.length ? items[0].value : 1;
    var covered = items.reduce(function (a, i) { return a + (i.percentage || 0); }, 0);
    $("#ov-endpoints-note").textContent = items.length
      ? num(data.totalUniqueEndpoints) + " different URLs were requested. These " + items.length + " account for " + pct(covered, 0) + " of the traffic."
      : "";
    $("#ov-endpoints").innerHTML = items.length ? items.map(function (item, i) {
      return '<li style="--i:' + i + '"><a class="rank-label truncate" href="#/explore" title="Show these requests" data-explore=\'' + esc(JSON.stringify({ endpoint: item.label })) + "'>" + esc(item.label) +
        '</a><span class="rank-value"><b>' + num(item.value) + "</b> · " + pct(item.percentage) + '</span><span class="rank-bar"><i style="width:' + (item.value / max * 100) + '%"></i></span></li>';
    }).join("") : '<li class="faint" style="display:block">No requests in this period.</li>';
  }

  function applyHiddenSections() {
    $$("[data-widget]").forEach(function (el) { el.hidden = state.hidden.indexOf(el.dataset.widget) >= 0; });
    $$("[data-widget-toggle]").forEach(function (box) { box.checked = state.hidden.indexOf(box.dataset.widgetToggle) < 0; });
  }

  function initOverview() {
    initSegmented("ov-interval", function (value) {
      state.interval = value;
      busy(loadTraffic(guard("overview-traffic"), { dateFrom: state.range.from, dateTo: state.range.to }));
    });
    var onRange = function () {
      var from = $("#ov-from").value, to = $("#ov-to").value;
      if (from && to && from > to) { toast("The start date must be on or before the end date.", "bad"); return; }
      state.range = { from: from, to: to };
      reload("overview");
    };
    $("#ov-from").addEventListener("change", onRange);
    $("#ov-to").addEventListener("change", onRange);
    $("#ov-range-clear").addEventListener("click", function () { state.range = { from: "", to: "" }; reload("overview"); });

    $("#ov-refresh").addEventListener("click", async function () {
      var btn = this;
      btn.disabled = true;
      try {
        await busy(API.refreshAnalytics(state.active.id));
        state.summary = null;
        forgetViews();
        toast("Analytics recalculated from the stored log entries.", "good");
        await reload("overview");
      } catch (err) { toast(err.message || "Recalculation failed.", "bad"); }
      btn.disabled = false;
    });

    bindPopover($("#ov-customise"), $("#ov-customise-pop"));
    $("#ov-customise-pop").addEventListener("change", function () {
      state.hidden = $$("[data-widget-toggle]").filter(function (b) { return !b.checked; }).map(function (b) { return b.dataset.widgetToggle; });
      save(STORE.hidden, state.hidden);
      applyHiddenSections();
      API.savePreferences({ hidden_widgets: state.hidden }).catch(function () {});
    });
  }

  // Cross-links into Explore carry their filter in a data attribute.
  document.addEventListener("click", function (e) {
    var link = e.target.closest("[data-explore]");
    if (!link) return;
    e.preventDefault();
    openExplore(JSON.parse(link.dataset.explore));
  });
  function openExplore(filters) {
    state.ex = Object.assign({ q: "", status: "all", method: "", ip: "", endpoint: "", from: "", to: "", page: 1 }, filters);
    go("explore");
  }

  // ---------------------------------------------------------------------------
  // Datasets and upload
  // ---------------------------------------------------------------------------

  var STATUS_TAGS = {
    completed: ['tag-good', "Ready"],
    processing: ['tag-accent tag-busy', "Processing"],
    pending: ['tag-accent tag-busy', "Queued"],
    failed: ['tag-high', "Failed"],
  };

  async function loadDatasets(force) {
    await refreshDatasets(force);
    renderDatasets();
    // Keep watching while anything is still being parsed.
    clearTimeout(state.polling);
    if (state.route === "datasets" && state.datasets.some(function (d) { return d.status === "pending" || d.status === "processing"; })) {
      state.polling = setTimeout(function () { if (state.route === "datasets") loadDatasets(true); }, 3000);
    }
  }

  function renderDatasets() {
    var rows = state.datasets, body = $("#ds-table-body");
    $("#ds-empty").hidden = rows.length > 0;
    $("#ds-table-wrap").hidden = rows.length === 0;
    var ready = completed().length;
    $("#ds-list-note").textContent = rows.length ? ready + " of " + rows.length + " ready to analyse" : "";

    body.innerHTML = rows.map(function (d, i) {
      var tag = STATUS_TAGS[d.status] || STATUS_TAGS.pending;
      var isActive = state.active && state.active.id === d.id;
      var actions = "";
      if (d.status === "completed") {
        actions += isActive ? '<span class="tag tag-accent">Working dataset</span>'
                            : '<button type="button" class="btn btn-sm" data-open="' + esc(d.id) + '">Open</button>';
      }
      if (CAN_EDIT) actions += '<button type="button" class="icon-btn" data-delete="' + esc(d.id) + '" aria-label="Delete ' + esc(d.name) + '" title="Delete">' + icon("trash") + "</button>";
      return '<tr class="row-enter' + (isActive ? " is-current" : "") + '" style="--i:' + i + '" data-row="' + esc(d.id) + '">' +
        '<td><span class="ds-row-name">' + esc(d.name) + "</span>" +
          (d.status === "failed" && d.processingError ? '<br /><span class="field-error" style="margin-top:4px">' + esc(d.processingError) + "</span>" : "") + "</td>" +
        '<td><span class="tag ' + tag[0] + '">' + tag[1] + "</span></td>" +
        '<td class="r">' + (d.totalEntries ? num(d.totalEntries) : "—") + "</td>" +
        '<td class="r hide-sm">' + bytes(d.sizeBytes) + "</td>" +
        '<td class="hide-sm nowrap">' + esc(localDate(d.uploadedAt)) + "</td>" +
        '<td class="r"><span class="row-actions">' + actions + "</span></td></tr>";
    }).join("");
  }

  function initDatasets() {
    $$("[data-requires-edit]").forEach(function (el) { el.hidden = !CAN_EDIT; });

    $("#ds-table-body").addEventListener("click", async function (e) {
      var open = e.target.closest("[data-open]"), del = e.target.closest("[data-delete]");
      var yes = e.target.closest("[data-confirm-delete]"), no = e.target.closest("[data-cancel-delete]");
      if (open) {
        var d = state.datasets.filter(function (x) { return x.id === open.dataset.open; })[0];
        setActive(d, true);
        toast("“" + d.name + "” is now the working dataset.", "good");
        go("overview");
      } else if (del) {
        // Confirm in place: the row itself asks, so the eye never leaves it.
        var cell = del.closest(".row-actions");
        cell.dataset.previous = cell.innerHTML;
        cell.innerHTML = '<span class="confirm">Delete this log and its entries? <button type="button" class="btn btn-sm btn-danger" data-confirm-delete="' + esc(del.dataset.delete) +
          '">Delete</button><button type="button" class="btn btn-sm btn-quiet" data-cancel-delete>Keep</button></span>';
        $("[data-cancel-delete]", cell).focus();
      } else if (no) {
        var c = no.closest(".row-actions");
        c.innerHTML = c.dataset.previous;
      } else if (yes) {
        yes.setAttribute("aria-busy", "true");
        try {
          await API.deleteDataset(yes.dataset.confirmDelete);
          API.invalidate();
          forgetViews();
          toast("Dataset deleted.", "good");
        } catch (err) { toast(err.message || "The dataset could not be deleted.", "bad"); }
        await loadDatasets(true);
      }
    });

    if (!CAN_EDIT) return;
    var zone = $("#dropzone"), input = $("#file-input"), limit = 500 * 1024 * 1024;
    API.getUploadConfig().then(function (cfg) {
      if (!cfg) return;
      if (cfg.max_upload_size) limit = cfg.max_upload_size;
      $("#upload-hint").textContent = "Apache or Nginx access logs: " + (cfg.accepted_types || [".log", ".txt"]).join(", ") + ". Up to " + bytes(limit) + " each.";
    });

    var accept = function (files) {
      Array.prototype.forEach.call(files || [], function (file) {
        if (!/\.(log|txt)(\.gz)?$/i.test(file.name)) { toast("“" + file.name + "” is not a .log or .txt file (optionally gzipped).", "bad"); return; }
        if (file.size > limit) { toast("“" + file.name + "” is larger than the " + bytes(limit) + " limit.", "bad"); return; }
        upload(file);
      });
    };
    input.addEventListener("change", function () { accept(input.files); input.value = ""; });
    ["dragenter", "dragover"].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); zone.classList.add("is-over"); });
    });
    ["dragleave", "drop"].forEach(function (name) {
      zone.addEventListener(name, function (e) { e.preventDefault(); if (name === "drop" || !zone.contains(e.relatedTarget)) zone.classList.remove("is-over"); });
    });
    zone.addEventListener("drop", function (e) { accept(e.dataTransfer && e.dataTransfer.files); });
  }

  function upload(file) {
    var row = document.createElement("div");
    row.className = "upload";
    row.innerHTML = '<span class="upload-name truncate">' + esc(file.name) + ' <span class="faint" style="font-weight:400">· ' + bytes(file.size) + '</span></span>' +
      '<span class="upload-state">Uploading…</span><span class="progress"><i></i></span>';
    $("#uploads").prepend(row);
    var stateEl = $(".upload-state", row);

    API.uploadDataset(file, {
      onProgress: function (status) {
        var done = status && Number(status.total_entries);
        stateEl.textContent = done ? "Reading lines… " + num(done) + " so far" : "Reading lines…";
      },
    }).then(async function (dataset) {
      row.classList.add("is-done");
      stateEl.innerHTML = num(dataset.totalEntries) + " requests read";
      API.invalidate();
      forgetViews();
      var hadActive = !!state.active;
      await loadDatasets(true);
      if (!hadActive) {
        toast("“" + dataset.name + "” is ready and has been opened.", "good");
        go("overview");
      } else {
        var open = document.createElement("button");
        open.type = "button"; open.className = "btn btn-sm"; open.textContent = "Open";
        open.addEventListener("click", function () {
          var d = state.datasets.filter(function (x) { return x.id === dataset.id; })[0];
          if (d) { setActive(d, true); go("overview"); }
        });
        stateEl.appendChild(document.createTextNode(" "));
        stateEl.appendChild(open);
        toast("“" + dataset.name + "” is ready to analyse.", "good");
      }
    }).catch(function (err) {
      row.classList.add("is-failed");
      stateEl.textContent = err.message || "Upload failed.";
      loadDatasets(true);
    });
  }

  // ---------------------------------------------------------------------------
  // Explore
  // ---------------------------------------------------------------------------

  var EX_FIELDS = { q: "ex-q", status: "ex-status", method: "ex-method", ip: "ex-ip", endpoint: "ex-endpoint", from: "ex-from", to: "ex-to" };
  var EX_LABELS = { q: "contains", status: "status", method: "method", ip: "client", endpoint: "path", from: "from", to: "to" };

  function exActive() {
    return Object.keys(EX_FIELDS).filter(function (k) { return k === "status" ? state.ex.status !== "all" : !!state.ex[k]; });
  }

  async function loadExplore() {
    var current = guard("explore");
    Object.keys(EX_FIELDS).forEach(function (k) { $("#" + EX_FIELDS[k]).value = state.ex[k] || (k === "status" ? "all" : ""); });
    renderChips();
    renderSaved();
    contextLine("ex-context").then(function (s) {
      var dr = s && s.dateRange;
      if (!dr || !dr.start) return;
      ["ex-from", "ex-to"].forEach(function (id) { $("#" + id).min = dr.start.slice(0, 16); $("#" + id).max = dr.end.slice(0, 16); });
    });
    await fetchLogs(current);
  }

  async function fetchLogs(current) {
    current = current || guard("explore");
    var body = $("#ex-body"), f = state.ex;
    if (!body.children.length) body.innerHTML = Array(8).join('<tr><td colspan="6">' + skeleton(1) + "</td></tr>");
    $("#ex-table-wrap").style.opacity = 0.55;

    var result = await API.getLogLines(state.active.id, {
      page: f.page, pageSize: 50, status: f.status, search: f.q, ip: f.ip, method: f.method, endpoint: f.endpoint,
      dateFrom: f.from, dateTo: f.to,
    });
    if (!current()) return;
    $("#ex-table-wrap").style.opacity = "";

    var filtered = exActive().length > 0;
    $("#ex-count").textContent = result.available
      ? num(result.total) + " " + plural(result.total, "request") + (filtered ? " match" + (result.total === 1 ? "es" : "") : " in this dataset")
      : "Requests";
    $("#ex-empty").hidden = !(result.available && result.total === 0);
    $("#ex-table-wrap").hidden = result.available && result.total === 0;

    body.innerHTML = result.items.map(function (line, i) {
      var cls = "status-" + String(line.status).charAt(0);
      return '<tr class="log-row row-enter" style="--i:' + i + '" tabindex="0" data-raw="' + esc(line.raw) + '" data-ip="' + esc(line.ip_address) + '" data-endpoint="' + esc(line.endpoint) + '">' +
        '<td class="mono nowrap">' + esc(utc(line.timestamp, "seconds")) + "</td>" +
        '<td class="mono hide-sm">' + esc(line.ip_address) + "</td>" +
        '<td class="mono">' + esc(line.method) + "</td>" +
        '<td class="path">' + esc(line.endpoint.length > 140 ? line.endpoint.slice(0, 140) + "…" : line.endpoint) + "</td>" +
        '<td class="r"><span class="status ' + cls + '">' + esc(line.status) + "</span></td>" +
        '<td class="r hide-sm nowrap">' + (line.responseSize != null ? bytes(line.responseSize) : "—") + "</td></tr>";
    }).join("");

    $("#ex-page").textContent = result.total ? "Page " + num(result.page) + " of " + num(result.totalPages) : "";
    $("#ex-prev").disabled = result.page <= 1;
    $("#ex-next").disabled = result.page >= result.totalPages;
    state.ex.page = result.page;
    state.loaded.explore = viewKey("explore");
  }

  function renderChips() {
    var keys = exActive();
    $("#ex-chips").innerHTML = keys.map(function (k) {
      var value = k === "from" || k === "to" ? state.ex[k].replace("T", " ") : state.ex[k];
      return '<span class="chip">' + EX_LABELS[k] + ": " + esc(value.length > 48 ? value.slice(0, 48) + "…" : value) +
        '<button type="button" data-remove-filter="' + k + '" aria-label="Remove ' + EX_LABELS[k] + ' filter">' + icon("x", "icon-sm") + "</button></span>";
    }).join("");
    $("#ex-save").disabled = keys.length === 0;
    $("#ex-clear").disabled = keys.length === 0;
  }

  function renderSaved() {
    var saved = load(STORE.saved, []);
    $("#ex-saved").innerHTML = saved.length
      ? '<span class="label-sm">Saved</span>' + saved.map(function (s, i) {
          return '<span class="chip chip-plain" style="padding-right:4px"><button type="button" data-apply-saved="' + i + '" style="width:auto;border-radius:0;font-weight:600">' + esc(s.name) +
            '</button><button type="button" data-delete-saved="' + i + '" aria-label="Delete saved filter ' + esc(s.name) + '">' + icon("x", "icon-sm") + "</button></span>";
        }).join("")
      : "";
  }

  function initExplore() {
    var timer;
    var apply = function (delay) {
      clearTimeout(timer);
      timer = setTimeout(function () {
        Object.keys(EX_FIELDS).forEach(function (k) { state.ex[k] = $("#" + EX_FIELDS[k]).value.trim(); });
        if (state.ex.from && state.ex.to && state.ex.from > state.ex.to) { toast("“From” must be earlier than “To”.", "bad"); return; }
        state.ex.page = 1;
        renderChips();
        busy(fetchLogs());
      }, delay);
    };
    $("#ex-form").addEventListener("input", function (e) { apply(e.target.tagName === "SELECT" || e.target.type === "datetime-local" ? 0 : 320); });
    $("#ex-form").addEventListener("submit", function (e) { e.preventDefault(); apply(0); });

    var clear = function () { openExplore({}); };
    $("#ex-clear").addEventListener("click", clear);
    $('[data-action="ex-clear"]').addEventListener("click", clear);

    $("#ex-chips").addEventListener("click", function (e) {
      var b = e.target.closest("[data-remove-filter]");
      if (!b) return;
      state.ex[b.dataset.removeFilter] = b.dataset.removeFilter === "status" ? "all" : "";
      state.ex.page = 1;
      reload("explore");
    });

    $("#ex-save").addEventListener("click", function () {
      var keys = exActive();
      if (!keys.length) return;
      var saved = load(STORE.saved, []);
      var name = keys.map(function (k) { return state.ex[k].replace("T", " "); }).join(" · ");
      saved.unshift({ name: name.length > 40 ? name.slice(0, 40) + "…" : name, filters: Object.assign({}, state.ex, { page: 1 }) });
      save(STORE.saved, saved.slice(0, 8));
      renderSaved();
      toast("Filter saved. It will stay available under “Saved”.", "good");
    });
    $("#ex-saved").addEventListener("click", function (e) {
      var use = e.target.closest("[data-apply-saved]"), del = e.target.closest("[data-delete-saved]");
      var saved = load(STORE.saved, []);
      if (use && saved[use.dataset.applySaved]) openExplore(saved[use.dataset.applySaved].filters);
      if (del) { saved.splice(Number(del.dataset.deleteSaved), 1); save(STORE.saved, saved); renderSaved(); }
    });

    $("#ex-prev").addEventListener("click", function () { state.ex.page = Math.max(1, state.ex.page - 1); busy(fetchLogs()); });
    $("#ex-next").addEventListener("click", function () { state.ex.page += 1; busy(fetchLogs()); });

    // A row opens to show the original line and one-click ways to narrow down.
    var toggle = function (row) {
      var next = row.nextElementSibling;
      if (next && next.classList.contains("log-detail")) { next.remove(); row.setAttribute("aria-expanded", "false"); return; }
      $$(".log-detail", row.parentNode).forEach(function (d) { d.remove(); });
      var detail = document.createElement("tr");
      detail.className = "log-detail";
      detail.innerHTML = '<td colspan="6"><div class="log-detail-inner"><code class="raw-line">' + esc(row.dataset.raw) + "</code>" +
        '<span class="row-actions" style="justify-content:flex-start">' +
        '<button type="button" class="btn btn-sm" data-explore=\'' + esc(JSON.stringify({ ip: row.dataset.ip })) + "'>All requests from " + esc(row.dataset.ip) + "</button>" +
        '<button type="button" class="btn btn-sm" data-explore=\'' + esc(JSON.stringify({ endpoint: row.dataset.endpoint })) + "'>All requests for this path</button></span></div></td>";
      row.after(detail);
      row.setAttribute("aria-expanded", "true");
    };
    $("#ex-body").addEventListener("click", function (e) { var row = e.target.closest(".log-row"); if (row) toggle(row); });
    $("#ex-body").addEventListener("keydown", function (e) {
      if ((e.key === "Enter" || e.key === " ") && e.target.classList.contains("log-row")) { e.preventDefault(); toggle(e.target); }
    });
  }

  // ---------------------------------------------------------------------------
  // Forecast
  // ---------------------------------------------------------------------------

  var FC_HOW = {
    ml: "<p><b>Random Forest.</b> A machine-learning model is trained on this dataset each time you open the page; nothing is pre-trained. Every past hour becomes one example described by the hour of day, the day of the week, and the traffic 1, 2, 3 and 24 hours earlier.</p><p>It predicts one hour ahead, adds that prediction to the history, and repeats. The shaded range comes from how much its 100 decision trees disagree. It needs at least three days of data.</p>",
    moving_avg: "<p><b>Seasonal pattern.</b> The average traffic for each hour of the day is worked out across the whole log, giving a “typical day”. That shape is projected forward and scaled by the overall direction of travel.</p><p>The shaded range is a fixed 20% either side of the forecast.</p>",
    linear_regression: "<p><b>Linear trend.</b> A straight line is fitted through the hourly history using ordinary least squares and extended forward. It shows direction clearly but cannot follow a daily rhythm.</p><p>The shaded range is a fixed 15% either side of the forecast.</p>",
  };

  async function loadForecast() {
    var current = guard("forecast");
    $("#fc-lede").innerHTML = skeleton(3, "skeleton-lede");
    $("#fc-note").hidden = true;
    contextLine("fc-context");
    setSegmented($("#fc-method"), state.fc.method);
    setSegmented($("#fc-horizon"), state.fc.periods);

    var data = await API.getTrafficForecast(state.active.id, state.fc);
    if (!current()) return;
    var fc = data.forecast || [], lede = $("#fc-lede");
    if (!data.available || !fc.length) {
      lede.textContent = "There is no hourly history in this dataset to forecast from.";
      Charts.destroy("chart-forecast");
      $("#fc-eval").hidden = true;
      return;
    }

    var total = fc.reduce(function (a, p) { return a + p.value; }, 0), avg = total / fc.length;
    var peak = fc.reduce(function (a, p) { return p.value > a.value ? p : a; }, fc[0]);
    var usual = data.summary && data.summary.avg_historical;
    var diff = usual ? (avg - usual) / usual * 100 : 0;
    var against = !usual ? "" : Math.abs(diff) < 5 ? " That is <b>in line with</b> this log's usual " + num(usual) + " an hour."
      : " That is <b>" + Math.abs(diff).toFixed(0) + "% " + (diff > 0 ? "above" : "below") + "</b> this log's usual " + num(usual) + " an hour.";
    var horizon = fc.length >= 48 ? (fc.length / 24) + " days" : fc.length + " hours";
    lede.innerHTML = "In the <b>" + horizon + "</b> after this log ends, expect about <span class=\"hl\">" + num(total) + " requests</span>: roughly <b>" + num(avg) +
      " an hour</b>, peaking near <b>" + esc(peak.label) + "</b> at about " + num(peak.value) + "." + against;

    var note = $("#fc-note");
    note.hidden = !data.note;
    $("span", note).textContent = data.note || "";
    setSegmented($("#fc-method"), data.requested_method || state.fc.method);

    Charts.forecast("chart-forecast", data);
    $("#fc-caption").innerHTML = "The solid line is traffic recorded in the log; the dashed line continues from its last hour (<b>" + esc((data.historical[data.historical.length - 1] || {}).label || "") +
      "</b>). Overall the history is <b>" + ({ increasing: "rising", decreasing: "falling", stable: "steady" }[data.trend] || "steady") + "</b>. Times are UTC.";
    $("#fc-how").innerHTML = FC_HOW[data.method] || "";

    var ev = data.evaluation, section = $("#fc-eval");
    section.hidden = !(ev && ev.results && ev.results.length);
    if (section.hidden) return;
    $("#fc-eval-desc").innerHTML = "The last <b>" + ev.holdout_hours + " hours</b> of the log were hidden. Each method predicted them from the " + num(ev.training_hours) +
      " hours before, and was scored against what really happened (real average: " + num(ev.mean_actual) + " requests an hour). Smaller is better.";
    var worst = Math.max.apply(null, ev.results.map(function (r) { return r.mae; })) || 1;
    $("#fc-eval-body").innerHTML = ev.results.map(function (r, i) {
      var shown = r.method === data.method, best = r.method === ev.best_method;
      return '<tr class="' + (shown ? "is-current" : "") + '"><td><b>' + esc(r.label) + "</b>" +
        (best ? ' <span class="tag tag-good">Most accurate</span>' : "") + (shown ? ' <span class="tag tag-accent">Shown above</span>' : "") + "</td>" +
        '<td style="width:34%"><span class="rank-bar' + (best ? " is-accent" : "") + '" style="display:block;height:8px;--i:' + i + '"><i style="width:' + (r.mae / worst * 100) + '%"></i></span></td>' +
        '<td class="r">' + Number(r.mae).toLocaleString("en") + '</td><td class="r">' + Number(r.rmse).toLocaleString("en") + "</td></tr>";
    }).join("");
  }

  function initForecast() {
    initSegmented("fc-method", function (v) { state.fc.method = v; reload("forecast"); });
    initSegmented("fc-horizon", function (v) { state.fc.periods = Number(v); reload("forecast"); });
  }

  // ---------------------------------------------------------------------------
  // Anomalies
  // ---------------------------------------------------------------------------

  var AN_TYPES = { request_spike: "Traffic spike", error_rate_spike: "Error spike", traffic_drop: "Traffic drop", unusual_pattern: "Unusual pattern" };
  var SEVERITY = { high: ["tag-high", "Critical"], medium: ["tag-medium", "Notable"], low: ["tag-low", "Minor"] };
  var AN_HOW = {
    isolation_forest: "<p><b>Isolation Forest.</b> An unsupervised machine-learning model trained on this dataset; it needs no labelled examples. Each hour is described by its request count, error rate, number of unique visitors, average response size, requests per visitor and time of day.</p><p>The model learns what an ordinary hour looks like and flags the hours that are easiest to separate from the rest, including hours that are only unusual <em>in combination</em>, such as normal volume from very few visitors. It needs at least 24 hours of data.</p>",
    statistical: "<p><b>Statistical threshold.</b> The average and spread of the hourly request count and error rate are calculated, and any hour that sits far above the average is flagged. Each measure is checked on its own, so an hour that is only odd in combination will not be caught.</p><p>Sensitivity sets how far above average an hour must be: 3, 2 or 1.5 standard deviations.</p>",
  };

  function worstAnomaly(list) {
    var rank = { high: 0, medium: 1, low: 2 };
    return list.slice().sort(function (a, b) {
      return (rank[a.severity] - rank[b.severity]) || ((a.score != null && b.score != null) ? a.score - b.score : b.value - a.value);
    })[0] || null;
  }
  function reason(a) {
    var text = String(a.explanation || "").replace(/^Isolation Forest flagged this hour:\s*/i, "");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  async function loadAnomalies() {
    var current = guard("anomalies");
    $("#an-lede").innerHTML = skeleton(3, "skeleton-lede");
    $("#an-note").hidden = true;
    contextLine("an-context");
    setSegmented($("#an-method"), state.an.method);
    setSegmented($("#an-sensitivity"), state.an.sensitivity);

    var data = await API.getAnomalies(state.active.id, state.an);
    if (!current()) return;
    if (!data.available) { $("#an-lede").textContent = "Anomaly detection could not be run on this dataset."; return; }
    state.an.data = data;

    var list = data.anomalies || [], s = data.summary || {}, series = data.historical_series || [];
    var worst = worstAnomaly(list);
    $("#an-lede").innerHTML = !series.length ? "This dataset has no hourly history to analyse."
      : !list.length ? '<span class="hl-good">Nothing stands out.</span> All <b>' + num(series.length) + " hours</b> in this log look consistent with one another at this sensitivity."
      : '<span class="hl">' + list.length + " of " + num(series.length) + " hours</span> stand out" +
        (s.high_severity ? ', <span class="hl-bad">' + s.high_severity + " of them critical</span>" : "") + ". The most unusual was <b>" + esc(worst.label) + "</b>: " + esc(reason(worst));

    var note = $("#an-note");
    note.hidden = !data.note;
    $("span", note).textContent = data.note || "";

    var flagged = {};
    list.forEach(function (a) { flagged[a.label] = (SEVERITY[a.severity] || SEVERITY.low)[1] + " · " + (AN_TYPES[a.type] || a.type); });
    Charts.anomalies("chart-anomalies", series, flagged);

    $("#an-tally").innerHTML = ["high", "medium", "low"].map(function (k) {
      var count = s[k + "_severity"] || 0;
      return '<span class="tally-item"><span class="tally-value">' + count + '</span><span class="tag ' + SEVERITY[k][0] + '">' + SEVERITY[k][1] + "</span></span>";
    }).join("");
    $("#an-how").innerHTML = AN_HOW[data.method] || "";
    renderAnomalyList();
  }

  function renderAnomalyList() {
    var data = state.an.data, host = $("#an-list");
    if (!data) return;
    var rank = { high: 0, medium: 1, low: 2 };
    var list = (data.anomalies || []).filter(function (a) { return state.an.filter === "all" || a.severity === state.an.filter; })
      .sort(function (a, b) { return (rank[a.severity] - rank[b.severity]) || String(a.timestamp).localeCompare(String(b.timestamp)); });
    if (!list.length) {
      host.innerHTML = '<p class="faint" style="padding:var(--space-md) var(--space-xs)">' +
        ((data.anomalies || []).length ? "No flagged hours at this severity." : "No hours were flagged.") + "</p>";
      return;
    }
    host.innerHTML = list.map(function (a, i) {
      var sev = SEVERITY[a.severity] || SEVERITY.low;
      var start = String(a.timestamp || "").slice(0, 13);
      var link = start ? JSON.stringify({ from: start + ":00", to: start + ":59:59" }) : "";
      return '<div class="finding"><div class="finding-head row-enter" style="--i:' + Math.min(i, 20) + '">' +
        '<span><span class="tag ' + sev[0] + '">' + sev[1] + "</span></span>" +
        '<span><span class="finding-title"><span class="finding-when">' + esc(a.label) + "</span> · " + esc(AN_TYPES[a.type] || a.type) + "</span>" +
        '<p class="finding-text">' + esc(reason(a)) + "</p></span>" +
        (link ? '<button type="button" class="btn btn-sm btn-quiet" data-explore=\'' + esc(link) + "'>View requests</button>" : "<span></span>") +
        "</div></div>";
    }).join("");
  }

  function initAnomalies() {
    initSegmented("an-method", function (v) { state.an.method = v; reload("anomalies"); });
    initSegmented("an-sensitivity", function (v) { state.an.sensitivity = v; reload("anomalies"); });
    initSegmented("an-filter", function (v) { state.an.filter = v; renderAnomalyList(); });
  }

  // ---------------------------------------------------------------------------
  // Security
  // ---------------------------------------------------------------------------

  var SOURCES = { ai: ["tag-accent", "AI classifier"], rule: ["tag-low", "Behaviour rule"], combined: ["tag-accent", "Rules + AI"] };

  function riskLevel(score) {
    if (score >= 70) return { word: "High risk", tag: "tag-high", cls: "hl-bad" };
    if (score >= 40) return { word: "Moderate risk", tag: "tag-medium", cls: "hl-warn" };
    if (score > 0) return { word: "Low risk", tag: "tag-low", cls: "" };
    return { word: "No threats found", tag: "tag-good", cls: "hl-good" };
  }

  function evidenceItem(e) {
    var what = [e.ip, e.endpoint].filter(Boolean).join(" → ");
    var meta = [];
    if (e.request_count != null) meta.push(num(e.request_count) + " " + plural(e.request_count, "request"));
    if (e.confidence != null) meta.push(Math.round(e.confidence * 100) + "% confident");
    if (e.distinct_paths != null) meta.push(e.distinct_paths + " different paths");
    if (e.error_count != null) meta.push(num(e.error_count) + " errors");
    if (e.status_code != null) meta.push("status " + e.status_code);
    if (Array.isArray(e.reasons)) meta.push(e.reasons.join(" + "));
    var filter = e.endpoint ? { endpoint: e.endpoint.slice(0, 200), ip: e.ip || "" } : e.ip ? { ip: e.ip } : null;
    return '<li><span class="path">' + esc(what.length > 220 ? what.slice(0, 220) + "…" : what) + '</span><span class="meta">' + esc(meta.join(" · ")) + "</span>" +
      (filter ? '<button type="button" class="link-btn" data-explore=\'' + esc(JSON.stringify(filter)) + "'>View requests</button>" : "") + "</li>";
  }

  async function loadSecurity() {
    var current = guard("security");
    $("#se-lede").innerHTML = skeleton(2, "skeleton-lede");
    $("#se-score-value").textContent = "—";
    $("#se-classes").innerHTML = skeleton(5);
    $("#se-findings").innerHTML = '<div style="padding:var(--space-md) 0">' + skeleton(4) + "</div>";
    contextLine("se-context");

    var data;
    try { data = await API.getSecurityInsights(state.active.id); }
    catch (err) {
      if (!current()) return;
      $("#se-lede").textContent = "The security analysis could not be run on this dataset.";
      $("#se-classes").innerHTML = ""; $("#se-findings").innerHTML = "";
      return;
    }
    if (!current()) return;

    var score = data.risk_score || 0, level = riskLevel(score), findings = data.findings || [];
    countUp($("#se-score-value"), score);
    $("#se-score-pin").style.setProperty("--score", "0%");
    requestAnimationFrame(function () { $("#se-score-pin").style.setProperty("--score", Math.min(100, score) + "%"); });

    var order = { high: 0, medium: 1, low: 2 };
    var lead = findings.slice().sort(function (a, b) { return (order[a.severity] - order[b.severity]) || (b.count - a.count); })
      .filter(function (f) { return f.source !== "combined"; }).slice(0, 2)
      .map(function (f) { return "<b>" + esc(f.title.replace(/ detected$/i, "").toLowerCase()) + "</b>"; });
    $("#se-lede").innerHTML = '<span class="' + level.cls + '"><b>' + level.word + ".</b></span> " +
      (findings.length ? findings.length + " " + plural(findings.length, "finding") + " in this log" + (lead.length ? ", led by " + lead.join(" and ") + "." : ".")
                       : "Neither the behaviour rules nor the classifier found anything suspicious in this log.");

    // Classifier breakdown.
    var c = data.classification || {};
    if (!c.available) {
      $("#se-class-desc").textContent = "";
      $("#se-classes").innerHTML = '<div class="notice notice-warn">' + icon("info") + "<span>" + esc(c.note || "The request classifier is not available.") + " Only the behaviour rules were used for this report.</span></div>";
      $("#se-model").textContent = "";
    } else {
      $("#se-class-desc").innerHTML = "It read <b>" + num(c.requests_analysed) + " requests</b> (" + num(c.distinct_urls) + " different URLs) and labelled <b>" +
        num(c.flagged_requests) + "</b> of them (" + c.flagged_share + "%) as attacks." + (c.note ? " " + esc(c.note) : "");
      var largest = Math.max.apply(null, c.classes.map(function (k) { return k.requests; }).concat([1]));
      $("#se-classes").innerHTML = c.classes.map(function (k, i) {
        return '<div class="class-row' + (k.requests ? "" : " is-zero") + '"><span><b>' + esc(k.label) + "</b></span>" +
          '<span class="rank-bar ' + (k.severity === "high" ? "is-bad" : "is-warn") + '" style="--i:' + i + '"><i style="width:' + (k.requests ? Math.max(1.5, k.requests / largest * 100) : 0) + '%"></i></span>' +
          '<span class="num nowrap">' + (k.requests ? "<b>" + num(k.requests) + "</b> " + plural(k.requests, "request") + " · " + num(k.unique_ips) + " " + plural(k.unique_ips, "client") : "none found") + "</span></div>";
      }).join("");
      var m = c.model || {}, bits = [esc(m.algorithm || "Machine-learning classifier")];
      if (m.training_examples) bits.push("trained on " + num(m.training_examples) + " labelled examples");
      if (m.holdout_accuracy != null) bits.push((m.holdout_accuracy * 100).toFixed(1) + "% accurate on attack patterns held out of training");
      bits.push("a request is reported only at " + Math.round((c.threshold || 0.7) * 100) + "% confidence or more");
      $("#se-model").innerHTML = bits.join(" · ") + ".";
    }

    // Findings, most severe first.
    $("#se-findings").innerHTML = findings.length ? findings.map(function (f, i) {
      var sev = f.severity === "high" ? ["tag-high", "High"] : ["tag-medium", "Medium"];
      var src = SOURCES[f.source] || SOURCES.rule, ev = f.evidence || [];
      return '<details class="finding row-enter" style="--i:' + i + '"><summary>' +
        '<span><span class="tag ' + sev[0] + '">' + sev[1] + "</span></span>" +
        '<span><span class="finding-title">' + esc(f.title) + ' <span class="tag tag-plain ' + src[0] + '">' + src[1] + "</span></span>" +
        '<p class="finding-text">' + esc(f.description) + "</p></span>" + icon("chevron-right") + "</summary>" +
        '<div class="finding-body">' + (ev.length ? '<p class="label-sm">Evidence' + (ev.length >= 10 ? " (first " + ev.length + ")" : "") + '</p><ul class="evidence">' + ev.map(evidenceItem).join("") + "</ul>"
                                                 : '<p class="faint">No itemised evidence for this finding.</p>') + "</div></details>";
    }).join("") : '<p class="faint" style="padding:var(--space-md) var(--space-xs)">No findings.</p>';
  }

  // ---------------------------------------------------------------------------
  // Compare
  // ---------------------------------------------------------------------------

  async function loadCompare() {
    var ready = completed(), a = $("#cmp-a"), b = $("#cmp-b");
    var options = ready.map(function (d) { return '<option value="' + esc(d.id) + '">' + esc(d.name) + "</option>"; }).join("");
    var keepA = a.value, keepB = b.value;
    a.innerHTML = options; b.innerHTML = options;
    var has = function (id) { return ready.some(function (d) { return d.id === id; }); };
    a.value = has(keepA) ? keepA : (state.active ? state.active.id : (ready[0] || {}).id || "");
    var other = ready.filter(function (d) { return d.id !== a.value; })[0];
    b.value = has(keepB) && keepB !== a.value ? keepB : (other ? other.id : "");

    if (ready.length < 2) {
      $("#cmp-results").hidden = true; $("#cmp-empty").hidden = false;
      $("#cmp-empty-title").textContent = "Two datasets are needed";
      $("#cmp-empty-text").innerHTML = 'Comparison sets one log against another. Upload a second access log on the <a href="#/datasets">Datasets</a> page to use it.';
      return;
    }
    await runCompare();
  }

  function delta(value, unit, badWhenUp) {
    if (value == null || isNaN(value)) return '<span class="delta-flat">—</span>';
    var n = Number(value);
    if (Math.abs(n) < 0.05) return '<span class="delta-flat">no change</span>';
    var colour = badWhenUp ? (n > 0 ? "color:var(--bad)" : "color:var(--good)") : "";
    return '<span class="' + (n > 0 ? "delta-up" : "delta-down") + '" style="' + colour + '">' + Math.abs(n).toFixed(1) + unit + " " + (n > 0 ? "higher" : "lower") + "</span>";
  }

  async function runCompare() {
    var current = guard("compare"), idA = $("#cmp-a").value, idB = $("#cmp-b").value;
    var results = $("#cmp-results"), empty = $("#cmp-empty");
    if (!idA || !idB || idA === idB) {
      results.hidden = true; empty.hidden = false;
      $("#cmp-empty-title").textContent = "Choose two different datasets";
      $("#cmp-empty-text").textContent = "The baseline and the comparison are currently the same log.";
      return;
    }
    var data;
    try { data = await API.compareDatasets(idA, idB); }
    catch (err) { toast(err.message || "Comparison failed.", "bad"); return; }
    if (!current()) return;

    empty.hidden = true; results.hidden = false;
    var A = data.dataset_a, B = data.dataset_b, ma = A.metrics || {}, mb = B.metrics || {}, d = data.deltas || {};
    var visA = ma.unique_visitors != null ? ma.unique_visitors : ma.unique_ips, visB = mb.unique_visitors != null ? mb.unique_visitors : mb.unique_ips;
    $("#cmp-name-a").textContent = A.name; $("#cmp-name-b").textContent = B.name;
    $("#cmp-legend-a").textContent = A.name; $("#cmp-legend-b").textContent = B.name;
    $("#cmp-top-a-title").textContent = "Most requested in " + A.name;
    $("#cmp-top-b-title").textContent = "Most requested in " + B.name;

    var vol = Number(d.total_requests || 0), err = Number(d.error_rate || 0);
    $("#cmp-lede").innerHTML = "<b>" + esc(B.name) + "</b> handled " +
      (Math.abs(vol) < 0.05 ? "about the same number of requests as" : '<span class="hl">' + Math.abs(vol).toFixed(0) + "% " + (vol > 0 ? "more" : "fewer") + " requests</span> than") +
      " <b>" + esc(A.name) + "</b>, and its error rate is " +
      (Math.abs(err) < 0.05 ? "the same" : '<span class="' + (err > 0 ? "hl-bad" : "hl-good") + '">' + Math.abs(err).toFixed(1) + " points " + (err > 0 ? "higher" : "lower") + "</span>") +
      " (" + pct(mb.error_rate) + " against " + pct(ma.error_rate) + ").";

    var rows = [
      ["Requests", num(ma.total_requests), num(mb.total_requests), delta(d.total_requests, "%")],
      ["Error rate", pct(ma.error_rate, 2), pct(mb.error_rate, 2), delta(d.error_rate, " pts", true)],
      ["Unique visitors", num(visA), num(visB), delta(d.unique_visitors, "%")],
      ["Average response", bytes(ma.avg_response_size), bytes(mb.avg_response_size), delta(d.avg_response_size, "%")],
    ];
    $("#cmp-body").innerHTML = rows.map(function (r) {
      return "<tr><td><b>" + r[0] + '</b></td><td class="r">' + r[1] + '</td><td class="r">' + r[2] + '</td><td class="r">' + r[3] + "</td></tr>";
    }).join("");

    var hourly = function (set) {
      var t = set.hourly_traffic || {};
      if (Array.isArray(t.traffic)) return t.traffic.map(function (x) { return x.count; });
      return Array.from({ length: 24 }, function (_, i) { return t[String(i)] || 0; });
    };
    var labels = Array.from({ length: 24 }, function (_, i) { return pad(i) + ":00"; });
    Charts.compare("chart-compare", labels, hourly(A), hourly(B), A.name, B.name);

    var top = function (set, cls) {
      var raw = set.top_endpoints || [], items = Array.isArray(raw) ? raw : (raw.endpoints || []);
      items = items.slice(0, 8);
      var max = items.length ? items[0].count : 1;
      return items.length ? items.map(function (e, i) {
        return '<li style="--i:' + i + '"><span class="rank-label truncate">' + esc(e.endpoint) + '</span><span class="rank-value"><b>' + num(e.count) +
          '</b></span><span class="rank-bar ' + cls + '"><i style="width:' + (e.count / max * 100) + '%"></i></span></li>';
      }).join("") : '<li class="faint" style="display:block">No requests.</li>';
    };
    $("#cmp-top-a").innerHTML = top(A, "");
    $("#cmp-top-b").innerHTML = top(B, "is-warn");
  }

  function initCompare() {
    $("#cmp-a").addEventListener("change", function () { busy(runCompare()); });
    $("#cmp-b").addEventListener("change", function () { busy(runCompare()); });
  }

  // ---------------------------------------------------------------------------
  // Reports
  // ---------------------------------------------------------------------------

  function loadReports() { return contextLine("rp-context"); }
  function initReports() {
    $("#rp-download").addEventListener("click", function () {
      var format = ($('input[name="rp-format"]:checked') || {}).value || "csv";
      try {
        API.downloadReport(state.active.id, format);
        toast("Your " + format.toUpperCase() + " report for “" + state.active.name + "” is downloading.", "good");
      } catch (err) { toast(err.message || "The report could not be downloaded.", "bad"); }
    });
  }

  // ---------------------------------------------------------------------------
  // Notifications
  // ---------------------------------------------------------------------------

  async function pollNotifications() {
    try {
      var data = await API.getNotifCount(), n = data.unread_count || 0, badge = $("#notif-badge");
      badge.hidden = n === 0;
      badge.textContent = n > 99 ? "99+" : n;
      $("#notif-btn").setAttribute("aria-label", n ? "Notifications, " + n + " unread" : "Notifications");
    } catch (_) {}
  }

  async function loadNotifications() {
    var list = $("#notif-list");
    list.innerHTML = '<div style="padding:var(--space-sm)">' + skeleton(3) + "</div>";
    try {
      var items = (await API.getNotifications()).notifications || [];
      list.innerHTML = items.length ? items.map(function (n) {
        return '<div class="notif' + (n.is_read ? "" : " is-unread") + '" data-id="' + n.id + '"><span class="notif-mark" aria-hidden="true"></span>' +
          '<span><span class="notif-text">' + esc(n.message) + '</span><br /><span class="notif-time">' + (n.is_read ? "" : "Unread · ") + esc(ago(n.created_at)) + "</span></span>" +
          '<button type="button" class="icon-btn" style="width:28px;height:28px" data-delete-notif aria-label="Delete notification">' + icon("x", "icon-sm") + "</button></div>";
      }).join("") : '<p class="faint" style="padding:var(--space-lg) var(--space-md);font-size:var(--text-sm)">Nothing yet. You will be told here when a log finishes processing.</p>';
    } catch (_) {
      list.innerHTML = '<p class="field-error" style="padding:var(--space-md)">Notifications could not be loaded.</p>';
    }
  }

  function initNotifications() {
    bindPopover($("#notif-btn"), $("#notif-popover"), loadNotifications);
    $("#notif-list").addEventListener("click", async function (e) {
      var row = e.target.closest(".notif");
      if (!row) return;
      if (e.target.closest("[data-delete-notif]")) { await API.deleteNotif(row.dataset.id); row.remove(); }
      else if (row.classList.contains("is-unread")) { await API.markNotifRead(row.dataset.id); row.classList.remove("is-unread"); }
      pollNotifications();
    });
    $("#notif-read-all").addEventListener("click", async function () { await API.markAllNotifsRead(); loadNotifications(); pollNotifications(); });
    $("#notif-clear").addEventListener("click", async function () { await API.deleteAllNotifs(); loadNotifications(); pollNotifications(); });
    pollNotifications();
    setInterval(pollNotifications, 30000);
  }

  // ---------------------------------------------------------------------------
  // Shell wiring and boot
  // ---------------------------------------------------------------------------

  var LOADERS = {
    overview: loadOverview,
    datasets: function () { return loadDatasets(true); },   // always fresh: statuses change
    explore: loadExplore,
    forecast: loadForecast,
    anomalies: loadAnomalies,
    security: loadSecurity,
    compare: loadCompare,
    reports: loadReports,
  };

  function initShell() {
    bindPopover($("#ds-button"), $("#ds-popover"));
    $("#ds-list").addEventListener("click", function (e) {
      var option = e.target.closest("[data-id]");
      if (!option) return;
      var d = state.datasets.filter(function (x) { return x.id === option.dataset.id; })[0];
      closePopover();
      if (d && (!state.active || d.id !== state.active.id)) setActive(d);
    });
    $("#ds-popover").addEventListener("click", function (e) { if (e.target.closest("a")) closePopover(); });

    $("#menu-btn").addEventListener("click", function () {
      var open = $("#shell").classList.toggle("nav-open");
      this.setAttribute("aria-expanded", String(open));
    });
    $("#scrim").addEventListener("click", function () { $("#shell").classList.remove("nav-open"); });

    // Global search goes straight to the matching requests.
    $("#global-search-form").addEventListener("submit", function (e) {
      e.preventDefault();
      var input = $("#global-search"), q = input.value.trim();
      if (!q) return;
      if (!state.active) { toast("Open a dataset first, then search its requests.", "bad"); return; }
      input.value = ""; input.blur();
      openExplore({ q: q });
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey) return;
      if (/^(INPUT|TEXTAREA|SELECT)$/.test(document.activeElement.tagName)) return;
      e.preventDefault();
      $("#global-search").focus();
    });
  }

  async function boot() {
    state.hidden = load(STORE.hidden, []);
    initShell();
    initOverview();
    initDatasets();
    initExplore();
    initForecast();
    initAnomalies();
    initCompare();
    initReports();
    initNotifications();

    (window.LOGSIGHT_MESSAGES || []).forEach(function (m) { toast(m.text, /error/.test(m.level) ? "bad" : "good"); });

    API.getPreferences().then(function (prefs) {
      if (prefs && Array.isArray(prefs.hidden_widgets) && prefs.hidden_widgets.length) {
        state.hidden = prefs.hidden_widgets; save(STORE.hidden, state.hidden); applyHiddenSections();
      }
    }).catch(function () {});

    try { await busy(refreshDatasets(false)); } catch (_) { toast("Your datasets could not be loaded. Check the connection and reload.", "bad"); }
    showRoute(parseHash());
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(function () { moveNavMarker(); syncAllSegmented(); });
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot); else boot();
})();
