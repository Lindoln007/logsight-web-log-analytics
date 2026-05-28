/**
 * LogSight SPA — routing, top navigation, uploads, datasets,
 * dashboard, log viewer, forecasting, anomaly detection, alerts, reports.
 */
(function () {
  "use strict";

  var PREFS_KEY    = "log-sight-prefs";           // localStorage fallback for preferences
  var routes       = ["dashboard", "upload", "datasets", "viewer",
                      "forecast", "anomalies", "alerts", "reports",
                      "security", "compare", "search", "simulate"];

  var viewerPage             = 1;
  var viewerSearchDebounce;
  var pendingViewerDatasetId = "";

  // -------------------------------------------------------------------------
  // DOM helpers
  // -------------------------------------------------------------------------

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function escapeHtml(value) {
    var div = document.createElement("div");
    div.textContent = value == null ? "" : String(value);
    return div.innerHTML;
  }

  function formatBytes(value) {
    var n = Number(value || 0);
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(2) + " MB";
    return (n / (1024 * 1024 * 1024)).toFixed(2) + " GB";
  }

  function formatDate(iso) {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }); }
    catch (_) { return iso; }
  }

  function formatNumber(value) {
    if (value == null) return null;
    return Number(value).toLocaleString();
  }

  function renderStatValue(id, value, suffix, fallback) {
    var el = $("#" + id);
    if (!el) return;
    if (value == null || value === "") { el.textContent = fallback || "—"; return; }
    el.textContent = String(value) + (suffix || "");
  }

  function renderSubText(id, text) {
    var el = $("#" + id);
    if (el) el.textContent = text || "";
  }

  function setTableMessage(tbody, message, colspan) {
    if (!tbody) return;
    tbody.innerHTML =
      '<tr><td colspan="' + (colspan || 6) + '" class="px-4 py-8 text-center text-slate-500 dark:text-slate-400">' +
      escapeHtml(message) + "</td></tr>";
  }

  function buildStatusBadge(status) {
    var v = String(status || "pending").toLowerCase();
    if (v === "completed")  return "border border-emerald-200 bg-emerald-50 text-emerald-700 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300";
    if (v === "failed")     return "border border-red-200 bg-red-50 text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300";
    if (v === "processing") return "border border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300";
    return "border border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300";
  }

  function severityBadge(sev) {
    if (sev === "high")   return "border border-red-200 bg-red-50 text-red-700 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300";
    if (sev === "medium") return "border border-amber-200 bg-amber-50 text-amber-700 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300";
    return "border border-slate-200 bg-slate-50 text-slate-600 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400";
  }

  // Convert an ISO timestamp to the value expected by <input type="datetime-local">
  // Format: YYYY-MM-DDTHH:MM
  function isoToDatetimeLocal(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      var pad = function (n) { return String(n).padStart(2, "0"); };
      return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) +
        "T" + pad(d.getHours()) + ":" + pad(d.getMinutes());
    } catch (_) { return ""; }
  }

  // -------------------------------------------------------------------------
  // Theme
  // -------------------------------------------------------------------------

  function initTheme() {
    localStorage.setItem("log-sight-theme", "light");
    applyTheme(false);
  }

  function applyTheme(dark) {
    document.documentElement.classList.remove("dark");
    var meta = $("#theme-color-meta");
    if (meta) meta.setAttribute("content", "#eef2f5");
  }

  // -------------------------------------------------------------------------
  // Top navigation
  // -------------------------------------------------------------------------

  function initTopNavigation() {
    var nav = $(".top-nav");
    var bubble = $("#nav-hover-bubble");
    var mobileBtn = $("#mobile-nav-toggle");
    var mobilePanel = $("#mobile-nav-panel");

    function moveBubble(target) {
      if (!nav || !bubble || !target) return;
      var navBox = nav.getBoundingClientRect();
      var box = target.getBoundingClientRect();
      bubble.style.left = (box.left - navBox.left) + "px";
      bubble.style.width = box.width + "px";
      bubble.style.opacity = "1";
    }

    if (nav && bubble) {
      $all("[data-nav-item]", nav).forEach(function (item) {
        var target = item.classList.contains("top-nav-group") ? $(".top-nav-link", item) : item;
        item.addEventListener("mouseenter", function () { moveBubble(target); });
        item.addEventListener("focusin", function () { moveBubble(target); });
      });
      nav.addEventListener("mouseleave", function () {
        var active = $(".top-nav-link.is-active-route", nav);
        if (active) moveBubble(active);
        else bubble.style.opacity = "0";
      });
      window.addEventListener("resize", function () {
        var active = $(".top-nav-link.is-active-route", nav);
        if (active) moveBubble(active);
      });
    }

    function closeMobileNav() {
      if (mobilePanel) mobilePanel.classList.remove("is-open");
    }

    if (mobileBtn && mobilePanel) {
      mobileBtn.addEventListener("click", function () {
        mobilePanel.classList.toggle("is-open");
      });
    }

    function closeDropdowns() {
      $all(".top-nav-group").forEach(function (group) {
        group.classList.add("is-closing");
        setTimeout(function () { group.classList.remove("is-closing"); }, 180);
      });
      if (document.activeElement && typeof document.activeElement.blur === "function") {
        document.activeElement.blur();
      }
    }

    window.closeMobileSidebar = closeMobileNav;
    window.closeTopNavMenus = closeDropdowns;
    window.refreshNavBubble = function () {
      var active = $(".top-nav-link.is-active-route", nav);
      if (active) moveBubble(active);
    };
  }

  // -------------------------------------------------------------------------
  // Routing
  // -------------------------------------------------------------------------

  function getHashRoute() {
    var value = (window.location.hash || "#/dashboard").replace(/^#\/?/, "").split("/")[0];
    return routes.indexOf(value) >= 0 ? value : "dashboard";
  }

  function setHashRoute(name) {
    var next = routes.indexOf(name) >= 0 ? name : "dashboard";
    var hash = "#/" + next;
    if (window.location.hash === hash) runRoute(next);
    else window.location.hash = hash;
  }

  function updateNavActive(route) {
    $all("[data-route]").forEach(function (link) {
      var active = link.getAttribute("data-route") === route;
      link.classList.toggle("is-active-route",     active);
    });
    var section = "";
    if (["upload", "datasets", "viewer"].indexOf(route) >= 0) section = "data";
    if (["forecast", "anomalies", "reports", "compare", "search"].indexOf(route) >= 0) section = "analytics";
    if (["alerts", "security", "simulate"].indexOf(route) >= 0) section = "monitoring";
    $all("[data-nav-section]").forEach(function (group) {
      var active = group.getAttribute("data-nav-section") === section;
      var trigger = $(".top-nav-link", group);
      if (trigger) trigger.classList.toggle("is-active-route", active);
    });
    if (typeof window.refreshNavBubble === "function") window.refreshNavBubble();
  }

  function showView(route) {
    // Destroy dashboard charts when leaving dashboard.
    if (route !== "dashboard" && typeof LogSightCharts !== "undefined") LogSightCharts.destroyAll();
    $all("[data-view]").forEach(function (section) {
      var on = section.getAttribute("data-view") === route;
      section.classList.toggle("hidden", !on);
      if (on) {
        section.classList.remove("view-enter");
        void section.offsetWidth;
        section.classList.add("view-enter");
      }
    });
    var titles = {
      dashboard: "Dashboard",
      upload:    "Upload Logs",
      datasets:  "Datasets",
      viewer:    "Log Viewer",
      forecast:  "Traffic Forecasting",
      anomalies: "Anomaly Detection",
      alerts:    "Alerts",
      reports:   "Reports",
      security:  "Security Insights",
      compare:   "Dataset Comparison",
      search:    "Advanced Search",
      simulate:  "Live Ingestion",
    };
    var titleEl = $("#page-title");
    if (titleEl) titleEl.textContent = titles[route] || "Dashboard";
    var period = $("#stat-period");
    if (period) period.classList.toggle("hidden", ["viewer", "forecast", "anomalies", "reports", "compare", "security"].indexOf(route) < 0);
    updateNavActive(route);
  }

  function runRoute(route) {
    showView(route);
    if (route === "dashboard")  loadDashboardData();
    if (route === "datasets")   loadDatasetsTable();
    if (route === "viewer")     initLogViewer();
    if (route === "forecast")   initForecastView();
    if (route === "anomalies")  initAnomalyView();
    if (route === "alerts")     initAlertsView();
    if (route === "reports")    initReportsView();
    if (route === "security")   initSecurityView();
    if (route === "compare")    initCompareView();
    if (route === "search")     initSearchView();
    if (route === "simulate")   initSimulateView();
  }

  function navigate(route) { setHashRoute(route); }

  window.addEventListener("hashchange", function () { runRoute(getHashRoute()); });

  // -------------------------------------------------------------------------
  // Shared: populate a <select> with completed datasets
  // -------------------------------------------------------------------------

  async function populateSelect(selectId, includeAll) {
    var select = $("#" + selectId);
    if (!select) return [];
    var current = select.value;
    var rows    = await LogSightAPI.listDatasets();
    var completed = rows.filter(function (d) { return d.status === "completed"; });
    var others    = rows.filter(function (d) { return d.status !== "completed"; });

    select.innerHTML = '<option value="">' + (includeAll ? "— Select dataset —" : "Select…") + "</option>";
    completed.concat(others).forEach(function (d) {
      var opt       = document.createElement("option");
      opt.value     = d.id;
      opt.textContent = d.name + (d.status !== "completed" ? " (" + d.status + ")" : "");
      select.appendChild(opt);
    });
    if (current && rows.some(function (r) { return r.id === current; })) select.value = current;
    return rows;
  }

  // -------------------------------------------------------------------------
  // Dashboard — dataset selector
  // -------------------------------------------------------------------------

  async function populateDashboardSelect() {
    var select = $("#dashboard-dataset");
    if (!select) return [];

    var current = LogSightAPI.getCurrentDataset() || select.value;
    var rows    = await LogSightAPI.listDatasets();
    var completed = rows.filter(function (d) { return d.status === "completed"; });
    var others    = rows.filter(function (d) { return d.status !== "completed"; });

    select.innerHTML = '<option value="">— Select dataset —</option>';
    completed.concat(others).forEach(function (d) {
      var opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.name + (d.status !== "completed" ? " (" + d.status + ")" : "");
      select.appendChild(opt);
    });

    if (current && rows.some(function (r) { return r.id === current; })) {
      select.value = current;
    } else if (completed.length > 0) {
      select.value = completed[0].id;
    }

    LogSightAPI.setCurrentDataset(select.value || null);
    return rows;
  }

  // -------------------------------------------------------------------------
  // Dashboard — preferences (widget visibility)
  // -------------------------------------------------------------------------

  function loadPrefsFromStorage() {
    try {
      var raw = localStorage.getItem(PREFS_KEY);
      return raw ? JSON.parse(raw) : { hidden_widgets: [] };
    } catch (_) { return { hidden_widgets: [] }; }
  }

  function savePrefsToStorage(prefs) {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) {}
  }

  function applyWidgetVisibility(hiddenWidgets) {
    var hidden = Array.isArray(hiddenWidgets) ? hiddenWidgets : [];
    $all(".widget-toggle").forEach(function (cb) {
      var widgetId = cb.getAttribute("data-widget");
      cb.checked = hidden.indexOf(widgetId) === -1;
    });
    $all("[id^='widget-']").forEach(function (el) {
      var widgetId = el.id.replace("widget-", "");
      el.classList.toggle("hidden", hidden.indexOf(widgetId) !== -1);
    });
  }

  function initDashboardCustomize() {
    var btn   = $("#dashboard-customize-btn");
    var panel = $("#dashboard-customize-panel");
    var save  = $("#dashboard-customize-save");
    if (!btn || !panel || !save) return;

    // Load saved prefs.
    var prefs = loadPrefsFromStorage();
    applyWidgetVisibility(prefs.hidden_widgets || []);

    btn.addEventListener("click", function () {
      panel.classList.toggle("hidden");
    });

    save.addEventListener("click", async function () {
      var hidden = [];
      $all(".widget-toggle").forEach(function (cb) {
        if (!cb.checked) hidden.push(cb.getAttribute("data-widget"));
      });
      applyWidgetVisibility(hidden);
      var newPrefs = { hidden_widgets: hidden };
      savePrefsToStorage(newPrefs);
      // Also persist to backend.
      try { await LogSightAPI.savePreferences(newPrefs); } catch (_) {}
      panel.classList.add("hidden");
    });
  }

  // -------------------------------------------------------------------------
  // Dashboard — data loading
  // -------------------------------------------------------------------------

  async function loadDashboardData() {
    renderStatValue("stat-requests", null, "", "Loading…");
    renderStatValue("stat-errors",   null, "", "Loading…");
    renderStatValue("stat-latency",  null, "", "Loading…");
    renderStatValue("stat-visitors", null, "", "Loading…");

    await populateDashboardSelect();
    var id = LogSightAPI.getCurrentDataset();

    if (!id) {
      renderStatValue("stat-requests", null, "", "No datasets");
      renderStatValue("stat-errors",   null, "", "—");
      renderStatValue("stat-latency",  null, "", "—");
      renderStatValue("stat-visitors", null, "", "—");
      if (typeof LogSightCharts !== "undefined") LogSightCharts.destroyAll();
      return;
    }

    var summary = await LogSightAPI.getDashboardSummary(id);

    renderStatValue("stat-requests", summary.totalRequests != null ? formatNumber(summary.totalRequests) : null, "", "No data");
    renderStatValue("stat-errors",   summary.errorRate    != null ? Number(summary.errorRate).toFixed(2) : null, "%", "No data");
    renderStatValue("stat-latency",  summary.avgResponseSize != null ? formatNumber(summary.avgResponseSize) : null, " B", "No data");
    renderStatValue("stat-visitors", summary.uniqueVisitors != null ? formatNumber(summary.uniqueVisitors) : null, "", "No data");

    if (summary.totalErrors != null) renderSubText("stat-errors-abs", formatNumber(summary.totalErrors));

    loadDashboardCharts(id);
  }

  async function loadDashboardCharts(datasetId) {
    var id = datasetId || LogSightAPI.getCurrentDataset();
    LogSightCharts.destroyAll();
    if (!id) return;

    var results = await Promise.all([
      LogSightAPI.getRequestFrequency(id),
      LogSightAPI.getStatusDistribution(id),
      LogSightAPI.getTopEndpoints(id),
      LogSightAPI.getTrafficPattern(id),
    ]);

    requestAnimationFrame(function () {
      LogSightCharts.renderRequestFrequency("chart-requests", results[0]);
      LogSightCharts.renderStatusDoughnut("chart-status",     results[1]);
      LogSightCharts.renderTopEndpoints("chart-endpoints",    results[2]);
      LogSightCharts.renderTrafficArea("chart-traffic",       results[3]);
    });
  }

  async function updateStatPeriod(datasetId) {
    var period = $("#stat-period");
    if (!period) return;
    if (!datasetId) { period.textContent = "Select a dataset"; return; }
    try {
      var result = await fetch(
        "/logs/datasets/" + encodeURIComponent(datasetId) + "/analytics/summary/",
        { credentials: "same-origin" }
      );
      if (!result.ok) return;
      var data = await result.json();
      var dr = data.date_range || {};
      if (dr.start && dr.end) {
        period.textContent = formatDate(dr.start) + " – " + formatDate(dr.end);
      } else if (data.periodLabel) {
        period.textContent = data.periodLabel;
      }
    } catch (_) {}
  }

  function initDashboardControls() {
    var select = $("#dashboard-dataset");
    if (select && !select._bound) {
      select._bound = true;
      select.addEventListener("change", function () {
        LogSightAPI.setCurrentDataset(select.value || null);
        loadDashboardData();
      });
    }

    var refreshBtn = $("#dashboard-refresh");
    if (refreshBtn && !refreshBtn._bound) {
      refreshBtn._bound = true;
      refreshBtn.addEventListener("click", async function () {
        var id = LogSightAPI.getCurrentDataset();
        if (!id) return;
        refreshBtn.disabled = true;
        refreshBtn.textContent = "Refreshing…";
        try { await LogSightAPI.refreshAnalytics(id); } catch (_) {}
        refreshBtn.disabled = false;
        refreshBtn.innerHTML =
          '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
          'd="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0' +
          'a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg> Refresh';
        loadDashboardData();
      });
    }

    initDashboardCustomize();
  }

  // -------------------------------------------------------------------------
  // Upload
  // -------------------------------------------------------------------------

  function initUpload() {
    var zone  = $("#drop-zone");
    var input = $("#file-input");
    var list  = $("#upload-queue");
    var hint  = $("#upload-hint");
    if (!zone || !input || !list) return;

    LogSightAPI.getUploadConfig().then(function (cfg) {
      if (!cfg || !hint) return;
      var maxSize = cfg.max_upload_size ? formatBytes(cfg.max_upload_size) : "server limit";
      hint.textContent = "Accepted: Apache / Nginx access logs (.log, .txt, .gz). Max size: " + maxSize + ".";
    });

    function handleFiles(files) {
      if (!files || !files.length) return;
      Array.prototype.forEach.call(files, queueUpload);
    }

    zone.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () { handleFiles(input.files); input.value = ""; });

    ["dragenter", "dragover", "dragleave", "drop"].forEach(function (ev) {
      zone.addEventListener(ev, function (e) { e.preventDefault(); e.stopPropagation(); });
    });
    zone.addEventListener("dragenter", function () {
      zone.classList.add("border-indigo-500", "bg-indigo-50", "dark:bg-indigo-950/40", "drop-zone-active");
    });
    zone.addEventListener("dragleave", function (e) {
      if (!zone.contains(e.relatedTarget))
        zone.classList.remove("border-indigo-500", "bg-indigo-50", "dark:bg-indigo-950/40", "drop-zone-active");
    });
    zone.addEventListener("drop", function (e) {
      zone.classList.remove("border-indigo-500", "bg-indigo-50", "dark:bg-indigo-950/40", "drop-zone-active");
      handleFiles(e.dataTransfer.files);
    });

    function queueUpload(file) {
      var row = document.createElement("div");
      row.className = "rounded-xl border border-slate-200 bg-white p-4 shadow-sm transition-all dark:border-slate-700 dark:bg-slate-900/80";
      row.innerHTML =
        '<div class="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">' +
        '<div class="min-w-0"><p class="truncate font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(file.name) + "</p>" +
        '<p class="text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(formatBytes(file.size)) + "</p></div>" +
        '<div class="w-full sm:w-56"><div class="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">' +
        '<div class="progress-shimmer h-full w-full rounded-full bg-indigo-500"></div></div>' +
        '<p class="mt-1 text-xs text-slate-500 dark:text-slate-400 upload-status">Uploading…</p>' +
        "</div></div>";
      list.prepend(row);

      var statusEl = $(".upload-status", row);

      LogSightAPI.uploadDataset(file, {
        onProgress: function () {
          if (statusEl) statusEl.textContent = "Parsing… this may take a moment for large files.";
        },
      }).then(function (dataset) {
        var shimmer = $(".progress-shimmer", row);
        if (shimmer) { shimmer.classList.remove("progress-shimmer"); shimmer.classList.add("bg-emerald-500"); }
        row.classList.add("ring-2", "ring-emerald-500/30");
        LogSightAPI.setCurrentDataset(dataset.id);
        if (getHashRoute() === "datasets") loadDatasetsTable();
        populateDashboardSelect().then(function () {
          if (statusEl) {
            var entries = dataset.totalEntries ? " (" + Number(dataset.totalEntries).toLocaleString() + " entries)" : "";
            statusEl.textContent = "Complete" + entries;
          }
        });
      }).catch(function (err) {
        if (statusEl) statusEl.textContent = err.message || "Upload failed.";
        var shimmer = $(".progress-shimmer", row);
        if (shimmer) { shimmer.classList.remove("progress-shimmer", "bg-indigo-500"); shimmer.classList.add("bg-red-500"); }
        row.classList.add("ring-2", "ring-red-500/20");
      });
    }
  }

  // -------------------------------------------------------------------------
  // Datasets table
  // -------------------------------------------------------------------------

  async function loadDatasetsTable() {
    var tbody = $("#datasets-body");
    if (!tbody) return;
    setTableMessage(tbody, "Loading datasets…", 7);

    var rows = await LogSightAPI.listDatasets();
    tbody.innerHTML = "";

    if (!rows.length) {
      setTableMessage(tbody, "No datasets yet. Upload a log file to get started.", 7);
      return;
    }

    rows.forEach(function (dataset, index) {
      var tr = document.createElement("tr");
      tr.className = "table-row-animate border-b border-slate-100 transition-colors hover:bg-cyan-50/60 dark:border-slate-800 dark:hover:bg-cyan-950/20";
      tr.style.animationDelay = index * 40 + "ms";

      var entriesText = dataset.totalEntries != null ? Number(dataset.totalEntries).toLocaleString() : "—";

      tr.innerHTML =
        '<td class="px-4 py-3 font-mono text-xs text-slate-600 dark:text-slate-400">' + escapeHtml(dataset.id) + "</td>" +
        '<td class="px-4 py-3 font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(dataset.name) + "</td>" +
        '<td class="px-4 py-3"><span class="inline-flex items-center rounded border border-slate-200 bg-slate-50 px-2 py-0.5 text-xs font-medium text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300">' + escapeHtml(dataset.source) + "</span></td>" +
        '<td class="px-4 py-3 text-slate-600 dark:text-slate-400">' + escapeHtml(formatBytes(dataset.sizeBytes)) + "</td>" +
        '<td class="px-4 py-3 tabular-nums text-slate-600 dark:text-slate-400">' + escapeHtml(entriesText) + "</td>" +
        '<td class="px-4 py-3 text-slate-600 dark:text-slate-400">' + escapeHtml(formatDate(dataset.uploadedAt)) + "</td>" +
        '<td class="px-4 py-3 text-right whitespace-nowrap">' +
          '<div class="inline-flex items-center gap-2">' +
          '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ' + buildStatusBadge(dataset.status) + '">' + escapeHtml(dataset.status) + "</span>" +
          '<button type="button" data-analyse-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-cyan-200 px-2.5 py-1 text-xs font-medium text-cyan-700 transition-colors hover:bg-cyan-50 dark:border-cyan-900/60 dark:text-cyan-300 dark:hover:bg-cyan-950/40"' +
          (dataset.status !== "completed" ? ' disabled title="Dataset not yet completed"' : "") + ">Analyse</button>" +
          '<button type="button" data-view-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800/80">View</button>' +
          '<button type="button" data-del-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-red-200 px-2.5 py-1 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 dark:border-red-900/50 dark:text-red-400 dark:hover:bg-red-950/30">Delete</button>' +
          "</div></td>";
      tbody.appendChild(tr);
    });

    tbody.querySelectorAll("[data-analyse-ds]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        LogSightAPI.setCurrentDataset(btn.getAttribute("data-analyse-ds"));
        setHashRoute("dashboard");
      });
    });

    tbody.querySelectorAll("[data-view-ds]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        pendingViewerDatasetId = btn.getAttribute("data-view-ds") || "";
        setHashRoute("viewer");
      });
    });

    tbody.querySelectorAll("[data-del-ds]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var dsId = btn.getAttribute("data-del-ds");
        if (!confirm("Permanently delete this dataset and all its log entries?")) return;
        try {
          await LogSightAPI.deleteDataset(dsId);
          await loadDatasetsTable();
          var viewerSelect = $("#viewer-dataset");
          if (viewerSelect && viewerSelect.value === dsId) {
            await populateViewerSelect();
            viewerPage = 1;
            fetchAndRenderLogs();
          }
          if (getHashRoute() === "dashboard") await populateDashboardSelect();
        } catch (err) {
          window.alert(err.message || "Deletion failed.");
        }
      });
    });
  }

  // -------------------------------------------------------------------------
  // Log viewer
  // -------------------------------------------------------------------------

  function setLogsLoadingMessage(message) {
    var tbody = $("#logs-body");
    var meta  = $("#logs-meta");
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500 dark:text-slate-400"><span class="loading-inline">' + escapeHtml(message) + "</span></td></tr>";
    if (meta) meta.textContent = "";
    updateLogPagination(true, true);
  }

  function setLogsMessage(message) {
    var tbody = $("#logs-body");
    var meta  = $("#logs-meta");
    if (tbody) tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500 dark:text-slate-400">' + escapeHtml(message) + "</td></tr>";
    if (meta) meta.textContent = "";
    updateLogPagination(true, true);
  }

  function updateLogPagination(prevDisabled, nextDisabled) {
    var prev = $("#logs-prev");
    var next = $("#logs-next");
    if (prev) prev.disabled = !!prevDisabled;
    if (next) next.disabled = !!nextDisabled;
  }

  async function populateViewerSelect() {
    var select = $("#viewer-dataset");
    if (!select) return [];

    var current = pendingViewerDatasetId || select.value;
    select.innerHTML = '<option value="">Loading…</option>';
    var rows = await LogSightAPI.listDatasets();
    select.innerHTML = '<option value="">Select a dataset…</option>';

    rows.forEach(function (d) {
      var opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.name + " (" + d.status + ")";
      select.appendChild(opt);
    });

    if (current && rows.some(function (r) { return r.id === current; })) select.value = current;
    pendingViewerDatasetId = "";
    return rows;
  }

  // ── Date range fix: set picker min/max/value from dataset analytics summary ──
  async function setViewerDateRange(datasetId) {
    if (!datasetId) return;
    var fromInput = $("#viewer-date-from");
    var toInput   = $("#viewer-date-to");
    var hint      = $("#viewer-date-hint");
    if (!fromInput || !toInput) return;

    try {
      var summary = await LogSightAPI.getDashboardSummary(datasetId);
      if (!summary.available) return;

      // getDashboardSummary exposes periodLabel but we need raw ISO dates.
      // Fetch the analytics summary endpoint directly for date_range.
      var result = await fetch(
        "/logs/datasets/" + encodeURIComponent(datasetId) + "/analytics/summary/",
        { credentials: "same-origin" }
      );
      if (!result.ok) return;
      var data = await result.json();
      var dr   = data.date_range || {};
      var start = dr.start;
      var end   = dr.end;
      if (!start || !end) return;

      var startLocal = isoToDatetimeLocal(start);
      var endLocal   = isoToDatetimeLocal(end);

      fromInput.min   = startLocal;
      fromInput.max   = endLocal;
      fromInput.value = startLocal;

      toInput.min   = startLocal;
      toInput.max   = endLocal;
      toInput.value = endLocal;

      if (hint) {
        hint.classList.remove("hidden");
        hint.textContent = "Date range set to log data: " + formatDate(start) + " → " + formatDate(end);
      }
    } catch (_) {}
  }

  async function fetchAndRenderLogs() {
    var select    = $("#viewer-dataset");
    var statusSel = $("#viewer-status");
    var search    = $("#viewer-search");
    var dateFrom  = $("#viewer-date-from");
    var dateTo    = $("#viewer-date-to");
    var tbody     = $("#logs-body");
    var meta      = $("#logs-meta");
    if (!select || !tbody || !meta) return;

    var datasetId = select.value;
    if (!datasetId) { setLogsMessage("Select a dataset to view log entries."); return; }

    setLogsLoadingMessage("Loading log entries…");

    var result = await LogSightAPI.getLogLines(datasetId, {
      page:     viewerPage,
      pageSize: 25,
      search:   search    ? search.value    : "",
      status:   statusSel ? statusSel.value : "all",
      dateFrom: dateFrom  ? dateFrom.value  : "",
      dateTo:   dateTo    ? dateTo.value    : "",
    });

    if (!result.available) {
      setLogsMessage(result.message || "Log entries could not be loaded.");
      return;
    }

    var from = result.items.length ? (result.page - 1) * result.pageSize + 1 : 0;
    var to   = (result.page - 1) * result.pageSize + result.items.length;
    meta.textContent = "Showing " + from + "–" + to + " of " + Number(result.total).toLocaleString() + " entries";

    tbody.innerHTML = "";
    if (!result.items.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500 dark:text-slate-400">No matching log entries.</td></tr>';
      updateLogPagination(result.page <= 1, result.page >= result.totalPages);
      return;
    }

    result.items.forEach(function (line, index) {
      var tr = document.createElement("tr");
      tr.className = "table-row-animate border-b border-slate-100 font-mono text-xs dark:border-slate-800";
      tr.style.animationDelay = index * 25 + "ms";

      var code = Number(line.status || 0);
      var badgeClass = "border border-slate-200 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300";
      if (code >= 500)      badgeClass = "border border-red-200 bg-red-50 text-red-800 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-300";
      else if (code >= 400) badgeClass = "border border-amber-200 bg-amber-50 text-amber-800 dark:border-amber-900/50 dark:bg-amber-950/30 dark:text-amber-300";
      else if (code >= 200) badgeClass = "border border-emerald-200 bg-emerald-50 text-emerald-800 dark:border-emerald-900/50 dark:bg-emerald-950/30 dark:text-emerald-300";

      tr.innerHTML =
        '<td class="whitespace-nowrap px-3 py-2 text-slate-500 dark:text-slate-400">' + escapeHtml(formatDate(line.timestamp)) + "</td>" +
        '<td class="px-3 py-2"><span class="inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold ' + badgeClass + '">' + escapeHtml(line.status || "—") + "</span></td>" +
        '<td class="px-3 py-2 text-slate-600 dark:text-slate-400">' + escapeHtml(line.method || "—") + "</td>" +
        '<td class="whitespace-nowrap px-3 py-2 font-mono text-xs text-slate-600 dark:text-slate-400">' + escapeHtml(line.ip_address || "—") + "</td>" +
        '<td class="max-w-md truncate px-3 py-2 text-slate-800 dark:text-slate-200" title="' + escapeHtml(line.raw || "") + '">' + escapeHtml(line.raw || "—") + "</td>";
      tbody.appendChild(tr);
    });

    updateLogPagination(result.page <= 1, result.page >= result.totalPages);

    var prev = $("#logs-prev");
    var next = $("#logs-next");
    if (prev) prev.onclick = function () { if (viewerPage > 1) { viewerPage--; fetchAndRenderLogs(); } };
    if (next) next.onclick = function () { if (viewerPage < result.totalPages) { viewerPage++; fetchAndRenderLogs(); } };
  }

  function initLogViewer() {
    populateViewerSelect().then(function (rows) {
      var select = $("#viewer-dataset");
      if (select && !select.value && rows.length) select.value = rows[0].id;
      viewerPage = 1;
      // Set date range from analytics before fetching logs.
      if (select && select.value) {
        setViewerDateRange(select.value).then(function () { fetchAndRenderLogs(); });
        updateStatPeriod(select.value);
      } else {
        fetchAndRenderLogs();
        updateStatPeriod("");
      }
    });

    var select    = $("#viewer-dataset");
    var statusSel = $("#viewer-status");
    var search    = $("#viewer-search");
    var dateFrom  = $("#viewer-date-from");
    var dateTo    = $("#viewer-date-to");
    var refresh   = $("#logs-refresh");

    if (select && !select._bound) {
      select._bound = true;
      select.addEventListener("change", function () {
        viewerPage = 1;
        // Reset date pickers first, then load.
        var fromInput = $("#viewer-date-from");
        var toInput   = $("#viewer-date-to");
        if (fromInput) { fromInput.value = ""; fromInput.min = ""; fromInput.max = ""; }
        if (toInput)   { toInput.value   = ""; toInput.min   = ""; toInput.max   = ""; }
        var hint = $("#viewer-date-hint");
        if (hint) hint.classList.add("hidden");
        if (select.value) {
          setViewerDateRange(select.value).then(function () { fetchAndRenderLogs(); });
          updateStatPeriod(select.value);
        } else {
          fetchAndRenderLogs();
          updateStatPeriod("");
        }
      });
    }
    if (statusSel && !statusSel._bound) {
      statusSel._bound = true;
      statusSel.addEventListener("change", function () { viewerPage = 1; fetchAndRenderLogs(); });
    }
    if (search && !search._bound) {
      search._bound = true;
      search.addEventListener("input", function () {
        clearTimeout(viewerSearchDebounce);
        viewerSearchDebounce = setTimeout(function () { viewerPage = 1; fetchAndRenderLogs(); }, 320);
      });
    }
    if (dateFrom && !dateFrom._bound) {
      dateFrom._bound = true;
      dateFrom.addEventListener("change", function () { viewerPage = 1; fetchAndRenderLogs(); });
    }
    if (dateTo && !dateTo._bound) {
      dateTo._bound = true;
      dateTo.addEventListener("change", function () { viewerPage = 1; fetchAndRenderLogs(); });
    }
    if (refresh && !refresh._bound) {
      refresh._bound = true;
      refresh.addEventListener("click", function () { fetchAndRenderLogs(); });
    }
  }

  // -------------------------------------------------------------------------
  // Traffic Forecasting view
  // -------------------------------------------------------------------------

  var forecastInitDone = false;

  async function initForecastView() {
    if (!forecastInitDone) {
      forecastInitDone = true;
      await populateSelect("forecast-dataset");
      var methodSel = $("#forecast-method");
      if (methodSel) {
        methodSel.addEventListener("change", function () {
          var desc = $("#forecast-method-desc");
          if (!desc) return;
          if (methodSel.value === "linear_regression") {
            desc.innerHTML = '<strong>Linear Regression:</strong> Fits a straight line to all historical hourly data using ordinary least squares, then extrapolates forward. Best for showing a clean overall trend direction.';
          } else {
            desc.innerHTML = '<strong>Seasonal Pattern:</strong> Averages traffic per hour-of-day across the full dataset history, then projects those daily rhythms forward with a trend adjustment. Produces realistic peaks and troughs rather than a flat line.';
          }
        });
      }
    } else {
      await populateSelect("forecast-dataset");
    }

    var runBtn = $("#forecast-run");
    if (runBtn && !runBtn._bound) {
      runBtn._bound = true;
      runBtn.addEventListener("click", runForecast);
    }
  }

  async function runForecast() {
    var dsSelect = $("#forecast-dataset");
    var methodSel = $("#forecast-method");
    var periodsInput = $("#forecast-periods");
    if (!dsSelect || !dsSelect.value) { window.alert("Please select a dataset first."); return; }

    var runBtn = $("#forecast-run");
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Running…"; }

    try {
      var data = await LogSightAPI.getTrafficForecast(dsSelect.value, {
        periods: parseInt(periodsInput ? periodsInput.value : "24", 10) || 24,
        method:  methodSel ? methodSel.value : "moving_avg",
      });

      // Show summary cards.
      var cards = $("#forecast-summary-cards");
      if (cards) {
        cards.classList.remove("hidden");
        cards.classList.add("grid");
      }
      renderStatValue("forecast-stat-points", data.summary && data.summary.total_historical_points, "", "—");
      renderStatValue("forecast-stat-avg",    data.summary && data.summary.avg_historical != null ? data.summary.avg_historical.toLocaleString() : null, "", "—");

      var trendEl = $("#forecast-stat-trend");
      if (trendEl) {
        var trendMap = { increasing: "↑ Increasing", decreasing: "↓ Decreasing", stable: "→ Stable" };
        trendEl.textContent = trendMap[data.trend] || data.trend || "—";
        trendEl.className = "mt-2 text-2xl font-bold " + (
          data.trend === "increasing" ? "text-emerald-600 dark:text-emerald-400" :
          data.trend === "decreasing" ? "text-red-600 dark:text-red-400" :
          "text-slate-900 dark:text-white"
        );
      }

      // Show chart, hide empty state.
      var empty = $("#forecast-empty");
      var wrap  = $("#forecast-chart-wrap");
      if (empty) empty.classList.add("hidden");
      if (wrap)  { wrap.classList.remove("hidden"); }

      requestAnimationFrame(function () {
        LogSightCharts.renderForecastChart("chart-forecast", data);
      });
      updateStatPeriod(dsSelect.value);

    } catch (err) {
      window.alert(err.message || "Forecast failed.");
    } finally {
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg> Run Forecast';
      }
    }
  }

  // -------------------------------------------------------------------------
  // Anomaly Detection view
  // -------------------------------------------------------------------------

  var anomalyInitDone = false;

  async function initAnomalyView() {
    if (!anomalyInitDone) {
      anomalyInitDone = true;
    }
    await populateSelect("anomaly-dataset");

    var runBtn = $("#anomaly-run");
    if (runBtn && !runBtn._bound) {
      runBtn._bound = true;
      runBtn.addEventListener("click", runAnomalyDetection);
    }
  }

  async function runAnomalyDetection() {
    var dsSelect  = $("#anomaly-dataset");
    var sensSel   = $("#anomaly-sensitivity");
    if (!dsSelect || !dsSelect.value) { window.alert("Please select a dataset first."); return; }

    // Reset previous results.
    var prevBanner = $("#anomaly-summary-banner");
    if (prevBanner) prevBanner.classList.add("hidden");

    var runBtn = $("#anomaly-run");
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Detecting…"; }

    try {
      var data = await LogSightAPI.getAnomalies(dsSelect.value, {
        sensitivity: sensSel ? sensSel.value : "medium",
      });

      var summary = data.summary || {};
      var cards   = $("#anomaly-summary-cards");
      if (cards) { cards.classList.remove("hidden"); cards.classList.add("grid"); }

      renderStatValue("anomaly-stat-total",  summary.total_anomalies,  "", "0");
      renderStatValue("anomaly-stat-high",   summary.high_severity,    "", "0");
      renderStatValue("anomaly-stat-medium", summary.medium_severity,  "", "0");
      renderStatValue("anomaly-stat-low",    summary.low_severity,     "", "0");

      // Show/hide chart area.
      var empty = $("#anomaly-empty");
      var wrap  = $("#anomaly-chart-wrap");
      if (empty) empty.classList.add("hidden");
      if (wrap)  wrap.classList.remove("hidden");

      // Build historical series from all request-spike anomaly data.
      // We re-use the historical data embedded in the anomalies response if present,
      // otherwise fetch it separately.
      var historicalData = data.historical_series || [];
      if (!historicalData.length) {
        // Fetch hourly trends for chart base series.
        var trends = await LogSightAPI.getRequestFrequency(dsSelect.value, { interval: "hour", limit: 500 });
        if (trends.available && trends.labels && trends.datasets && trends.datasets[0]) {
          historicalData = trends.labels.map(function (lbl, i) {
            return { label: lbl, value: trends.datasets[0].data[i] || 0 };
          });
        }
      }

      // Filter to request_spike anomalies for the chart overlay.
      var requestAnomalies = (data.anomalies || []).filter(function (a) { return a.type === "request_spike"; });

      requestAnimationFrame(function () {
        LogSightCharts.renderAnomalyChart("chart-anomalies", historicalData, requestAnomalies);
      });

      // Render anomaly list.
      renderAnomalyList(data.anomalies || []);
      updateStatPeriod(dsSelect.value);

    } catch (err) {
      window.alert(err.message || "Anomaly detection failed.");
    } finally {
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg> Detect Anomalies';
      }
    }
  }

  function renderAnomalyList(anomalies) {
    var wrap   = $("#anomaly-list-wrap");
    var list   = $("#anomaly-list");
    var banner = $("#anomaly-summary-banner");
    var bannerText = $("#anomaly-summary-text");
    if (!wrap || !list) return;

    if (!anomalies.length) {
      wrap.classList.add("hidden");
      if (banner) {
        banner.className = "rounded-2xl border border-emerald-200 bg-emerald-50 px-5 py-4 dark:border-emerald-800/40 dark:bg-emerald-950/20";
        banner.classList.remove("hidden");
        if (bannerText) bannerText.textContent = "No anomalies detected — traffic and error patterns look normal for this dataset.";
        if (bannerText) bannerText.className = "text-sm font-medium text-emerald-700 dark:text-emerald-300";
      }
      return;
    }

    // Build plain-English summary sentence.
    var high   = anomalies.filter(function (a) { return a.severity === "high"; }).length;
    var medium = anomalies.filter(function (a) { return a.severity === "medium"; }).length;
    var low    = anomalies.filter(function (a) { return a.severity === "low"; }).length;
    var parts  = [];
    if (high)   parts.push(high   + " critical");
    if (medium) parts.push(medium + " notable");
    if (low)    parts.push(low    + " minor");
    var summaryLine = "Found " + anomalies.length + " anomal" + (anomalies.length === 1 ? "y" : "ies") +
      " — " + parts.join(", ") + ".";

    if (banner) {
      var isHigh = high > 0;
      banner.className = "rounded-2xl border px-5 py-4 " + (
        isHigh
          ? "border-red-200 bg-red-50 dark:border-red-800/40 dark:bg-red-950/20"
          : "border-amber-200 bg-amber-50 dark:border-amber-800/40 dark:bg-amber-950/20"
      );
      banner.classList.remove("hidden");
      if (bannerText) {
        bannerText.textContent = summaryLine;
        bannerText.className = "text-sm font-medium " + (
          isHigh
            ? "text-red-700 dark:text-red-300"
            : "text-amber-700 dark:text-amber-300"
        );
      }
    }

    wrap.classList.remove("hidden");
    list.innerHTML = "";

    var typeLabels = { request_spike: "Traffic spike", error_rate_spike: "Error spike" };

    anomalies.forEach(function (a) {
      var typeLabel = typeLabels[a.type] || a.type;
      var dotColor  = a.severity === "high"
        ? "bg-red-500"
        : a.severity === "medium" ? "bg-amber-400" : "bg-slate-400";

      var item = document.createElement("div");
      item.className = "flex items-start gap-4 px-5 py-4";
      item.innerHTML =
        '<span class="mt-1.5 h-2.5 w-2.5 shrink-0 rounded-full ' + dotColor + '"></span>' +
        '<div class="min-w-0 flex-1">' +
          '<p class="text-sm font-medium text-slate-800 dark:text-slate-200">' +
            escapeHtml(a.label || "") +
            ' <span class="ml-1 text-xs font-normal text-slate-400 dark:text-slate-500">· ' + escapeHtml(typeLabel) + '</span>' +
          '</p>' +
          '<p class="mt-0.5 text-sm text-slate-500 dark:text-slate-400">' + escapeHtml(a.explanation || "") + '</p>' +
        '</div>';
      list.appendChild(item);
    });
  }

  // -------------------------------------------------------------------------
  // Alerts view
  // -------------------------------------------------------------------------

  var alertsInitDone = false;

  async function initAlertsView() {
    if (!alertsInitDone) {
      alertsInitDone = true;
      var checkBtn = $("#alerts-check");
      if (checkBtn && !checkBtn._bound) {
        checkBtn._bound = true;
        checkBtn.addEventListener("click", runAutoCheck);
      }
    }

    await populateSelect("alerts-dataset");

    var dsSel = $("#alerts-dataset");
    if (dsSel && !dsSel._alertsBound) {
      dsSel._alertsBound = true;
      dsSel.addEventListener("change", function () {
        // Clear previous results when dataset changes.
        var banner  = $("#alerts-status-banner");
        var results = $("#alerts-results");
        if (banner)  banner.classList.add("hidden");
        if (results) results.classList.add("hidden");
      });
    }
  }

  async function runAutoCheck() {
    var dsSel = $("#alerts-dataset");
    if (!dsSel || !dsSel.value) { window.alert("Please select a dataset first."); return; }

    var checkBtn = $("#alerts-check");
    if (checkBtn) { checkBtn.disabled = true; checkBtn.textContent = "Checking…"; }

    try {
      var data = await LogSightAPI.autoCheckAlerts(dsSel.value);
      renderAutoCheckResults(data);
    } catch (err) {
      window.alert(err.message || "Health check failed.");
    } finally {
      if (checkBtn) {
        checkBtn.disabled = false;
        checkBtn.innerHTML =
          '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>' +
          ' Run Health Check';
      }
    }
  }

  function renderAutoCheckResults(data) {
    var banner     = $("#alerts-status-banner");
    var bannerText = $("#alerts-status-text");
    var resultsEl  = $("#alerts-results");
    if (!banner || !resultsEl) return;

    var status = data.status || "ok";
    var statusConfig = {
      ok:       { border: "border-emerald-200", bg: "bg-emerald-50 dark:bg-emerald-950/20", text: "text-emerald-700 dark:text-emerald-300", label: "All clear — no issues detected." },
      warning:  { border: "border-amber-200",   bg: "bg-amber-50 dark:bg-amber-950/20",     text: "text-amber-700 dark:text-amber-300",   label: "Warning — one or more checks need attention." },
      critical: { border: "border-red-200",      bg: "bg-red-50 dark:bg-red-950/20",         text: "text-red-700 dark:text-red-300",       label: "Critical — immediate attention required." },
    };
    var cfg = statusConfig[status] || statusConfig.ok;

    banner.className = "rounded-2xl border px-5 py-4 " + cfg.border + " " + cfg.bg;
    banner.classList.remove("hidden");
    if (bannerText) {
      bannerText.textContent = cfg.label;
      bannerText.className   = "text-sm font-semibold " + cfg.text;
    }

    resultsEl.innerHTML = "";
    resultsEl.classList.remove("hidden");
    resultsEl.classList.add("grid");

    (data.results || []).forEach(function (r) {
      var cardStatus = r.status || "ok";
      var cardCfg = {
        ok:       { border: "border-emerald-200/80", icon: "text-emerald-500", dot: "bg-emerald-500", label: "OK" },
        warning:  { border: "border-amber-200/80",   icon: "text-amber-500",   dot: "bg-amber-400",   label: "Warning" },
        critical: { border: "border-red-200/80",      icon: "text-red-500",     dot: "bg-red-500",     label: "Critical" },
      }[cardStatus] || { border: "border-slate-200/80", icon: "text-slate-400", dot: "bg-slate-400", label: "OK" };

      var card = document.createElement("div");
      card.className = "card-lift rounded-2xl border " + cardCfg.border + " bg-white p-5 shadow-card dark:bg-slate-900/60";
      card.innerHTML =
        '<div class="flex items-center justify-between gap-2">' +
          '<p class="text-sm font-semibold text-slate-800 dark:text-slate-200">' + escapeHtml(r.label || r.check) + '</p>' +
          '<span class="inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ' +
            (cardStatus === "ok" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" :
             cardStatus === "warning" ? "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300" :
             "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300") + '">' +
            '<span class="h-1.5 w-1.5 rounded-full ' + cardCfg.dot + '"></span>' +
            escapeHtml(cardCfg.label) +
          '</span>' +
        '</div>' +
        '<p class="mt-3 text-sm text-slate-600 dark:text-slate-400">' + escapeHtml(r.message || "") + '</p>';
      resultsEl.appendChild(card);
    });
  }

  // -------------------------------------------------------------------------
  // Reports view
  // -------------------------------------------------------------------------

  async function initReportsView() {
    await populateSelect("report-dataset");

    var reportDataset = $("#report-dataset");
    if (reportDataset && !reportDataset._bound_period) {
      reportDataset._bound_period = true;
      reportDataset.addEventListener("change", function () {
        updateStatPeriod(reportDataset.value);
      });
    }

    var downloadBtn = $("#report-download");
    if (downloadBtn && !downloadBtn._bound) {
      downloadBtn._bound = true;
      downloadBtn.addEventListener("click", function () {
        var dsSel    = $("#report-dataset");
        var fmtSel   = $("#report-format");
        if (!dsSel || !dsSel.value) { window.alert("Please select a dataset first."); return; }
        try {
          LogSightAPI.downloadReport(dsSel.value, fmtSel ? fmtSel.value : "csv");
        } catch (err) {
          window.alert(err.message || "Download failed.");
        }
      });
    }
  }

  // -------------------------------------------------------------------------
  // Nav binding
  // -------------------------------------------------------------------------

  function bindNav() {
    $all("[data-route]").forEach(function (link) {
      link.addEventListener("click", function (event) {
        event.preventDefault();
        navigate(link.getAttribute("data-route"));
        if (typeof window.closeTopNavMenus === "function") window.closeTopNavMenus();
        if (typeof window.closeMobileSidebar === "function" && window.matchMedia("(max-width: 1023px)").matches) {
          window.closeMobileSidebar();
        }
      });
    });
  }

  // -------------------------------------------------------------------------
  // Boot
  // -------------------------------------------------------------------------

  // -------------------------------------------------------------------------
  // Dashboard date filter
  // -------------------------------------------------------------------------

  function initDashboardDateFilter() {
    var toggleBtn = $("#dash-date-toggle");
    var filterRow = $("#dashboard-date-filter");
    var clearBtn  = $("#dash-date-clear");
    var fromIn    = $("#dash-date-from");
    var toIn      = $("#dash-date-to");

    if (toggleBtn && !toggleBtn._bound) {
      toggleBtn._bound = true;
      toggleBtn.addEventListener("click", function () {
        filterRow.classList.toggle("hidden");
        filterRow.classList.toggle("flex");
      });
    }
    if (clearBtn && !clearBtn._bound) {
      clearBtn._bound = true;
      clearBtn.addEventListener("click", function () {
        if (fromIn) fromIn.value = "";
        if (toIn)   toIn.value   = "";
        filterRow.classList.add("hidden");
        filterRow.classList.remove("flex");
        loadDashboardData();
      });
    }
    if (fromIn && !fromIn._dboundFilter) {
      fromIn._dboundFilter = true;
      fromIn.addEventListener("change", loadDashboardData);
    }
    if (toIn && !toIn._dboundFilter) {
      toIn._dboundFilter = true;
      toIn.addEventListener("change", loadDashboardData);
    }
  }

  function getDashboardDateFilter() {
    var from = $("#dash-date-from");
    var to   = $("#dash-date-to");
    return {
      dateFrom: from && from.value ? from.value : null,
      dateTo:   to   && to.value   ? to.value   : null,
    };
  }

  // -------------------------------------------------------------------------
  // Security Insights
  // -------------------------------------------------------------------------

  async function initSecurityView() {
    await populateSelect("security-dataset");
    var runBtn = $("#security-run");
    if (runBtn && !runBtn._bound) {
      runBtn._bound = true;
      runBtn.addEventListener("click", runSecurityAnalysis);
    }
  }

  async function runSecurityAnalysis() {
    var dsSel = $("#security-dataset");
    if (!dsSel || !dsSel.value) { window.alert("Please select a dataset first."); return; }
    var runBtn = $("#security-run");
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Analysing…"; }
    var empty = $("#security-empty");
    if (empty) empty.classList.add("hidden");

    try {
      var data = await LogSightAPI.getSecurityInsights(dsSel.value);
      renderSecurityResults(data);
      updateStatPeriod(dsSel.value);
    } catch (err) {
      window.alert(err.message || "Security analysis failed.");
    } finally {
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML =
          '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" /></svg> Run Analysis';
      }
    }
  }

  function renderSecurityResults(data) {
    var banner   = $("#security-banner");
    var summary  = $("#security-summary");
    var scoreBdg = $("#security-score-badge");
    var findingsEl = $("#security-findings");
    var empty    = $("#security-empty");

    var score = data.risk_score || 0;
    var scoreColor = score >= 60 ? "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300"
                   : score >= 30 ? "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300"
                   : "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300";
    var bannerColor = score >= 60 ? "border-red-200 bg-red-50 dark:border-red-800/40 dark:bg-red-950/20"
                    : score >= 30 ? "border-amber-200 bg-amber-50 dark:border-amber-800/40 dark:bg-amber-950/20"
                    : "border-emerald-200 bg-emerald-50 dark:border-emerald-800/40 dark:bg-emerald-950/20";

    if (banner) {
      banner.className = "rounded-2xl border px-5 py-4 " + bannerColor;
      banner.classList.remove("hidden");
    }
    if (summary) summary.textContent = data.summary || "Analysis complete.";
    if (scoreBdg) {
      scoreBdg.textContent = "Risk: " + score + "/100";
      scoreBdg.className = "rounded-full px-3 py-1 text-sm font-bold " + scoreColor;
    }

    if (findingsEl) {
      findingsEl.innerHTML = "";
      var findings = data.findings || [];
      if (!findings.length) {
        if (empty) empty.classList.remove("hidden");
        return;
      }
      findings.forEach(function (f) {
        var severityBorder = f.severity === "high"
          ? "border-red-200/80 dark:border-red-800/30"
          : "border-amber-200/80 dark:border-amber-800/30";
        var dotClass = f.severity === "high" ? "bg-red-500" : "bg-amber-400";

        var card = document.createElement("div");
        card.className = "card-lift rounded-2xl border " + severityBorder + " bg-white p-5 shadow-card dark:bg-slate-900/60";

        var evidenceHtml = "";
        if (f.evidence && f.evidence.length) {
          evidenceHtml = '<ul class="mt-3 space-y-1">';
          f.evidence.slice(0, 5).forEach(function (e) {
            var line = e.ip || "";
            if (e.endpoint) line += " → " + e.endpoint;
            if (e.count)    line += " (" + e.count + " requests)";
            if (e.path)     line += " [" + e.path + "]";
            evidenceHtml += '<li class="font-mono text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(line) + "</li>";
          });
          if (f.evidence.length > 5) evidenceHtml += '<li class="text-xs text-slate-400">…and ' + (f.evidence.length - 5) + ' more</li>';
          evidenceHtml += '</ul>';
        }

        card.innerHTML =
          '<div class="flex items-start gap-3">' +
            '<span class="mt-1 h-2.5 w-2.5 shrink-0 rounded-full ' + dotClass + '"></span>' +
            '<div class="min-w-0 flex-1">' +
              '<p class="font-semibold text-slate-800 dark:text-slate-200">' + escapeHtml(f.title || f.type) + '</p>' +
              '<p class="mt-1 text-sm text-slate-600 dark:text-slate-400">' + escapeHtml(f.description || "") + '</p>' +
              evidenceHtml +
            '</div>' +
          '</div>';
        findingsEl.appendChild(card);
      });
    }
  }

  // -------------------------------------------------------------------------
  // Dataset Comparison
  // -------------------------------------------------------------------------

  async function initCompareView() {
    await populateSelect("compare-dataset-a");
    await populateSelect("compare-dataset-b");
    var runBtn = $("#compare-run");
    if (runBtn && !runBtn._bound) {
      runBtn._bound = true;
      runBtn.addEventListener("click", runComparison);
    }
  }

  async function runComparison() {
    var selA = $("#compare-dataset-a");
    var selB = $("#compare-dataset-b");
    if (!selA || !selA.value || !selB || !selB.value) {
      window.alert("Please select both datasets."); return;
    }
    if (selA.value === selB.value) {
      window.alert("Please select two different datasets."); return;
    }
    var runBtn = $("#compare-run");
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Comparing…"; }

    try {
      var data = await LogSightAPI.compareDatasets(selA.value, selB.value);
      renderComparisonResults(data);
    } catch (err) {
      window.alert(err.message || "Comparison failed.");
    } finally {
      if (runBtn) {
        runBtn.disabled = false;
        runBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" /></svg> Compare';
      }
    }
  }

  function renderComparisonResults(data) {
    var results = $("#compare-results");
    var empty   = $("#compare-empty");
    var metricsEl = $("#compare-metrics-table");
    var endpointsEl = $("#compare-endpoints-a");

    if (!data.dataset_a || !data.dataset_b) return;
    if (empty)   empty.classList.add("hidden");
    if (results) { results.classList.remove("hidden"); }

    var a = data.dataset_a.metrics || {};
    var b = data.dataset_b.metrics || {};
    var d = data.deltas || {};
    var nameA = data.dataset_a.name || "Dataset A";
    var nameB = data.dataset_b.name || "Dataset B";

    function deltaSpan(val) {
      if (val == null) return "";
      var n = Number(val);
      var sign = n > 0 ? "+" : "";
      var cls  = n > 0 ? "text-red-500" : n < 0 ? "text-emerald-600" : "text-slate-400";
      return '<span class="ml-1 text-xs ' + cls + '">' + sign + n.toFixed(1) + '</span>';
    }

    var rows = [
      ["Total Requests",    formatNumber(a.total_requests),  formatNumber(b.total_requests),  d.total_requests],
      ["Error Rate (%)",    a.error_rate != null ? Number(a.error_rate).toFixed(2) + "%" : "—", b.error_rate != null ? Number(b.error_rate).toFixed(2) + "%" : "—", d.error_rate],
      ["Avg Response Size", a.avg_response_size ? formatNumber(a.avg_response_size) + " B" : "—", b.avg_response_size ? formatNumber(b.avg_response_size) + " B" : "—", null],
      ["Unique Visitors",   formatNumber(a.unique_visitors), formatNumber(b.unique_visitors),  d.unique_visitors],
    ];

    if (metricsEl) {
      var tableHtml = '<table class="min-w-full text-sm"><thead class="border-b border-slate-100 bg-slate-50/60 dark:border-slate-800 dark:bg-slate-800/20"><tr>' +
        '<th class="px-5 py-3 text-left font-semibold text-slate-600 dark:text-slate-400">Metric</th>' +
        '<th class="px-5 py-3 text-left font-semibold text-indigo-600 dark:text-indigo-400">' + escapeHtml(nameA) + '</th>' +
        '<th class="px-5 py-3 text-left font-semibold text-amber-600 dark:text-amber-400">' + escapeHtml(nameB) + '</th>' +
        '<th class="px-5 py-3 text-left font-semibold text-slate-500 dark:text-slate-400">Δ (B vs A)</th>' +
        '</tr></thead><tbody class="divide-y divide-slate-100 dark:divide-slate-800">';
      rows.forEach(function (r) {
        tableHtml += '<tr><td class="px-5 py-3 text-slate-700 dark:text-slate-300">' + escapeHtml(r[0]) + '</td>' +
          '<td class="px-5 py-3 font-medium text-slate-800 dark:text-slate-200">' + (r[1] || "—") + '</td>' +
          '<td class="px-5 py-3 font-medium text-slate-800 dark:text-slate-200">' + (r[2] || "—") + '</td>' +
          '<td class="px-5 py-3">' + deltaSpan(r[3]) + '</td></tr>';
      });
      tableHtml += '</tbody></table>';
      metricsEl.innerHTML = tableHtml;
    }

    if (endpointsEl) {
      var endpoints = data.dataset_a.top_endpoints || [];
      endpointsEl.innerHTML = endpoints.length
        ? endpoints.slice(0, 10).map(function (e) {
            return '<div class="flex items-center justify-between gap-2 py-1 text-xs"><span class="font-mono truncate text-slate-600 dark:text-slate-400">' + escapeHtml(e.endpoint || e.path || "—") + '</span><span class="shrink-0 font-semibold text-slate-700 dark:text-slate-200">' + (e.count || 0).toLocaleString() + '</span></div>';
          }).join("")
        : '<p class="text-xs text-slate-400">No endpoint data.</p>';
    }

    // Traffic comparison chart
    var hourlyA = data.dataset_a.hourly_traffic || {};
    var hourlyB = data.dataset_b.hourly_traffic || {};
    var hours = Array.from({length: 24}, function (_, i) { return String(i).padStart(2, "0") + ":00"; });
    var valA  = hours.map(function (_, i) { return hourlyA[String(i)] || 0; });
    var valB  = hours.map(function (_, i) { return hourlyB[String(i)] || 0; });
    if (typeof LogSightCharts !== "undefined" && LogSightCharts.renderComparisonChart) {
      LogSightCharts.renderComparisonChart("chart-compare-traffic", hours, valA, valB, nameA, nameB);
    }
  }

  // -------------------------------------------------------------------------
  // Advanced Search
  // -------------------------------------------------------------------------

  var searchPage = 1;
  var SEARCH_FIELDS = [
    { value: "keyword",    label: "Keyword (raw)" },
    { value: "ip",         label: "IP Address" },
    { value: "method",     label: "Method" },
    { value: "endpoint",   label: "Endpoint" },
    { value: "status",     label: "Status Code" },
    { value: "date_from",  label: "Date from" },
    { value: "date_to",    label: "Date to" },
  ];
  var SAVED_SEARCHES_KEY = "ls-saved-searches";

  async function initSearchView() {
    await populateSelect("search-dataset");

    var addBtn = $("#search-add-filter");
    if (addBtn && !addBtn._bound) {
      addBtn._bound = true;
      addBtn.addEventListener("click", function () { addFilterRow(); });
    }
    var runBtn = $("#search-run");
    if (runBtn && !runBtn._bound) {
      runBtn._bound = true;
      runBtn.addEventListener("click", function () { searchPage = 1; runAdvancedSearch(); });
    }
    var saveBtn = $("#search-save");
    if (saveBtn && !saveBtn._bound) {
      saveBtn._bound = true;
      saveBtn.addEventListener("click", saveCurrentSearch);
    }

    if (!$("#search-filter-rows").children.length) addFilterRow();
    renderSavedSearches();
  }

  function addFilterRow(field, value) {
    var container = $("#search-filter-rows");
    if (!container) return;
    var row = document.createElement("div");
    row.className = "flex items-center gap-2";

    var fieldSel = document.createElement("select");
    fieldSel.className = "rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-800 shadow-sm focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
    SEARCH_FIELDS.forEach(function (f) {
      var opt = document.createElement("option");
      opt.value = f.value;
      opt.textContent = f.label;
      if (field && f.value === field) opt.selected = true;
      fieldSel.appendChild(opt);
    });

    var valIn = document.createElement("input");
    valIn.type = "text";
    valIn.placeholder = "value…";
    valIn.value = value || "";
    valIn.className = "flex-1 rounded-lg border border-slate-200 bg-white px-2.5 py-1.5 text-sm text-slate-800 shadow-sm focus:border-indigo-500 focus:outline-none dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100";
    valIn.addEventListener("keydown", function (e) { if (e.key === "Enter") { searchPage = 1; runAdvancedSearch(); } });

    var rmBtn = document.createElement("button");
    rmBtn.type = "button";
    rmBtn.textContent = "✕";
    rmBtn.className = "rounded-lg px-2 py-1 text-xs text-slate-400 hover:text-slate-600";
    rmBtn.addEventListener("click", function () { container.removeChild(row); });

    row.appendChild(fieldSel);
    row.appendChild(valIn);
    row.appendChild(rmBtn);
    container.appendChild(row);
  }

  function getSearchFilters() {
    var rows = $all("#search-filter-rows > div");
    var params = {};
    rows.forEach(function (row) {
      var sel = row.querySelector("select");
      var inp = row.querySelector("input");
      if (sel && inp && inp.value.trim()) {
        params[sel.value] = inp.value.trim();
      }
    });
    return params;
  }

  async function runAdvancedSearch() {
    var dsSel = $("#search-dataset");
    if (!dsSel || !dsSel.value) { window.alert("Please select a dataset first."); return; }
    var filters = getSearchFilters();
    var runBtn = $("#search-run");
    if (runBtn) { runBtn.disabled = true; runBtn.textContent = "Searching…"; }
    var meta = $("#search-meta");
    if (meta) meta.textContent = "Loading…";

    try {
      var params = {
        page: searchPage,
        pageSize: 25,
        search:    filters.keyword  || "",
        ip:        filters.ip       || "",
        method:    filters.method   || "",
        endpoint:  filters.endpoint || "",
        status:    filters.status   || "all",
        dateFrom:  filters.date_from || "",
        dateTo:    filters.date_to   || "",
      };
      var result = await LogSightAPI.getLogLines(dsSel.value, params);
      renderSearchResults(result);
    } catch (err) {
      if (meta) meta.textContent = "Error: " + (err.message || "search failed");
    } finally {
      if (runBtn) { runBtn.disabled = false; runBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" /></svg> Search'; }
    }
  }

  function renderSearchResults(result) {
    var wrap    = $("#search-results-wrap");
    var tbody   = $("#search-results-body");
    var countEl = $("#search-result-count");
    var pageEl  = $("#search-page-info");
    var prevBtn = $("#search-prev");
    var nextBtn = $("#search-next");
    var meta    = $("#search-meta");

    if (!wrap || !tbody) return;
    wrap.classList.remove("hidden");

    var total = result.total || 0;
    var from  = total ? (result.page - 1) * result.pageSize + 1 : 0;
    var to    = (result.page - 1) * result.pageSize + (result.items || []).length;
    if (countEl) countEl.textContent = total.toLocaleString() + " results";
    if (meta)    meta.textContent    = "";
    if (pageEl)  pageEl.textContent  = total ? from + "–" + to + " of " + total.toLocaleString() : "No results";
    if (prevBtn) { prevBtn.disabled = result.page <= 1; prevBtn.onclick = function () { if (searchPage > 1) { searchPage--; runAdvancedSearch(); } }; }
    if (nextBtn) { nextBtn.disabled = result.page >= result.totalPages; nextBtn.onclick = function () { if (searchPage < result.totalPages) { searchPage++; runAdvancedSearch(); } }; }

    tbody.innerHTML = "";
    (result.items || []).forEach(function (line, idx) {
      var tr = document.createElement("tr");
      tr.className = "table-row-animate border-b border-slate-100 font-mono text-xs dark:border-slate-800";
      tr.style.animationDelay = idx * 20 + "ms";
      var code = Number(line.status || 0);
      var badgeCls = code >= 500 ? "border-red-200 bg-red-50 text-red-700" : code >= 400 ? "border-amber-200 bg-amber-50 text-amber-700" : code >= 200 ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-slate-200 bg-slate-50 text-slate-700";
      tr.innerHTML =
        '<td class="whitespace-nowrap px-4 py-2.5 text-slate-500">' + escapeHtml(formatDate(line.timestamp)) + "</td>" +
        '<td class="px-4 py-2.5"><span class="inline-flex rounded px-1.5 py-0.5 text-[10px] font-semibold border ' + badgeCls + '">' + escapeHtml(line.status || "—") + "</span></td>" +
        '<td class="px-4 py-2.5 text-slate-600">' + escapeHtml(line.method || "—") + "</td>" +
        '<td class="px-4 py-2.5 text-slate-600">' + escapeHtml(line.ip_address || "—") + "</td>" +
        '<td class="max-w-xs truncate px-4 py-2.5 text-slate-800 dark:text-slate-200">' + escapeHtml(line.endpoint || line.raw || "—") + "</td>";
      tbody.appendChild(tr);
    });
  }

  function saveCurrentSearch() {
    var filters = getSearchFilters();
    var dsSel   = $("#search-dataset");
    if (!Object.keys(filters).length) { window.alert("Add at least one filter before saving."); return; }
    var name = window.prompt("Name this search:");
    if (!name) return;
    var saved = JSON.parse(localStorage.getItem(SAVED_SEARCHES_KEY) || "[]");
    saved.unshift({ name: name, filters: filters, datasetId: dsSel ? dsSel.value : "" });
    if (saved.length > 10) saved = saved.slice(0, 10);
    localStorage.setItem(SAVED_SEARCHES_KEY, JSON.stringify(saved));
    renderSavedSearches();
  }

  function renderSavedSearches() {
    var wrap = $("#search-saved-wrap");
    var list = $("#search-saved-list");
    if (!wrap || !list) return;
    var saved = JSON.parse(localStorage.getItem(SAVED_SEARCHES_KEY) || "[]");
    if (!saved.length) { wrap.classList.add("hidden"); return; }
    wrap.classList.remove("hidden");
    list.innerHTML = "";
    saved.forEach(function (s, idx) {
      var row = document.createElement("div");
      row.className = "flex items-center justify-between gap-3 px-5 py-3";
      var summary = Object.entries(s.filters).map(function (kv) { return kv[0] + ": " + kv[1]; }).join(" · ");
      row.innerHTML =
        '<div class="min-w-0 flex-1 cursor-pointer" data-load-search="' + idx + '">' +
          '<p class="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400">' + escapeHtml(s.name) + '</p>' +
          '<p class="text-xs text-slate-400 truncate">' + escapeHtml(summary) + '</p>' +
        '</div>' +
        '<button type="button" data-del-search="' + idx + '" class="shrink-0 text-xs text-slate-400 hover:text-red-500">Remove</button>';
      row.querySelector("[data-load-search]").addEventListener("click", function () {
        var container = $("#search-filter-rows");
        if (container) container.innerHTML = "";
        Object.entries(s.filters).forEach(function (kv) { addFilterRow(kv[0], kv[1]); });
        var dsSel = $("#search-dataset");
        if (dsSel && s.datasetId) dsSel.value = s.datasetId;
        searchPage = 1;
        runAdvancedSearch();
      });
      row.querySelector("[data-del-search]").addEventListener("click", function () {
        var items = JSON.parse(localStorage.getItem(SAVED_SEARCHES_KEY) || "[]");
        items.splice(idx, 1);
        localStorage.setItem(SAVED_SEARCHES_KEY, JSON.stringify(items));
        renderSavedSearches();
      });
      list.appendChild(row);
    });
  }

  // -------------------------------------------------------------------------
  // Live Ingestion Simulator
  // -------------------------------------------------------------------------

  var simulateAutoTimer = null;
  var simulateTotalGenerated = 0;

  async function initSimulateView() {
    await populateSelect("simulate-dataset");
    var onceBtn = $("#simulate-once");
    var autoBtn = $("#simulate-auto");
    if (onceBtn && !onceBtn._bound) {
      onceBtn._bound = true;
      onceBtn.addEventListener("click", function () { runSimulateBatch(false); });
    }
    if (autoBtn && !autoBtn._bound) {
      autoBtn._bound = true;
      autoBtn.addEventListener("click", toggleSimulateAuto);
    }
  }

  async function runSimulateBatch(silent) {
    var dsSel  = $("#simulate-dataset");
    var countIn = $("#simulate-count");
    if (!dsSel || !dsSel.value) { if (!silent) window.alert("Please select a target dataset first."); return; }
    var count = Math.min(50, parseInt((countIn && countIn.value) || "10", 10) || 10);

    try {
      var data = await LogSightAPI.simulateLogs(dsSel.value, count);
      simulateTotalGenerated += data.generated || 0;

      var totalEl  = $("#simulate-total");
      var lastEl   = $("#simulate-last");
      var statusEl = $("#simulate-status");
      var statsEl  = $("#simulate-stats");
      var feed     = $("#simulate-feed");

      if (statsEl) { statsEl.classList.remove("hidden"); statsEl.classList.add("grid"); }
      if (totalEl)  totalEl.textContent  = simulateTotalGenerated.toLocaleString();
      if (lastEl)   lastEl.textContent   = (data.generated || 0) + " entries";
      if (statusEl) statusEl.textContent = simulateAutoTimer ? "● Streaming" : "Done";

      if (feed && data.entries) {
        if (feed.querySelector("p")) feed.innerHTML = "";
        data.entries.slice(0, 10).forEach(function (e) {
          var line = document.createElement("div");
          var code = Number(e.status_code || 0);
          var cls  = code >= 500 ? "text-red-500" : code >= 400 ? "text-amber-500" : "text-emerald-500";
          line.className = "py-0.5 border-b border-slate-100/40 dark:border-slate-800/40";
          line.innerHTML = '<span class="text-slate-400">' + new Date(e.timestamp).toLocaleTimeString() + '</span> ' +
            '<span class="' + cls + '">' + escapeHtml(String(e.status_code)) + '</span> ' +
            escapeHtml(e.method) + ' ' + escapeHtml(e.endpoint) + ' — ' + escapeHtml(e.ip_address);
          feed.insertBefore(line, feed.firstChild);
          // Keep feed trimmed to 100 lines.
          while (feed.children.length > 100) feed.removeChild(feed.lastChild);
        });
      }
    } catch (err) {
      if (!silent) window.alert(err.message || "Simulation failed.");
    }
  }

  function toggleSimulateAuto() {
    var autoBtn  = $("#simulate-auto");
    var statusEl = $("#simulate-status");
    if (simulateAutoTimer) {
      clearInterval(simulateAutoTimer);
      simulateAutoTimer = null;
      if (autoBtn)  autoBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14.752 11.168l-3.197-2.132A1 1 0 0010 9.87v4.263a1 1 0 001.555.832l3.197-2.132a1 1 0 000-1.664z" /><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg> Auto (every 3s)';
      if (statusEl) statusEl.textContent = "Stopped";
    } else {
      runSimulateBatch(false);
      simulateAutoTimer = setInterval(function () { runSimulateBatch(true); }, 3000);
      if (autoBtn)  autoBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10 9v6m4-6v6m7-3a9 9 0 11-18 0 9 9 0 0118 0z" /></svg> Stop';
      if (statusEl) statusEl.textContent = "● Streaming";
    }
  }

  // -------------------------------------------------------------------------
  // Notifications
  // -------------------------------------------------------------------------

  var notifPollTimer = null;

  function initNotifications() {
    var bell     = $("#notif-bell");
    var dropdown = $("#notif-dropdown");
    var markAll  = $("#notif-mark-all");

    if (bell && !bell._bound) {
      bell._bound = true;
      bell.addEventListener("click", function (e) {
        e.stopPropagation();
        dropdown && dropdown.classList.toggle("hidden");
        if (dropdown && !dropdown.classList.contains("hidden")) loadNotifications();
      });
    }
    if (markAll && !markAll._bound) {
      markAll._bound = true;
      markAll.addEventListener("click", async function () {
        try { await LogSightAPI.markAllNotifsRead(); } catch (_) {}
        loadNotifications();
      });
    }
    document.addEventListener("click", function (e) {
      if (dropdown && !dropdown.classList.contains("hidden") && !bell.contains(e.target) && !dropdown.contains(e.target)) {
        dropdown.classList.add("hidden");
      }
    });

    pollNotifCount();
    notifPollTimer = setInterval(pollNotifCount, 30000);
  }

  async function pollNotifCount() {
    try {
      var data  = await LogSightAPI.getNotifCount();
      var badge = $("#notif-badge");
      if (!badge) return;
      var count = data.unread_count || 0;
      badge.textContent = count > 99 ? "99+" : String(count);
      badge.classList.toggle("hidden", count === 0);
    } catch (_) {}
  }

  async function loadNotifications() {
    var list = $("#notif-list");
    if (!list) return;
    try {
      var data  = await LogSightAPI.getNotifications();
      var notifs = data.notifications || [];
      if (!notifs.length) {
        list.innerHTML = '<p class="px-4 py-6 text-center text-xs text-slate-400">No notifications yet.</p>';
        return;
      }
      list.innerHTML = "";
      notifs.forEach(function (n) {
        var item = document.createElement("div");
        item.className = "notif-item" + (n.is_read ? "" : " unread");
        var dotColor = n.severity === "critical" ? "bg-red-500" : n.severity === "warning" ? "bg-amber-400" : "bg-indigo-400";
        item.innerHTML =
          '<span class="notif-dot ' + dotColor + '"></span>' +
          '<div class="min-w-0 flex-1">' +
            '<p class="text-xs leading-snug text-slate-700 dark:text-slate-300">' + escapeHtml(n.message) + '</p>' +
            '<p class="mt-0.5 text-[10px] text-slate-400">' + escapeHtml(formatDate(n.created_at)) + '</p>' +
          '</div>';
        if (!n.is_read) {
          item.addEventListener("click", async function () {
            try { await LogSightAPI.markNotifRead(n.id); } catch (_) {}
            item.classList.remove("unread");
            pollNotifCount();
          });
        }
        list.appendChild(item);
      });
    } catch (_) {
      list.innerHTML = '<p class="px-4 py-4 text-xs text-red-500">Failed to load notifications.</p>';
    }
  }

  // -------------------------------------------------------------------------
  // Boot
  // -------------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initTopNavigation();
    initUpload();
    bindNav();
    initDashboardControls();
    initDashboardDateFilter();
    initNotifications();

    // Load backend preferences and apply widget visibility.
    LogSightAPI.getPreferences().then(function (prefs) {
      if (prefs && prefs.hidden_widgets && prefs.hidden_widgets.length) {
        savePrefsToStorage(prefs);
        applyWidgetVisibility(prefs.hidden_widgets);
      }
    }).catch(function () {});

    if (!window.location.hash) {
      window.location.hash = "#/dashboard";
    } else {
      runRoute(getHashRoute());
    }
  });
})();
