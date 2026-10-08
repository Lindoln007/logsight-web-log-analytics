"""
Security insights service for LogSight.

One report built from two complementary kinds of analysis:

Behaviour rules — count what each client did across many requests:
  - Brute force login attempts (high error rate to one endpoint from one IP)
  - Path scanning (one IP hitting many distinct endpoints, mostly failing)
  - Sensitive path access (.env, admin, wp-login, etc.)
  - High error concentration (IP responsible for >= 100 errors)

Machine-learning classification — read the content of each request:
  - A trained classifier labels every request target as normal, SQL injection,
    cross-site scripting, path traversal, command injection or scanner probe
    (see ``security_classification.py``).

Neither can do the other's job: a brute-force attack uses perfectly ordinary
URLs, and a single injection attempt leaves no behavioural trace.  Clients
caught by more than one kind of finding are reported as repeat offenders.
"""
from __future__ import annotations

import logging
from collections import defaultdict
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
# A scanner guesses paths, so most of what it asks for does not exist.  A real
# visitor or search-engine crawler also touches many paths, but they succeed.
_PATH_SCAN_MIN_ERROR_SHARE = 0.5
_HIGH_ERROR_MIN_COUNT = 100

# Weights for risk score computation (0-100 scale, capped at 100).
_WEIGHTS = {
    # Behaviour rules
    "brute_force": 20,
    "path_scanning": 10,
    "sensitive_path": 10,
    "high_error_concentration": 10,
    # Machine-learning classes
    "sqli": 20,
    "xss": 10,
    "path_traversal": 15,
    "command_injection": 20,
    "scanner_probe": 10,
    # Clients caught by more than one kind of finding
    "multi_vector": 10,
}

# An ML class counts in full once it has this many requests behind it; fewer
# requests count for half, so a single borderline request cannot swing the score.
_ML_FULL_WEIGHT_REQUESTS = 5


def get_security_insights(dataset_id: int, owner) -> dict[str, Any]:
    """
    Run the behaviour rules and the request classifier against a dataset and
    return one structured findings dict with a 0-100 risk score::

        {
            "findings": [{"type", "source": "rule" | "ai", "severity", "title",
                          "description", "evidence", "count"}, ...],
            "risk_score": int,
            "summary": str,
            "classification": {...} ,   # ML summary for the page's breakdown
        }

    All model imports are local to avoid circular dependency issues.
    """
    from logs.models import LogEntry

    base_qs = LogEntry.objects.filter(dataset_id=dataset_id, dataset__owner=owner)

    findings: list[dict[str, Any]] = []
    risk_components: list[float] = []

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

    for finding in findings:
        finding["source"] = "rule"

    # ------------------------------------------------------------------
    # 5. Machine-learning request classification
    # ------------------------------------------------------------------
    classification = _run_classification(dataset_id, owner)
    ip_classes: dict[str, list[str]] = classification.pop("ip_classes", {})
    for ml_class in classification.get("classes", []):
        if not ml_class["requests"]:
            continue
        findings.append(_classification_finding(ml_class))
        weight = _WEIGHTS.get(ml_class["key"], 10)
        full = ml_class["requests"] >= _ML_FULL_WEIGHT_REQUESTS
        risk_components.append(weight if full else weight / 2)

    # ------------------------------------------------------------------
    # 6. Clients caught by more than one kind of finding
    # ------------------------------------------------------------------
    mv_finding = _check_multi_vector(findings, ip_classes)
    if mv_finding:
        findings.append(mv_finding)
        risk_components.append(_WEIGHTS["multi_vector"])

    risk_score = min(100, round(sum(risk_components)))

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
        "classification": classification,
    }


# ---------------------------------------------------------------------------
# Machine-learning findings
# ---------------------------------------------------------------------------

def _run_classification(dataset_id: int, owner) -> dict[str, Any]:
    """Classify the dataset's requests; never let a model problem break the report."""
    try:
        from logs.services.security_classification import classify_dataset
        return dict(classify_dataset(dataset_id, owner))
    except Exception:
        logger.exception("Security classification failed for dataset_id=%s", dataset_id)
        return {
            "available": False,
            "note": "The request classifier could not be run on this dataset.",
        }


