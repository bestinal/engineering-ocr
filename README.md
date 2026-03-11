# Engineering Drawing OCR Extractor
**Trane Technologies / Thermo King** — Automated extraction of structured data from engineering drawings.

---

## Project Overview

This tool uses **Claude Vision AI** (OCR + LLM) to extract structured information from engineering drawing PDFs/images:

| Extracted Section | Fields |
|---|---|
| **Drawing Metadata** | Title, Drawing ID, Revision, Drawn/Checked/Approved by, Date, Scale, Sheet, Finish, Material, Company |
| **Revision History** | Rev letter, Description, Date, Approved by |
| **Component List** | Item No, Part Number, Description, Quantity, Material, Notes |
| **Other Data** | Notes, Tables, Tolerances, Raw text blocks |

All data is stored in a **SQLite RDBMS** (swappable to PostgreSQL/MySQL).

---

## Project Structure

```
engineering_ocr/
├── src/
│   ├── extractor.py     # PDF→image conversion + Claude Vision extraction
│   ├── database.py      # SQLite RDBMS storage + query helpers
│   └── pipeline.py      # End-to-end pipeline + CLI dashboard
├── tests/
│   └── test_extractor.py  # Unit tests (pytest)
├── output/              # JSON results + SQLite DB (auto-created)
├── sample_input/        # Put your PDF/image files here
└── requirements.txt
```

---

## Setup

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Set your Anthropic API key
```bash
# Windows
set ANTHROPIC_API_KEY=sk-ant-...

# Mac/Linux
export ANTHROPIC_API_KEY=sk-ant-...
```

Get your API key from: https://console.anthropic.com/

---

## Usage

### Process a single PDF
```bash
cd src
python pipeline.py extract ../sample_input/drawing.pdf
```

### Process a whole folder of PDFs
```bash
python pipeline.py extract ../sample_input/
```

### View database dashboard
```bash
python pipeline.py dashboard
```

### Run unit tests
```bash
cd ..
python -m pytest tests/ -v
```

---

## Output Files

For each processed drawing, two JSON files are created in `output/`:

**`<filename>_extracted.json`** — Full extracted data:
```json
{
  "drawing_metadata": { "title": "...", "drawing_id": "...", ... },
  "revision_history": [ { "rev": "A", "description": "...", ... } ],
  "component_list":   [ { "item_no": "1", "part_number": "...", ... } ],
  "other_data":       { "notes": [...], "tables": [...] },
  "processing_time_seconds": 8.3
}
```

**`<filename>_metrics.json`** — Accuracy metrics:
```json
{
  "metadata_completeness": 0.83,
  "revision_history_row_completeness": 1.0,
  "component_list_row_completeness": 0.75,
  "revision_history_rows_extracted": 4,
  "component_list_rows_extracted": 6,
  "parse_errors": 0,
  "overall_score": 0.86
}
```

---

## Accuracy Metrics Explained

| Metric | Description |
|---|---|
| `metadata_completeness` | % of required metadata fields that were extracted |
| `revision_history_row_completeness` | Average % of fields filled per revision row |
| `component_list_row_completeness` | Average % of fields filled per component row |
| `overall_score` | Average of the three above |
| `parse_errors` | Number of pages where JSON parsing failed |

---

## Database Queries

```python
from database import get_all_drawings, search_by_part_number, find_duplicate_parts

# All drawings
drawings = get_all_drawings()

# Find drawings with a specific part
results = search_by_part_number("19220240")

# Detect duplicate/similar parts across drawings
dups = find_duplicate_parts()
```

---

## Model & Approach

- **Pre-processing**: PyMuPDF converts PDF pages to 200 DPI PNG images
- **OCR Model**: Claude claude-opus-4-5 Vision (multimodal) — reads the drawing image and returns structured JSON
- **Post-processing**: Pages merged, deduplicated, stored in SQLite
- **Processing time**: ~5–15 seconds per page depending on drawing complexity
- **Scalability**: Batch folder processing supported; can parallelize with `concurrent.futures`
