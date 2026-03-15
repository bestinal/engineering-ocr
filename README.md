# Engineering Drawing OCR Extractor
**Trane Technologies / Thermo King** — Automated extraction of structured data from engineering drawings.

---

## Project Overview

This tool uses **free, open-source OCR** (PyMuPDF + Tesseract) to extract structured information from engineering drawing PDFs/images:

| Extracted Section | Fields |
|---|---|
| **Drawing Metadata** | Title, Drawing ID, Revision, Drawn/Checked/Approved by, Date, Scale, Sheet, Finish, Material, Company |
| **Revision History** | Rev letter, Description, Date, Approved by |
| **Component List** | Item No, Part Number, Description, Quantity, Material, Notes |
| **Other Data** | Notes, Tables, Tolerances, Raw text blocks |

All data is stored in **SQLite** by default (swappable to MySQL via environment variable).

---

## Project Structure

```
engineering_ocr/
├── src/
│   ├── extractor.py     # PDF→image conversion + Tesseract OCR + regex parsing
│   ├── database.py      # SQLite/MySQL storage + query helpers
│   └── pipeline.py      # End-to-end pipeline + CLI dashboard
├── dashboard/
│   ├── app.py           # Flask web dashboard
│   └── templates/       # Jinja2 HTML templates
├── tests/
│   └── test_extractor.py  # Unit tests (pytest)
├── output/              # JSON results + SQLite DB (auto-created)
├── sample_input/        # Put your PDF/image files here
└── requirements.txt
```

---

## Setup

### 1. Install Python dependencies
```bash
pip install -r requirements.txt
```

### 2. Install Tesseract OCR binary
```bash
# Ubuntu / Debian
sudo apt install tesseract-ocr

# macOS (Homebrew)
brew install tesseract

# Windows — download installer from:
# https://github.com/UB-Mannheim/tesseract/wiki
```

### 3. Configure database (optional)

By default the tool uses **SQLite** (`output/engineering_drawings.db`).  
To switch to MySQL, set environment variables before running:

```bash
export DB_TYPE=mysql
export MYSQL_HOST=localhost
export MYSQL_USER=root
export MYSQL_PASSWORD=your_password
export MYSQL_DATABASE=engineering_ocr
```

To set a custom SQLite file path:
```bash
export SQLITE_PATH=/path/to/my.db
```

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

- **Pre-processing**: PyMuPDF converts PDF pages to 300 DPI grayscale PNG images; Pillow applies contrast enhancement and sharpening
- **OCR Engine**: Tesseract OCR (PSM 6, OEM 3) — reads the preprocessed image and outputs raw text
- **Parsing**: Regex patterns extract structured fields from the raw OCR text (metadata, revisions, components)
- **Fallback**: If the PDF has a native text layer, that is used directly without running Tesseract
- **Post-processing**: Pages merged, duplicate rows deduplicated, results stored in SQLite/MySQL
- **Processing time**: ~1–10 seconds per page depending on drawing complexity
- **Scalability**: Batch folder processing supported

---

## Dynamic Layout Scan

Engineering drawings sometimes have sections (title block, revision history, parts list, notes) at **non-standard positions**. Standard regex parsing on a full-page OCR blob can mis-identify or miss those sections entirely.

The **dynamic layout scan** (enabled by default) adds an automatic detection pass:

| Step | What happens |
|---|---|
| **1. Detect anchors** | Tesseract `image_to_data` is called to get word-level bounding boxes. Section headers (`REVISION HISTORY`, `LIST OF MATERIAL`, `DRAWING NO`, etc.) are located on the image. |
| **2. Check standard layout** | Each detected header is tested against its expected standard zone (ANSI/ISO conventions). If **all** headers are within their expected zones (±20 % tolerance), the drawing is a standard layout. |
| **3a. Standard layout** | Skip region cropping — use the fast, existing full-page OCR path unchanged. |
| **3b. Non-standard layout** | Crop each section's region and run a separate, focused OCR pass on it. For any section whose header was not detected, the full-page text is used as a fallback so no data is lost. |

### Standard zone definitions

| Section | Expected position |
|---|---|
| Drawing metadata / title block | Bottom-right quadrant (x > 45 %, y > 65 %) |
| Revision history | Upper-right (x > 55 %, y < 55 %) |
| Component / BOM list | Left-to-centre strip (x < 55 %) |
| Notes / other data | Lower area (y > 55 %) |

### CLI usage

```bash
# Dynamic scan is ON by default
python pipeline.py extract ../sample_input/drawing.pdf

# Disable when you know the drawing is already standard (faster)
python pipeline.py extract ../sample_input/drawing.pdf --no-dynamic-scan
```

### Web dashboard

When uploading a drawing via the web dashboard, a **"Dynamic layout scan"** checkbox is shown. It is checked (enabled) by default.
