"""
Task policy classifier — shadow AI branch for the ByteCurve Payroll Adjustment Automation.

Runs in parallel with the existing keyword-based determine_task_policy() without
replacing it.  Every classification decision is logged to SQLite so that:

  1. Agreement/disagreement rates are visible over time.
  2. The training data grows with real production task codes and names.
  3. The model can eventually replace keyword rules once confidence is established.

On first use the model is bootstrapped from the existing keyword rules and saved
to task_classifier_model.pkl.  Subsequent starts load the saved model instantly.
Retraining uses agreed production entries as additional ground-truth samples.

Public API:
    shadow_compare(task_code, task_name, keyword_policy)  — call after every policy lookup
    retrain_from_log(min_samples)                         — call at end of each run
    predict(task_code, task_name)                         — returns (label, confidence)
"""

import logging
import os
import sqlite3
from datetime import datetime
from typing import Optional, Tuple

MODEL_FILE  = "task_classifier_model.pkl"
LOG_DB_FILE = "task_classifier_log.db"

# Label set mirrors TaskPolicy enum names plus SKIP (for None returns)
LABELS = [
    "SKIP",
    "EXTRA_WORK",
    "S2S_CHARTER",
    "SPARE_CDL",
    "SPARE_MONITOR",
    "HTS_UNITS",
    "HTS_HOURS",
    "REGULAR",
]

# Module-level cached model — loaded once per process
_model = None


# ---------------------------------------------------------------------------
# Bootstrap training data
# ---------------------------------------------------------------------------

def _bootstrap_training_data() -> list:
    """
    Generates labeled (task_code, task_name, label) examples from the
    existing keyword rules, with deliberate variations so the classifier
    can generalise beyond exact keyword matches.
    """
    data: list = []

    def add(pairs: list, label: str) -> None:
        for code, name in pairs:
            data.append((code, name, label))

    add([
        ("Bridge Charter", ""),
        ("Bridge Charter", "Bridge Charter Run"),
        ("Bridge Charter", "AM Route"),
        ("Bridge Charter", "PM Route"),
        ("BridgeCharter", ""),
        ("BridgeCharter", "Charter"),
        ("BC",             "Bridge Charter"),
        ("",               "Bridge Charter Route"),
        ("Bridge Charter", "Route"),
    ], "SKIP")

    add([
        ("Extra Work", ""),
        ("Extra Work", "Extra Work"),
        ("Extra Work", "AM Extra"),
        ("Extra Work", "PM Extra"),
        ("Extra Work", "Driver Extra"),
        ("EW",         "Extra Work"),
        ("EW",         "Extra Work AM"),
        ("Extra Work", "Run"),
    ], "EXTRA_WORK")

    add([
        ("S2S Charter", ""),
        ("S2S Charter", "S2S Charter"),
        ("S2S Charter", "AM Charter"),
        ("S2S Charter", "Charter Run"),
        ("S2S",         "S2S Charter"),
        ("S2S",         "Charter"),
        ("S2S Charter", "Run"),
    ], "S2S_CHARTER")

    add([
        ("Spare CDL", ""),
        ("Spare CDL", "Spare CDL"),
        ("Spare CDL", "CDL Driver"),
        ("Spare CDL", "AM Spare CDL"),
        ("SPR CDL",   "Spare CDL"),
        ("",          "Spare CDL Route"),
        ("Spare CDL", "CDL Spare"),
        ("Spare CDL", "Driver"),
    ], "SPARE_CDL")

    add([
        ("Spare Monitor", ""),
        ("Spare Monitor", "Spare Monitor"),
        ("Spare Monitor", "Monitor"),
        ("Spare Monitor", "AM Spare Monitor"),
        ("SPR MON",       "Spare Monitor"),
        ("",              "Spare Monitor Route"),
        ("Spare Monitor", "Monitor Spare"),
        ("Spare Monitor", "Driver"),
    ], "SPARE_MONITOR")

    add([
        ("HTS",       "Units"),
        ("HTS",       "HTS Units"),
        ("HTS Units", ""),
        ("HTS",       "AM Units"),
        ("HTS Units", "Route"),
        ("HTS",       "Route Units"),
        ("HTS",       "Unit"),
        ("HTS Units", "AM"),
    ], "HTS_UNITS")

    add([
        ("HTS",       "Hrs"),
        ("HTS",       "Hours"),
        ("HTS",       "HTS Hours"),
        ("HTS Hrs",   ""),
        ("HTS Hours", ""),
        ("HTS",       "Route Hrs"),
        ("HTS",       "AM Hrs"),
        ("HTS Hrs",   "Route"),
    ], "HTS_HOURS")

    add([
        ("HTS",   "Route"),
        ("HTS",   "HTS Route"),
        ("HTS",   "Driver Route"),
        ("AM",    "Morning Route"),
        ("AM",    "AM Route"),
        ("PM",    "Afternoon Route"),
        ("PM",    "PM Route"),
        ("Route", ""),
        ("Route", "Regular"),
        ("REG",   "Regular Route"),
        ("RT",    "Route"),
        ("DRV",   "Driver"),
        ("",      "Route"),
        ("HTS",   "AM Route"),
        ("HTS",   "PM Route"),
    ], "REGULAR")

    return data


