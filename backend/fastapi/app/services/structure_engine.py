# structure_engine.py
#
# Buckets raw Tekla member objects into role groups using the shared
# role_classifier. Re-exports classify_role for backward compatibility.

from app.models.role_classifier import classify_role  # noqa: F401  (re-exported)


def build_structural_model(data) -> dict:
    result = {
        "PRIMARY_COLUMN": [],
        "PRIMARY_BEAM":   [],
        "SECONDARY":      [],
        "UNKNOWN":        [],
    }

    if isinstance(data, dict):
        elements = data.get("data") or data.get("elements") or []
    else:
        elements = data or []

    for obj in elements:
        if not isinstance(obj, dict):
            continue
        role = classify_role(obj)
        result[role].append(obj)

    return result