"""
Alert management service.

Handles CRUD operations for AlertRule records and the logic that checks
whether a dataset currently violates any active rules, creating Alert
instances when it does.
"""
from __future__ import annotations

from django.utils import timezone


# ---------------------------------------------------------------------------
# Rule serialisation
# ---------------------------------------------------------------------------

def _serialize_rule(rule) -> dict:
    return {
        "id":                        rule.pk,
        "name":                      rule.name,
        "error_rate_threshold":      float(rule.error_rate_threshold)      if rule.error_rate_threshold      is not None else None,
        "request_spike_multiplier":  float(rule.request_spike_multiplier)  if rule.request_spike_multiplier  is not None else None,
        "is_active":                 rule.is_active,
        "created_at":                rule.created_at.isoformat(),
        "alert_count":               rule.alerts.count(),
    }


def _serialize_alert(alert) -> dict:
    return {
        "id":           alert.pk,
        "rule_id":      alert.rule_id,
        "rule_name":    alert.rule.name,
        "triggered_at": alert.triggered_at.isoformat(),
        "alert_type":   alert.alert_type,
        "value":        float(alert.value),
        "threshold":    float(alert.threshold),
        "severity":     alert.severity,
        "message":      alert.message,
    }


# ---------------------------------------------------------------------------
# CRUD helpers
# ---------------------------------------------------------------------------

def get_alert_rules(dataset_id: int, owner) -> list[dict]:
    """Return all alert rules for *dataset_id* owned by *owner*."""
    from logs.models import AlertRule
    rules = AlertRule.objects.filter(dataset_id=dataset_id, owner=owner)
    return [_serialize_rule(r) for r in rules]


def create_alert_rule(dataset_id: int, owner, data: dict) -> dict:
    """Create and return a new alert rule."""
    from logs.models import AlertRule, Dataset
    dataset = Dataset.objects.get(pk=dataset_id, owner=owner)
    rule = AlertRule.objects.create(
        dataset=dataset,
        owner=owner,
        name=data.get("name", "Alert Rule"),
        error_rate_threshold=data.get("error_rate_threshold") or None,
        request_spike_multiplier=data.get("request_spike_multiplier") or None,
        is_active=data.get("is_active", True),
    )
    return _serialize_rule(rule)


def update_alert_rule(rule_id: int, owner, data: dict) -> dict:
    """Update an existing alert rule and return the serialised result."""
    from logs.models import AlertRule
    rule = AlertRule.objects.get(pk=rule_id, owner=owner)
    if "name" in data:
        rule.name = data["name"]
    if "error_rate_threshold" in data:
        rule.error_rate_threshold = data["error_rate_threshold"] or None
    if "request_spike_multiplier" in data:
        rule.request_spike_multiplier = data["request_spike_multiplier"] or None
    if "is_active" in data:
        rule.is_active = bool(data["is_active"])
    rule.save()
    return _serialize_rule(rule)


def delete_alert_rule(rule_id: int, owner) -> None:
    """Delete an alert rule (and cascade its Alert records)."""
    from logs.models import AlertRule
    AlertRule.objects.filter(pk=rule_id, owner=owner).delete()


def get_alert_history(dataset_id: int, owner, limit: int = 50) -> list[dict]:
    """Return the most-recent *limit* alerts for *dataset_id*."""
    from logs.models import Alert
    alerts = (
        Alert.objects
        .filter(rule__dataset_id=dataset_id, rule__owner=owner)
        .select_related("rule")
        .order_by("-triggered_at")[:limit]
    )
    return [_serialize_alert(a) for a in alerts]


# ---------------------------------------------------------------------------
# Alert checking
# ---------------------------------------------------------------------------

def check_and_trigger_alerts(dataset_id: int, owner) -> list[dict]:
    """
    Evaluate all active AlertRule records for *dataset_id* against the current
    analytics and create Alert records for any violated thresholds.

    Returns the list of newly-created alerts (serialised).
    """
    from logs.models import Alert, AlertRule
    from logs.services.analytics import get_summary_metrics, get_request_trends

    rules = AlertRule.objects.filter(
        dataset_id=dataset_id, owner=owner, is_active=True
    )
    if not rules.exists():
        return []

    metrics        = get_summary_metrics(dataset_id, owner=owner)
    error_rate     = float(metrics.get("error_rate", 0) or 0)
    triggered: list[dict] = []

    for rule in rules:

        # ── Error-rate threshold ─────────────────────────────────────────────
        if rule.error_rate_threshold is not None:
            threshold = float(rule.error_rate_threshold)
            if error_rate > threshold:
                alert = Alert.objects.create(
                    rule=rule,
                    triggered_at=timezone.now(),
                    alert_type=Alert.ALERT_TYPE_ERROR_RATE,
                    value=error_rate,
                    threshold=threshold,
                    severity=_severity(error_rate, threshold),
                    message=(
                        f"Error rate {error_rate:.2f}% exceeded the configured "
                        f"threshold of {threshold:.2f}%."
                    ),
                )
                triggered.append(_serialize_alert(alert))

        # ── Request-spike multiplier ─────────────────────────────────────────
        if rule.request_spike_multiplier is not None:
            multiplier = float(rule.request_spike_multiplier)
            trends     = get_request_trends(dataset_id, owner=owner, interval="hour", limit=168)
            counts     = [item["count"] for item in trends.get("data", [])]
            if counts:
                mean   = sum(counts) / len(counts)
                recent = counts[-1]
                spike_threshold = mean * multiplier
                if mean > 0 and recent > spike_threshold:
                    alert = Alert.objects.create(
                        rule=rule,
                        triggered_at=timezone.now(),
                        alert_type=Alert.ALERT_TYPE_REQUEST_SPIKE,
                        value=recent,
                        threshold=round(spike_threshold, 2),
                        severity=_severity(recent, spike_threshold),
                        message=(
                            f"Most-recent hourly request count {recent:,} exceeded "
                            f"{multiplier}× the mean ({mean:.0f}), "
                            f"threshold = {spike_threshold:,.0f}."
                        ),
                    )
                    triggered.append(_serialize_alert(alert))

    return triggered


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _severity(value: float, threshold: float) -> str:
    """Map the ratio of value to threshold to a severity label."""
    from logs.models import Alert
    if threshold == 0:
        return Alert.SEVERITY_MEDIUM
    ratio = value / threshold
    if ratio >= 2.0:
        return Alert.SEVERITY_HIGH
    if ratio >= 1.5:
        return Alert.SEVERITY_MEDIUM
    return Alert.SEVERITY_LOW