# ---------------------------------------------------------------------------
# Feature engineering
# ---------------------------------------------------------------------------

def _build_feature_text(task_code: str, task_name: str) -> str:
    """Combines code and name into a single normalised feature string."""
    return f"{task_code.strip()} {task_name.strip()}".strip().lower()


# ---------------------------------------------------------------------------
# Model training and persistence
# ---------------------------------------------------------------------------

def _train_and_save(data: list) -> Optional[object]:
    """
    Trains a TF-IDF + Logistic Regression pipeline on *data* and saves it.
    Returns the fitted pipeline, or None if scikit-learn is not installed.
    """
    try:
        from sklearn.pipeline import Pipeline
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        import joblib
    except ImportError:
        logging.warning(
            "CLASSIFIER: scikit-learn not installed — run: pip install scikit-learn"
        )
        return None

    texts  = [_build_feature_text(c, n) for c, n, _ in data]
    labels = [lbl for _, _, lbl in data]

    pipeline = Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer    = "word",
            ngram_range = (1, 2),
            min_df      = 1,
        )),
        ("clf", LogisticRegression(max_iter=1000, C=1.0, solver="lbfgs")),
    ])
    pipeline.fit(texts, labels)

    try:
        joblib.dump(pipeline, MODEL_FILE)
        logging.info(
            f"CLASSIFIER: Model trained on {len(texts)} examples "
            f"and saved to '{MODEL_FILE}'."
        )
    except Exception as e:
        logging.warning(f"CLASSIFIER: Could not save model to disk: {e}")

    return pipeline


def _load_or_train() -> Optional[object]:
    """
    Loads the pipeline from disk if available; otherwise bootstraps and trains.
    Returns None if scikit-learn is not installed.
    """
    try:
        import joblib
    except ImportError:
        logging.warning("CLASSIFIER: joblib not available — pip install scikit-learn")
        return None

    if os.path.exists(MODEL_FILE):
        try:
            model = joblib.load(MODEL_FILE)
            logging.info(f"CLASSIFIER: Model loaded from '{MODEL_FILE}'.")
            return model
        except Exception as e:
            logging.warning(
                f"CLASSIFIER: Could not load saved model ({e}) — retraining from bootstrap."
            )

    return _train_and_save(_bootstrap_training_data())


def _get_model() -> Optional[object]:
    """Returns the module-level cached model, loading it on first call."""
    global _model
    if _model is None:
        _model = _load_or_train()
    return _model


# ---------------------------------------------------------------------------
# Prediction
# ---------------------------------------------------------------------------

