"""
Train the security-event classifier and save it next to the code.

    python manage.py train_security_classifier

Optional inputs
---------------
--harvest-benign LOG [LOG ...]
    Read real access logs and add the URLs that were served successfully to
    several different visitors to logs/ml/data/benign_urls.txt as extra
    "normal" examples.  URLs that a first-pass model is confident are attacks
    are left out and printed for review.

--extra-data FILE.csv
    A CSV with ``text,label`` columns to mix in (for example a public labelled
    dataset).  Labels must be one of: normal, sqli, xss, path_traversal,
    command_injection, scanner_probe.

The command prints a hold-out evaluation in which whole attack patterns are
kept out of training, then trains the final model on everything and writes:

    logs/ml/security_classifier.joblib   the model
    logs/ml/security_classifier.json     training summary and metrics
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from logs.ml.security_corpus import build_corpus
from logs.ml.security_model import (
    ATTACK_CLASSES,
    BENIGN_URLS_PATH,
    CLASSES,
    MAX_REQUEST_LENGTH,
    METADATA_PATH,
    MODEL_PATH,
    NORMAL,
    build_pipeline,
    normalise_request,
    reset_model_cache,
)

HARVEST_MIN_VISITORS = 3      # served successfully to this many different clients
HARVEST_MAX_PER_LOG = 4_000
HARVEST_REJECT_CONFIDENCE = 0.9
TEST_FRACTION = 0.25


class Command(BaseCommand):
    help = "Train the security-event classifier used by Security Insights."

    def add_arguments(self, parser):
        parser.add_argument("--harvest-benign", nargs="+", metavar="LOG", default=[],
                            help="Access logs to harvest normal URLs from.")
        parser.add_argument("--extra-data", metavar="CSV", default=None,
                            help="Extra labelled examples (text,label columns).")
        parser.add_argument("--seed", type=int, default=42)

    def handle(self, *args, **options):
        try:
            import joblib
            import sklearn
            from sklearn.metrics import classification_report, confusion_matrix
            from sklearn.model_selection import GroupShuffleSplit
        except ImportError as exc:
            raise CommandError("scikit-learn is required: pip install -r requirements.txt") from exc

        seed = options["seed"]

        if options["harvest_benign"]:
            self._harvest(options["harvest_benign"], seed)

        real_normal = _read_benign_urls()
        corpus = build_corpus(seed=seed, real_normal=real_normal)
        extra = self._read_extra(options["extra_data"])
        corpus.extend(extra)

        texts = [normalise_request(text) for text, _label, _group in corpus]
        labels = [label for _text, label, _group in corpus]
        groups = [group for _text, _label, group in corpus]

        counts = Counter(labels)
        self.stdout.write(f"Training examples: {len(corpus):,}")
        for key in CLASSES:
            self.stdout.write(f"  {key:<18} {counts.get(key, 0):>6,}")

        # ---- Hold-out evaluation: whole patterns are unseen in training ----
        splitter = GroupShuffleSplit(n_splits=1, test_size=TEST_FRACTION, random_state=seed)
        train_idx, test_idx = next(splitter.split(texts, labels, groups))
        evaluation_model = build_pipeline()
        evaluation_model.fit([texts[i] for i in train_idx], [labels[i] for i in train_idx])
        expected = [labels[i] for i in test_idx]
        predicted = evaluation_model.predict([texts[i] for i in test_idx])

        class_keys = list(CLASSES)
        report = classification_report(
            expected, predicted, labels=class_keys, output_dict=True, zero_division=0
        )
        matrix = confusion_matrix(expected, predicted, labels=class_keys)
        normal_total = sum(1 for label in expected if label == NORMAL)
        false_alarms = sum(1 for e, p in zip(expected, predicted) if e == NORMAL and p != NORMAL)
        missed = sum(1 for e, p in zip(expected, predicted) if e != NORMAL and p == NORMAL)
        attack_total = len(expected) - normal_total

        self.stdout.write("\nHold-out evaluation (attack patterns in the test set were never seen in training)")
        self.stdout.write(classification_report(expected, predicted, labels=class_keys, zero_division=0))
        self.stdout.write(
            f"Normal requests wrongly flagged: {false_alarms} of {normal_total:,} "
            f"({false_alarms / max(normal_total, 1):.2%})"
        )
        self.stdout.write(
            f"Attacks missed (classified normal): {missed} of {attack_total:,} "
            f"({missed / max(attack_total, 1):.2%})\n"
        )

        # ---- Final model: train on everything ------------------------------
        model = build_pipeline()
        model.fit(texts, labels)
        MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(model, MODEL_PATH, compress=3)

        metadata = {
            "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "algorithm": "TF-IDF character n-grams (1-4) + Logistic Regression",
            "scikit_learn_version": sklearn.__version__,
            "seed": seed,
            "examples": {key: counts.get(key, 0) for key in CLASSES},
            "sources": {
                "generated": len(corpus) - len(real_normal) - len(extra),
                "harvested_benign_urls": len(real_normal),
                "extra_data": len(extra),
            },
            "evaluation": {
                "method": "Group hold-out: 25% of patterns kept out of training",
                "test_examples": len(expected),
                "accuracy": round(report["accuracy"], 4),
                "macro_f1": round(report["macro avg"]["f1-score"], 4),
                "false_alarm_rate": round(false_alarms / max(normal_total, 1), 4),
                "missed_attack_rate": round(missed / max(attack_total, 1), 4),
                "per_class": {
                    key: {
                        "precision": round(report[key]["precision"], 4),
                        "recall": round(report[key]["recall"], 4),
                        "f1": round(report[key]["f1-score"], 4),
                        "support": int(report[key]["support"]),
                    }
                    for key in class_keys
                },
                "confusion_matrix": {"labels": class_keys, "rows": matrix.tolist()},
            },
        }
        METADATA_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
        reset_model_cache()

        size_kb = MODEL_PATH.stat().st_size / 1024
        self.stdout.write(self.style.SUCCESS(f"Saved {MODEL_PATH.name} ({size_kb:,.0f} KB) and {METADATA_PATH.name}"))

    # ------------------------------------------------------------------
    # Inputs
    # ------------------------------------------------------------------

    def _read_extra(self, path: str | None) -> list[tuple[str, str, str]]:
        if not path:
            return []
        examples = []
        with open(path, newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or not {"text", "label"} <= set(reader.fieldnames):
                raise CommandError("--extra-data needs a header row with 'text' and 'label' columns.")
            for index, row in enumerate(reader):
                label = (row["label"] or "").strip().lower()
                text = (row["text"] or "").strip()
                if not text:
                    continue
                if label not in CLASSES:
                    raise CommandError(f"Unknown label {label!r} on row {index + 2}.")
                examples.append((text, label, f"extra-{label}-{index % 40}"))
        self.stdout.write(f"Loaded {len(examples):,} extra examples from {path}")
        return examples

    def _harvest(self, log_paths: list[str], seed: int) -> None:
        """Add popular, successfully served URLs from real logs to the benign list."""
        import random

        from logs.services.log_parser import LogParsingService

        # First-pass model trained without any harvested URLs, used only to
        # keep suspicious-looking requests out of the "normal" examples.
        first_pass_corpus = build_corpus(seed=seed, real_normal=None)
        first_pass = build_pipeline()
        first_pass.fit(
            [normalise_request(text) for text, _l, _g in first_pass_corpus],
            [label for _t, label, _g in first_pass_corpus],
        )
        normal_index = list(first_pass.classes_).index(NORMAL)

        known = {normalise_request(url): url for url in _read_benign_urls()}
        rng = random.Random(seed)

        for log_path in log_paths:
            path = Path(log_path)
            if not path.exists():
                raise CommandError(f"Log file not found: {log_path}")
            parser = LogParsingService()
            visitors: dict[str, set[str]] = {}
            with parser._open_log_file(path) as handle:
                for line in handle:
                    row = parser._parse_row(line)
                    if row is None or not 200 <= row[5] < 400:   # status code
                        continue
                    seen = visitors.setdefault(row[3][:MAX_REQUEST_LENGTH], set())
                    if len(seen) < HARVEST_MIN_VISITORS:
                        seen.add(row[1])                          # client address

            popular = sorted(url for url, seen in visitors.items() if len(seen) >= HARVEST_MIN_VISITORS)
            rng.shuffle(popular)
            popular = popular[:HARVEST_MAX_PER_LOG]
            if not popular:
                self.stdout.write(f"{path.name}: no URL was served to {HARVEST_MIN_VISITORS}+ visitors")
                continue

            probabilities = first_pass.predict_proba([normalise_request(url) for url in popular])
            kept = 0
            rejected: list[str] = []
            for url, row in zip(popular, probabilities):
                if 1.0 - row[normal_index] >= HARVEST_REJECT_CONFIDENCE:
                    rejected.append(url)
                    continue
                key = normalise_request(url)
                if key and key not in known:
                    known[key] = url
                    kept += 1
            self.stdout.write(
                f"{path.name}: added {kept:,} normal URLs, "
                f"left out {len(rejected):,} that look like attacks"
            )
            for url in rejected[:15]:
                self.stdout.write(f"    left out: {url[:120]}")

        BENIGN_URLS_PATH.parent.mkdir(parents=True, exist_ok=True)
        BENIGN_URLS_PATH.write_text("\n".join(sorted(known.values())) + "\n", encoding="utf-8")
        self.stdout.write(f"{BENIGN_URLS_PATH.name} now holds {len(known):,} URLs\n")


def _read_benign_urls() -> list[str]:
    try:
        lines = BENIGN_URLS_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [line for line in lines if line.strip()]
