# Hybrid Training Planner Data Layer

This folder adds coaching intelligence on top of `dist/exercises.json`.

## Files

- `exercise_overlay.json`: approved catalogue exercises with planner metadata.
- `custom_exercises.json`: specialist exercises absent from the raw catalogue.
- `overlay_schema.json`: validation schema for the overlay.
- `build_overlay.py`: converts the curated exercise CSV into overlay/custom JSON.
- `match_report.csv`: generated audit file showing exact, fuzzy, and custom matches.

## Build

1. Export the curated exercise workbook as CSV.
2. Run:

```bash
python planner/build_overlay.py   --catalog dist/exercises.json   --curated curated_exercises.csv   --output-dir planner
```

The planner should select only records with `enabled: true`. Raw instructions, images,
equipment, and muscles come from `dist/exercises.json`; planner-specific fatigue,
recovery, tier, rotation, and coaching fields come from the overlay.
