# LogSight — System Reference

**Project:** Design and Implementation of a Web-Based Log Analytics and Visualization System  
**Version:** 2.0  
**Stack:** Django 5 · PostgreSQL · Chart.js 4 · Tailwind CSS · Vanilla JS SPA

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Project Structure](#2-project-structure)
3. [Authentication & Accounts](#3-authentication--accounts)
4. [Role-Based Access Control (RBAC)](#4-role-based-access-control-rbac)
5. [Log Upload](#5-log-upload)
6. [Log Parsing Engine](#6-log-parsing-engine)
7. [Dataset Management](#7-dataset-management)
8. [Analytics Engine](#8-analytics-engine)
9. [Dashboard](#9-dashboard)
10. [Dashboard Date Range Filter](#10-dashboard-date-range-filter)
11. [Dashboard Customization](#11-dashboard-customization)
12. [Log Viewer](#12-log-viewer)
13. [Advanced Search](#13-advanced-search)
14. [Traffic Forecasting](#14-traffic-forecasting)
15. [Anomaly Detection](#15-anomaly-detection)
16. [Alerts](#16-alerts)
17. [Security Insights](#17-security-insights)
18. [Dataset Comparison](#18-dataset-comparison)
19. [Reports](#19-reports)
20. [Live Ingestion Simulator](#20-live-ingestion-simulator)
21. [Notifications](#21-notifications)
22. [Frontend SPA Architecture](#22-frontend-spa-architecture)
23. [Database Schema](#23-database-schema)
24. [Full API Reference](#24-full-api-reference)

---

## 1. System Overview

LogSight is a full-stack web application that ingests raw Apache and Nginx access log files, parses them into a relational database, computes analytics, and presents the results through an interactive single-page application.

### Core data flow

```
User uploads .log / .txt / .gz file
        │
        ▼
Django receives file → Dataset record created (status: pending)
        │
        ▼
Background thread: LogParsingService streams file line-by-line
        │   Regex matches each line → LogEntry rows bulk-inserted
        ▼
Dataset status → "completed" + dataset_ready notification sent
        │
        ▼
AnalyticsService aggregates at DB level → AnalyticsSummary persisted + cache primed
        │
        ▼
SPA fetches JSON from REST API → Chart.js renders visualizations
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Background thread for parsing | Returns HTTP 202 immediately; files up to 500 MB parse without blocking |
| DB-level aggregation | All analytics use Django ORM `.annotate()` + `GROUP BY` — no Python-side row iteration |
| 15-minute in-memory cache | Avoids re-running aggregations on every page load; bypassed when date filters are active |
| Hash-based SPA routing | No full page reloads between modules; one Django template serves all views |
| Pure Python algorithms | No ML library dependencies; fully explainable statistics suitable for academic assessment |
| Console email backend (dev) | Emails print to the terminal; swap `EMAIL_BACKEND` in settings for production SMTP |

---

## 2. Project Structure

```
logsight/
├── loganalytics/                  # Django project configuration
│   ├── settings.py                # DB, upload limits, cache, auth, email (SMTP / console)
│   ├── urls.py                    # Root URL dispatcher
│   └── wsgi.py
│
├── accounts/                      # Authentication app
│   ├── views.py                   # Login, register, logout, profile
│   ├── forms.py
│   └── templates/accounts/
│       ├── login.html             # Standalone split-panel login page
│       ├── register.html
│       └── profile.html           # Standalone profile page (matches app nav)
│
├── logs/                          # Core analytics app
│   ├── models.py                  # Dataset, LogEntry, AnalyticsSummary,
│   │                              #   AlertRule, Alert, DashboardPreference,
│   │                              #   Notification, UserProfile
│   ├── views.py                   # All REST API endpoints (~950 lines)
│   ├── urls.py                    # 35 URL patterns
│   ├── forms.py                   # LogUploadForm
│   ├── admin.py
│   ├── apps.py                    # Connects post_save signal for UserProfile
│   ├── signals.py                 # Auto-creates UserProfile; first user → admin role
│   ├── migrations/                # 7 migrations (0001 – 0007)
│   └── services/
│       ├── __init__.py            # Public re-exports for all services
│       ├── log_parser.py          # Apache/Nginx Combined Log Format parser
│       ├── analytics.py           # Aggregation queries, caching, filtered summary
│       ├── log_entries.py         # Paginated log viewer with multi-field filters
│       ├── dataset_management.py  # Upload, background parse, delete, notifications
│       ├── forecasting.py         # Seasonal pattern + linear regression + confidence bands
│       ├── anomaly_detection.py   # Mean ± k·σ spike detection
│       ├── alerts.py              # Auto health check, persistent Alert records, history
│       ├── reports.py             # CSV and JSON report generation
│       ├── security.py            # Brute-force, scanner, sensitive-path, error-concentration
│       ├── comparison.py          # Side-by-side dataset analytics
│       ├── notifications.py       # Create, list, mark-read, email dispatch
│       ├── simulator.py           # Synthetic log generation, error/traffic spike injection
│       └── rbac.py                # Role management, require_role() decorator
│
├── frontend/
│   ├── js/
│   │   ├── app.js                 # SPA routing + all module UI logic (~1 500 lines)
│   │   ├── api.js                 # HTTP client, all API methods (~850 lines)
│   │   └── charts.js              # Chart.js wrappers, 7 chart renderers
│   ├── css/
│   │   └── styles.css             # Design system, nav, context toolbar,
│   │                              #   notification bell, auth pages
│   └── assets/
│       └── logsight-logo.png
│
├── .env                           # DB credentials, Gmail app password (not committed)
├── requirements.txt
└── manage.py
```

---

## 3. Authentication & Accounts

**App:** `accounts/`

All analytics data is strictly scoped to the authenticated user. Every `logs/` view applies `dataset__owner=request.user` at the ORM level — no user can read another user's datasets through any endpoint.

### Login page

A standalone split-panel page (does not extend the auth base shell):
- **Left panel** — dark teal-navy background with a live log stream preview showing colour-coded status codes (2xx green, 3xx blue, 4xx amber, 5xx red) and three stat tiles matching the dashboard aesthetic.
- **Right panel** — light background matching the main app; username/password fields with icon prefixes; teal sign-in button.

### Profile page

A standalone page that matches the main app's topbar. Shows the user's avatar initial, username, join date, last login, and an account details form for updating name and email.

### Routes

| URL | Purpose |
|---|---|
| `/accounts/login/` | Sign in |
| `/accounts/register/` | Create account |
| `/accounts/logout/` | POST logout |
| `/accounts/profile/` | Edit profile |

---

## 4. Role-Based Access Control (RBAC)

**Files:** `logs/services/rbac.py`, `logs/signals.py`, `logs/models.py` (`UserProfile`)

### Roles

| Role | Permissions |
|---|---|
| `admin` | Full access — upload, delete, analyse, manage users |
| `analyst` | Upload and analyse own datasets; no user management |
| `viewer` | Read-only dashboard and log viewer; cannot upload or delete |
| `pending` | New user awaiting role assignment; cannot access any data |

### How it works

- A `UserProfile` record is created automatically via a `post_save` signal on `User`.
- The **first user ever registered** on the system is automatically assigned the `admin` role.
- All subsequent users start as `pending` until an admin assigns them a role.
- The `require_role(*roles)` decorator restricts views to specific roles.
- An admin-only user management page (`GET /logs/admin/users/`) lists all users with their profiles; role assignment is done via `POST /logs/admin/users/<id>/role/`.

### `UserProfile` model

| Field | Type | Notes |
|---|---|---|
| `user` | OneToOneField | Links to Django `User` |
| `role` | CharField | `admin` / `analyst` / `viewer` / `pending` |
| `created_at` | DateTimeField | |

---

## 5. Log Upload

**Route:** `#/upload` → `POST /logs/upload/`

### How it works

1. User drags a file onto the drop zone or clicks to browse. Multiple files are accepted and queued.
2. The frontend sends a `multipart/form-data` POST.
3. `LogUploadForm` validates file type (`.log`, `.txt`, `.gz`) and size.
4. `create_dataset_from_upload()` saves the file to `media/uploads/`, creates a `Dataset` record (`status=pending`), and spawns a **daemon thread** for parsing.
5. The view returns **HTTP 202 Accepted** immediately.
6. The frontend polls `GET /logs/datasets/<id>/status/` every second until `status` is `completed` or `failed`.
7. On completion, a `dataset_ready` notification is automatically created for the owner.

### Configurable limits (`settings.py`)

| Setting | Default |
|---|---|
| `LOGS_MAX_UPLOAD_SIZE` | 500 MB |
| `FILE_UPLOAD_MAX_MEMORY_SIZE` | 2 MB (larger files stream to disk) |
| `LOGS_PARSE_BATCH_SIZE` | 2 000 rows per `bulk_create()` |

---

## 6. Log Parsing Engine

**File:** `logs/services/log_parser.py`

`LogParsingService.parse_dataset()` runs in the background thread:

1. Opens the file — supports `.gz` via `gzip.open`.
2. Reads line by line; never loads the full file into memory.
3. Each line is matched against `ACCESS_LOG_PATTERN` (compiled regex) extracting: IP address, timestamp, method, endpoint, protocol, status code, response size, referrer, user agent.
4. Parsed entries accumulate in a list; every 2 000 entries, `LogEntry.objects.bulk_create()` inserts the batch in a single transaction.
5. After all lines are processed: `dataset.status = completed`, `compute_and_persist_analytics()` runs, and a notification is sent.
6. On any failure: `dataset.status = failed`, error message stored in `dataset.processing_error`.

### Supported formats

| Format | Description |
|---|---|
| Apache Combined | `IP - - [timestamp] "METHOD /path HTTP/x.x" status size "referrer" "user-agent"` |
| Nginx access | Identical to Apache Combined Log Format |

---

## 7. Dataset Management

**Route:** `#/datasets`

Lists all datasets with: ID, filename, source format, file size, entry count, upload date, and processing status badge.

**Actions per row:**
- **Analyse** — sets the dataset as active and navigates to the Dashboard.
- **View** — opens the Log Viewer with that dataset pre-selected.
- **Delete** — `POST /logs/datasets/<id>/delete/` cascades to delete all `LogEntry` rows and removes the file from disk.

---

## 8. Analytics Engine

**File:** `logs/services/analytics.py`

All functions use Django ORM `.annotate()` with `GROUP BY` — no Python-side row scanning. Results are cached in `LocMemCache` (15-minute TTL, per dataset per metric type). When date filters are provided, the cache is bypassed and `LogEntry` is queried directly.

### Functions

| Function | Returns | Date-filter support |
|---|---|---|
| `get_summary_metrics()` | Totals, error rate, unique IPs, avg response size, date range | No (uses cache) |
| `get_filtered_summary()` | Same shape as summary but filtered by date range | Yes (always live) |
| `get_status_distribution()` | Counts per status code and per category (1xx–5xx) | Yes |
| `get_request_trends()` | Time-series counts by hour or day | Yes |
| `get_top_endpoints()` | Top N endpoints ranked by request count | Yes |
| `get_hourly_traffic()` | Request counts per hour of day (0–23) | Yes |
| `get_method_distribution()` | Counts per HTTP method | No |
| `get_error_breakdown()` | 4xx/5xx totals, top error codes, top error endpoints | No |
| `get_analytics_overview()` | All of the above combined | No |

---

## 9. Dashboard

**Route:** `#/dashboard`

The main landing view. Shows four stat tiles and four charts for the selected dataset.

### Stat tiles

| Tile | Metric |
|---|---|
| Total requests | `total_requests` |
| Error rate | `error_rate` (%) |
| Avg. response size | `avg_response_size` (bytes) |
| Unique visitors | Distinct IP addresses |

### Charts

| Chart | Type | Endpoint |
|---|---|---|
| Request frequency | Line (area fill) | `/analytics/request-trends/` |
| Status distribution | Doughnut | `/analytics/status-distribution/` |
| Top endpoints | Horizontal bar | `/analytics/top-endpoints/` |
| Traffic patterns | Area line | `/analytics/hourly-traffic/` |

When a date filter is active, all four charts and all four stat tiles are re-fetched with the filtered date range applied at the database level.

---

## 10. Dashboard Date Range Filter

**Location:** Dashboard toolbar (inline with the dataset selector)

Allows narrowing all dashboard analytics to a specific date range within the dataset.

### How it works

1. **"Filter by date"** button toggles a From/To date picker row into view. A small indigo dot appears on the button whenever a filter is active.
2. When a dataset is selected, the system fetches the dataset's `date_range.start` and `date_range.end` from the analytics summary and:
   - Sets the `min` and `max` attributes on both inputs (visually constrains Chrome/Edge calendar pickers).
   - Shows the allowed range as text next to the inputs (e.g. `2015-05-17 – 2015-06-04`) so every browser shows it.
   - Programmatically clamps any typed value that escapes the range on `change`.
3. Cross-field logic: From cannot exceed To; To cannot precede From.
4. When dates are set, `loadDashboardData` uses `GET /analytics/filtered-summary/` for stat tiles and passes `date_from`/`date_to` to all chart endpoints, bypassing the cache.
5. **Clear** button resets both inputs, removes the active dot, and reloads the full dataset view.

---

## 11. Dashboard Customization

**Location:** Dashboard → Customize button

Lets users show or hide individual dashboard widgets, with preferences persisted to the database.

### Toggleable widgets

`stat-requests`, `stat-errors`, `stat-latency`, `stat-visitors`, `chart-requests`, `chart-status`, `chart-endpoints`, `chart-traffic`

### Persistence

`DashboardPreference` model (one record per user, OneToOne): stores a `hidden_widgets` list as a JSONField. On load, `GET /logs/preferences/` fetches saved preferences; `POST /logs/preferences/` saves them. A `localStorage` copy acts as a fallback.

---

## 12. Log Viewer

**Route:** `#/viewer` → `GET /logs/datasets/<id>/entries/`

Paginated, filterable table of raw log entries. 25 entries per page.

### Filters

| Filter | Behaviour |
|---|---|
| Dataset | Switches the active log source |
| Status | All / 2xx / 3xx / 4xx / 5xx / All Errors |
| From / To | Datetime range auto-constrained to the dataset's actual log span |
| Search | Substring match on endpoint or raw line (debounced 320 ms) |

When a dataset is selected, `setViewerDateRange()` fetches the analytics summary to read `date_range_start`/`date_range_end` and pre-fills the date pickers to the actual log span, preventing the picker from defaulting to today's date on old log files.

---

## 13. Advanced Search

**Route:** `#/search`

Multi-field query builder for power-user log analysis across a selected dataset.

### Filter fields

| Field | Match type |
|---|---|
| Keyword (raw) | Substring in raw log line |
| IP Address | Substring / exact |
| Method | Exact (GET, POST, etc.) |
| Endpoint | Substring |
| Status Code | Exact |
| Date from / Date to | Inclusive range |

### How it works

- Users add filter rows with **Add filter**. Each row is `[field selector] [value input]`.
- AND logic is applied across all active rows.
- **Search** sends all filters as query params to `GET /logs/datasets/<id>/entries/`, which applies them at the database level via Django ORM `Q` objects.
- Results render in a paginated table (Previous / Next) with the total count displayed.
- **Save Search** stores the current filter set in `localStorage` (up to 10 saved searches). Clicking a saved search re-populates the filter rows and runs the query immediately.

---

## 14. Traffic Forecasting

**Route:** `#/forecast` → `GET /logs/datasets/<id>/forecast/`  
**File:** `logs/services/forecasting.py`

Predicts future hourly request volumes from historical log data.

### Methods

#### Seasonal Pattern (`moving_avg` parameter)

Averages traffic per hour-of-day across the full dataset history to produce the dataset's "typical day" profile, then projects that pattern forward with a trend adjustment:

```
hourly_avg[h] = mean of all historical values that fall in hour h

trend_factor[i] = 1 + (OLS_slope × (i+1)) / overall_mean

forecast[i] = max(0, hourly_avg[next_hour] × trend_factor[i])
```

This produces a wavy, realistic forecast that rises and falls with the dataset's natural daily rhythm rather than projecting a flat line.

#### Linear Regression (`linear_regression` parameter)

Fits `y = mx + b` to the full historical series using Ordinary Least Squares, then extrapolates:

```
m = Σ((x - x̄)(y - ȳ)) / Σ((x - x̄)²)
forecast[i] = max(0, m × (n + i) + b)
```

Best for showing a clean overall trend direction.

### Confidence bands

Both methods return `upper` (forecast × 1.20) and `lower` (forecast × 0.80) alongside every forecast point. The chart renders a shaded amber band between upper and lower bounds to communicate uncertainty.

### Chart

Historical data renders as a solid indigo area line; the forecast renders as a dashed amber line; the confidence band renders as a translucent amber fill behind the forecast line.

### Trend classification

| Relative OLS slope | Label |
|---|---|
| > +2% per period | Increasing |
| < −2% per period | Decreasing |
| Between | Stable |

---

## 15. Anomaly Detection

**Route:** `#/anomalies` → `GET /logs/datasets/<id>/anomalies/`  
**File:** `logs/services/anomaly_detection.py`

Identifies hours with abnormally high request volumes or error rates using **mean ± k·σ thresholding**.

### Detection passes

| Type | What is measured |
|---|---|
| `request_spike` | Request count per hourly bucket |
| `error_rate_spike` | Error percentage per hourly bucket |

### Sensitivity (controls the k factor)

| Label | k value | Effect |
|---|---|---|
| Low — only major spikes | 3.0 | Only extreme outliers |
| Medium — balanced (default) | 2.0 | Moderate outliers |
| High — catch subtle shifts | 1.5 | More sensitive, more flags |

### Severity

| Deviation above threshold | Severity label |
|---|---|
| ≥ 2k · σ | Critical |
| ≥ 1.4k · σ | Notable |
| Above threshold | Minor |

### Results

A colour-coded summary banner describes findings in plain English ("Found 4 anomalies — 1 critical spike detected"). Each anomaly is listed as a dot + timestamp + plain-English explanation without raw threshold numbers.

---

## 16. Alerts

**Route:** `#/alerts`  
**Files:** `logs/services/alerts.py`, `logs/models.py` (`AlertRule`, `Alert`)

### Auto health check

No rule configuration required. The system runs two built-in checks when **Run Health Check** is clicked (or after every simulator batch):

| Check | Warning | Critical |
|---|---|---|
| Error Rate | > 10% | > 25% |
| Traffic Spike | Last hour > 2× dataset mean | Last hour > 3× dataset mean |

Results appear as colour-coded cards (green / amber / red). Warnings and critical findings are **automatically persisted as `Alert` records** and trigger a **notification** (bell badge) and an **email** to `mbayaasante@gmail.com`.

### Alert history table

A persistent "Triggered Alerts — All Datasets" table at the bottom of the Alerts module shows every alert that has ever fired across all datasets. Columns: timestamp, dataset name, type (Error Rate / Traffic Spike), severity badge, message. Loads automatically on module open; has a Refresh button.

### System AlertRule

A hidden `__auto_check__` `AlertRule` is created automatically per dataset on first check. This is how `Alert` records are stored without user-created rules.

### Email delivery

Alert emails are sent to `LOGSIGHT_ALERT_EMAIL` (default: `mbayaasante@gmail.com`). Configure `GMAIL_APP_PASSWORD` and `GMAIL_ADDRESS` in `.env` for real Gmail SMTP delivery. Without those variables, emails print to the Django `runserver` terminal (console backend).

---

## 17. Security Insights

**Route:** `#/security` → `GET /logs/datasets/<id>/security/`  
**File:** `logs/services/security.py`

Analyses log data for suspicious patterns. Every finding is derived directly from the actual log entries — no predefined rules or signatures.

### Checks

| Check | Detection method | Severity |
|---|---|---|
| **Brute force** | IPs with ≥ 20 failed (4xx) requests to the same endpoint | High |
| **Path scanning** | IPs that hit ≥ 15 distinct unique endpoints | Medium |
| **Sensitive path access** | Requests to `.env`, `admin`, `wp-login`, `phpmyadmin`, `.git`, `passwd`, `shell`, `setup.php`, `wp-admin`, `config` | High |
| **High error concentration** | IPs responsible for ≥ 100 error responses (4xx/5xx) | Medium |

### Risk score

`min(100, sum of weights for active findings)`:
- Brute force: 30
- Sensitive path: 25
- High error concentration: 25
- Path scanning: 20

### Output

Each finding card shows: title, plain-English description with actual data values, and up to 5 evidence items (IP addresses, endpoint paths, counts). A risk score badge (0–100) appears in the summary banner.

---

## 18. Dataset Comparison

**Route:** `#/compare` → `GET /logs/compare/?a=<id>&b=<id>`  
**File:** `logs/services/comparison.py`

Compares two datasets side by side using the existing analytics functions called twice.

### Metrics table

Shows Total Requests, Error Rate (%), Avg Response Size, and Unique Visitors for Dataset A and Dataset B, with a delta column (B − A) colour-coded: red for increases, green for decreases.

### Traffic patterns chart

Both datasets' hourly-by-hour-of-day traffic overlaid on the same line chart — Dataset A as solid indigo, Dataset B as dashed amber.

### Top endpoints (Dataset A)

A ranked list of Dataset A's top 10 endpoints with request counts.

---

## 19. Reports

**Route:** `#/reports` → `GET /logs/datasets/<id>/report/?format=csv|json`  
**File:** `logs/services/reports.py`

Generates a downloadable summary report. The browser navigates `window.location.href` to trigger a file download via `Content-Disposition: attachment`.

### Report sections

| Section | Content |
|---|---|
| Summary metrics | Total requests, errors, error rate, unique IPs, avg response size, date range |
| Top 20 endpoints | Ranked by request count with percentage share |
| Status distribution | Count per HTTP status code |
| Error breakdown | 4xx vs 5xx totals, top error codes |
| Anomalies | All detected anomalies (medium sensitivity) with explanations |

### Formats

- **CSV** — named section headers; open in Excel, Google Sheets, or any analysis tool.
- **JSON** — machine-readable nested dict; useful for integration or archiving.

---

## 20. Live Ingestion Simulator

**Route:** `#/simulate`  
**File:** `logs/services/simulator.py`

Simulates real-time log ingestion by generating synthetic Apache/Nginx entries on demand and appending them to a selected dataset. Useful for demoing the analytics pipeline and testing alert thresholds without a live web server.

### Buttons

| Button | Action |
|---|---|
| **Generate Once** | Inserts one batch of N synthetic entries (1–50, default 10) |
| **Auto (every 3 s)** | Starts an interval timer that generates a batch every 3 seconds; click again to stop |
| **Spike Errors** | Injects 30 entries that are ~70% 4xx/5xx to push error rate above critical threshold |
| **Spike Traffic** | Injects 50 normal GET 200 entries in one shot to push last-hour count above 3× mean |
| **Download CSV** | Downloads all simulator-generated entries for the dataset as a CSV file |

### Alert integration

After every batch (regular, error spike, or traffic spike), the backend automatically runs `auto_check_alerts()`. If a warning or critical condition is detected:
- An `Alert` record is persisted.
- A `Notification` is created (bell badge updates).
- An email is sent to `mbayaasante@gmail.com`.
- The simulator's status tile shows "⚠ CRITICAL ALERT" or "⚡ WARNING" in red/amber.

### Live feed

New entries appear at the top of the feed panel in real time, styled with colour-coded status codes matching the rest of the app. The feed is capped at 100 visible lines.

---

## 21. Notifications

**Route:** Bell icon in top navigation  
**File:** `logs/services/notifications.py`, `logs/models.py` (`Notification`)

In-app notification system for alert triggers, dataset processing completions, and other system events.

### How it works

- A bell icon sits in the topbar with a red badge showing the unread count.
- The count is polled every 30 seconds via `GET /logs/notifications/count/`.
- Clicking the bell opens a dropdown showing the latest notifications with severity dots (red = critical, amber = warning, indigo = info), message text, and timestamp.
- Clicking an unread notification marks it read.
- **Mark all read** button clears all at once.

### Notification types

| Type | When created |
|---|---|
| `dataset_ready` | When a dataset finishes parsing |
| `alert` | When `auto_check_alerts()` detects a warning or critical condition |

### Email dispatch

Every `warning` or `critical` notification automatically triggers an email to `LOGSIGHT_ALERT_EMAIL`. In development (no `GMAIL_APP_PASSWORD` in `.env`), the email is printed to the `runserver` terminal. With Gmail SMTP credentials in `.env`, the email is delivered to the real inbox.

### `Notification` model

| Field | Type |
|---|---|
| `user` | ForeignKey to User |
| `message` | TextField |
| `severity` | `info` / `warning` / `critical` |
| `notification_type` | `dataset_ready` / `alert` / `general` |
| `is_read` | BooleanField (default False) |
| `created_at` | DateTimeField (indexed) |
| `dataset` | ForeignKey to Dataset (nullable) |

---

## 22. Frontend SPA Architecture

The entire frontend is a **single-page application** served from `logs/templates/logs/app_shell.html`. Navigation never triggers a full page reload.

### Routing

Hash-based routing (`window.location.hash`) maps URL fragments to `<section data-view="...">` elements:

| Hash | Module |
|---|---|
| `#/dashboard` | Dashboard |
| `#/upload` | Log Upload |
| `#/datasets` | Dataset Management |
| `#/viewer` | Log Viewer |
| `#/forecast` | Traffic Forecasting |
| `#/anomalies` | Anomaly Detection |
| `#/compare` | Dataset Comparison |
| `#/search` | Advanced Search |
| `#/reports` | Reports |
| `#/alerts` | Alerts |
| `#/security` | Security Insights |
| `#/simulate` | Live Ingestion Simulator |

On `hashchange`, `runRoute()` shows the target section (CSS enter animation), hides all others, and calls the appropriate init function.

### JavaScript files

| File | Responsibility |
|---|---|
| `api.js` | All HTTP communication. Every method returns a normalised null-safe object. CSRF token injection, 401 redirect handling, `_dateQs()` helper for date-range query strings. |
| `charts.js` | Chart.js 4 wrappers. Theme-aware colour palette. Seven renderers: request frequency, status doughnut, top endpoints, traffic area, forecast (with confidence band), anomaly overlay, dataset comparison. |
| `app.js` | SPA routing, all module init/run/render functions, notification polling, dashboard date filter, theme toggle, upload queue. |

### Top navigation

The desktop nav has four groups: **Dashboard**, **Data** (Upload, Datasets, Log Viewer), **Analytics** (Forecasting, Anomalies, Compare, Advanced Search, Reports), **Monitoring** (Alerts, Security Insights, Live Ingestion). Hover-activated dropdowns with smooth scale/fade transitions.

### Context toolbar

A sticky toolbar below the topbar shows the current page title and — on modules where it is meaningful (Log Viewer, Forecasting, Anomaly Detection, Reports, Security, Compare) — a date range badge showing the dataset's actual log span.

---

## 23. Database Schema

```
User (Django built-in)
 ├── UserProfile (user OneToOne)       ← RBAC role
 ├── DashboardPreference (user OneToOne)
 ├── Notification (user FK, dataset FK nullable)
 └── Dataset (owner FK)
      ├── LogEntry (dataset FK)         ← one per parsed log line
      ├── AnalyticsSummary (dataset OneToOne)
      └── AlertRule (dataset FK, owner FK)
           └── Alert (rule FK)          ← one per triggered threshold
```

### `LogEntry` indexes

| Index | Columns | Purpose |
|---|---|---|
| Compound | `(dataset, timestamp)` | Time-ordered retrieval per dataset |
| Compound | `(dataset, error_flag)` | Error count per dataset |
| Compound | `(dataset, status_code)` | Status distribution per dataset |
| Single | `timestamp` | Range queries in log viewer and date filter |
| Single | `status_code` | Status filter |
| Single | `ip_address` | IP search and security checks |
| Single | `endpoint` | Endpoint search |
| Single | `error_flag` | Error filter |

---

## 24. Full API Reference

All endpoints require authentication. All dataset-scoped endpoints filter by `dataset__owner=request.user`.

### Authentication

| Method | URL | Description |
|---|---|---|
| `POST` | `/accounts/login/` | Sign in |
| `POST` | `/accounts/register/` | Create account |
| `POST` | `/accounts/logout/` | Sign out |
| `GET/POST` | `/accounts/profile/` | View / update profile |

### Dataset management

| Method | URL | Notes |
|---|---|---|
| `GET` | `/logs/upload/` | Returns accepted file types and size limit |
| `POST` | `/logs/upload/` | Upload and queue a log file |
| `GET` | `/logs/datasets/` | List all datasets |
| `GET` | `/logs/datasets/<id>/` | Dataset detail |
| `GET` | `/logs/datasets/<id>/status/` | Lightweight polling (id, status, entry count) |
| `POST` | `/logs/datasets/<id>/delete/` | Delete dataset and all its log entries |

### Log entries

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/entries/` | `page`, `page_size`, `status`, `search`, `ip`, `method`, `endpoint`, `date_from`, `date_to` |

### Analytics

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/analytics/` | `interval`, `limit` |
| `GET` | `/logs/datasets/<id>/analytics/summary/` | — |
| `GET` | `/logs/datasets/<id>/analytics/filtered-summary/` | `date_from`, `date_to` (YYYY-MM-DD) |
| `GET` | `/logs/datasets/<id>/analytics/status-distribution/` | `date_from`, `date_to` |
| `GET` | `/logs/datasets/<id>/analytics/request-trends/` | `interval`, `limit`, `date_from`, `date_to` |
| `GET` | `/logs/datasets/<id>/analytics/top-endpoints/` | `limit`, `date_from`, `date_to` |
| `GET` | `/logs/datasets/<id>/analytics/hourly-traffic/` | `date_from`, `date_to` |
| `GET` | `/logs/datasets/<id>/analytics/method-distribution/` | — |
| `GET` | `/logs/datasets/<id>/analytics/errors/` | — |
| `POST` | `/logs/datasets/<id>/analytics/refresh/` | — |

### Forecasting

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/forecast/` | `periods` (1–168), `method` (`moving_avg` / `linear_regression`) |

### Anomaly detection

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/anomalies/` | `sensitivity` (`low` / `medium` / `high`) |

### Alerts

| Method | URL | Notes |
|---|---|---|
| `GET` | `/logs/datasets/<id>/alerts/` | List alert rules and recent history |
| `POST` | `/logs/datasets/<id>/alerts/` | Create rule `{ name, error_rate_threshold, request_spike_multiplier }` |
| `PUT/DELETE` | `/logs/datasets/<id>/alerts/<rule_id>/` | Update or delete a rule |
| `POST` | `/logs/datasets/<id>/alerts/check/` | Evaluate active rules |
| `GET` | `/logs/datasets/<id>/alerts/history/` | `limit` |
| `GET` | `/logs/datasets/<id>/alerts/auto/` | Run built-in smart health check |
| `GET` | `/logs/alerts/all-history/` | All triggered alerts across all datasets |

### Security insights

| Method | URL |
|---|---|
| `GET` | `/logs/datasets/<id>/security/` |

### Dataset comparison

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/compare/` | `a=<dataset_id>&b=<dataset_id>` |

### Reports

| Method | URL | Query params |
|---|---|---|
| `GET` | `/logs/datasets/<id>/report/` | `format` (`csv` / `json`) |

### Live ingestion simulator

| Method | URL | Notes |
|---|---|---|
| `POST` | `/logs/datasets/<id>/simulate/` | `?count=N` (1–50) |
| `POST` | `/logs/datasets/<id>/simulate/error-spike/` | Injects 30 high-error entries |
| `POST` | `/logs/datasets/<id>/simulate/traffic-spike/` | Injects 50 burst entries |
| `GET` | `/logs/datasets/<id>/simulate/download/` | CSV download of simulated entries |

### Notifications

| Method | URL | Notes |
|---|---|---|
| `GET` | `/logs/notifications/` | List notifications (`?unread=1` for unread only) |
| `GET` | `/logs/notifications/count/` | `{ unread_count }` |
| `POST` | `/logs/notifications/<id>/read/` | Mark one notification read |
| `POST` | `/logs/notifications/read-all/` | Mark all notifications read |

### Dashboard preferences

| Method | URL | Body |
|---|---|---|
| `GET` | `/logs/preferences/` | — |
| `POST` | `/logs/preferences/` | `{ hidden_widgets: [...] }` |

### RBAC administration

| Method | URL | Notes |
|---|---|---|
| `GET` | `/logs/admin/users/` | Admin only — list all users with roles |
| `POST` | `/logs/admin/users/<id>/role/` | Admin only — `{ role: "analyst" }` |

---

*LogSight — Final Year Project · Web-Based Log Analytics & Visualization System*
