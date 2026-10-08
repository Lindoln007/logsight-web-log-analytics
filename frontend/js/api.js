/**
 * LogSight API layer.
 *
 * Centralises all backend communication.  Each public method returns a
 * normalised object whose shape is stable regardless of whether the backend
 * returns real data or the request fails — callers never need to null-check.
 *
 * Dataset-scoped analytics
 * ------------------------
 * All analytics endpoints require a dataset ID.  The module keeps a
 * _currentDatasetId variable that is set by the app layer when the user
 * selects a dataset.  Analytics methods use it automatically, but every
 * method also accepts an explicit datasetId argument that takes precedence.
 *
 * Usage:
 *   LogSightAPI.setCurrentDataset("42");
 *   const summary = await LogSightAPI.getDashboardSummary();
 *   // or explicitly:
 *   const summary = await LogSightAPI.getDashboardSummary("42");
 */
(function (global) {
  "use strict";

  // ---------------------------------------------------------------------------
  // Config
  // ---------------------------------------------------------------------------

  const existingConfig = global.LogSightConfig || {};
  const apiConfig = existingConfig.api || {};

  /**
   * Build a date-range query-string fragment from an opts object.
   * opts = { dateFrom: "YYYY-MM-DD", dateTo: "YYYY-MM-DD" }
   * alreadyHasQs = true when the URL already contains a "?" character.
   */
  function _dateQs(opts, alreadyHasQs) {
    var parts = [];
    if (opts && opts.dateFrom) parts.push("date_from=" + encodeURIComponent(opts.dateFrom));
    if (opts && opts.dateTo)   parts.push("date_to="   + encodeURIComponent(opts.dateTo));
    if (!parts.length) return "";
    return (alreadyHasQs ? "&" : "?") + parts.join("&");
  }

  /** Build a dataset-scoped analytics URL from the path suffix. */
  function analyticsUrl(datasetId, suffix) {
    return (
      "/logs/datasets/" +
      encodeURIComponent(datasetId) +
      "/analytics/" +
      (suffix ? suffix + "/" : "")
    );
  }

  /** Build the log entries URL for a dataset with optional query params. */
  function entriesUrl(datasetId, query) {
    var base = "/logs/datasets/" + encodeURIComponent(datasetId) + "/entries/";
    var q = query || {};
    var parts = [];
    if (q.page)     parts.push("page="      + encodeURIComponent(q.page));
    if (q.pageSize) parts.push("page_size=" + encodeURIComponent(q.pageSize));
    if (q.status && q.status !== "all") parts.push("status=" + encodeURIComponent(q.status));
    if (q.search)   parts.push("search="   + encodeURIComponent(q.search));
    if (q.dateFrom) parts.push("date_from=" + encodeURIComponent(q.dateFrom));
    if (q.dateTo)   parts.push("date_to="   + encodeURIComponent(q.dateTo));
    return parts.length ? base + "?" + parts.join("&") : base;
  }

  const config = {
    api: {
      datasets: apiConfig.datasets || "/logs/datasets/",
      upload: apiConfig.upload || "/logs/upload/",
      deleteDataset:
        apiConfig.deleteDataset ||
        function (datasetId) {
          return "/logs/datasets/" + encodeURIComponent(datasetId) + "/delete/";
        },
      // datasetLogs is now built dynamically via entriesUrl(); keep the slot
      // for any external override but default to the real backend endpoint.
      datasetLogs:
        apiConfig.datasetLogs ||
        function (datasetId, query) {
          return entriesUrl(datasetId, query);
        },
    },
  };

  global.LogSightConfig = config;

  // ---------------------------------------------------------------------------
  // Current dataset state
  // ---------------------------------------------------------------------------

  let _currentDatasetId = null;
  const _responseCache = new Map();
  const RESPONSE_CACHE_TTL_MS = 30 * 1000;

  // ---------------------------------------------------------------------------
  // HTTP helpers
  // ---------------------------------------------------------------------------

  function getCookie(name) {
    const parts = (document.cookie || "").split(";").map(function (p) {
      return p.trim();
    });
    for (var i = 0; i < parts.length; i++) {
      if (parts[i].indexOf(name + "=") === 0) {
        return decodeURIComponent(parts[i].slice(name.length + 1));
      }
    }
    return "";
  }

  function buildHeaders(extra) {
    var headers = new Headers(extra || {});
    var csrf = getCookie("csrftoken");
    if (csrf && !headers.has("X-CSRFToken")) headers.set("X-CSRFToken", csrf);
    return headers;
  }

  async function fetchJson(url, options) {
    if (!url) return { ok: false, status: 0, missing: true, data: null };
    try {
      var response = await fetch(url, {
        credentials: "same-origin",
        ...(options || {}),
        headers: buildHeaders(options && options.headers),
      });
      if (response.redirected && response.url && response.url.indexOf("/accounts/login/") >= 0) {
        window.location.href = response.url;
        return { ok: false, status: 302, missing: false, data: null };
      }
      var ct = response.headers.get("content-type") || "";
      var data = ct.indexOf("application/json") >= 0 ? await response.json() : null;
      return { ok: response.ok, status: response.status, missing: response.status === 404, data: data };
    } catch (_) {
      return { ok: false, status: 0, missing: true, data: null };
    }
  }

  async function fetchCachedJson(cacheKey, url, options) {
    var now = Date.now();
    var cached = _responseCache.get(cacheKey);
    if (cached && now - cached.createdAt < RESPONSE_CACHE_TTL_MS) return cached.value;

    var value = cached && cached.value && typeof cached.value.then === "function"
      ? cached.value
      : fetchJson(url, options);
    _responseCache.set(cacheKey, { createdAt: now, value: value });

    var result = await value;
    if (!result.ok) _responseCache.delete(cacheKey);
    else if (_responseCache.get(cacheKey) && _responseCache.get(cacheKey).value === value) {
      _responseCache.set(cacheKey, { createdAt: Date.now(), value: result });
    }
    return result;
  }

  function clearResponseCache(prefix) {
    _responseCache.forEach(function (_, key) {
      if (!prefix || key.indexOf(prefix) === 0) _responseCache.delete(key);
    });
  }

  // ---------------------------------------------------------------------------
  // Safe fallback shapes
  // ---------------------------------------------------------------------------

  function emptyDashboardSummary() {
    return {
      available: false,
      totalRequests: null,
      errorRate: null,
      errors4xx: null,
      errors5xx: null,
      avgResponseSize: null,
      uniqueVisitors: null,
      periodLabel: "Select a dataset to see analytics",
      datasetId: null,
    };
  }

  function emptyRequestFrequency() {
    return { available: false, labels: [], datasets: [{ label: "Requests", data: [] }] };
  }

  function emptyStatusDistribution() {
    return { available: false, labels: [], data: [], by_code: [] };
  }

  function emptyTopEndpoints() {
    return { available: false, items: [] };
  }

  function emptyTrafficPattern() {
    return {
      available: false,
      labels: [],
      datasets: [{ label: "Requests by Hour", data: [] }],
      peak_hour: null,
      quiet_hour: null,
    };
  }

  function emptyLogLines(page, pageSize) {
    return {
      available: false,
      message: "Select a dataset to view log entries.",
      items: [],
      total: 0,
      page: page,
      pageSize: pageSize,
      totalPages: 1,
    };
  }

  // ---------------------------------------------------------------------------
  // Normalisation helpers
  // ---------------------------------------------------------------------------

  function normalizeDataset(dataset) {
    var fileName = dataset.file_name || dataset.fileName || dataset.name || "Untitled dataset";
    return {
      id: String(dataset.id),
      name: dataset.name || fileName,
      fileName: fileName,
      source: detectSource(fileName),
      sizeBytes: dataset.file_size || dataset.fileSize || 0,
      uploadedAt: dataset.uploaded_at || dataset.uploadedAt || null,
      totalEntries: dataset.total_entries || dataset.totalEntries || 0,
      status: dataset.status || "pending",
      description: dataset.description || "",
      processingError: dataset.processing_error || null,
    };
  }

  function detectSource(fileName) {
    var v = String(fileName || "").toLowerCase();
    if (v.indexOf("apache") >= 0) return "Apache";
    if (v.indexOf("nginx") >= 0) return "Nginx";
    if (v) return "Log file";
    return "Unknown";
  }

  function formatDateRange(start, end) {
    if (!start) return "No date range";
    try {
      var s = new Date(start).toLocaleDateString(undefined, { dateStyle: "medium" });
      if (!end) return s;
      var e = new Date(end).toLocaleDateString(undefined, { dateStyle: "medium" });
      return s === e ? s : s + " \u2013 " + e;
    } catch (_) {
      return start;
    }
  }

  function formatHourLabel(iso) {
    if (!iso) return "";
    try {
      var d = new Date(iso);
      return d.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    } catch (_) {
      return iso;
    }
  }

  function normalizeLogLine(line, index) {
    return {
      id: String(line.id || "log-" + index),
      timestamp: line.timestamp || line.time || null,
      // Backend sends status_code (int); viewer expects status (string).
      status: String(line.status_code || line.status || line.statusCode || ""),
      method: line.method || "",
      endpoint: line.endpoint || "",
      ip_address: line.ip_address || line.ipAddress || "",
      responseSize: line.response_size || line.responseSize || null,
      raw: line.raw || line.raw_line || line.rawLine || "",
    };
  }

  function extractErrorMessage(errors) {
    if (!errors || typeof errors !== "object") return "";
    var firstKey = Object.keys(errors)[0];
    if (!firstKey || !Array.isArray(errors[firstKey]) || !errors[firstKey].length) return "";
    return errors[firstKey][0].message || "";
  }

  // ---------------------------------------------------------------------------
  // Public API
  // ---------------------------------------------------------------------------

  global.LogSightAPI = {

    // ---- Dataset state -------------------------------------------------------

    /** Store the ID of the currently selected dataset. */
    setCurrentDataset: function (datasetId) {
      _currentDatasetId = datasetId ? String(datasetId) : null;
    },

    /** Retrieve the currently selected dataset ID. */
    getCurrentDataset: function () {
      return _currentDatasetId;
    },

    // ---- Dataset management --------------------------------------------------

    async getUploadConfig() {
      var result = await fetchJson(config.api.upload, { method: "GET" });
      return result.ok && result.data ? result.data : null;
    },

    async listDatasets() {
      var result = await fetchCachedJson("datasets", config.api.datasets, { method: "GET" });
      if (!result.ok || !result.data) return [];
      var datasets = Array.isArray(result.data.datasets) ? result.data.datasets : [];
      return datasets.map(normalizeDataset);
    },

    async uploadDataset(file, options) {
      var formData = new FormData();
      formData.append("file", file);
      formData.append("name", file.name || "Uploaded log");

      var result = await fetchJson(config.api.upload, { method: "POST", body: formData });

      if (!result.ok || !result.data || !result.data.dataset) {
        var errors = result.data && result.data.errors ? result.data.errors : null;
        throw new Error(extractErrorMessage(errors) || "Upload failed.");
      }

      var dataset = normalizeDataset(result.data.dataset);
      clearResponseCache("datasets");

      // 202 Accepted means the file was saved and parsing is running in the
      // background.  Poll until the dataset reaches a terminal status.
      if (result.status === 202) {
        var onProgress = options && typeof options.onProgress === "function"
          ? options.onProgress
          : null;
        dataset = await LogSightAPI.pollDatasetUntilComplete(dataset.id, onProgress);
      }

      return dataset;
    },

    /**
     * Poll GET /logs/datasets/<id>/status/ until status is "completed" or "failed".
     *
     * Uses the lightweight status endpoint rather than the full dataset detail
     * response to keep polling traffic minimal.
     *
     * Resolves with a normalised dataset object when status reaches "completed".
     * Rejects with an Error if:
     *   - status reaches "failed"  (error message from server is included)
     *   - the dataset is still not finished after 30 minutes
     *
     * @param {string}   datasetId
     * @param {Function} [onProgress]  called with the current status object on each tick
     */
    async pollDatasetUntilComplete(datasetId, onProgress) {
      var deadline  = Date.now() + 30 * 60 * 1000;
      var statusUrl = "/logs/datasets/" + encodeURIComponent(datasetId) + "/status/";

      for (var i = 0; Date.now() < deadline; i++) {
        // Poll every second at first, then ease off for long-running parses.
        var intervalMs = i < 30 ? 1000 : 2500;
        await new Promise(function (resolve) { setTimeout(resolve, intervalMs); });

        var result = await fetchJson(statusUrl, { method: "GET" });

        if (result.ok && result.data) {
          var data   = result.data;
          var status = data.status || "pending";

          if (status === "completed") {
            // Return a shape compatible with normalizeDataset so callers receive
            // a consistent object regardless of whether processing was sync or async.
            return normalizeDataset({
              id:               data.id,
              name:             data.name,
              file_name:        data.name,
              file_size:        data.file_size,
              uploaded_at:      data.uploaded_at,
              total_entries:    data.total_entries,
              status:           data.status,
              processing_error: data.processing_error,
            });
          }

          if (status === "failed") {
            var reason = data.processing_error
              || "Log parsing failed. Make sure the file is a valid Apache or Nginx access log.";
            throw new Error(reason);
          }

          if (typeof onProgress === "function") onProgress(data);
        }
      }

      throw new Error(
        "Processing is taking longer than expected. Check the Datasets page for the current status."
      );
    },

    async deleteDataset(datasetId) {
      var deleteUrl =
        typeof config.api.deleteDataset === "function"
          ? config.api.deleteDataset(datasetId)
          : config.api.deleteDataset;

      var result = await fetchJson(deleteUrl, { method: "POST" });
      if (!result.ok) throw new Error("Dataset deletion failed.");
      // Clear current dataset state if the deleted one was active
      if (_currentDatasetId === String(datasetId)) _currentDatasetId = null;
      clearResponseCache("datasets");
      return result.data || { deleted: true };
    },

    // ---- Analytics -----------------------------------------------------------

    /**
     * Return summary metrics for a dataset.
     * Normalises the backend shape into the flat object expected by the dashboard stat tiles.
     */
    async getDashboardSummary(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) return emptyDashboardSummary();

      var result = await fetchCachedJson("summary:" + id, analyticsUrl(id, "summary"), { method: "GET" });
      if (!result.ok || !result.data) return emptyDashboardSummary();

      var d = result.data;
      var dr = d.date_range || {};

      return {
        available: true,
        datasetId: id,
        totalRequests: d.total_requests != null ? d.total_requests : null,
        totalErrors: d.total_errors != null ? d.total_errors : null,
        errors4xx: d.errors_4xx != null ? d.errors_4xx : null,
        errors5xx: d.errors_5xx != null ? d.errors_5xx : null,
        errorRate: d.error_rate != null ? d.error_rate : null,
        avgResponseSize: d.avg_response_size != null ? Math.round(d.avg_response_size) : null,
        uniqueVisitors: d.unique_ips != null ? d.unique_ips : null,
        periodLabel: formatDateRange(dr.start, dr.end),
        durationHours: dr.duration_hours || null,
        dateRange: dr,
      };
    },

    /**
     * Return hourly time-series data shaped for Chart.js line charts.
     * Accepts ?interval=hour|day via the optional params argument.
     */
    async getRequestFrequency(datasetId, params) {
      var id = datasetId || _currentDatasetId;
      if (!id) return emptyRequestFrequency();

      var interval = (params && params.interval) || "hour";
      var limit    = (params && params.limit)    || 168;
      var url = analyticsUrl(id, "request-trends") +
        "?interval=" + encodeURIComponent(interval) +
        "&limit="    + encodeURIComponent(limit) +
        _dateQs(params, true);

      var result = await fetchCachedJson("trends:" + id + ":" + url, url, { method: "GET" });
      if (!result.ok || !result.data || !Array.isArray(result.data.data)) {
        return emptyRequestFrequency();
      }

      var items = result.data.data;
      return {
        available: true,
        interval: result.data.interval || interval,
        labels: items.map(function (item) {
          return item.timestamp ? formatHourLabel(item.timestamp) : "";
        }),
        datasets: [
          {
            label: "Requests",
            data: items.map(function (item) {
              return item.count;
            }),
          },
        ],
      };
    },

    /**
     * Return status-code distribution shaped for Chart.js doughnut charts.
     * by_category keys (2xx, 3xx, 4xx, 5xx) become chart labels.
     */
    async getStatusDistribution(datasetId, opts) {
      var id = datasetId || _currentDatasetId;
      if (!id) return emptyStatusDistribution();

      var url = analyticsUrl(id, "status-distribution") + _dateQs(opts);
      var result = await fetchCachedJson("status:" + id + ":" + url, url, { method: "GET" });
      if (!result.ok || !result.data) return emptyStatusDistribution();

      var categories = result.data.by_category || {};
      // Only include categories that actually have data to keep the chart clean.
      var labels = Object.keys(categories).filter(function (k) {
        return categories[k] > 0;
      });
      var data = labels.map(function (k) {
        return categories[k];
      });

      return {
        available: true,
        labels: labels,
        data: data,
        by_code: result.data.by_code || [],
        by_category: categories,
      };
    },

    /**
     * Return top endpoints shaped for Chart.js bar charts.
     * items[].label  — truncated endpoint path
     * items[].value  — request count
     * items[].percentage — share of total traffic
     */
    async getTopEndpoints(datasetId, limit, opts) {
      var id = datasetId || _currentDatasetId;
      if (!id) return emptyTopEndpoints();

      var n   = limit || 10;
      var url = analyticsUrl(id, "top-endpoints") + "?limit=" + encodeURIComponent(n) + _dateQs(opts, true);
      var result = await fetchCachedJson("endpoints:" + id + ":" + url, url, { method: "GET" });
      if (!result.ok || !result.data) return emptyTopEndpoints();

      var endpoints = Array.isArray(result.data.endpoints) ? result.data.endpoints : [];
      return {
        available: true,
        totalUniqueEndpoints: result.data.total_unique_endpoints || 0,
        items: endpoints.map(function (e) {
          return {
            label: e.endpoint,
            value: e.count,
            percentage: e.percentage,
          };
        }),
      };
    },

    /**
     * Return hourly traffic pattern shaped for Chart.js area/bar charts.
     * 24 labels (0:00 – 23:00) with corresponding request counts.
     */
    async getTrafficPattern(datasetId, opts) {
      var id = datasetId || _currentDatasetId;
      if (!id) return emptyTrafficPattern();

      var url = analyticsUrl(id, "hourly-traffic") + _dateQs(opts);
      var result = await fetchCachedJson("hourly:" + id + ":" + url, url, { method: "GET" });
      if (!result.ok || !result.data || !Array.isArray(result.data.traffic)) {
        return emptyTrafficPattern();
      }

      var traffic = result.data.traffic;
      return {
        available: true,
        peak_hour: result.data.peak_hour,
        quiet_hour: result.data.quiet_hour,
        labels: traffic.map(function (t) {
          var h = String(t.hour).padStart(2, "0");
          return h + ":00";
        }),
        datasets: [
          {
            label: "Requests by Hour",
            data: traffic.map(function (t) {
              return t.count;
            }),
          },
        ],
      };
    },

    /**
     * Return error breakdown (4xx vs 5xx, top error codes, top error endpoints).
     * Not consumed by the default dashboard charts but available for detail panels.
     */
    async getErrorBreakdown(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) return { available: false, total_errors: 0, errors_4xx: 0, errors_5xx: 0 };

      var result = await fetchJson(analyticsUrl(id, "errors"), { method: "GET" });
      if (!result.ok || !result.data) {
        return { available: false, total_errors: 0, errors_4xx: 0, errors_5xx: 0 };
      }
      return { available: true, ...result.data };
    },

    /**
     * Force-recompute analytics for a dataset and return the refresh timestamp.
     */
    async refreshAnalytics(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");

      var result = await fetchJson(analyticsUrl(id, "refresh"), { method: "POST" });
      if (!result.ok) throw new Error("Analytics refresh failed.");
      clearResponseCache("summary:" + id);
      clearResponseCache("trends:" + id);
      clearResponseCache("status:" + id);
      clearResponseCache("endpoints:" + id);
      clearResponseCache("hourly:" + id);
      return result.data || { refreshed: true };
    },

    // ---- Log viewer ----------------------------------------------------------

    async getLogLines(datasetId, query) {
      var q = query || {};
      var page = Math.max(1, parseInt(q.page, 10) || 1);
      var pageSize = Math.min(100, Math.max(10, parseInt(q.pageSize, 10) || 25));

      var id = datasetId || _currentDatasetId;
      if (!id) return emptyLogLines(page, pageSize);

      var url =
        typeof config.api.datasetLogs === "function"
          ? config.api.datasetLogs(id, q)
          : config.api.datasetLogs;

      if (!url) return emptyLogLines(page, pageSize);

      var result = await fetchJson(url, { method: "GET" });
      if (!result.ok || !result.data) return emptyLogLines(page, pageSize);

      var d = result.data;
      var items = Array.isArray(d.items) ? d.items.map(normalizeLogLine) : [];

      var resolvedPageSize = d.page_size || d.pageSize || pageSize;
      var totalPages       = d.total_pages || d.totalPages || 1;

      return {
        available: true,
        message: "",
        items: items,
        total: d.total || 0,
        page: d.page || page,
        pageSize: resolvedPageSize,
        totalPages: totalPages,
      };
    },

    // ---- Traffic forecasting ------------------------------------------------

    /**
     * Fetch a traffic forecast for *datasetId*.
     * @param {string} datasetId
     * @param {object} params  { periods: int, method: "moving_avg"|"linear_regression" }
     */
    async getTrafficForecast(datasetId, params) {
      var id = datasetId || _currentDatasetId;
      if (!id) return { available: false, historical: [], forecast: [], trend: "stable", summary: {} };

      var p = params || {};
      var url = "/logs/datasets/" + encodeURIComponent(id) + "/forecast/?" +
        "periods=" + encodeURIComponent(p.periods || 24) +
        "&method=" + encodeURIComponent(p.method || "moving_avg");

      var result = await fetchJson(url, { method: "GET" });
      if (!result.ok || !result.data) {
        return { available: false, historical: [], forecast: [], trend: "stable", summary: {} };
      }
      return { available: true, ...result.data };
    },

    // ---- Anomaly detection --------------------------------------------------

    /**
     * Run anomaly detection for *datasetId*.
     * @param {string} datasetId
     * @param {object} params  { sensitivity: "low"|"medium"|"high" }
     */
    async getAnomalies(datasetId, params) {
      var id = datasetId || _currentDatasetId;
      if (!id) return { available: false, anomalies: [], summary: { total_anomalies: 0 } };

      var sensitivity = (params && params.sensitivity) || "medium";
      var method = (params && params.method) || "statistical";
      var url = "/logs/datasets/" + encodeURIComponent(id) + "/anomalies/" +
        "?sensitivity=" + encodeURIComponent(sensitivity) +
        "&method=" + encodeURIComponent(method);

      var result = await fetchJson(url, { method: "GET" });
      if (!result.ok || !result.data) {
        return { available: false, anomalies: [], summary: { total_anomalies: 0 } };
      }
      return { available: true, ...result.data };
    },

    // ---- Alerts -------------------------------------------------------------

    /** List alert rules and recent history for *datasetId*. */
    async getAlerts(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) return { rules: [], recent_alerts: [] };

      var result = await fetchJson("/logs/datasets/" + encodeURIComponent(id) + "/alerts/", { method: "GET" });
      if (!result.ok || !result.data) return { rules: [], recent_alerts: [] };
      return result.data;
    },

    /** Create a new alert rule. */
    async createAlertRule(datasetId, data) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");

      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/alerts/",
        { method: "POST", body: JSON.stringify(data), headers: { "Content-Type": "application/json" } }
      );
      if (!result.ok) throw new Error("Failed to create alert rule.");
      return result.data;
    },

    /** Delete an alert rule by ID. */
    async deleteAlertRule(datasetId, ruleId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");

      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/alerts/" + encodeURIComponent(ruleId) + "/",
        { method: "DELETE" }
      );
      if (!result.ok) throw new Error("Failed to delete alert rule.");
      return result.data;
    },

    /** Check active alert rules against current dataset metrics. */
    async checkAlerts(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");

      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/alerts/check/",
        { method: "POST" }
      );
      if (!result.ok) throw new Error("Alert check failed.");
      return result.data;
    },

    /** Run the built-in smart health checks (no user rules required). */
    async autoCheckAlerts(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");

      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/alerts/auto/",
        { method: "GET" }
      );
      if (!result.ok) throw new Error("Health check failed.");
      return result.data;
    },

    // ---- Security Insights --------------------------------------------------

    async getSecurityInsights(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");
      var result = await fetchJson("/logs/datasets/" + encodeURIComponent(id) + "/security/");
      if (!result.ok) throw new Error("Security analysis failed.");
      return result.data;
    },

    // ---- Dataset Comparison -------------------------------------------------

    async compareDatasets(idA, idB) {
      if (!idA || !idB) throw new Error("Two dataset IDs required.");
      var result = await fetchJson("/logs/compare/?a=" + encodeURIComponent(idA) + "&b=" + encodeURIComponent(idB));
      if (!result.ok) throw new Error("Comparison failed.");
      return result.data;
    },

    // ---- Filtered Summary (dashboard date range) ----------------------------

    async getFilteredSummary(datasetId, dateFrom, dateTo) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");
      var qs = "";
      if (dateFrom) qs += "?date_from=" + encodeURIComponent(dateFrom);
      if (dateTo)   qs += (qs ? "&" : "?") + "date_to=" + encodeURIComponent(dateTo);
      var url = "/logs/datasets/" + encodeURIComponent(id) + "/analytics/filtered-summary/" + qs;
      var result = await fetchCachedJson("filtered-summary:" + id + ":" + qs, url);
      if (!result.ok) throw new Error("Filtered summary failed.");
      return result.data;
    },

    // ---- Notifications ------------------------------------------------------

    async getNotifications(unreadOnly) {
      var qs = unreadOnly ? "?unread=1" : "";
      var result = await fetchJson("/logs/notifications/" + qs);
      if (!result.ok) throw new Error("Failed to load notifications.");
      return result.data;
    },

    async getNotifCount() {
      var result = await fetchJson("/logs/notifications/count/");
      if (!result.ok) return { unread_count: 0 };
      return result.data;
    },

    async markNotifRead(notifId) {
      var result = await fetchJson("/logs/notifications/" + encodeURIComponent(notifId) + "/read/", { method: "POST" });
      return result.ok;
    },

    async markAllNotifsRead() {
      var result = await fetchJson("/logs/notifications/read-all/", { method: "POST" });
      return result.ok;
    },

    async deleteNotif(notifId) {
      var result = await fetchJson("/logs/notifications/" + encodeURIComponent(notifId) + "/delete/", { method: "POST" });
      return result.ok;
    },

    async deleteAllNotifs() {
      var result = await fetchJson("/logs/notifications/delete-all/", { method: "POST" });
      return result.ok;
    },

    // ---- Log Simulator ------------------------------------------------------

    async simulateLogs(datasetId, count) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");
      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/simulate/?count=" + (count || 10),
        { method: "POST" }
      );
      if (!result.ok) throw new Error("Simulation failed.");
      return result.data;
    },

    async simulateErrorSpike(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");
      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/simulate/error-spike/",
        { method: "POST" }
      );
      if (!result.ok) throw new Error("Error spike simulation failed.");
      return result.data;
    },

    async simulateTrafficSpike(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");
      var result = await fetchJson(
        "/logs/datasets/" + encodeURIComponent(id) + "/simulate/traffic-spike/",
        { method: "POST" }
      );
      if (!result.ok) throw new Error("Traffic spike simulation failed.");
      return result.data;
    },

    simulateDownload(datasetId) {
      var id = datasetId || _currentDatasetId;
      if (!id) return;
      window.location.href = "/logs/datasets/" + encodeURIComponent(id) + "/simulate/download/";
    },

    async getAllAlertsHistory(limit) {
      var qs = limit ? "?limit=" + limit : "";
      var result = await fetchJson("/logs/alerts/all-history/" + qs);
      if (!result.ok) throw new Error("Failed to load alert history.");
      return result.data;
    },

    // ---- RBAC ---------------------------------------------------------------

    async getUsers() {
      var result = await fetchJson("/logs/admin/users/");
      if (!result.ok) throw new Error("Failed to load users.");
      return result.data;
    },

    async assignRole(userId, role) {
      var result = await fetchJson(
        "/logs/admin/users/" + encodeURIComponent(userId) + "/role/",
        { method: "POST", body: JSON.stringify({ role: role }) }
      );
      if (!result.ok) throw new Error("Role assignment failed.");
      return result.data;
    },

    async createInvite(email, role, sendEmail) {
      var result = await fetchJson("/logs/invites/", {
        method: "POST",
        body: JSON.stringify({ email: email, role: role, send_email: sendEmail !== false }),
      });
      if (!result.ok) throw new Error((result.data && result.data.error) || "Failed to create invite.");
      return result.data;
    },

    async listInvites() {
      var result = await fetchJson("/logs/invites/");
      if (!result.ok) throw new Error("Failed to load invites.");
      return result.data;
    },

    async revokeInvite(inviteId) {
      var result = await fetchJson(
        "/logs/invites/" + encodeURIComponent(inviteId) + "/revoke/",
        { method: "POST" }
      );
      if (!result.ok) throw new Error("Failed to revoke invite.");
      return result.data;
    },

    // ---- Reports ------------------------------------------------------------

    /**
     * Trigger a report download by navigating the browser to the report URL.
     * The backend responds with Content-Disposition: attachment so the browser
     * saves the file directly without changing the page.
     *
     * @param {string} datasetId
     * @param {string} format "csv" | "json"
     */
    downloadReport(datasetId, format) {
      var id = datasetId || _currentDatasetId;
      if (!id) throw new Error("No dataset selected.");
      var fmt = (format || "csv").toLowerCase();
      window.location.href = "/logs/datasets/" + encodeURIComponent(id) + "/report/?format=" + encodeURIComponent(fmt);
    },

    // ---- Dashboard preferences ----------------------------------------------

    /** Fetch the current user's dashboard preferences. */
    async getPreferences() {
      var result = await fetchJson("/logs/preferences/", { method: "GET" });
      if (!result.ok || !result.data) return { hidden_widgets: [], preferences: {} };
      return result.data;
    },

    /** Save dashboard preferences to the backend. */
    async savePreferences(data) {
      var result = await fetchJson(
        "/logs/preferences/",
        { method: "POST", body: JSON.stringify(data), headers: { "Content-Type": "application/json" } }
      );
      if (!result.ok) throw new Error("Failed to save preferences.");
      return result.data;
    },
  };
})(typeof window !== "undefined" ? window : globalThis);
