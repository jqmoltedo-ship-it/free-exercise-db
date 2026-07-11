#!/usr/bin/env python3
"""
Safer overlay builder for the Hybrid Training Planner.

Automatically accepts:
1) exact normalized matches
2) explicit aliases
3) only very high-confidence fuzzy matches with strong token overlap

Everything else becomes a custom exercise, which is safer than linking the
wrong movement from the catalogue.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

FIELD_MAP = {
    "Category": "planner_category",
    "Tier": "tier",
    "Movement Pattern": "movement_pattern",
    "Fatigue Band": "fatigue_band",
    "Base LMF": "base_lmf",
    "Base SF": "base_sf",
    "Base JS": "base_js",
    "Base GF": "base_gf",
    "Base SD": "base_sd",
    "Base RC": "base_rc",
    "Base SFR": "base_sfr",
    "Weekly Frequency": "weekly_frequency",
    "Typical Recovery": "typical_recovery",
    "Avoid / Modify When": "avoid_when",
    "Coach Notes": "coach_notes",
    "Progressions": "progressions",
    "Regressions / Substitutions": "regressions_substitutions",
    "Modality": "modality",
    "Equipment": "curated_equipment",
    "Primary Muscles": "curated_primary_muscles",
    "Secondary / Synergists": "curated_secondary_muscles",
    "Source": "source",
}

ALIASES = {
    "barbell bench press": "barbell bench press medium grip",
    "bench press": "barbell bench press medium grip",
    "barbell overhead press": "standing military press",
    "overhead press": "standing military press",
    "back squat": "barbell squat",
    "deadlift": "barbell deadlift",
    "pull up": "pullups",
    "pull ups": "pullups",
    "weighted pull up": "weighted pull ups",
    "weighted pull ups": "weighted pull ups",
    "front squat": "front barbell squat",
    "ab wheel rollout": "ab roller",
}

CONFLICT_GROUPS = [
    {"ring", "bench"},
    {"row", "upright"},
    {"push up", "dip"},
    {"pull up", "pulldown"},
    {"press", "row"},
]

def norm(text: Any) -> str:
    s = "" if text is None else str(text)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())

def split_list(value: Any) -> list[str]:
    if value is None:
        return []
    text = str(value).strip()
    if not text:
        return []
    return [x.strip() for x in re.split(r";|\|", text) if x.strip()]

def number_or_text(value: Any) -> Any:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return text

def read_catalog(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Catalog must be a JSON array.")
    return data

def read_curated(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows or "Exercise" not in rows[0]:
        raise ValueError("Curated CSV must contain an 'Exercise' column.")
    return rows

def build_name_index(catalog: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_name: dict[str, list[dict[str, Any]]] = {}
    for item in catalog:
        key = norm(item.get("name"))
        if key:
            by_name.setdefault(key, []).append(item)
    return by_name

def token_overlap(a: str, b: str) -> float:
    a_tokens = set(a.split())
    b_tokens = set(b.split())
    if not a_tokens or not b_tokens:
        return 0.0
    return len(a_tokens & b_tokens) / len(a_tokens | b_tokens)

def has_conflict(query: str, candidate: str) -> bool:
    for group in CONFLICT_GROUPS:
        present_q = [term for term in group if term in query]
        present_c = [term for term in group if term in candidate]
        if present_q and present_c and present_q != present_c:
            return True
    return False

def candidate_score(query: str, item: dict[str, Any]) -> tuple[float, float]:
    name = norm(item.get("name"))
    seq = SequenceMatcher(None, query, name).ratio()
    overlap = token_overlap(query, name)
    score = min(seq + 0.20 * overlap, 1.0)
    return score, overlap

def match_exercise(
    name: str,
    catalog: list[dict[str, Any]],
    by_name: dict[str, list[dict[str, Any]]],
):
    original = norm(name)
    alias_target = ALIASES.get(original)

    if alias_target:
        exact = by_name.get(alias_target)
        if exact:
            return exact[0], 1.0, "alias"

    exact = by_name.get(original)
    if exact:
        return exact[0], 1.0, "exact"

    best = None
    best_score = 0.0
    best_overlap = 0.0

    for item in catalog:
        candidate_name = norm(item.get("name"))
        if has_conflict(original, candidate_name):
            continue

        score, overlap = candidate_score(original, item)
        if score > best_score:
            best = item
            best_score = score
            best_overlap = overlap

    # Conservative acceptance:
    # - very high similarity
    # - meaningful token overlap
    # - same main movement vocabulary
    accepted = (
        best is not None
        and best_score >= 0.94
        and best_overlap >= 0.50
    )

    return (
        best if accepted else None,
        best_score,
        "fuzzy" if accepted else "unmatched",
    )

def overlay_record(row: dict[str, str], matched: dict[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {"exercise_id": matched["id"], "enabled": True}
    for source_col, target_col in FIELD_MAP.items():
        value = row.get(source_col)
        if target_col.startswith("base_"):
            rec[target_col] = number_or_text(value)
        elif target_col in {
            "avoid_when",
            "progressions",
            "regressions_substitutions",
            "curated_primary_muscles",
            "curated_secondary_muscles",
        }:
            rec[target_col] = split_list(value)
        else:
            rec[target_col] = value.strip() if isinstance(value, str) and value.strip() else None
    rec["anchor"] = str(row.get("Tier", "")).strip().lower() == "anchor"
    return rec

def custom_record(row: dict[str, str]) -> dict[str, Any]:
    name = row["Exercise"].strip()
    custom_id = "custom_" + re.sub(r"_+", "_", norm(name).replace(" ", "_"))
    rec: dict[str, Any] = {
        "id": custom_id,
        "name": name,
        "enabled": True,
        "source_type": "custom",
    }
    for source_col, target_col in FIELD_MAP.items():
        value = row.get(source_col)
        if target_col.startswith("base_"):
            rec[target_col] = number_or_text(value)
        elif target_col in {
            "avoid_when",
            "progressions",
            "regressions_substitutions",
            "curated_primary_muscles",
            "curated_secondary_muscles",
        }:
            rec[target_col] = split_list(value)
        else:
            rec[target_col] = value.strip() if isinstance(value, str) and value.strip() else None
    rec["anchor"] = str(row.get("Tier", "")).strip().lower() == "anchor"
    return rec

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, type=Path)
    parser.add_argument("--curated", required=True, type=Path)
    parser.add_argument("--output-dir", default=Path("planner"), type=Path)
    args = parser.parse_args()

    catalog = read_catalog(args.catalog)
    curated = read_curated(args.curated)
    by_name = build_name_index(catalog)

    overlay, custom, report = [], [], []
    seen_ids, seen_custom = set(), set()

    for row in curated:
        name = row.get("Exercise", "").strip()
        if not name:
            continue

        matched, score, status = match_exercise(name, catalog, by_name)

        if matched:
            if matched["id"] not in seen_ids:
                overlay.append(overlay_record(row, matched))
                seen_ids.add(matched["id"])

            report.append({
                "curated_name": name,
                "matched_name": matched.get("name", ""),
                "exercise_id": matched.get("id", ""),
                "score": f"{score:.3f}",
                "status": status,
            })
        else:
            rec = custom_record(row)
            if rec["id"] not in seen_custom:
                custom.append(rec)
                seen_custom.add(rec["id"])

            report.append({
                "curated_name": name,
                "matched_name": "",
                "exercise_id": rec["id"],
                "score": f"{score:.3f}",
                "status": "custom",
            })

    args.output_dir.mkdir(parents=True, exist_ok=True)

    (args.output_dir / "exercise_overlay.json").write_text(
        json.dumps(overlay, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    (args.output_dir / "custom_exercises.json").write_text(
        json.dumps(custom, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    with (args.output_dir / "match_report.csv").open(
        "w",
        newline="",
        encoding="utf-8",
    ) as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "curated_name",
                "matched_name",
                "exercise_id",
                "score",
                "status",
            ],
        )
        writer.writeheader()
        writer.writerows(report)

    print(f"Catalogue exercises: {len(catalog)}")
    print(f"Curated rows: {len(curated)}")
    print(f"Matched overlay rows: {len(overlay)}")
    print(f"Custom rows: {len(custom)}")
    print(f"Outputs written to: {args.output_dir}")

if __name__ == "__main__":
    main()
