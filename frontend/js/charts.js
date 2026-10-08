/**
 * LogSight charts — a thin layer over Chart.js that gives every chart the
 * same editorial treatment: ink lines on paper, hairline grid, one accent.
 *
 * Colours are hex equivalents of the CSS tokens in logsight.css (Chart.js
 * cannot derive hover shades from oklch()).
 */
(function (global) {
  "use strict";

  var C = {
    ink: "#1b2030",
    ink2: "#4a5166",
    ink3: "#6b7285",
    rule: "#e2e5ec",
    paper: "#f9fafc",
    accent: "#2f4fd8",
    accentFill: "rgba(47, 79, 216, 0.13)",
    inkFill: "rgba(27, 32, 48, 0.07)",
    bad: "#c63a2c",
    warn: "#cf9426",
  };
  var FONT = "'Libre Franklin', system-ui, sans-serif";
  var charts = {};
  var reduceMotion = global.matchMedia && global.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function destroy(key) {
    if (charts[key]) { charts[key].destroy(); delete charts[key]; }
  }

  function context(canvasId) {
    var el = document.getElementById(canvasId);
    return el ? el.getContext("2d") : null;
  }

  function number(v) {
    return v == null ? "" : Number(v).toLocaleString("en");
  }

  function baseOptions(extra) {
    var options = {
      responsive: true,
      maintainAspectRatio: false,
      animation: reduceMotion ? false : { duration: 900, easing: "easeOutQuart" },
      interaction: { mode: "index", intersect: false },
      layout: { padding: { top: 8, right: 4 } },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: C.ink,
          titleColor: "#ffffff",
          bodyColor: "#d9dce6",
          titleFont: { family: FONT, size: 12, weight: "600" },
          bodyFont: { family: FONT, size: 12 },
          padding: 10,
          cornerRadius: 6,
          boxWidth: 8,
          boxHeight: 8,
          boxPadding: 4,
          displayColors: true,
          usePointStyle: true,
          callbacks: {
            label: function (item) {
              if (item.raw == null) return null;
              return " " + item.dataset.label + ": " + number(item.raw);
            },
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          border: { color: C.ink },
          ticks: { color: C.ink3, font: { family: FONT, size: 11 }, maxRotation: 0, autoSkipPadding: 24, maxTicksLimit: 9 },
        },
        y: {
          beginAtZero: true,
          grid: { color: C.rule, drawTicks: false },
          border: { display: false },
          ticks: { color: C.ink3, font: { family: FONT, size: 11 }, padding: 8, maxTicksLimit: 5, callback: function (v) { return number(v); } },
        },
      },
    };
    if (extra && extra.plugins) Object.assign(options.plugins, extra.plugins);
    return options;
  }

  /** Requests over time: a single ink line with a faint fill. */
  function traffic(canvasId, labels, values) {
    destroy(canvasId);
    var ctx = context(canvasId);
    if (!ctx) return;
    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [{
          label: "Requests", data: values,
          borderColor: C.ink, backgroundColor: C.inkFill, fill: true,
          borderWidth: 1.6, tension: 0.25, pointRadius: 0, pointHoverRadius: 4,
          pointHoverBackgroundColor: C.accent, pointHoverBorderColor: C.paper, pointHoverBorderWidth: 2,
        }],
      },
      options: baseOptions(),
    });
  }

  /** Requests by hour of day: bars, with the busiest hour in the accent. */
  function hours(canvasId, labels, values, peakIndex) {
    destroy(canvasId);
    var ctx = context(canvasId);
    if (!ctx) return;
    var options = baseOptions();
    options.scales.x.ticks.maxTicksLimit = 8;
    charts[canvasId] = new Chart(ctx, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [{
          label: "Requests", data: values,
          backgroundColor: values.map(function (_, i) { return i === peakIndex ? C.accent : C.ink; }),
          hoverBackgroundColor: C.accent,
          borderRadius: 2, barPercentage: 0.78, categoryPercentage: 0.9,
        }],
      },
      options: options,
    });
  }

  /**
   * Forecast: recent recorded traffic (ink), forecast (dashed accent) and the
   * likely range (accent band).  A vertical rule marks where the log ends.
   */
  function forecast(canvasId, data) {
    destroy(canvasId);
    var ctx = context(canvasId);
    if (!ctx) return;

    var fc = Array.isArray(data.forecast) ? data.forecast : [];
    var all = Array.isArray(data.historical) ? data.historical : [];
    // Enough history to give the forecast context without dwarfing it.
    var history = all.slice(-Math.max(72, fc.length * 3));
    var n = history.length;
    if (!n && !fc.length) return;

    var labels = history.map(function (p) { return p.label; }).concat(fc.map(function (p) { return p.label; }));
    var last = n ? history[n - 1].value : null;
    var pad = history.map(function () { return null; });
    var bridge = function (key) {
      var series = pad.slice(0, Math.max(0, n - 1));
      if (n) series.push(last);
      return series.concat(fc.map(function (p) { return p[key] != null ? p[key] : p.value; }));
    };

    var endOfLog = {
      id: "endOfLog",
      afterDatasetsDraw: function (chart) {
        if (!n || !fc.length) return;
        var x = chart.scales.x.getPixelForValue(n - 1);
        var area = chart.chartArea;
        var c = chart.ctx;
        c.save();
        c.strokeStyle = C.ink3; c.lineWidth = 1; c.setLineDash([3, 3]);
        c.beginPath(); c.moveTo(x, area.top); c.lineTo(x, area.bottom); c.stroke();
        c.setLineDash([]); c.fillStyle = C.ink3; c.font = "11px " + FONT; c.textAlign = "right";
        c.fillText("end of log", x - 6, area.top + 10);
        c.restore();
      },
    };

    var options = baseOptions();
    options.plugins.tooltip.filter = function (item) { return item.dataset.label !== "Low" && item.raw != null; };

    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "High", data: bridge("upper"), borderWidth: 0, pointRadius: 0, pointHoverRadius: 0, backgroundColor: C.accentFill, fill: "+1", tension: 0.3 },
          { label: "Low", data: bridge("lower"), borderWidth: 0, pointRadius: 0, pointHoverRadius: 0, fill: false, tension: 0.3 },
          { label: "Forecast", data: bridge("value"), borderColor: C.accent, borderWidth: 2, borderDash: [6, 4], pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: C.accent, tension: 0.3, fill: false },
          { label: "Recorded", data: history.map(function (p) { return p.value; }).concat(fc.map(function () { return null; })), borderColor: C.ink, borderWidth: 1.6, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: C.ink, tension: 0.25, fill: false },
        ],
      },
      options: options,
      plugins: [endOfLog],
    });
  }

  /** Requests per hour with flagged hours drawn as ringed points. */
  function anomalies(canvasId, series, flagged) {
    destroy(canvasId);
    var ctx = context(canvasId);
    if (!ctx) return;
    var labels = series.map(function (p) { return p.label; });
    var values = series.map(function (p) { return p.value; });
    var marks = labels.map(function (label, i) { return flagged[label] ? values[i] : null; });

    var options = baseOptions();
    options.plugins.tooltip.callbacks.afterBody = function (items) {
      var label = items.length ? items[0].label : "";
      return flagged[label] ? ["", flagged[label]] : [];
    };
    options.plugins.tooltip.filter = function (item) { return item.datasetIndex === 1; };

    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: "Flagged", data: marks, showLine: false, pointRadius: 5, pointHoverRadius: 7, pointBackgroundColor: C.paper, pointBorderColor: C.bad, pointBorderWidth: 2, order: 1 },
          { label: "Requests", data: values, borderColor: C.ink, backgroundColor: C.inkFill, fill: true, borderWidth: 1.4, tension: 0.2, pointRadius: 0, pointHoverRadius: 3, order: 2 },
        ],
      },
      options: options,
    });
  }

  /** Two datasets on the same 24-hour axis. */
  function compare(canvasId, labels, a, b, nameA, nameB) {
    destroy(canvasId);
    var ctx = context(canvasId);
    if (!ctx) return;
    var options = baseOptions();
    options.scales.x.ticks.maxTicksLimit = 12;
    charts[canvasId] = new Chart(ctx, {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          { label: nameA, data: a, borderColor: C.ink, borderWidth: 2, tension: 0.3, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: C.ink },
          { label: nameB, data: b, borderColor: C.warn, borderWidth: 2, tension: 0.3, pointRadius: 0, pointHoverRadius: 4, pointHoverBackgroundColor: C.warn },
        ],
      },
      options: options,
    });
  }

  global.LogSightCharts = {
    traffic: traffic,
    hours: hours,
    forecast: forecast,
    anomalies: anomalies,
    compare: compare,
    destroy: destroy,
  };
})(window);
