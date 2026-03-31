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
              const alpha = 0.55 + (index % 4) * 0.1;
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
    const g2 = ctx.createLinearGradient(0, 0, 0, el.height || 200);
    g2.addColorStop(0, "rgba(99,102,241,0.3)");
    g2.addColorStop(1, "transparent");
    const first = data && data.datasets && data.datasets[0] ? data.datasets[0] : { label: "Inbound", data: [] };
    const second = data && data.datasets && data.datasets[1] ? data.datasets[1] : { label: "Outbound", data: [] };

    charts.traffic = new Chart(ctx, {
      type: "line",
      data: {
        labels: data && data.labels ? data.labels : [],
        datasets: [
          {
            label: first.label || "Inbound",
            data: Array.isArray(first.data) ? first.data : [],
            borderColor: c.accent,
            backgroundColor: g1,
            fill: true,
            tension: 0.35,
            borderWidth: 2,
            pointRadius: 0,
          },
          {
            label: second.label || "Outbound",
            data: Array.isArray(second.data) ? second.data : [],
            borderColor: c.primary,
            backgroundColor: g2,
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

  global.LogSightCharts = {
    destroyAll: destroyAll,
    renderRequestFrequency: renderRequestFrequency,
    renderStatusDoughnut: renderStatusDoughnut,
    renderTopEndpoints: renderTopEndpoints,
    renderTrafficArea: renderTrafficArea,
    getThemeColors: getThemeColors,
  };
})(typeof window !== "undefined" ? window : globalThis);