def _classification_finding(ml_class: dict[str, Any]) -> dict[str, Any]:
    """Turn one attack class from the classifier into a finding card."""
    requests = ml_class["requests"]
    urls = ml_class["distinct_urls"]
    ips = ml_class["unique_ips"]
    successful = ml_class["successful_requests"]
    label = ml_class["label"]

    description = (
        f"The classifier recognised {requests:,} request(s) across {urls:,} different "
        f"URL(s) as {label.lower()}"
        + (f", sent by {ips:,} client(s)." if ips else ".")
    )
    if successful:
        description += (
            f" {successful:,} of them were answered successfully (2xx/3xx) rather than "
            "rejected, so they are worth checking first."
        )
    top_ips = ml_class.get("top_ips") or []
    if top_ips:
        description += f" Most active source: {top_ips[0]['ip']} ({top_ips[0]['requests']:,} requests)."

    return {
        "type": ml_class["key"],
        "source": "ai",
        "severity": ml_class["severity"],
        "title": f"{label} detected",
        "description": description,
        "evidence": [
            {
                "endpoint": example["endpoint"],
                "request_count": example["requests"],
                "confidence": example["confidence"],
            }
            for example in ml_class["examples"]
        ],
        "count": requests,
        "top_ips": top_ips,
    }


def _check_multi_vector(
    findings: list[dict[str, Any]],
    ip_classes: dict[str, list[str]],
) -> dict[str, Any] | None:
    """
    Flag clients that appear in two or more different kinds of finding — for
    example an address that both scanned paths and sent injection attempts.
    """
    reasons: dict[str, set[str]] = defaultdict(set)

    for finding in findings:
        if finding.get("source") != "rule":
            continue
        for item in finding.get("evidence", []):
            if item.get("ip"):
                reasons[item["ip"]].add(finding["title"])

    titles = {f["type"]: f["title"] for f in findings if f.get("source") == "ai"}
    for ip, keys in ip_classes.items():
        for key in keys:
            if key in titles:
                reasons[ip].add(titles[key])

    offenders = sorted(
        ((ip, sorted(why)) for ip, why in reasons.items() if len(why) >= 2),
        key=lambda pair: (-len(pair[1]), pair[0]),
    )
    if not offenders:
        return None

    top_ip, top_reasons = offenders[0]
    return {
        "type": "multi_vector",
        "source": "combined",
        "severity": "high",
        "title": "Repeat Offenders",
        "description": (
            f"{len(offenders)} client(s) appear in more than one finding, which makes "
            f"deliberate attack activity far more likely than coincidence. "
            f"Most implicated: {top_ip} ({len(top_reasons)} findings)."
        ),
        "evidence": [
            {"ip": ip, "reasons": why} for ip, why in offenders[:20]
        ],
        "count": len(offenders),
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
    Flag IPs that requested >= 15 distinct endpoints *and* got an error for at
    least half of their requests (path enumeration / scanning).

    The error share separates scanners, which guess at paths that do not
    exist, from ordinary heavy visitors and search-engine crawlers.
    """
    from django.db.models import Count, Q

    qs = (
        base_qs
        .values("ip_address")
        .annotate(
            distinct_paths=Count("endpoint", distinct=True),
            total=Count("id"),
            errors=Count("id", filter=Q(status_code__gte=400)),
        )
        .filter(distinct_paths__gte=_PATH_SCAN_MIN_PATHS)
        .order_by("-distinct_paths")
    )

    rows = [
        r for r in qs
        if r["total"] and r["errors"] / r["total"] >= _PATH_SCAN_MIN_ERROR_SHARE
    ]
    if not rows:
        return None

    evidence = [
        {
            "ip": r["ip_address"],
            "distinct_paths": r["distinct_paths"],
            "error_count": r["errors"],
        }
        for r in rows[:20]
    ]

    top = rows[0]
    description = (
        f"{len(rows)} IP address(es) requested an unusually large number of distinct "
        f"endpoints and mostly received errors, which indicates automated path "
        f"scanning or reconnaissance. "
        f"Top scanner: {top['ip_address']} tried {top['distinct_paths']} unique endpoints."
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
