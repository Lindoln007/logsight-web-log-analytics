# LogSight — Module Reference

**Project:** Design and Implementation of a Web-Based Log Analytics and Visualization System for Large-Scale Data  
**Stack:** Django 5 · PostgreSQL · Chart.js 4 · Tailwind CSS · Vanilla JS SPA

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Project Structure](#2-project-structure)
3. [Authentication Module](#3-authentication-module)
4. [Log Upload Module](#4-log-upload-module)
5. [Log Parsing Engine](#5-log-parsing-engine)
6. [Dataset Management Module](#6-dataset-management-module)
7. [Analytics Engine](#7-analytics-engine)
8. [Dashboard Module](#8-dashboard-module)
9. [Log Viewer Module](#9-log-viewer-module)
10. [Traffic Forecasting Module](#10-traffic-forecasting-module)
11. [Anomaly Detection Module](#11-anomaly-detection-module)
12. [Alerts Module](#12-alerts-module)
13. [Report Generation Module](#13-report-generation-module)
14. [Dashboard Customization Module](#14-dashboard-customization-module)
15. [Frontend SPA Architecture](#15-frontend-spa-architecture)
16. [Database Schema](#16-database-schema)
17. [API Reference](#17-api-reference)

---

## 1. System Overview

LogSight is a full-stack web application that ingests raw Apache and Nginx access log files, parses them into a relational database, computes analytics, and presents results through an interactive single-page application (SPA).

### Core data flow

```
User uploads .log/.txt/.gz file
        │
        ▼
Django receives file → creates Dataset record (status: pending)
        │
        ▼
Background thread: LogParsingService streams file line-by-line
        │   Regex-matches each line → creates LogEntry rows in batches
        ▼
Dataset status → "completed"
        │
        ▼
AnalyticsService aggregates at database level → AnalyticsSummary persisted
        │
        ▼
SPA fetches JSON from REST API → Chart.js renders visualizations
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Background thread for parsing | Returns HTTP 202 immediately; large files (500 MB+) parse without blocking the request |
| Database-level aggregation | All analytics use Django ORM `.annotate()` + `GROUP BY` — no Python-side row scanning |
| In-memory cache (15 min TTL) | Avoids re-running aggregations on every dashboard refresh |
| Hash-based SPA routing | No full page reloads between modules; single Django template serves all views |
| Pure Python forecasting/anomaly | No ML library dependencies; fully explainable algorithms suitable for academic assessment |

---

## 2. Project Structure

```
logsight/
├── loganalytics/              # Django project configuration
│   ├── settings.py            # Database, upload limits, caching, auth
│   ├── urls.py                # Root URL dispatcher
│   └── wsgi.py                # WSGI entry point (Gunicorn)
│
├── accounts/                  # Authentication app
│   ├── views.py               # Login, register, logout, profile
│   ├── forms.py               # LoginForm, RegistrationForm, ProfileUpdateForm
│   └── templates/accounts/    # login.html, register.html, profile.html
│
├── logs/                      # Core analytics app
│   ├── models.py              # Dataset, LogEntry, AnalyticsSummary,
│   │                          # AlertRule, Alert, DashboardPreference
│   ├── views.py               # All REST API endpoints
│   ├── urls.py                # URL patterns for all endpoints
│   ├── forms.py               # LogUploadForm (file validation)
│   ├── admin.py               # Django admin registration
│   ├── migrations/            # 6 database migrations
│   ├── services/
│   │   ├── __init__.py        # Public re-exports
│   │   ├── log_parser.py      # Apache/Nginx regex parser
│   │   ├── analytics.py       # Aggregation queries + caching
│   │   ├── log_entries.py     # Paginated log viewer queries
│   │   ├── dataset_management.py  # Upload handling + background thread
│   │   ├── forecasting.py     # Moving average + linear regression
│   │   ├── anomaly_detection.py   # Mean ± k·σ detection
│   │   ├── alerts.py          # Alert rule CRUD + trigger evaluation
│   │   └── reports.py         # CSV and JSON report generation
│   └── templates/logs/
│       └── app_shell.html     # Single HTML file for the entire SPA
│
├── frontend/
│   ├── js/
│   │   ├── app.js             # SPA routing, all module UI logic (~900 lines)
│   │   ├── api.js             # HTTP client, all API methods (~750 lines)
│   │   └── charts.js          # Chart.js wrappers, 6 chart renderers
│   ├── css/
│   │   └── styles.css         # Animations, card hover, scrollbars
│   └── assets/
│       └── logsight-logo.png
│
├── requirements.txt
├── gunicorn.conf.py
└── manage.py
```

---

## 3. Authentication Module

**App:** `accounts/`  
**Purpose:** Manages user registration, login, logout, and profile updates. All analytics data is scoped to the authenticated user — no user can access another user's datasets.

### How it works

- Django's built-in `User` model and session-based authentication are used.
- All `logs/` views are decorated with `@login_required`; a 401 response redirects to `/accounts/login/`.
- Every analytics query filters by `dataset__owner=request.user`, enforcing data isolation at the database level.

### Key files

| File | Responsibility |
|---|---|
| `accounts/views.py` | `UserLoginView`, `register_view`, `logout_view`, `profile_view` |
| `accounts/forms.py` | Styled forms for login, registration, and profile editing |
| `accounts/templates/` | Server-rendered HTML pages (not part of the SPA) |

### Routes

| URL | View |
|---|---|
| `/accounts/login/` | Login form |
| `/accounts/register/` | Registration form |
| `/accounts/logout/` | POST logout |
| `/accounts/profile/` | Edit username, email, name |

---

## 4. Log Upload Module

**Route:** `#/upload` (SPA) → `POST /logs/upload/`  
**Purpose:** Accepts Apache or Nginx access log files (`.log`, `.txt`, `.gz`) from the user, stores them to disk, and triggers background parsing.

### How it works

1. The user drags a file onto the drop zone or clicks to browse.
2. The frontend sends a `multipart/form-data` POST to `/logs/upload/`.
3. `LogUploadForm` validates file type and size (max 500 MB by default).
4. `create_dataset_from_upload()` saves the file to `media/uploads/`, creates a `Dataset` record with `status=pending`, and spawns a **daemon thread** for parsing.
5. The view returns **HTTP 202 Accepted** immediately — the file is queued but not yet parsed.
6. The frontend polls `GET /logs/datasets/<id>/status/` every second until `status` reaches `completed` or `failed`.
7. A progress bar and status message update in real time.

### Upload limits (configurable in `settings.py`)

| Setting | Default |
|---|---|
| `LOGS_MAX_UPLOAD_SIZE` | 500 MB |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | 2 MB (larger files streamed to disk) |
| `LOGS_PARSE_BATCH_SIZE` | 2,000 rows per `bulk_create()` |

---

## 5. Log Parsing Engine

**File:** `logs/services/log_parser.py`  
**Purpose:** Converts raw log file text into structured `LogEntry` database rows.

### How it works

`LogParsingService.parse_dataset()` is called in the background thread:

1. Opens the file (supports `.gz` via `gzip.open`).
2. Reads line by line — never loads the entire file into memory.
3. Each line is matched against a compiled regex (`ACCESS_LOG_PATTERN`) that extracts:
   - IP address, timestamp, HTTP method, endpoint, protocol
   - Status code, response size, referrer, user agent
4. Parsed lines are accumulated into a list; every 2,000 lines `LogEntry.objects.bulk_create()` inserts the batch in a single transaction.
5. After all lines are processed, `dataset.status` is set to `completed` and `compute_and_persist_analytics()` is called.
6. If parsing fails at any point, `dataset.status` is set to `failed` and the error message is stored in `dataset.processing_error`.

### Supported log formats

| Format | Example line |
|---|---|
| Apache Combined | `192.168.1.1 - - [01/Jan/2024:12:00:00 +0000] "GET /index.html HTTP/1.1" 200 1024 "-" "Mozilla/5.0"` |
| Nginx access | Same Combined Log Format |

---

## 6. Dataset Management Module

**Route:** `#/datasets` (SPA)  
**Purpose:** Lists all datasets owned by the current user, with actions to analyse, view logs, or delete.

### How it works

- `GET /logs/datasets/` returns all datasets for the logged-in user, ordered by upload date.
- Each row shows: ID, filename, detected source (Apache/Nginx), file size, entry count, upload date, processing status.
- **Analyse** button: sets the dataset as active and navigates to the Dashboard.
- **View** button: navigates to the Log Viewer with that dataset pre-selected.
- **Delete** button: calls `POST /logs/datasets/<id>/delete/` which cascades to delete all `LogEntry` rows and removes the file from disk.

### Key files

| File | Responsibility |
|---|---|
| `logs/services/dataset_management.py` | `create_dataset_from_upload`, `delete_dataset_and_file`, `list_datasets`, `serialize_dataset` |
| `logs/views.py` | `dataset_list_view`, `dataset_detail_view`, `dataset_status_view`, `delete_dataset_view` |

---

## 7. Analytics Engine

**File:** `logs/services/analytics.py`  
**Purpose:** Computes all statistical summaries from `LogEntry` rows using database-level aggregation.

### How it works

All functions issue a single queryset annotated with Django ORM aggregates (`Count`, `Avg`, `TruncHour`, etc.) — no Python loops over raw rows.

Results are cached in Django's `LocMemCache` with a **15-minute TTL** per dataset per metric type. Cache keys include the dataset ID to prevent cross-dataset contamination.

After initial parsing, `compute_and_persist_analytics()` also writes results to the `AnalyticsSummary` model as a persistent fallback.

### Analytics functions

| Function | Returns |
|---|---|
| `get_summary_metrics()` | Total requests, errors, error rate, unique IPs, avg response size, date range |
| `get_status_distribution()` | Request counts per status code and per category (1xx–5xx) |
| `get_request_trends()` | Time-series counts bucketed by hour or day |
| `get_top_endpoints()` | Top N endpoints ranked by request count with percentage share |
| `get_hourly_traffic()` | Request counts for each hour of day (0–23), peak hour, quiet hour |
| `get_method_distribution()` | Counts per HTTP method (GET, POST, PUT, DELETE, etc.) |
| `get_error_breakdown()` | 4xx vs 5xx totals, top error codes, top error-generating endpoints |
| `get_analytics_overview()` | All of the above combined into one response |

---

## 8. Dashboard Module

**Route:** `#/dashboard` (SPA)  
**Purpose:** The main landing view. Displays four stat tiles and four charts for the selected dataset.

### How it works

1. On load, `populateDashboardSelect()` fetches all datasets and pre-selects the most recently completed one.
2. `loadDashboardData()` calls `GET /logs/datasets/<id>/analytics/summary/` to populate stat tiles.
3. `loadDashboardCharts()` fires four parallel API calls and renders charts once all resolve.
4. The **Refresh** button calls `POST /logs/datasets/<id>/analytics/refresh/` to invalidate the cache and recompute from raw entries.

### Stat tiles

| Tile | Data source |
|---|---|
| Total requests | `summary.total_requests` |
| Error rate | `summary.error_rate` (%) |
| Avg. response size | `summary.avg_response_size` (bytes) |
| Unique visitors | `summary.unique_ips` |

### Charts

| Chart | Type | Data source |
|---|---|---|
| Request frequency | Line (area fill) | `/analytics/request-trends/` |
| Status distribution | Doughnut | `/analytics/status-distribution/` |
| Top endpoints | Horizontal bar | `/analytics/top-endpoints/` |
| Traffic patterns | Area line | `/analytics/hourly-traffic/` |

---

## 9. Log Viewer Module

**Route:** `#/viewer` (SPA) → `GET /logs/datasets/<id>/entries/`  
**Purpose:** Paginated, filterable table of raw log entries for detailed inspection.

### How it works

- Entries are fetched 25 per page from the backend, which applies filters at the database level using indexed fields.
- **Date range fix:** When a dataset is selected, the viewer calls `/analytics/summary/` to retrieve `date_range_start` and `date_range_end`, then sets the datetime-local input `min`, `max`, and `value` to match the actual log data range. This prevents the picker from defaulting to current dates when logs may be years old.

### Filters

| Filter | Behaviour |
|---|---|
| Dataset | Switches the active log source |
| HTTP status | All / 2xx / 3xx / 4xx / 5xx / All Errors |
| From / To | Datetime range restricted to the dataset's actual log span |
| Search | Substring match on endpoint path or IP address (debounced 320 ms) |

### Pagination

Results are paginated server-side. The backend returns `total`, `page`, `page_size`, and `total_pages`; Previous/Next buttons are enabled/disabled accordingly.

---

## 10. Traffic Forecasting Module

**Route:** `#/forecast` (SPA) → `GET /logs/datasets/<id>/forecast/`  
**File:** `logs/services/forecasting.py`  
**Purpose:** Predicts future hourly request volumes using historical log data.

### How it works

1. The service queries all `LogEntry` rows for the dataset, groups them into hourly buckets using `TruncHour`, and builds an ordered list of `(timestamp, count)` pairs.
2. The user selects a **forecasting method** and a **number of future periods** (1–168 hours).
3. The chosen algorithm generates `periods` predicted values beyond the last historical data point.
4. The API response includes both `historical` and `forecast` arrays; the frontend renders them as two overlaid series on a single Chart.js line chart — solid indigo for history, dashed amber for predictions.

### Algorithms

**Moving Average (`moving_avg`)**

Uses the mean of the last 6 hourly buckets as a base, then applies an OLS-derived trend slope to each predicted step so the line rises or falls rather than projecting flat:

```
base  = mean(last 6 values)
slope = OLS slope over full history
forecast[i] = max(0, base + slope × (i + 1))
```

**Linear Regression (`linear_regression`)**

Fits `y = mx + b` to the full historical series using Ordinary Least Squares, then extrapolates forward:

```
m = Σ((x - x̄)(y - ȳ)) / Σ((x - x̄)²)
b = ȳ - m·x̄
forecast[i] = max(0, m × (n + i) + b)
```

### Trend classification

The overall trend (increasing / decreasing / stable) is derived from the OLS slope relative to the series mean:

| Relative slope | Classification |
|---|---|
| > +2% per period | Increasing |
| < −2% per period | Decreasing |
| Between | Stable |

---

## 11. Anomaly Detection Module

**Route:** `#/anomalies` (SPA) → `GET /logs/datasets/<id>/anomalies/`  
**File:** `logs/services/anomaly_detection.py`  
**Purpose:** Identifies hours with abnormally high request volumes or error rates.

### How it works

The module uses **mean ± k·σ (standard deviation) thresholding** — a standard statistical outlier detection method:

1. All hourly buckets are computed from `LogEntry` rows.
2. The mean (μ) and population standard deviation (σ) of all bucket values are calculated in Python.
3. Any hour where the value exceeds `μ + k·σ` is flagged as an anomaly.
4. The user controls sensitivity via the `k` factor:

| Sensitivity | k value | Effect |
|---|---|---|
| Low | 3.0 | Only extreme outliers flagged |
| Medium (default) | 2.0 | Moderate outliers |
| High | 1.5 | More sensitive, more flags |

### Two detection passes

| Type | What is measured | Unit |
|---|---|---|
| `request_spike` | Request count per hour | Requests |
| `error_rate_spike` | Error percentage per hour | % |

### Severity classification

Each anomaly is assigned a severity based on how far the value exceeds the threshold:

| Deviation | Severity |
|---|---|
| ≥ 2k·σ above mean | High |
| ≥ 1.4k·σ above mean | Medium |
| Above threshold | Low |

### Output

Each anomaly record includes: type, timestamp, measured value, threshold, mean, standard deviation, severity, and a human-readable explanation string. The chart overlays red scatter markers at anomalous hours over the full request-volume series.

---

## 12. Alerts Module

**Route:** `#/alerts` (SPA)  
**Files:** `logs/services/alerts.py`, `logs/models.py` (`AlertRule`, `Alert`)  
**Purpose:** Lets users define threshold rules and check whether current dataset metrics violate them.

### How it works

#### Alert Rules (`AlertRule` model)

Each rule belongs to one dataset and one user. Two threshold types are supported:

| Threshold | Trigger condition |
|---|---|
| `error_rate_threshold` (%) | Dataset's overall error rate exceeds this value |
| `request_spike_multiplier` (×) | Most-recent hourly count exceeds `mean × multiplier` |

Rules can be individually enabled/disabled via `is_active`.

#### Checking alerts

When the user clicks **Check Now** (or `POST /logs/datasets/<id>/alerts/check/`):

1. All active `AlertRule` records for the dataset are fetched.
2. Current analytics are retrieved via `get_summary_metrics()` and `get_request_trends()`.
3. Each rule's threshold is evaluated against the live data.
4. For every violated threshold, an `Alert` record is created with: type, measured value, threshold, severity, message, and timestamp.
5. The view returns the list of newly-triggered alerts.

#### Alert history

`GET /logs/datasets/<id>/alerts/history/` returns the most recent 50 triggered alerts, ordered by time descending. The UI renders them in a severity-badged list with timestamps and explanations.

### Models

**`AlertRule`** — stores the configuration:
- `error_rate_threshold` (DecimalField, nullable)
- `request_spike_multiplier` (DecimalField, nullable)
- `is_active` (BooleanField)
- FK to `Dataset` and `User`

**`Alert`** — stores each triggered instance:
- `alert_type`: `error_rate` | `request_spike`
- `value`: the measured value that fired the rule
- `threshold`: the limit that was exceeded
- `severity`: `high` | `medium` | `low`
- `message`: human-readable description
- FK to `AlertRule`

---

## 13. Report Generation Module

**Route:** `#/reports` (SPA) → `GET /logs/datasets/<id>/report/`  
**File:** `logs/services/reports.py`  
**Purpose:** Generates a downloadable summary report of a dataset's analytics.

### How it works

The user selects a dataset and a format, then clicks **Download Report**. The frontend navigates `window.location.href` to the report URL, triggering a file download via `Content-Disposition: attachment`.

The backend computes all report sections fresh (bypassing cache) and serializes the result.

### Report sections

| Section | Content |
|---|---|
| Summary metrics | Total requests, errors, error rate, unique IPs, avg response size, date range |
| Top 20 endpoints | Ranked by request count with percentage share |
| Status distribution | Count per HTTP status code |
| Error breakdown | 4xx vs 5xx totals, top error codes |
| Anomalies | All detected anomalies at medium sensitivity, with explanations |

### Formats

**CSV** — structured with named section headers, suitable for Excel or Google Sheets. Generated with Python's built-in `csv` module; no external dependencies.

**JSON** — a machine-readable nested dict. Useful for archiving, integration, or further programmatic analysis. Served as `application/json` with the attachment header.

---

## 14. Dashboard Customization Module

**Route:** `#/dashboard` (Customize panel)  
**File:** `logs/models.py` (`DashboardPreference`), `logs/views.py` (`dashboard_preferences_view`)  
**Purpose:** Allows users to show or hide individual dashboard widgets and persist their preferences.

### How it works

1. On page load, `LogSightAPI.getPreferences()` fetches `GET /logs/preferences/` to retrieve the user's saved `hidden_widgets` list.
2. If no backend record exists, `localStorage` is used as a fallback so preferences survive page reloads even before the first save.
3. The **Customize** button reveals a panel with a checkbox per widget.
4. Unchecking a widget sets `display: none` on its wrapper (`id="widget-<name>"`).
5. Clicking **Save** calls `POST /logs/preferences/` to persist the list in `DashboardPreference` and stores a copy to `localStorage`.

### Toggleable widgets

| Widget ID | What it controls |
|---|---|
| `stat-requests` | Total requests tile |
| `stat-errors` | Error rate tile |
| `stat-latency` | Avg. response size tile |
| `stat-visitors` | Unique visitors tile |
| `chart-requests` | Request frequency chart |
| `chart-status` | Status distribution chart |
| `chart-endpoints` | Top endpoints chart |
| `chart-traffic` | Traffic patterns chart |

### `DashboardPreference` model

One record per user (`OneToOneField`):
- `hidden_widgets` — JSONField list of widget ID strings
- `preferences` — JSONField dict for arbitrary future preference keys
- `updated_at` — auto-updated timestamp

---

## 15. Frontend SPA Architecture

The entire frontend is a **single-page application** served from one Django template (`app_shell.html`). Navigation never triggers a full page reload.

### Routing

Hash-based routing (`window.location.hash`) maps URL fragments to view sections:

| Hash | View section | Module |
|---|---|---|
| `#/dashboard` | `[data-view="dashboard"]` | Dashboard |
| `#/upload` | `[data-view="upload"]` | Upload |
| `#/datasets` | `[data-view="datasets"]` | Dataset Management |
| `#/viewer` | `[data-view="viewer"]` | Log Viewer |
| `#/forecast` | `[data-view="forecast"]` | Traffic Forecasting |
| `#/anomalies` | `[data-view="anomalies"]` | Anomaly Detection |
| `#/alerts` | `[data-view="alerts"]` | Alerts |
| `#/reports` | `[data-view="reports"]` | Reports |

On `hashchange`, `runRoute()` shows the target section (with a CSS enter animation), hides all others, and calls the appropriate initialisation function.

### JavaScript modules

| File | Responsibility |
|---|---|
| `api.js` | All HTTP communication. Every method returns a normalised, null-safe object — callers never need to guard against missing fields. CSRF token injection, 401 redirect handling. |
| `charts.js` | Chart.js 4 wrappers. Theme-aware colour palette (dark/light). Six renderers: request frequency, status doughnut, top endpoints, traffic area, forecast, anomaly overlay. Custom empty-state plugin. |
| `app.js` | All UI logic: routing, sidebar, theme toggle, upload queue, all module views. |

### Theme system

Dark mode is toggled via a class on `<html>`. The preference is stored in `localStorage`. Chart colours are re-derived on toggle and all charts are redrawn to match.

---

## 16. Database Schema

```
User (Django built-in)
 └── Dataset (owner FK)
      ├── LogEntry (dataset FK) — one per parsed log line
      ├── AnalyticsSummary (dataset OneToOne) — pre-computed aggregates
      └── AlertRule (dataset FK, owner FK)
           └── Alert (rule FK) — one per triggered threshold

User
 └── DashboardPreference (user OneToOne)
```

### Index strategy

`LogEntry` is the largest table and carries the most indexes:

| Index | Columns | Purpose |
|---|---|---|
| Compound | `(dataset, timestamp)` | Time-ordered entry retrieval per dataset |
| Compound | `(dataset, error_flag)` | Error count per dataset |
| Compound | `(dataset, status_code)` | Status distribution per dataset |
| Single | `timestamp` | Range queries in log viewer |
| Single | `status_code` | Status filter |
| Single | `ip_address` | Search |
| Single | `endpoint` | Search |
| Single | `error_flag` | Error filter |

---

## 17. API Reference

All endpoints require authentication. All analytics endpoints are scoped to the authenticated user's datasets.

### Dataset endpoints

| Method | URL | Description |
|---|---|---|
| `GET` | `/logs/upload/` | Returns accepted file types and size limit |
| `POST` | `/logs/upload/` | Upload and queue a log file for parsing |
| `GET` | `/logs/datasets/` | List all datasets |
| `GET` | `/logs/datasets/<id>/` | Dataset detail |
| `GET` | `/logs/datasets/<id>/status/` | Lightweight polling (id, status, entries) |
| `POST` | `/logs/datasets/<id>/delete/` | Delete dataset and all log entries |

### Analytics endpoints

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/analytics/` | `interval`, `limit` |
| `GET` | `/logs/datasets/<id>/analytics/summary/` | — |
| `GET` | `/logs/datasets/<id>/analytics/status-distribution/` | — |
| `GET` | `/logs/datasets/<id>/analytics/request-trends/` | `interval=hour\|day`, `limit` |
| `GET` | `/logs/datasets/<id>/analytics/top-endpoints/` | `limit` |
| `GET` | `/logs/datasets/<id>/analytics/hourly-traffic/` | — |
| `GET` | `/logs/datasets/<id>/analytics/method-distribution/` | — |
| `GET` | `/logs/datasets/<id>/analytics/errors/` | — |
| `POST` | `/logs/datasets/<id>/analytics/refresh/` | — |

### Log viewer

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/entries/` | `page`, `page_size`, `status`, `search`, `date_from`, `date_to` |

### New module endpoints

| Method | URL | Query params / Body |
|---|---|---|
| `GET` | `/logs/datasets/<id>/forecast/` | `periods` (1–168), `method` (`moving_avg`\|`linear_regression`) |
| `GET` | `/logs/datasets/<id>/anomalies/` | `sensitivity` (`low`\|`medium`\|`high`) |
| `GET` | `/logs/datasets/<id>/alerts/` | — |
| `POST` | `/logs/datasets/<id>/alerts/` | `{ name, error_rate_threshold, request_spike_multiplier }` |
| `POST` | `/logs/datasets/<id>/alerts/check/` | — |
| `GET` | `/logs/datasets/<id>/alerts/history/` | `limit` |
| `PUT/DELETE` | `/logs/datasets/<id>/alerts/<rule_id>/` | `{ name, is_active, ... }` |
| `GET` | `/logs/datasets/<id>/report/` | `format` (`csv`\|`json`) |
| `GET` | `/logs/preferences/` | — |
| `POST` | `/logs/preferences/` | `{ hidden_widgets: [...], preferences: {...} }` |

---

*LogSight — Final Year Project · Web-Based Log Analytics & Visualization System*