def predict(task_code: str, task_name: str) -> Tuple[str, float]:
    """
    Returns (predicted_label, confidence) for a task code/name pair.
    Falls back to ("UNKNOWN", 0.0) if the model is unavailable.
    """
    model = _get_model()
    if model is None:
        return "UNKNOWN", 0.0

    try:
        text       = _build_feature_text(task_code, task_name)
        label      = model.predict([text])[0]
        confidence = float(model.predict_proba([text])[0].max())
        return label, confidence
    except Exception as e:
        logging.warning(
            f"CLASSIFIER: Prediction failed for "
            f"code='{task_code}' name='{task_name}': {e}"
        )
        return "UNKNOWN", 0.0


# ---------------------------------------------------------------------------
# SQLite logging
# ---------------------------------------------------------------------------

def _init_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classifications (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp      TEXT    NOT NULL,
            task_code      TEXT    NOT NULL,
            task_name      TEXT    NOT NULL,
            keyword_policy TEXT    NOT NULL,
            ml_policy      TEXT    NOT NULL,
            ml_confidence  REAL    NOT NULL,
            agreed         INTEGER NOT NULL
        )
    """)
    conn.commit()


def _policy_to_label(policy) -> str:
    """Converts a TaskPolicy enum (or None) to a plain string label."""
    if policy is None:
        return "SKIP"
    return policy.name if hasattr(policy, "name") else str(policy)


def shadow_compare(task_code: str, task_name: str, keyword_policy) -> None:
    """
    Runs the ML classifier alongside the keyword-based policy and logs the result.

    This is the only integration point needed in the main automation file.
    It is intentionally silent — all exceptions are caught so the automation
    flow is never interrupted.

    Args:
        task_code:      The task code string from the grid row.
        task_name:      The task name string from the grid row.
        keyword_policy: The TaskPolicy (or None) returned by determine_task_policy().
    """
    try:
        ml_label, confidence = predict(task_code, task_name)
        kw_label = _policy_to_label(keyword_policy)
        agreed   = 1 if ml_label == kw_label else 0

        if not agreed:
            logging.info(
                f"CLASSIFIER: Disagreement — "
                f"code='{task_code}' name='{task_name}' | "
                f"keyword={kw_label}  ML={ml_label} ({confidence:.0%} confidence)"
            )

        with sqlite3.connect(LOG_DB_FILE) as conn:
            _init_db(conn)
            conn.execute(
                "INSERT INTO classifications "
                "(timestamp, task_code, task_name, keyword_policy, "
                " ml_policy, ml_confidence, agreed) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now().isoformat(),
                    task_code, task_name,
                    kw_label, ml_label, confidence, agreed,
                ),
            )
            conn.commit()

    except Exception as e:
        logging.warning(f"CLASSIFIER: shadow_compare failed silently: {e}")


# ---------------------------------------------------------------------------
# Retraining from production data
# ---------------------------------------------------------------------------

def retrain_from_log(min_samples: int = 30) -> bool:
    """
    Retrains the model by combining bootstrap data with agreed production entries.

    Production entries where keyword_policy == ml_policy are used as additional
    ground-truth samples.  Only triggers when at least *min_samples* such entries
    exist so the model does not overfit on a handful of observations.

    Args:
        min_samples: Minimum agreed entries required before retraining.

    Returns:
        True if the model was retrained, False otherwise.
    """
    if not os.path.exists(LOG_DB_FILE):
        return False

    try:
        with sqlite3.connect(LOG_DB_FILE) as conn:
            rows = conn.execute(
                "SELECT task_code, task_name, keyword_policy "
                "FROM classifications WHERE agreed = 1"
            ).fetchall()

        if len(rows) < min_samples:
            logging.info(
                f"CLASSIFIER: {len(rows)} agreed samples in log "
                f"(need {min_samples} to retrain) — using bootstrap model."
            )
            return False

        bootstrap  = _bootstrap_training_data()
        production = [(r[0], r[1], r[2]) for r in rows]
        combined   = bootstrap + production

        global _model
        _model = _train_and_save(combined)

        logging.info(
            f"CLASSIFIER: Model retrained on {len(combined)} examples "
            f"({len(production)} from production log + {len(bootstrap)} bootstrap)."
        )
        return True

    except Exception as e:
        logging.warning(f"CLASSIFIER: retrain_from_log failed: {e}")
        return False
