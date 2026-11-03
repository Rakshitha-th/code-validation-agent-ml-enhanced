from difflib import SequenceMatcher
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import StratifiedGroupKFold


EXPERIMENT_DIR = Path(__file__).resolve().parent
DATASET_PATH = EXPERIMENT_DIR / "dataset" / "python_security_context.csv"
DATA_PATH = EXPERIMENT_DIR / "graphcodebert_embeddings_83.npz"
MODEL_PATH = EXPERIMENT_DIR / "vulnerability_classifier_83_grouped_cv.joblib"
NEAR_DUPLICATE_THRESHOLD = 0.82
N_SPLITS = 3


def build_near_duplicate_groups(codes: list[str]) -> np.ndarray:
    normalized = [" ".join(code.split()) for code in codes]
    parent = list(range(len(normalized)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(first: int, second: int) -> None:
        first_root = find(first)
        second_root = find(second)
        if first_root != second_root:
            parent[second_root] = first_root

    for first in range(len(normalized)):
        for second in range(first + 1, len(normalized)):
            if SequenceMatcher(
                None, normalized[first], normalized[second], autojunk=False
            ).ratio() >= NEAR_DUPLICATE_THRESHOLD:
                union(first, second)

    return np.asarray([find(index) for index in range(len(normalized))])


def print_metrics(title: str, labels: np.ndarray, predictions: np.ndarray) -> None:
    matrix = confusion_matrix(labels, predictions, labels=[0, 1])
    true_negative, false_positive, false_negative, true_positive = matrix.ravel()
    false_positive_rate = (
        false_positive / (false_positive + true_negative)
        if false_positive + true_negative
        else 0.0
    )

    print(f"\n===== {title} =====")
    print("Accuracy:", accuracy_score(labels, predictions))
    print("Vulnerable precision:", precision_score(labels, predictions, zero_division=0))
    print("Vulnerable recall:", recall_score(labels, predictions, zero_division=0))
    print("Vulnerable F1:", f1_score(labels, predictions, zero_division=0))
    print("False-positive rate:", false_positive_rate)
    print("Confusion matrix (rows=true, columns=predicted; labels=[0, 1]):")
    print(matrix)
    print(
        classification_report(
            labels,
            predictions,
            labels=[0, 1],
            target_names=["Safe", "Vulnerable"],
            zero_division=0,
        )
    )


def main() -> None:
    if MODEL_PATH.exists():
        raise FileExistsError(
            f"Refusing to overwrite the existing model: {MODEL_PATH}"
        )

    dataset = pd.read_csv(DATASET_PATH)
    with np.load(DATA_PATH, allow_pickle=False) as data:
        X = data["embeddings"]
        y = data["labels"]

    dataset_labels = dataset["label"].to_numpy(dtype=np.int64)
    if X.ndim != 2 or y.ndim != 1 or len(X) != len(y):
        raise ValueError("Embedding and label arrays have incompatible shapes.")
    if len(dataset) != len(y):
        raise ValueError(
            "Dataset and embeddings have different row counts; "
            "re-extract embeddings from the current dataset."
        )
    if not np.array_equal(dataset_labels, y):
        raise ValueError(
            "Dataset labels do not match the embedding labels row by row; "
            "re-extract embeddings from the current dataset."
        )
    if set(np.unique(y)) != {0, 1}:
        raise ValueError("Expected binary labels 0 (Safe) and 1 (Vulnerable).")
    if not np.isfinite(X).all():
        raise ValueError("Embeddings contain NaN or infinite values.")

    codes = dataset["code"].fillna("").astype(str).tolist()
    groups = build_near_duplicate_groups(codes)
    group_counts = {
        label: len(np.unique(groups[y == label]))
        for label in (0, 1)
    }
    if min(group_counts.values()) < N_SPLITS:
        raise ValueError(
            f"Each class needs at least {N_SPLITS} distinct groups for "
            f"stratified grouped evaluation; found {group_counts}."
        )

    print("Dataset shape:", X.shape)
    print("Label distribution:", np.bincount(y, minlength=2))
    print("Near-duplicate grouping threshold:", NEAR_DUPLICATE_THRESHOLD)
    print("Unique near-duplicate groups:", len(np.unique(groups)))

    splitter = StratifiedGroupKFold(
        n_splits=N_SPLITS,
        shuffle=True,
        random_state=42,
    )
    model_predictions = np.empty_like(y)
    baseline_predictions = np.empty_like(y)
    seen_test_rows = np.zeros(len(y), dtype=bool)

    for fold, (train_indices, test_indices) in enumerate(
        splitter.split(X, y, groups),
        start=1,
    ):
        train_groups = set(groups[train_indices])
        test_groups = set(groups[test_indices])
        if not train_groups.isdisjoint(test_groups):
            raise RuntimeError("A near-duplicate group crossed the fold boundary.")
        if seen_test_rows[test_indices].any():
            raise RuntimeError("A row appeared in more than one test fold.")
        seen_test_rows[test_indices] = True

        classifier = LogisticRegression(
            max_iter=1000,
            class_weight="balanced",
            random_state=42,
        )
        classifier.fit(X[train_indices], y[train_indices])
        model_predictions[test_indices] = classifier.predict(X[test_indices])

        baseline = DummyClassifier(strategy="most_frequent")
        baseline.fit(X[train_indices], y[train_indices])
        baseline_predictions[test_indices] = baseline.predict(X[test_indices])

        print(
            f"Fold {fold}: train={len(train_indices)}, "
            f"test={len(test_indices)}, "
            f"train groups={len(train_groups)}, test groups={len(test_groups)}"
        )

    if not seen_test_rows.all():
        raise RuntimeError("Grouped cross-validation did not evaluate every row.")

    print_metrics("Grouped out-of-fold Logistic Regression", y, model_predictions)
    print_metrics("Grouped out-of-fold majority baseline", y, baseline_predictions)
    print(
        "\nMetrics are exploratory: the dataset is small and the similarity "
        "threshold is a heuristic, not a verified semantic duplicate detector."
    )

    final_classifier = LogisticRegression(
        max_iter=1000,
        class_weight="balanced",
        random_state=42,
    )
    final_classifier.fit(X, y)
    joblib.dump(final_classifier, MODEL_PATH)
    print("\nFull-data classifier saved to:", MODEL_PATH)


if __name__ == "__main__":
    main()
