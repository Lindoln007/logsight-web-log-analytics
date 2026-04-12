/**
 * LogSight SPA — routing, theme, sidebar, uploads, datasets,
 * dashboard, log viewer, forecasting, anomaly detection, alerts, reports.
 */
(function () {
  "use strict";

  var THEME_KEY    = "log-sight-theme";
  var SIDEBAR_KEY  = "log-sight-sidebar-collapsed";
  var PREFS_KEY    = "log-sight-prefs";           // localStorage fallback for preferences
  var routes       = ["dashboard", "upload", "datasets", "viewer",
                      "forecast", "anomalies", "alerts", "reports"];

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
    if (v === "completed")  return "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300";
    if (v === "failed")     return "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300";
    if (v === "processing") return "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";
    return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
  }

  function severityBadge(sev) {
    if (sev === "high")   return "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300";
    if (sev === "medium") return "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";
    return "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400";
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
    var stored     = localStorage.getItem(THEME_KEY);
    var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    var dark       = stored === "dark" || (stored !== "light" && prefersDark);
    applyTheme(dark);
    var btn = $("#theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        var isDark = document.documentElement.classList.toggle("dark");
        localStorage.setItem(THEME_KEY, isDark ? "dark" : "light");
        applyTheme(isDark);
        // Redraw dashboard charts to pick up new theme colours.
        if (getHashRoute() === "dashboard") loadDashboardCharts();
      });
    }
  }

  function applyTheme(dark) {
    document.documentElement.classList.toggle("dark", dark);
    var meta = $("#theme-color-meta");
    if (meta) meta.setAttribute("content", dark ? "#0f172a" : "#f8fafc");
  }

  // -------------------------------------------------------------------------
  // Sidebar
  // -------------------------------------------------------------------------

  function initSidebar() {
    var collapsed  = localStorage.getItem(SIDEBAR_KEY) === "1";
    var aside      = $("#sidebar");
    var main       = $("#main-shell");
    var toggle     = $("#sidebar-toggle");
    var mobileBtn  = $("#sidebar-mobile-toggle");
    var backdrop   = $("#sidebar-backdrop");
    var mq         = window.matchMedia("(min-width: 1024px)");

    function isDesktop() { return mq.matches; }

    function closeMobileDrawer() {
      if (!aside || !backdrop) return;
      aside.classList.add("-translate-x-full");
      backdrop.classList.add("hidden");
    }
    function openMobileDrawer() {
      if (!aside || !backdrop) return;
      aside.classList.remove("-translate-x-full");
      backdrop.classList.remove("hidden");
    }

    function apply(c) {
      if (!aside || !main) return;
      if (isDesktop()) {
        aside.classList.toggle("w-64",    !c);
        aside.classList.toggle("w-[72px]", c);
        main.classList.toggle("lg:pl-64",    !c);
        main.classList.toggle("lg:pl-[72px]", c);
      } else {
        aside.classList.remove("w-[72px]");
        aside.classList.add("w-64");
      }
      $all("[data-sidebar-text]").forEach(function (el) {
        var hide = c && isDesktop();
        el.classList.toggle("opacity-0",        hide);
        el.classList.toggle("pointer-events-none", hide);
        el.classList.toggle("w-0",              hide);
        el.classList.toggle("overflow-hidden",  hide);
      });
      aside.setAttribute("aria-expanded", c ? "false" : "true");
    }

    apply(collapsed);
    if (!isDesktop()) closeMobileDrawer();

    if (toggle) {
      toggle.addEventListener("click", function () {
        if (!isDesktop()) return;
        var next = !aside.classList.contains("w-[72px]");
        localStorage.setItem(SIDEBAR_KEY, next ? "1" : "0");
        apply(next);
      });
    }

    var brandLink = document.getElementById("sidebar-brand");
    if (brandLink) {
      brandLink.addEventListener("click", function (e) {
        if (!isDesktop()) return;
        if (localStorage.getItem(SIDEBAR_KEY) === "1") {
          e.preventDefault();
          e.stopPropagation();
          localStorage.setItem(SIDEBAR_KEY, "0");
          apply(false);
        }
      });
    }

    if (mobileBtn) mobileBtn.addEventListener("click", openMobileDrawer);
    if (backdrop)  backdrop.addEventListener("click", closeMobileDrawer);

    mq.addEventListener("change", function () {
      if (isDesktop()) { closeMobileDrawer(); apply(localStorage.getItem(SIDEBAR_KEY) === "1"); }
      else             { apply(false); closeMobileDrawer(); }
    });

    window.closeMobileSidebar = closeMobileDrawer;
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
      link.classList.toggle("bg-indigo-600/15",   active);
      link.classList.toggle("text-indigo-600",     active);
      link.classList.toggle("dark:text-indigo-400", active);
      link.classList.toggle("text-slate-600",      !active);
      link.classList.toggle("dark:text-slate-400", !active);
      link.classList.toggle("hover:bg-slate-100",  !active);
      link.classList.toggle("dark:hover:bg-slate-800/80", !active);
    });
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
    };
    var titleEl = $("#page-title");
    if (titleEl) titleEl.textContent = titles[route] || "Dashboard";
    var period = $("#stat-period");
    if (period) period.classList.toggle("hidden", route !== "dashboard");
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

    var period = $("#stat-period");
    if (period) { period.textContent = "Loading…"; period.classList.remove("hidden"); }

    await populateDashboardSelect();
    var id = LogSightAPI.getCurrentDataset();

    if (!id) {
      renderStatValue("stat-requests", null, "", "No datasets");
      renderStatValue("stat-errors",   null, "", "—");
      renderStatValue("stat-latency",  null, "", "—");
      renderStatValue("stat-visitors", null, "", "—");
      if (period) period.textContent = "Upload a log file to begin";
      if (typeof LogSightCharts !== "undefined") LogSightCharts.destroyAll();
      return;
    }

    var summary = await LogSightAPI.getDashboardSummary(id);

    renderStatValue("stat-requests", summary.totalRequests != null ? formatNumber(summary.totalRequests) : null, "", "No data");
    renderStatValue("stat-errors",   summary.errorRate    != null ? Number(summary.errorRate).toFixed(2) : null, "%", "No data");
    renderStatValue("stat-latency",  summary.avgResponseSize != null ? formatNumber(summary.avgResponseSize) : null, " B", "No data");
    renderStatValue("stat-visitors", summary.uniqueVisitors != null ? formatNumber(summary.uniqueVisitors) : null, "", "No data");

    if (summary.totalErrors != null) renderSubText("stat-errors-abs", formatNumber(summary.totalErrors));
    if (period) period.textContent = summary.periodLabel || id;

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
      tr.className = "table-row-animate border-b border-slate-100 transition-colors hover:bg-slate-50/80 dark:border-slate-800 dark:hover:bg-slate-800/50";
      tr.style.animationDelay = index * 40 + "ms";

      var entriesText = dataset.totalEntries != null ? Number(dataset.totalEntries).toLocaleString() : "—";

      tr.innerHTML =
        '<td class="px-4 py-3 font-mono text-xs text-slate-600 dark:text-slate-400">' + escapeHtml(dataset.id) + "</td>" +
        '<td class="px-4 py-3 font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(dataset.name) + "</td>" +
        '<td class="px-4 py-3"><span class="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">' + escapeHtml(dataset.source) + "</span></td>" +
        '<td class="px-4 py-3 text-slate-600 dark:text-slate-400">' + escapeHtml(formatBytes(dataset.sizeBytes)) + "</td>" +
        '<td class="px-4 py-3 tabular-nums text-slate-600 dark:text-slate-400">' + escapeHtml(entriesText) + "</td>" +
        '<td class="px-4 py-3 text-slate-600 dark:text-slate-400">' + escapeHtml(formatDate(dataset.uploadedAt)) + "</td>" +
        '<td class="px-4 py-3 text-right whitespace-nowrap">' +
          '<div class="inline-flex items-center gap-2">' +
          '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ' + buildStatusBadge(dataset.status) + '">' + escapeHtml(dataset.status) + "</span>" +
          '<button type="button" data-analyse-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-indigo-200 px-2.5 py-1 text-xs font-medium text-indigo-600 transition-colors hover:bg-indigo-50 dark:border-indigo-800 dark:text-indigo-400 dark:hover:bg-indigo-950/50"' +
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
      var badgeClass = "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
      if (code >= 500)      badgeClass = "bg-red-100 text-red-800 dark:bg-red-950/50 dark:text-red-300";
      else if (code >= 400) badgeClass = "bg-amber-100 text-amber-800 dark:bg-amber-950/50 dark:text-amber-300";
      else if (code >= 200) badgeClass = "bg-emerald-100 text-emerald-800 dark:bg-emerald-950/50 dark:text-emerald-300";

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
      } else {
        fetchAndRenderLogs();
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
        } else {
          fetchAndRenderLogs();
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
            desc.innerHTML = '<strong>Linear Regression:</strong> Fits a straight line (y = mx + b) to all historical hourly data using ordinary least squares (OLS), then extrapolates forward. Good for capturing long-term trends.';
          } else {
            desc.innerHTML = '<strong>Moving Average:</strong> The forecast is based on the average of the last 6 historical hourly buckets, adjusted by the per-hour trend slope. Simple, robust, and easy to interpret.';
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
    var wrap = $("#anomaly-list-wrap");
    var list = $("#anomaly-list");
    if (!wrap || !list) return;

    if (!anomalies.length) {
      wrap.classList.add("hidden");
      return;
    }

    wrap.classList.remove("hidden");
    list.innerHTML = "";

    anomalies.forEach(function (a) {
      var typeLabel = a.type === "request_spike" ? "Request Spike" : "Error Rate Spike";
      var item = document.createElement("div");
      item.className = "flex flex-col gap-1 px-5 py-4 sm:flex-row sm:items-start sm:gap-4";
      item.innerHTML =
        '<div class="flex items-center gap-2 sm:w-44 sm:shrink-0">' +
        '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ' + severityBadge(a.severity) + '">' + escapeHtml(a.severity) + "</span>" +
        '<span class="text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(typeLabel) + "</span></div>" +
        '<div class="min-w-0 flex-1">' +
        '<p class="text-sm font-medium text-slate-800 dark:text-slate-200">' + escapeHtml(a.label || a.timestamp || "") + "</p>" +
        '<p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(a.explanation || "") + "</p>" +
        "</div>" +
        '<div class="shrink-0 text-right text-xs text-slate-500 dark:text-slate-400">' +
        'Value: <strong>' + escapeHtml(String(a.value)) + "</strong><br>" +
        'Threshold: ' + escapeHtml(String(a.threshold)) +
        "</div>";
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
      var createBtn = $("#alert-create");
      if (createBtn && !createBtn._bound) {
        createBtn._bound = true;
        createBtn.addEventListener("click", createAlertRule);
      }
      var checkBtn = $("#alerts-check");
      if (checkBtn && !checkBtn._bound) {
        checkBtn._bound = true;
        checkBtn.addEventListener("click", checkAlerts);
      }
    }

    await populateSelect("alerts-dataset");

    var dsSel = $("#alerts-dataset");
    if (dsSel && !dsSel._alertsBound) {
      dsSel._alertsBound = true;
      dsSel.addEventListener("change", function () {
        if (dsSel.value) loadAlertsData(dsSel.value);
      });
    }

    if (dsSel && dsSel.value) loadAlertsData(dsSel.value);
  }

  async function loadAlertsData(datasetId) {
    var rulesList   = $("#alert-rules-list");
    var historyList = $("#alert-history-list");
    if (!datasetId) return;

    try {
      var data = await LogSightAPI.getAlerts(datasetId);
      renderAlertRules(data.rules || [], datasetId);
      renderAlertHistory(data.recent_alerts || []);
    } catch (_) {
      if (rulesList) rulesList.innerHTML = '<p class="px-5 py-4 text-sm text-red-500">Failed to load alert rules.</p>';
    }
  }

  function renderAlertRules(rules, datasetId) {
    var list = $("#alert-rules-list");
    if (!list) return;

    if (!rules.length) {
      list.innerHTML = '<p class="px-5 py-6 text-center text-sm text-slate-500 dark:text-slate-400">No alert rules yet. Create one above.</p>';
      return;
    }

    list.innerHTML = "";
    rules.forEach(function (rule) {
      var row = document.createElement("div");
      row.className = "flex flex-col gap-2 px-5 py-4 sm:flex-row sm:items-center sm:justify-between";

      var thresholdParts = [];
      if (rule.error_rate_threshold != null) thresholdParts.push("Error rate &gt; " + rule.error_rate_threshold + "%");
      if (rule.request_spike_multiplier != null) thresholdParts.push("Spike &gt; " + rule.request_spike_multiplier + "× mean");

      row.innerHTML =
        '<div class="min-w-0 flex-1">' +
        '<div class="flex items-center gap-2">' +
        '<p class="font-medium text-slate-800 dark:text-slate-200">' + escapeHtml(rule.name) + "</p>" +
        '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ' + (rule.is_active ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300" : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400") + '">' + (rule.is_active ? "Active" : "Inactive") + "</span></div>" +
        '<p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">' + (thresholdParts.join(" · ") || "No thresholds configured") + "</p>" +
        '<p class="mt-0.5 text-xs text-slate-500 dark:text-slate-400">Triggered ' + (rule.alert_count || 0) + ' time(s)</p>' +
        "</div>" +
        '<button type="button" data-del-rule="' + escapeHtml(rule.id) + '" class="shrink-0 rounded-lg border border-red-200 px-2.5 py-1 text-xs font-medium text-red-600 transition hover:bg-red-50 dark:border-red-900/50 dark:text-red-400 dark:hover:bg-red-950/30">Delete</button>';

      list.appendChild(row);
    });

    list.querySelectorAll("[data-del-rule]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var ruleId = btn.getAttribute("data-del-rule");
        if (!confirm("Delete this alert rule and all its history?")) return;
        try {
          await LogSightAPI.deleteAlertRule(datasetId, ruleId);
          loadAlertsData(datasetId);
        } catch (err) {
          window.alert(err.message || "Deletion failed.");
        }
      });
    });
  }

  function renderAlertHistory(history) {
    var list = $("#alert-history-list");
    if (!list) return;

    if (!history.length) {
      list.innerHTML = '<p class="px-5 py-6 text-center text-sm text-slate-500 dark:text-slate-400">No alerts triggered yet.</p>';
      return;
    }

    list.innerHTML = "";
    history.forEach(function (alert) {
      var typeLabel = alert.alert_type === "error_rate" ? "Error Rate" : "Request Spike";
      var row = document.createElement("div");
      row.className = "flex flex-col gap-1 px-5 py-3 sm:flex-row sm:items-center sm:gap-4";
      row.innerHTML =
        '<div class="flex items-center gap-2 sm:w-40 sm:shrink-0">' +
        '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold ' + severityBadge(alert.severity) + '">' + escapeHtml(alert.severity) + "</span>" +
        '<span class="text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(typeLabel) + "</span></div>" +
        '<p class="min-w-0 flex-1 text-sm text-slate-700 dark:text-slate-300">' + escapeHtml(alert.message) + "</p>" +
        '<p class="shrink-0 text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(formatDate(alert.triggered_at)) + "</p>";
      list.appendChild(row);
    });
  }

  async function createAlertRule() {
    var dsSel  = $("#alerts-dataset");
    var name   = $("#alert-name");
    var errRate = $("#alert-error-rate");
    var spike  = $("#alert-spike");
    var msg    = $("#alert-create-msg");

    if (!dsSel || !dsSel.value) { window.alert("Please select a dataset first."); return; }

    var payload = {
      name: (name && name.value.trim()) || "Alert Rule",
      error_rate_threshold:     (errRate && errRate.value) ? parseFloat(errRate.value) : null,
      request_spike_multiplier: (spike   && spike.value)   ? parseFloat(spike.value)   : null,
    };
    if (payload.error_rate_threshold === null && payload.request_spike_multiplier === null) {
      window.alert("Please enter at least one threshold value.");
      return;
    }

    try {
      await LogSightAPI.createAlertRule(dsSel.value, payload);
      if (name)    name.value    = "";
      if (errRate) errRate.value = "";
      if (spike)   spike.value   = "";
      if (msg)     { msg.classList.remove("hidden"); setTimeout(function () { msg.classList.add("hidden"); }, 3000); }
      loadAlertsData(dsSel.value);
    } catch (err) {
      window.alert(err.message || "Failed to create rule.");
    }
  }

  async function checkAlerts() {
    var dsSel = $("#alerts-dataset");
    if (!dsSel || !dsSel.value) { window.alert("Please select a dataset first."); return; }

    var checkBtn = $("#alerts-check");
    var resultBanner = $("#alerts-check-result");
    if (checkBtn) { checkBtn.disabled = true; checkBtn.textContent = "Checking…"; }

    try {
      var result = await LogSightAPI.checkAlerts(dsSel.value);
      if (resultBanner) {
        resultBanner.classList.remove("hidden");
        if (result.count === 0) {
          resultBanner.textContent = "No thresholds exceeded — all clear.";
          resultBanner.className = "rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-800 dark:border-emerald-800 dark:bg-emerald-950/30 dark:text-emerald-300";
        } else {
          resultBanner.textContent = result.count + " alert(s) triggered! See history below.";
          resultBanner.className = "rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300";
        }
        setTimeout(function () { resultBanner.classList.add("hidden"); }, 6000);
      }
      loadAlertsData(dsSel.value);
    } catch (err) {
      window.alert(err.message || "Alert check failed.");
    } finally {
      if (checkBtn) {
        checkBtn.disabled = false;
        checkBtn.innerHTML = '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" /></svg> Check Now';
      }
    }
  }

  // -------------------------------------------------------------------------
  // Reports view
  // -------------------------------------------------------------------------

  async function initReportsView() {
    await populateSelect("report-dataset");

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
        if (typeof window.closeMobileSidebar === "function" && window.matchMedia("(max-width: 1023px)").matches) {
          window.closeMobileSidebar();
        }
      });
    });
  }

  // -------------------------------------------------------------------------
  // Boot
  // -------------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    initTheme();
    initSidebar();
    initUpload();
    bindNav();
    initDashboardControls();

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
