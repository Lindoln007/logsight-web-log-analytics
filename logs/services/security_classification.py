"""
Security-event classification for a dataset.

Runs the trained request classifier (see ``logs/ml/security_model.py``) over
every distinct request target in a dataset and summarises what it finds per
attack class.  ``logs/services/security.py`` blends this summary with the
behaviour-based rules into the single Security Insights report.

Work is done per *distinct URL* rather than per row: a dataset with a million
requests usually has only a few thousand different URLs, so the model runs on
those and the request counts are carried along.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from typing import Any

from django.core.cache import cache
from django.db.models import Count, Q

from logs.ml.security_model import (
    ATTACK_CLASSES,
    CLASSES,
    MODEL_PATH,
    NORMAL,
    load_metadata,
    load_model,
    normalise_request,
)

logger = logging.getLogger(__name__)

# A request is only reported as an attack when the model is at least this sure.
CONFIDENCE_THRESHOLD = 0.70

MAX_DISTINCT_URLS = 200_000      # safety cap on how many URLs are scored
MAX_URLS_FOR_IP_LOOKUP = 2_000   # flagged URLs traced back to client addresses
EXAMPLES_PER_CLASS = 10
TOP_IPS_PER_CLASS = 10
CACHE_TTL = 15 * 60


def classify_dataset(dataset_id: int, owner) -> dict[str, Any]:
    """
    Classify every distinct request target in *dataset_id*.

    Returns ``{"available": False, "note": reason}`` when the model cannot be
    used, otherwise::

        {
            "available": True,
            "model": {...training summary...},
            "threshold": 0.7,
            "requests_analysed": int,
            "distinct_urls": int,
            "flagged_requests": int,
            "flagged_share": float,          # % of all requests
            "note": str | None,
            "classes": [
                {
                    "key": "sqli", "label": "SQL injection", "severity": "high",
                    "requests": int, "distinct_urls": int,
                    "successful_requests": int,     # answered with a 2xx/3xx
                    "unique_ips": int,
                    "top_ips": [{"ip": str, "requests": int}, ...],
                    "examples": [{"endpoint", "requests", "successful", "confidence"}, ...],
                },
                ...                                 # one entry per attack class
            ],
            "ip_classes": {ip: [class keys]},       # which classes each client sent
        }
    """
    from logs.models import Dataset, LogEntry

    model, error = load_model()
    if model is None:
        return {"available": False, "note": error}

    total_entries = (
        Dataset.objects.filter(pk=dataset_id, owner=owner)
        .values_list("total_entries", flat=True)
        .first()
    )
    if total_entries is None:
        return {"available": False, "note": "Dataset not found."}

    cache_key = f"security:classification:{dataset_id}:{total_entries}:{int(MODEL_PATH.stat().st_mtime)}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    base_qs = LogEntry.objects.filter(dataset_id=dataset_id, dataset__owner=owner)

    url_rows = list(
        base_qs.values("endpoint")
        .annotate(
            requests=Count("id"),
            successful=Count("id", filter=Q(status_code__lt=400)),
        )
        .order_by("-requests")[: MAX_DISTINCT_URLS + 1]
    )
    note = None
    if len(url_rows) > MAX_DISTINCT_URLS:
        url_rows = url_rows[:MAX_DISTINCT_URLS]
        note = (
            f"This dataset has more than {MAX_DISTINCT_URLS:,} different URLs; "
            "only the most requested ones were classified."
        )

    flagged = _score_urls(model, url_rows)

    per_class: dict[str, list[dict]] = defaultdict(list)
    for item in flagged:
        per_class[item["class"]].append(item)

    ip_counts, ip_classes = _attribute_to_clients(base_qs, flagged)

    classes = []
    for key in ATTACK_CLASSES:
        label, severity = CLASSES[key]
        items = sorted(per_class.get(key, []), key=lambda i: (-i["requests"], -i["confidence"]))
        top_ips = ip_counts.get(key, Counter()).most_common(TOP_IPS_PER_CLASS)
        classes.append({
            "key": key,
            "label": label,
            "severity": severity,
            "requests": sum(i["requests"] for i in items),
            "distinct_urls": len(items),
            "successful_requests": sum(i["successful"] for i in items),
            "unique_ips": len(ip_counts.get(key, ())),
            "top_ips": [{"ip": ip, "requests": count} for ip, count in top_ips],
            "examples": [
                {
                    "endpoint": i["endpoint"],
                    "requests": i["requests"],
                    "successful": i["successful"],
                    "confidence": i["confidence"],
                }
                for i in items[:EXAMPLES_PER_CLASS]
            ],
        })

    requests_analysed = sum(row["requests"] for row in url_rows)
    flagged_requests = sum(c["requests"] for c in classes)
    metadata = load_metadata()
    evaluation = metadata.get("evaluation", {})

    result = {
        "available": True,
        "model": {
            "algorithm": metadata.get("algorithm", "TF-IDF character n-grams + Logistic Regression"),
            "trained_at": metadata.get("trained_at"),
            "training_examples": sum(metadata.get("examples", {}).values()) or None,
            "holdout_accuracy": evaluation.get("accuracy"),
            "holdout_macro_f1": evaluation.get("macro_f1"),
            "false_alarm_rate": evaluation.get("false_alarm_rate"),
        },
        "threshold": CONFIDENCE_THRESHOLD,
        "requests_analysed": requests_analysed,
        "distinct_urls": len(url_rows),
        "flagged_requests": flagged_requests,
        "flagged_share": round(flagged_requests / requests_analysed * 100, 3) if requests_analysed else 0.0,
        "note": note,
        "classes": classes,
        "ip_classes": {ip: sorted(keys) for ip, keys in ip_classes.items()},
    }
    cache.set(cache_key, result, CACHE_TTL)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _score_urls(model, url_rows: list[dict]) -> list[dict]:
    """Return the URLs the model assigns to an attack class with enough confidence."""
    if not url_rows:
        return []

    # Many URLs collapse to the same text once numbers are masked, so each
    # distinct normalised form is scored only once.
    normalised = [normalise_request(row["endpoint"]) for row in url_rows]
    unique = sorted(set(normalised))
    probabilities = model.predict_proba(unique)
    class_names = list(model.classes_)
    best = probabilities.argmax(axis=1)
    verdict = {
        text: (class_names[best[i]], float(probabilities[i, best[i]]))
        for i, text in enumerate(unique)
    }

    flagged = []
    for row, text in zip(url_rows, normalised):
        label, confidence = verdict[text]
        if label != NORMAL and confidence >= CONFIDENCE_THRESHOLD:
            flagged.append({
                "endpoint": row["endpoint"],
                "class": label,
                "confidence": round(confidence, 3),
                "requests": row["requests"],
                "successful": row["successful"],
            })
    return flagged


def _attribute_to_clients(base_qs, flagged: list[dict]):
    """
    Trace flagged URLs back to the clients that requested them.

    Returns ``(requests per class per ip, classes per ip)``.  Only the most
    requested flagged URLs are traced, which keeps the query bounded.
    """
    ip_counts: dict[str, Counter] = defaultdict(Counter)
    ip_classes: dict[str, set] = defaultdict(set)
    if not flagged:
        return ip_counts, ip_classes

    traced = sorted(flagged, key=lambda i: -i["requests"])[:MAX_URLS_FOR_IP_LOOKUP]
    class_of = {item["endpoint"]: item["class"] for item in traced}
    endpoints = list(class_of)

    for start in range(0, len(endpoints), 200):
        chunk = endpoints[start : start + 200]
        rows = (
            base_qs.filter(endpoint__in=chunk)
            .values("ip_address", "endpoint")
            .annotate(requests=Count("id"))
        )
        for row in rows:
            key = class_of.get(row["endpoint"])
            if key is None:
                continue
            ip_counts[key][row["ip_address"]] += row["requests"]
            ip_classes[row["ip_address"]].add(key)
    return ip_counts, ip_classes
