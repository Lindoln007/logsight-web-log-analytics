"""
Security-event classifier: shared definitions used by both training and serving.

The classifier reads the request target of one log line (path + query string)
and assigns it to one of the classes below.  It is a scikit-learn pipeline:

    TF-IDF over character n-grams  →  multinomial Logistic Regression

Character n-grams let the model recognise attack fragments such as ``' or 1=``,
``<scr``, ``../`` or ``;cat`` wherever they appear in a URL, including in
variants it has never seen spelled exactly that way.

Train with:  python manage.py train_security_classifier
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from urllib.parse import unquote_plus

logger = logging.getLogger(__name__)

ML_DIR = Path(__file__).resolve().parent
MODEL_PATH = ML_DIR / "security_classifier.joblib"
METADATA_PATH = ML_DIR / "security_classifier.json"
BENIGN_URLS_PATH = ML_DIR / "data" / "benign_urls.txt"

NORMAL = "normal"

# key → (label shown in the UI, severity of a finding of this class)
CLASSES: dict[str, tuple[str, str]] = {
    NORMAL:              ("Normal request",            "none"),
    "sqli":              ("SQL injection",             "high"),
    "xss":               ("Cross-site scripting (XSS)", "high"),
    "path_traversal":    ("Path traversal / file inclusion", "high"),
    "command_injection": ("Command / code injection",  "high"),
    "scanner_probe":     ("Scanner / reconnaissance probe", "medium"),
}
ATTACK_CLASSES = tuple(key for key in CLASSES if key != NORMAL)

MAX_REQUEST_LENGTH = 600

_DIGIT_RUN = re.compile(r"\d+")
_WHITESPACE_RUN = re.compile(r"\s+")


def normalise_request(target: str) -> str:
    """
    Put a request target into the canonical form the model is trained on.

    * URL-decode (twice, to undo double encoding such as ``%2527``).
    * Lower-case.
    * Replace every run of digits with ``0`` so ``id=7`` and ``id=4821`` look
      the same and the model learns structure rather than specific numbers.
    * Collapse whitespace.
    """
    text = (target or "")[:MAX_REQUEST_LENGTH]
    for _ in range(2):
        decoded = unquote_plus(text)
        if decoded == text:
            break
        text = decoded
    text = text.lower()
    text = _DIGIT_RUN.sub("0", text)
    return _WHITESPACE_RUN.sub(" ", text).strip()


def build_pipeline():
    """Return the untrained scikit-learn pipeline."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline

    return Pipeline(
        [
            (
                "tfidf",
                TfidfVectorizer(
                    analyzer="char",
                    ngram_range=(1, 4),
                    min_df=2,
                    sublinear_tf=True,
                    lowercase=False,       # normalise_request already lower-cases
                    max_features=60_000,
                ),
            ),
            (
                "classifier",
                LogisticRegression(C=10.0, max_iter=2_000, class_weight="balanced"),
            ),
        ]
    )


# ---------------------------------------------------------------------------
# Loading the trained model (once per process)
# ---------------------------------------------------------------------------

_model = None
_model_error: str | None = None
_model_lock = threading.Lock()


def load_model():
    """
    Return ``(pipeline, None)`` or ``(None, reason)`` if the model cannot be
    used.  The result is cached for the life of the process.
    """
    global _model, _model_error
    if _model is not None or _model_error is not None:
        return _model, _model_error
    with _model_lock:
        if _model is not None or _model_error is not None:
            return _model, _model_error
        if not MODEL_PATH.exists():
            _model_error = (
                "The security classifier has not been trained yet. "
                "Run: python manage.py train_security_classifier"
            )
            return None, _model_error
        try:
            import joblib
            _model = joblib.load(MODEL_PATH)
        except ImportError:
            _model_error = "scikit-learn is not installed on the server."
        except Exception:
            logger.exception("Could not load %s", MODEL_PATH)
            _model_error = (
                "The saved security classifier could not be loaded (it was "
                "probably trained with a different scikit-learn version). "
                "Re-run: python manage.py train_security_classifier"
            )
        return _model, _model_error


def reset_model_cache() -> None:
    """Forget the loaded model (used after retraining and in tests)."""
    global _model, _model_error
    with _model_lock:
        _model = None
        _model_error = None


def load_metadata() -> dict:
    """Training summary written next to the model (empty if absent)."""
    try:
        return json.loads(METADATA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
