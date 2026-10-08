"""
Background warm-up for the machine-learning libraries.

scikit-learn is imported lazily by the services that use it so that pages
which never touch a model do not pay for it.  The first import in a fresh
server process takes a few seconds (longer on a cold disk), which would
otherwise land on whoever first clicks "Run Forecast" or "Detect Anomalies".
``warm_up()`` moves that one-off cost to a background thread.
"""
from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_started = False
_lock = threading.Lock()


def warm_up() -> None:
    """Start importing the ML stack in the background (at most once per process)."""
    global _started
    if _started:
        return
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_import_ml_stack, name="logsight-ml-warmup", daemon=True).start()


def _import_ml_stack() -> None:
    try:
        import sklearn.ensemble  # noqa: F401
    except Exception:  # missing or broken install: the services fall back gracefully
        logger.warning("scikit-learn could not be imported; ML features will fall back.")
