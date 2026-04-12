/**
 * Chart.js instances — theme-aware; renders polished empty states until backend data is available.
 */
(function (global) {
  const charts = {};
  let emptyStatePluginRegistered = false;

  function ensureEmptyStatePlugin() {
    if (emptyStatePluginRegistered || typeof Chart === "undefined") return;

    Chart.register({
      id: "logSightEmptyState",
      afterDraw: function (chart, _args, pluginOptions) {
        if (!pluginOptions || !pluginOptions.enabled) return;
        if (chartHasData(chart)) return;

        const ctx = chart.ctx;
        const area = chart.chartArea;
        if (!area) return;

        ctx.save();
        ctx.textAlign = "center";
        ctx.fillStyle = pluginOptions.color || "#94a3b8";
        ctx.font = pluginOptions.titleFont || "600 14px Inter, system-ui, sans-serif";
        ctx.fillText(pluginOptions.title || "No data available yet", (area.left + area.right) / 2, area.top + area.height / 2 - 4);
        if (pluginOptions.description) {
          ctx.font = pluginOptions.bodyFont || "400 12px Inter, system-ui, sans-serif";
          ctx.fillText(pluginOptions.description, (area.left + area.right) / 2, area.top + area.height / 2 + 18);
        }
        ctx.restore();
      },
    });

    emptyStatePluginRegistered = true;
  }

  function chartHasData(chart) {
    return chart.data.datasets.some(function (dataset) {
      return Array.isArray(dataset.data) && dataset.data.some(function (value) {
        return typeof value === "number" && !Number.isNaN(value);
      });
    });
  }

  function getThemeColors() {
    const dark = document.documentElement.classList.contains("dark");
    return {
      dark: dark,
      text: dark ? "#e2e8f0" : "#334155",
      textMuted: dark ? "#94a3b8" : "#64748b",
      grid: dark ? "rgba(148,163,184,0.12)" : "rgba(148,163,184,0.25)",
      primary: "#6366f1",
      primarySoft: dark ? "rgba(99,102,241,0.25)" : "rgba(99,102,241,0.15)",
      accent: "#22d3ee",
      danger: "#f87171",
      warn: "#fbbf24",
      success: "#34d399",
    };
  }

  function defaultChartOptions() {
    const c = getThemeColors();
    return {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 900, easing: "easeOutQuart" },
      plugins: {
        legend: {
          labels: { color: c.text, font: { family: "'Inter', system-ui, sans-serif", size: 11 } },
        },
        logSightEmptyState: {
          enabled: true,
          color: c.textMuted,
          title: "No analytics data yet",
          description: "Connect backend metrics to populate this chart.",
        },
      },
      scales: {},
    };
  }

  function destroyChart(key) {
    if (charts[key]) {
      charts[key].destroy();
      delete charts[key];
    }
  }

  function destroyAll() {
    Object.keys(charts).forEach(destroyChart);
  }

  function renderRequestFrequency(canvasId, data) {
    ensureEmptyStatePlugin();
    destroyChart("freq");
    const el = document.getElementById(canvasId);
    if (!el) return;
    const c = getThemeColors();
    const ctx = el.getContext("2d");
    const grad = ctx.createLinearGradient(0, 0, 0, el.height || 200);
    grad.addColorStop(0, c.primarySoft);
    grad.addColorStop(1, "transparent");
    const series = data && data.datasets && data.datasets[0] ? data.datasets[0] : { label: "Requests", data: [] };

    charts.freq = new Chart(ctx, {
      type: "line",
      data: {
        labels: data && data.labels ? data.labels : [],
        datasets: [
          {
            label: series.label || "Requests",
            data: Array.isArray(series.data) ? series.data : [],
            borderColor: c.primary,
            backgroundColor: grad,
            fill: true,
            tension: 0.4,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 5,
          },
        ],
      },
      options: {
        ...defaultChartOptions(),
        interaction: { intersect: false, mode: "index" },
        plugins: {
          ...defaultChartOptions().plugins,
          tooltip: {
            backgroundColor: c.dark ? "#1e293b" : "#fff",
            titleColor: c.text,
            bodyColor: c.textMuted,
            borderColor: c.grid,
            borderWidth: 1,
            padding: 12,
            cornerRadius: 8,
          },
        },
        scales: {
          x: {
            grid: { color: c.grid },
            ticks: { color: c.textMuted, maxRotation: 45 },
          },
          y: {
            beginAtZero: true,
            grid: { color: c.grid },
            ticks: { color: c.textMuted },
          },
        },
      },
    });
  }

  function renderStatusDoughnut(canvasId, dist) {
    ensureEmptyStatePlugin();
    destroyChart("status");
    const el = document.getElementById(canvasId);
    if (!el) return;
    const c = getThemeColors();

    charts.status = new Chart(el, {
      type: "doughnut",
      data: {
        labels: dist && dist.labels ? dist.labels : [],
        datasets: [
          {
            data: dist && dist.data ? dist.data : [],
            backgroundColor: [c.success, "#60a5fa", c.warn, c.danger],
            borderWidth: 0,
            hoverOffset: 10,
          },
        ],
      },
      options: {
        ...defaultChartOptions(),
        cutout: "62%",
        plugins: {
          ...defaultChartOptions().plugins,
          legend: {
            position: "bottom",
            labels: { color: c.text, padding: 14, usePointStyle: true },
          },
          tooltip: {
            backgroundColor: c.dark ? "#1e293b" : "#fff",
            titleColor: c.text,
            bodyColor: c.textMuted,
            borderColor: c.grid,
            borderWidth: 1,
          },
        },
      },
    });
  }

  function renderTopEndpoints(canvasId, payload) {
    ensureEmptyStatePlugin();
    destroyChart("endpoints");
    const el = document.getElementById(canvasId);
    if (!el) return;
    const c = getThemeColors();
    const items = payload && payload.items ? payload.items : [];
    const labels = items.map(function (item) {
      // api.js normalises to .label; fall back to raw .endpoint for resilience.
      const path = item.label || item.endpoint || item.path || "";
      return path.length > 28 ? "\u2026" + path.slice(-26) : path;
    });
    const values = items.map(function (item) {
      // api.js normalises to .value; fall back to raw .count.
      return item.value || item.count || 0;
    });

    charts.endpoints = new Chart(el, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Requests",
            data: values,
            backgroundColor: values.map(function (_, index) {
              const alpha = 0.85 - (index % 4) * 0.1;
              return c.dark ? "rgba(129,140,248," + alpha + ")" : "rgba(79,70,229," + alpha + ")";
            }),
            borderRadius: 6,
            borderSkipped: false,
          },
        ],
      },
      options: {
        indexAxis: "y",
        ...defaultChartOptions(),
        plugins: {
          ...defaultChartOptions().plugins,
          legend: { display: false },
          tooltip: {
            backgroundColor: c.dark ? "#1e293b" : "#fff",
            titleColor: c.text,
            bodyColor: c.textMuted,
            borderColor: c.grid,
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            beginAtZero: true,
            grid: { color: c.grid },
            ticks: { color: c.textMuted },
          },
          y: {
            grid: { display: false },
            ticks: { color: c.textMuted, font: { size: 10 } },
          },
        },
      },
    });
  }

  function renderTrafficArea(canvasId, data) {
    ensureEmptyStatePlugin();
    destroyChart("traffic");
    const el = document.getElementById(canvasId);
    if (!el) return;
    const c = getThemeColors();
    const ctx = el.getContext("2d");
    const g1 = ctx.createLinearGradient(0, 0, 0, el.height || 200);
    g1.addColorStop(0, "rgba(34,211,238,0.35)");
    g1.addColorStop(1, "transparent");
    const first = data && data.datasets && data.datasets[0] ? data.datasets[0] : { label: "Requests by Hour", data: [] };

    charts.traffic = new Chart(ctx, {
      type: "line",
      data: {
        labels: data && data.labels ? data.labels : [],
        datasets: [
          {
            label: first.label || "Requests by Hour",
            data: Array.isArray(first.data) ? first.data : [],
            borderColor: c.accent,
            backgroundColor: g1,
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 0,
          },
        ],
      },
      options: {
        ...defaultChartOptions(),
        interaction: { intersect: false, mode: "index" },
        plugins: {
          ...defaultChartOptions().plugins,
          tooltip: {
            backgroundColor: c.dark ? "#1e293b" : "#fff",
            titleColor: c.text,
            bodyColor: c.textMuted,
            borderColor: c.grid,
            borderWidth: 1,
          },
        },
        scales: {
          x: {
            grid: { color: c.grid },
            ticks: { color: c.textMuted, maxRotation: 45 },
          },
          y: {
            beginAtZero: true,
            grid: { color: c.grid },
            ticks: { color: c.textMuted },
          },
        },
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Forecast chart
  // ---------------------------------------------------------------------------
  //
  // Renders two overlaid line series on a single chart:
  //   - "Historical" (solid indigo line) for actual recorded request counts.
  //   - "Forecast"   (dashed amber line) for predicted future values.
  //
  // The two series share the same x-axis labels (concatenated timestamps).

  function renderForecastChart(canvasId, data) {
    ensureEmptyStatePlugin();
    destroyChart("forecast");
    const el = document.getElementById(canvasId);
    if (!el) return;
    const c = getThemeColors();
    const ctx = el.getContext("2d");

    const historical = data && Array.isArray(data.historical) ? data.historical : [];
    const forecast   = data && Array.isArray(data.forecast)   ? data.forecast   : [];

    // Build a unified label array.
    const histLabels = historical.map(function (p) { return p.label || ""; });
    const fcstLabels = forecast.map(function (p) { return p.label || ""; });
    const allLabels  = histLabels.concat(fcstLabels);

    // Historical dataset: real values then nulls (so it ends where history ends).
    const histValues = historical.map(function (p) { return p.value; })
      .concat(forecast.map(function () { return null; }));

    // Forecast dataset: nulls up to the last historical point, then predictions.
    const lastHistIdx = historical.length > 0 ? historical.length - 1 : 0;
    const fcstPad = new Array(lastHistIdx).fill(null);
    // Connect at the last historical point for a visual join.
    const connectValue = historical.length > 0 ? historical[historical.length - 1].value : null;
    const fcstValues = fcstPad.concat([connectValue]).concat(forecast.map(function (p) { return p.value; }));

    const gradHist = ctx.createLinearGradient(0, 0, 0, el.height || 200);
    gradHist.addColorStop(0, c.primarySoft);
    gradHist.addColorStop(1, "transparent");

    charts.forecast = new Chart(ctx, {
      type: "line",
      data: {
        labels: allLabels,
        datasets: [
          {
            label: "Historical",
            data: histValues,
            borderColor: c.primary,
            backgroundColor: gradHist,
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            spanGaps: false,
          },
          {
            label: "Forecast",
            data: fcstValues,
            borderColor: c.warn,
            backgroundColor: "transparent",
            fill: false,
            tension: 0.35,
            borderWidth: 2,
            borderDash: [6, 4],
            pointRadius: 0,
            pointHoverRadius: 4,
            spanGaps: false,
          },
        ],
      },
      options: {
        ...defaultChartOptions(),
        interaction: { intersect: false, mode: "index" },
        plugins: {
          ...defaultChartOptions().plugins,
          legend: {
            labels: { color: c.text, font: { family: "'Inter', system-ui, sans-serif", size: 11 }, usePointStyle: true },
          },
          tooltip: {
            backgroundColor: c.dark ? "#1e293b" : "#fff",
            titleColor: c.text,
            bodyColor: c.textMuted,
            borderColor: c.grid,
            borderWidth: 1,
            padding: 12,
            cornerRadius: 8,
            filter: function (item) { return item.raw !== null; },
          },
          logSightEmptyState: { enabled: false },
        },
        scales: {
          x: {
            grid: { color: c.grid },
            ticks: { color: c.textMuted, maxRotation: 45, maxTicksLimit: 16 },
          },
          y: {
            beginAtZero: true,
            grid: { color: c.grid },
            ticks: { color: c.textMuted },
          },
        },
      },
    });
  }

  // ---------------------------------------------------------------------------
  // Anomaly detection chart
  // ---------------------------------------------------------------------------
  //
  // Renders the full hourly request series as a blue area chart, then overlays
  // red scatter-point markers at each detected anomalous hour.

  function renderAnomalyChart(canvasId, historicalData, anomalies) {
    ensureEmptyStatePlugin();
    destroyChart("anomalies");
    const el = document.getElementById(canvasId);
    if (!el) return;
    const c = getThemeColors();
    const ctx = el.getContext("2d");

    const hist   = Array.isArray(historicalData) ? historicalData : [];
    const anomArr = Array.isArray(anomalies)     ? anomalies      : [];

    const labels = hist.map(function (p) { return p.label || ""; });
    const values = hist.map(function (p) { return p.value; });

    // Build a lookup: label → value for anomaly overlay.
    const anomalySet = {};
    anomArr.forEach(function (a) {
      if (a.type === "request_spike") anomalySet[a.label] = a.value;
    });

    // Anomaly scatter data aligned to the same x-axis.
    const anomalyPoints = labels.map(function (lbl, i) {
      return Object.prototype.hasOwnProperty.call(anomalySet, lbl) ? values[i] : null;
    });

    const grad = ctx.createLinearGradient(0, 0, 0, el.height || 200);
    grad.addColorStop(0, "rgba(99,102,241,0.2)");
    grad.addColorStop(1, "transparent");

    charts.anomalies = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Requests",
            data: values,
            borderColor: c.primary,
            backgroundColor: grad,
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            order: 2,
          },
          {
            label: "Anomaly",
            data: anomalyPoints,
            type: "scatter",
            borderColor: c.danger,
            backgroundColor: c.danger,
            pointRadius: 7,
            pointHoverRadius: 9,
            pointStyle: "circle",
            order: 1,
          },
        ],
      },
      options: {
        ...defaultChartOptions(),
        interaction: { intersect: false, mode: "index" },
        plugins: {
          ...defaultChartOptions().plugins,
          legend: {
            labels: { color: c.text, font: { family: "'Inter', system-ui, sans-serif", size: 11 }, usePointStyle: true },
          },
          tooltip: {
            backgroundColor: c.dark ? "#1e293b" : "#fff",
            titleColor: c.text,
            bodyColor: c.textMuted,
            borderColor: c.grid,
            borderWidth: 1,
            padding: 12,
            cornerRadius: 8,
            filter: function (item) { return item.raw !== null; },
          },
          logSightEmptyState: { enabled: false },
        },
        scales: {
          x: {
            grid: { color: c.grid },
            ticks: { color: c.textMuted, maxRotation: 45, maxTicksLimit: 16 },
          },
          y: {
            beginAtZero: true,
            grid: { color: c.grid },
            ticks: { color: c.textMuted },
          },
        },
      },
    });
  }

  global.LogSightCharts = {
    destroyAll: destroyAll,
    renderRequestFrequency: renderRequestFrequency,
    renderStatusDoughnut: renderStatusDoughnut,
    renderTopEndpoints: renderTopEndpoints,
    renderTrafficArea: renderTrafficArea,
    renderForecastChart: renderForecastChart,
    renderAnomalyChart: renderAnomalyChart,
    getThemeColors: getThemeColors,
  };
})(typeof window !== "undefined" ? window : globalThis);
