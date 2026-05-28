"""
Security insights service for LogSight.

Analyses log entries for common attack / abuse patterns:
  - Brute force login attempts (high error rate to one endpoint from one IP)
  - Path scanning (one IP hitting many distinct endpoints)
  - Sensitive path access (.env, admin, wp-login, etc.)
  - High error concentration (IP responsible for >= 100 errors)
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Sensitive endpoint substrings that indicate reconnaissance / exploitation attempts.
_SENSITIVE_PATTERNS = [
    ".env", "admin", "wp-login", "phpmyadmin", "config",
    ".git", "passwd", "wp-admin", "shell", "setup.php",
]

# Minimum thresholds for each check.
_BRUTE_FORCE_MIN_ERRORS = 20
_PATH_SCAN_MIN_PATHS = 15
_HIGH_ERROR_MIN_COUNT = 100

# Weights for risk score computation (0-100 scale).
_WEIGHTS = {
    "brute_force": 30,
    "path_scanning": 20,
    "sensitive_path": 25,
    "high_error_concentration": 25,
}


def get_security_insights(dataset_id: int, owner) -> dict[str, Any]:
    """
    Run four security checks against a dataset's log entries and return a
    structured findings dict with a 0-100 risk score.

    All model imports are local to avoid circular dependency issues.
    """
    from logs.models import LogEntry

    base_qs = LogEntry.objects.filter(dataset_id=dataset_id, dataset__owner=owner)

    findings: list[dict[str, Any]] = []
    risk_components: list[int] = []

    # ------------------------------------------------------------------
    # 1. Brute-force detection
    # ------------------------------------------------------------------
    bf_finding = _check_brute_force(base_qs)
    if bf_finding:
        findings.append(bf_finding)
        risk_components.append(_WEIGHTS["brute_force"])

    # ------------------------------------------------------------------
    # 2. Path scanning detection
    # ------------------------------------------------------------------
    ps_finding = _check_path_scanning(base_qs)
    if ps_finding:
        findings.append(ps_finding)
        risk_components.append(_WEIGHTS["path_scanning"])

    # ------------------------------------------------------------------
    # 3. Sensitive path access
    # ------------------------------------------------------------------
    sp_finding = _check_sensitive_paths(base_qs)
    if sp_finding:
        findings.append(sp_finding)
        risk_components.append(_WEIGHTS["sensitive_path"])

    # ------------------------------------------------------------------
    # 4. High error concentration
    # ------------------------------------------------------------------
    he_finding = _check_high_error_concentration(base_qs)
    if he_finding:
        findings.append(he_finding)
        risk_components.append(_WEIGHTS["high_error_concentration"])

    risk_score = min(100, sum(risk_components))

    if not findings:
        summary = "No security threats detected in the dataset."
    elif risk_score >= 70:
        summary = (
            f"High-risk dataset: {len(findings)} security finding(s) detected "
            f"(risk score {risk_score}/100). Immediate review recommended."
        )
    elif risk_score >= 40:
        summary = (
            f"Moderate risk: {len(findings)} security finding(s) detected "
            f"(risk score {risk_score}/100). Investigation advised."
        )
    else:
        summary = (
            f"{len(findings)} low-severity security finding(s) detected "
            f"(risk score {risk_score}/100)."
        )

    return {
        "findings": findings,
        "risk_score": risk_score,
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

def _check_brute_force(base_qs) -> dict[str, Any] | None:
    """
    Flag (ip_address, endpoint) pairs where >= 20 requests were errors (status >= 400).

    Groups by ip+endpoint and counts errors; any pair with error_count >= 20 is flagged.
    """
    from django.db.models import Count, Q

    qs = (
        base_qs
        .filter(status_code__gte=400)
        .values("ip_address", "endpoint")
        .annotate(
            error_count=Count("id"),
        )
        .filter(error_count__gte=_BRUTE_FORCE_MIN_ERRORS)
        .order_by("-error_count")
    )

    rows = list(qs)
    if not rows:
        return None

    evidence = [
        {
            "ip": r["ip_address"],
            "endpoint": r["endpoint"],
            "error_count": r["error_count"],
        }
        for r in rows[:20]
    ]

    top = rows[0]
    description = (
        f"{len(rows)} IP/endpoint combination(s) show repeated error responses "
        f"consistent with brute-force behaviour. "
        f"Worst offender: {top['ip_address']} → {top['endpoint']} "
        f"with {top['error_count']} error requests."
    )

    return {
        "type": "brute_force",
        "severity": "high",
        "title": "Possible Brute-Force Attack",
        "description": description,
        "evidence": evidence,
        "count": len(rows),
    }


def _check_path_scanning(base_qs) -> dict[str, Any] | None:
    """
    Flag IPs that accessed >= 15 distinct endpoints (path enumeration / scanning).
    """
    from django.db.models import Count

    qs = (
        base_qs
        .values("ip_address")
        .annotate(distinct_paths=Count("endpoint", distinct=True))
        .filter(distinct_paths__gte=_PATH_SCAN_MIN_PATHS)
        .order_by("-distinct_paths")
    )

    rows = list(qs)
    if not rows:
        return None

    evidence = [
        {
            "ip": r["ip_address"],
            "distinct_paths": r["distinct_paths"],
        }
        for r in rows[:20]
    ]

    top = rows[0]
    description = (
        f"{len(rows)} IP address(es) accessed an unusually large number of distinct "
        f"endpoints, which may indicate automated path scanning or reconnaissance. "
        f"Top scanner: {top['ip_address']} hit {top['distinct_paths']} unique endpoints."
    )

    return {
        "type": "path_scanning",
        "severity": "medium",
        "title": "Path Scanning Detected",
        "description": description,
        "evidence": evidence,
        "count": len(rows),
    }


def _check_sensitive_paths(base_qs) -> dict[str, Any] | None:
    """
    Flag any requests to endpoints that contain sensitive path substrings.
    Returns up to 20 matching entries.
    """
    from django.db.models import Q

    sensitive_q = Q()
    for pattern in _SENSITIVE_PATTERNS:
        sensitive_q |= Q(endpoint__icontains=pattern)

    qs = (
        base_qs
        .filter(sensitive_q)
        .values("ip_address", "endpoint", "status_code", "timestamp")
        .order_by("-timestamp")[:20]
    )

    rows = list(qs)
    if not rows:
        return None

    evidence = [
        {
            "ip": r["ip_address"],
            "endpoint": r["endpoint"],
            "status_code": r["status_code"],
            "timestamp": r["timestamp"].isoformat(),
        }
        for r in rows
    ]

    # Count total (not just the capped 20) for the count field.
    total_count = base_qs.filter(sensitive_q).count()

    description = (
        f"{total_count} request(s) targeted sensitive paths such as "
        f".env files, admin panels, WordPress login pages, or configuration endpoints. "
        f"These requests may indicate exploitation attempts or vulnerability scanning."
    )

    return {
        "type": "sensitive_path",
        "severity": "high",
        "title": "Sensitive Path Access Detected",
        "description": description,
        "evidence": evidence,
        "count": total_count,
    }


def _check_high_error_concentration(base_qs) -> dict[str, Any] | None:
    """
    Flag IPs responsible for >= 100 error responses (status >= 400).
    """
    from django.db.models import Count

    qs = (
        base_qs
        .filter(status_code__gte=400)
        .values("ip_address")
        .annotate(error_count=Count("id"))
        .filter(error_count__gte=_HIGH_ERROR_MIN_COUNT)
        .order_by("-error_count")
    )

    rows = list(qs)
    if not rows:
        return None

    evidence = [
        {
            "ip": r["ip_address"],
            "error_count": r["error_count"],
        }
        for r in rows[:20]
    ]

    top = rows[0]
    description = (
        f"{len(rows)} IP address(es) generated an unusually high number of error "
        f"responses (4xx/5xx), which may indicate automated attacks, misconfigured "
        f"clients, or probing activity. "
        f"Top offender: {top['ip_address']} with {top['error_count']} errors."
    )

    return {
        "type": "high_error_concentration",
        "severity": "medium",
        "title": "High Error Concentration",
        "description": description,
        "evidence": evidence,
        "count": len(rows),
    }
