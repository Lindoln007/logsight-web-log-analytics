/**
 * Log Sight — SPA shell: routing, theme, uploads, datasets, dashboard, log viewer.
 */
(function () {
  "use strict";

  var THEME_KEY   = "log-sight-theme";
  var SIDEBAR_KEY = "log-sight-sidebar-collapsed";
  var routes = ["dashboard", "upload", "datasets", "viewer"];

  var viewerPage = 1;
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
    try {
      return new Date(iso).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    } catch (_) { return iso; }
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
      "<tr><td colspan=\"" + (colspan || 6) + "\" class=\"px-4 py-8 text-center text-slate-500 dark:text-slate-400\">" +
      escapeHtml(message) + "</td></tr>";
  }

  function buildStatusBadge(status) {
    var v = String(status || "pending").toLowerCase();
    if (v === "completed") return "bg-emerald-100 text-emerald-700 dark:bg-emerald-950/40 dark:text-emerald-300";
    if (v === "failed")    return "bg-red-100 text-red-700 dark:bg-red-950/40 dark:text-red-300";
    if (v === "processing") return "bg-amber-100 text-amber-700 dark:bg-amber-950/40 dark:text-amber-300";
    return "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300";
  }

  // -------------------------------------------------------------------------
  // Theme
  // -------------------------------------------------------------------------

  function initTheme() {
    var stored = localStorage.getItem(THEME_KEY);
    var prefersDark = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
    var dark = stored === "dark" || (stored !== "light" && prefersDark);
    applyTheme(dark);
    var btn = $("#theme-toggle");
    if (btn) {
      btn.addEventListener("click", function () {
        var isDark = document.documentElement.classList.toggle("dark");
        localStorage.setItem(THEME_KEY, isDark ? "dark" : "light");
        applyTheme(isDark);
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
    var collapsed = localStorage.getItem(SIDEBAR_KEY) === "1";
    var aside    = $("#sidebar");
    var main     = $("#main-shell");
    var toggle   = $("#sidebar-toggle");
    var mobileBtn = $("#sidebar-mobile-toggle");
    var backdrop = $("#sidebar-backdrop");
    var mq = window.matchMedia("(min-width: 1024px)");

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
        aside.classList.toggle("w-64", !c);
        aside.classList.toggle("w-[72px]", c);
        main.classList.toggle("lg:pl-64", !c);
        main.classList.toggle("lg:pl-[72px]", c);
      } else {
        aside.classList.remove("w-[72px]");
        aside.classList.add("w-64");
      }
      $all("[data-sidebar-text]").forEach(function (el) {
        var hide = c && isDesktop();
        el.classList.toggle("opacity-0", hide);
        el.classList.toggle("pointer-events-none", hide);
        el.classList.toggle("w-0", hide);
        el.classList.toggle("overflow-hidden", hide);
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

    // Clicking the brand logo while the sidebar is collapsed on desktop expands it.
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
      else { apply(false); closeMobileDrawer(); }
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
      link.classList.toggle("bg-indigo-600/15", active);
      link.classList.toggle("text-indigo-600",  active);
      link.classList.toggle("dark:text-indigo-400", active);
      link.classList.toggle("text-slate-600",   !active);
      link.classList.toggle("dark:text-slate-400", !active);
    });
  }

  function showView(route) {
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
    var titles = { dashboard: "Dashboard", upload: "Upload Logs", datasets: "Datasets", viewer: "Log Viewer" };
    var titleEl = $("#page-title");
    if (titleEl) titleEl.textContent = titles[route] || "Dashboard";
    updateNavActive(route);
  }

  function runRoute(route) {
    showView(route);
    if (route === "dashboard") loadDashboardData();
    if (route === "datasets")  loadDatasetsTable();
    if (route === "viewer")    initLogViewer();
  }

  function navigate(route) { setHashRoute(route); }

  window.addEventListener("hashchange", function () { runRoute(getHashRoute()); });

  // -------------------------------------------------------------------------
  // Dashboard — dataset selector
  // -------------------------------------------------------------------------

  /**
   * Populate the dashboard dataset <select> with all datasets,
   * preserving any pre-existing selection.
   * Returns the list of datasets.
   */
  async function populateDashboardSelect() {
    var select = $("#dashboard-dataset");
    if (!select) return [];

    var current = LogSightAPI.getCurrentDataset() || select.value;
    var rows = await LogSightAPI.listDatasets();

    // Build option list — completed datasets first, then others.
    var completed = rows.filter(function (d) { return d.status === "completed"; });
    var others    = rows.filter(function (d) { return d.status !== "completed"; });

    select.innerHTML = '<option value="">— Select dataset —</option>';
    completed.concat(others).forEach(function (d) {
      var opt = document.createElement("option");
      opt.value = d.id;
      opt.textContent = d.name + (d.status !== "completed" ? " (" + d.status + ")" : "");
      select.appendChild(opt);
    });

    // Restore prior selection or default to the newest completed dataset.
    if (current && rows.some(function (r) { return r.id === current; })) {
      select.value = current;
    } else if (completed.length > 0) {
      select.value = completed[0].id;
    }

    // Keep API state in sync.
    LogSightAPI.setCurrentDataset(select.value || null);
    return rows;
  }

  // -------------------------------------------------------------------------
  // Dashboard — data loading
  // -------------------------------------------------------------------------

  async function loadDashboardData() {
    // Show loading state.
    renderStatValue("stat-requests", null, "", "Loading…");
    renderStatValue("stat-errors",   null, "", "Loading…");
    renderStatValue("stat-latency",  null, "", "Loading…");
    renderStatValue("stat-visitors", null, "", "Loading…");

    var period = $("#stat-period");
    if (period) { period.textContent = "Loading…"; period.classList.remove("hidden"); }

    // Populate selector and determine which dataset to analyse.
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

    // Stat tiles.
    renderStatValue("stat-requests", summary.totalRequests != null ? formatNumber(summary.totalRequests) : null, "", "No data");
    renderStatValue("stat-errors",   summary.errorRate    != null ? Number(summary.errorRate).toFixed(2) : null, "%", "No data");
    renderStatValue("stat-latency",  summary.avgResponseSize != null ? formatNumber(summary.avgResponseSize) : null, " B", "No data");
    renderStatValue("stat-visitors", summary.uniqueVisitors != null ? formatNumber(summary.uniqueVisitors) : null, "", "No data");

    // Sub-labels.
    if (summary.totalErrors != null) {
      renderSubText("stat-errors-abs", formatNumber(summary.totalErrors));
    }

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
    // Dataset selector change.
    var select = $("#dashboard-dataset");
    if (select && !select._bound) {
      select._bound = true;
      select.addEventListener("change", function () {
        LogSightAPI.setCurrentDataset(select.value || null);
        loadDashboardData();
      });
    }

    // Refresh button.
    var refreshBtn = $("#dashboard-refresh");
    if (refreshBtn && !refreshBtn._bound) {
      refreshBtn._bound = true;
      refreshBtn.addEventListener("click", async function () {
        var id = LogSightAPI.getCurrentDataset();
        if (!id) return;
        refreshBtn.disabled = true;
        refreshBtn.textContent = "Refreshing…";
        try {
          await LogSightAPI.refreshAnalytics(id);
        } catch (_) { /* non-fatal */ }
        refreshBtn.disabled = false;
        refreshBtn.innerHTML =
          '<svg class="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">' +
          '<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" ' +
          'd="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0' +
          'a8.003 8.003 0 01-15.357-2m15.357 2H15" /></svg> Refresh';
        loadDashboardData();
      });
    }
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
        '<div class="min-w-0">' +
        '<p class="truncate font-medium text-slate-800 dark:text-slate-100">' + escapeHtml(file.name) + "</p>" +
        '<p class="text-xs text-slate-500 dark:text-slate-400">' + escapeHtml(formatBytes(file.size)) + "</p>" +
        "</div>" +
        '<div class="w-full sm:w-56">' +
        '<div class="h-2 overflow-hidden rounded-full bg-slate-200 dark:bg-slate-700">' +
        '<div class="progress-shimmer h-full w-full rounded-full bg-indigo-500"></div>' +
        "</div>" +
        '<p class="mt-1 text-xs text-slate-500 dark:text-slate-400 upload-status">Uploading…</p>' +
        "</div></div>";
      list.prepend(row);

      var statusEl = $(".upload-status", row);

      function onParseProgress() {
        if (statusEl) statusEl.textContent = "Parsing… this may take a moment for large files.";
      }

      LogSightAPI.uploadDataset(file, { onProgress: onParseProgress }).then(function (dataset) {
        var shimmer = $(".progress-shimmer", row);
        if (shimmer) { shimmer.classList.remove("progress-shimmer"); shimmer.classList.add("bg-emerald-500"); }
        row.classList.add("ring-2", "ring-emerald-500/30");

        // Auto-select the freshly uploaded dataset on the dashboard.
        LogSightAPI.setCurrentDataset(dataset.id);

        if (getHashRoute() === "datasets") loadDatasetsTable();
        // Refresh the dashboard selector silently so it includes the new dataset.
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
        '<td class="px-4 py-3">' +
          '<span class="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700 dark:bg-slate-800 dark:text-slate-300">' +
          escapeHtml(dataset.source) + "</span></td>" +
        '<td class="px-4 py-3 text-slate-600 dark:text-slate-400">' + escapeHtml(formatBytes(dataset.sizeBytes)) + "</td>" +
        '<td class="px-4 py-3 tabular-nums text-slate-600 dark:text-slate-400">' + escapeHtml(entriesText) + "</td>" +
        '<td class="px-4 py-3 text-slate-600 dark:text-slate-400">' + escapeHtml(formatDate(dataset.uploadedAt)) + "</td>" +
        '<td class="px-4 py-3 text-right whitespace-nowrap">' +
          '<div class="inline-flex items-center gap-2">' +
          '<span class="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ' + buildStatusBadge(dataset.status) + '">' + escapeHtml(dataset.status) + "</span>" +
          '<button type="button" data-analyse-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-indigo-200 px-2.5 py-1 text-xs font-medium text-indigo-600 transition-colors hover:bg-indigo-50 dark:border-indigo-800 dark:text-indigo-400 dark:hover:bg-indigo-950/50"' +
          (dataset.status !== "completed" ? " disabled title=\"Dataset not yet completed\"" : "") + ">Analyse</button>" +
          '<button type="button" data-view-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-slate-200 px-2.5 py-1 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50 dark:border-slate-600 dark:text-slate-300 dark:hover:bg-slate-800/80">View</button>' +
          '<button type="button" data-del-ds="' + escapeHtml(dataset.id) + '" class="inline-flex items-center rounded-lg border border-red-200 px-2.5 py-1 text-xs font-medium text-red-600 transition-colors hover:bg-red-50 dark:border-red-900/50 dark:text-red-400 dark:hover:bg-red-950/30">Delete</button>' +
          "</div></td>";
      tbody.appendChild(tr);
    });

    // "Analyse" — switch to dashboard with that dataset selected.
    tbody.querySelectorAll("[data-analyse-ds]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var dsId = btn.getAttribute("data-analyse-ds");
        LogSightAPI.setCurrentDataset(dsId);
        setHashRoute("dashboard");
      });
    });

    // "View" — open log viewer for that dataset.
    tbody.querySelectorAll("[data-view-ds]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        pendingViewerDatasetId = btn.getAttribute("data-view-ds") || "";
        setHashRoute("viewer");
      });
    });

    // "Delete".
    tbody.querySelectorAll("[data-del-ds]").forEach(function (btn) {
      btn.addEventListener("click", async function () {
        var dsId = btn.getAttribute("data-del-ds");
        if (!confirm("Permanently delete this dataset and all its log entries?")) return;
        try {
          await LogSightAPI.deleteDataset(dsId);
          await loadDatasetsTable();
          // Refresh viewer select if the deleted dataset was loaded there.
          var viewerSelect = $("#viewer-dataset");
          if (viewerSelect && viewerSelect.value === dsId) {
            await populateViewerSelect();
            viewerPage = 1;
            fetchAndRenderLogs();
          }
          // Refresh dashboard select if needed.
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
    if (tbody) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500 dark:text-slate-400">' +
        '<span class="loading-inline">' + escapeHtml(message) + "</span></td></tr>";
    }
    if (meta) meta.textContent = "";
    updateLogPagination(true, true);
  }

  function setLogsMessage(message) {
    var tbody = $("#logs-body");
    var meta  = $("#logs-meta");
    if (tbody) {
      tbody.innerHTML =
        '<tr><td colspan="5" class="px-4 py-8 text-center text-slate-500 dark:text-slate-400">' +
        escapeHtml(message) + "</td></tr>";
    }
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

    if (current && rows.some(function (r) { return r.id === current; })) {
      select.value = current;
    }
    pendingViewerDatasetId = "";
    return rows;
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

    // Pagination meta.
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
    if (prev) {
      prev.onclick = function () {
        if (viewerPage > 1) { viewerPage--; fetchAndRenderLogs(); }
      };
    }
    if (next) {
      next.onclick = function () {
        if (viewerPage < result.totalPages) { viewerPage++; fetchAndRenderLogs(); }
      };
    }
  }

  function initLogViewer() {
    populateViewerSelect().then(function (rows) {
      var select = $("#viewer-dataset");
      // If a pending dataset was requested (from "View" button), it's already set.
      // Otherwise default to first row.
      if (select && !select.value && rows.length) select.value = rows[0].id;
      viewerPage = 1;
      fetchAndRenderLogs();
    });

    var select    = $("#viewer-dataset");
    var statusSel = $("#viewer-status");
    var search    = $("#viewer-search");
    var dateFrom  = $("#viewer-date-from");
    var dateTo    = $("#viewer-date-to");
    var refresh   = $("#logs-refresh");

    if (select && !select._bound) {
      select._bound = true;
      select.addEventListener("change", function () { viewerPage = 1; fetchAndRenderLogs(); });
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

    if (!window.location.hash) {
      window.location.hash = "#/dashboard";
    } else {
      runRoute(getHashRoute());
    }
  });
})();
