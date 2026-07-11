#!/usr/bin/env python3
"""
Build the Hybrid Training Planner overlay from:
1) dist/exercises.json (raw 800+ exercise catalogue)
2) a CSV export of the curated exercise sheet

Usage:
  python planner/build_overlay.py \
      --catalog dist/exercises.json \
      --curated curated_exercises.csv \
      --output-dir planner

Outputs:
  planner/exercise_overlay.json
  planner/custom_exercises.json
  planner/match_report.csv
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
    "pull-up": "pullups",
    "weighted pull up": "weighted pull ups",
    "romanian deadlift": "romanian deadlift",
    "front squat": "front barbell squat",
    "ab wheel rollout": "ab roller",
}

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

def candidate_score(query: str, item: dict[str, Any]) -> float:
    name = norm(item.get("name"))
    score = SequenceMatcher(None, query, name).ratio()
    q_tokens, n_tokens = set(query.split()), set(name.split())
    if q_tokens and n_tokens:
        score += 0.20 * len(q_tokens & n_tokens) / len(q_tokens | n_tokens)
    return min(score, 1.0)

def match_exercise(name: str, catalog: list[dict[str, Any]], by_name: dict[str, list[dict[str, Any]]]):
    query = ALIASES.get(norm(name), norm(name))
    exact = by_name.get(query)
    if exact:
        return exact[0], 1.0, "exact"
    best = None
    best_score = 0.0
    for item in catalog:
        score = candidate_score(query, item)
        if score > best_score:
            best, best_score = item, score
    status = "fuzzy" if best_score >= 0.78 else "unmatched"
    return (best if status == "fuzzy" else None), best_score, status

def overlay_record(row: dict[str, str], matched: dict[str, Any]) -> dict[str, Any]:
    rec: dict[str, Any] = {"exercise_id": matched["id"], "enabled": True}
    for source_col, target_col in FIELD_MAP.items():
        value = row.get(source_col)
        if target_col.startswith("base_"):
            rec[target_col] = number_or_text(value)
        elif target_col in {
            "avoid_when", "progressions", "regressions_substitutions",
            "curated_primary_muscles", "curated_secondary_muscles",
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
            "avoid_when", "progressions", "regressions_substitutions",
            "curated_primary_muscles", "curated_secondary_muscles",
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
        json.dumps(overlay, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (args.output_dir / "custom_exercises.json").write_text(
        json.dumps(custom, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (args.output_dir / "match_report.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["curated_name", "matched_name", "exercise_id", "score", "status"]
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
