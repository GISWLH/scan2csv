# Pipeline

```text
upload
  → ingest (PNG + thumb)
  → agent_perceive (vision JSON: page_schema + enhance_prompt)
  → enhance (APIYI images/edits)   [optional skip]
  → ocr (PaddleOCR if installed, else empty boxes)
  → agent_structure (vision/text JSON → table + draft CSV)
  → agent_review (patch / risk_cells)
  → export (final_wide.csv, final_long.csv, manifest.json)
```

Job directory:

```text
jobs/{job_id}/
  source/
  page-001/
    source.png
    perceive.json
    page_schema.json
    enhance_prompt.txt
    enhanced.png
    ocr_raw.json
    table.json
    draft_wide.csv
    final_wide.csv
    final_long.csv
    agent_review.json
    manifest.json
  job.json
```
