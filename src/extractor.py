"""
Engineering Drawing OCR Extractor — FREE VERSION
Trane Technologies / Thermo King Project

Uses ONLY free, open-source tools:
  - PyMuPDF (fitz)  → PDF to image conversion + native text extraction
  - Tesseract OCR   → reads text from scanned/image-based drawings
  - Pillow          → image pre-processing for better OCR accuracy
  - regex           → structured field parsing

NO paid API. NO internet required after install.

Install:
    pip install pymupdf pillow pytesseract
    # Also install Tesseract binary:
    # Windows : https://github.com/UB-Mannheim/tesseract/wiki
    # Ubuntu  : sudo apt install tesseract-ocr
    # Mac     : brew install tesseract
"""

import re
import json
import time
from pathlib import Path
from datetime import datetime

try:
    import fitz
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False
    print("WARNING: PyMuPDF not found. Run: pip install pymupdf")

try:
    from PIL import Image, ImageFilter, ImageEnhance
    HAS_PIL = True
except ImportError:
    HAS_PIL = False
    print("WARNING: Pillow not found. Run: pip install pillow")

try:
    import pytesseract
    HAS_TESSERACT = True
except ImportError:
    HAS_TESSERACT = False
    print("WARNING: pytesseract not found. Run: pip install pytesseract")


# ─────────────────────────────────────────────
# OPTIONAL: uncomment and set path if tesseract
# is not on your system PATH (Windows users)
# ─────────────────────────────────────────────
# pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


# ─────────────────────────────────────────────
# PDF → Images
# ─────────────────────────────────────────────

def pdf_to_images(pdf_path: str, dpi: int = 300) -> list:
    if not HAS_PYMUPDF:
        raise ImportError("Run: pip install pymupdf")
    pdf_path = Path(pdf_path)
    out_dir = pdf_path.parent / f"{pdf_path.stem}_pages"
    out_dir.mkdir(exist_ok=True)
    doc = fitz.open(str(pdf_path))
    paths = []
    for i, page in enumerate(doc):
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = page.get_pixmap(matrix=mat, colorspace=fitz.csGRAY)
        img_path = out_dir / f"page_{i+1:03d}.png"
        pix.save(str(img_path))
        paths.append(str(img_path))
        print(f"  Converted page {i+1}/{len(doc)}")
    doc.close()
    return paths


def extract_native_text(pdf_path: str) -> list:
    """Extract native text layer from PDF (fast, no OCR)."""
    if not HAS_PYMUPDF:
        return []
    doc = fitz.open(pdf_path)
    pages = [page.get_text("text").strip() for page in doc]
    doc.close()
    return pages


# ─────────────────────────────────────────────
# Image pre-processing + Tesseract OCR
# ─────────────────────────────────────────────

def preprocess_image(img_path: str):
    img = Image.open(img_path).convert("L")
    img = ImageEnhance.Contrast(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)
    return img


def ocr_image(img_path: str) -> str:
    if not HAS_TESSERACT:
        raise ImportError("Run: pip install pytesseract  AND install Tesseract binary")
    img = preprocess_image(img_path)
    return pytesseract.image_to_string(img, config="--psm 6 --oem 3")


# ─────────────────────────────────────────────
# Dynamic layout scanning
# ─────────────────────────────────────────────
#
# Engineering drawings follow ANSI/ISO conventions:
#   • Title block (metadata)  — bottom-right quadrant
#   • Revision block          — upper-right or right strip
#   • Parts list (BOM)        — left or centre area
#   • Notes                   — lower portion
#
# The dynamic scan works in three steps:
#   1. Detect the pixel position of each section header using Tesseract
#      word-level bounding boxes (image_to_data).
#   2. If every detected header sits inside its expected standard zone →
#      return {standard: True} so the caller uses the normal full-page OCR.
#   3. Otherwise crop each section's region from the image and run a
#      targeted OCR pass, which yields higher accuracy for non-standard layouts.
# ─────────────────────────────────────────────

# Standard zone positions as (x1, y1, x2, y2) fractions of image dimensions.
# Based on ANSI/ISO engineering drawing conventions.
_STANDARD_ZONES = {
    "metadata":         (0.45, 0.65, 1.00, 1.00),  # title block: bottom-right
    "revision_history": (0.55, 0.00, 1.00, 0.55),  # revision block: upper-right
    "component_list":   (0.00, 0.02, 0.55, 0.95),  # parts list: left/centre
    "other_data":       (0.00, 0.55, 0.70, 1.00),  # notes: lower area
}

# Regex patterns used to detect each section header in OCR word rows.
_SECTION_KEYWORDS = {
    "metadata":
        r"(?:DRAWING|DWG)\s*NO|TITLE[\s:]+|DRAWN\s*BY|CHECKED\s*BY",
    "revision_history":
        r"REV(?:ISION)?\s*(?:HISTORY|DESCRIPTION|CHANGE|TABLE)|REVISION\s+RECORD",
    "component_list":
        r"LIST\s+OF\s+MATERIAL|BILL\s+OF\s+MATERIAL|COMPONENT\s+LIST|PARTS\s+LIST",
    "other_data":
        r"(?:GENERAL\s+)?NOTES?\s*[:\.]|NOTES?\s*$",
}

# Fractional tolerance used when deciding whether an anchor is "in its zone".
_STANDARD_TOL = 0.20


def _get_word_data(img) -> list:
    """Run Tesseract image_to_data on a Pillow image.

    Returns a list of word dicts with keys: text, left, top, width, height.
    Only words with Tesseract confidence >= 30 are included.
    """
    if not HAS_TESSERACT:
        return []
    raw = pytesseract.image_to_data(
        img, config="--psm 6 --oem 3",
        output_type=pytesseract.Output.DICT,
    )
    result = []
    for i in range(len(raw["text"])):
        word = raw["text"][i].strip()
        try:
            conf = int(raw["conf"][i])
        except (ValueError, TypeError):
            conf = 0
        if word and conf >= 30:
            result.append({
                "text":   word.upper(),
                "left":   raw["left"][i],
                "top":    raw["top"][i],
                "width":  raw["width"][i],
                "height": raw["height"][i],
            })
    return result


def _find_section_anchors(words: list) -> dict:
    """Scan word bounding-box data for section-header keywords.

    Words are grouped into approximate rows (20-pixel buckets) before regex
    matching so that adjacent words on the same line form a single string.

    Returns {section_name: (left_px, top_bucket_px)} for each detected section.
    """
    BUCKET = 20
    rows: dict = {}
    for w in words:
        bucket = (w["top"] // BUCKET) * BUCKET
        rows.setdefault(bucket, []).append(w)

    anchors = {}
    for section, pattern in _SECTION_KEYWORDS.items():
        for bucket in sorted(rows):
            row_text = " ".join(w["text"] for w in rows[bucket])
            if re.search(pattern, row_text):
                leftmost = min(w["left"] for w in rows[bucket])
                anchors[section] = (leftmost, bucket)
                break
    return anchors


def _is_standard_layout(anchors: dict, img_w: int, img_h: int) -> bool:
    """Return True when every detected anchor falls inside its expected zone.

    A tolerance of ``_STANDARD_TOL`` (fraction of image dimension) is applied
    on each side of the zone boundary.  An empty anchors dict (no section
    headers found) is treated as standard so the caller falls back to the
    existing full-page OCR path.
    """
    for section, (ax, ay) in anchors.items():
        if section not in _STANDARD_ZONES:
            continue
        zx1, zy1, zx2, zy2 = _STANDARD_ZONES[section]
        tol_x = _STANDARD_TOL * img_w
        tol_y = _STANDARD_TOL * img_h
        in_zone = (
            zx1 * img_w - tol_x <= ax <= zx2 * img_w + tol_x
            and zy1 * img_h - tol_y <= ay <= zy2 * img_h + tol_y
        )
        if not in_zone:
            return False
    return True


def _crop_section_region(img, anchor_x: int, anchor_y: int,
                          img_w: int, img_h: int, section: str):
    """Return a Pillow image crop for *section* starting at the detected anchor.

    The horizontal bounds come from ``_STANDARD_ZONES`` (sections typically
    stay on the same side of the page even in non-standard layouts).  The
    vertical upper bound is the detected anchor row; the lower bound follows
    the standard zone bottom.
    """
    zx1, zy1, zx2, zy2 = _STANDARD_ZONES.get(section, (0.0, 0.0, 1.0, 1.0))
    # Horizontal: widest of standard zone left and detected anchor position
    x1 = max(0, min(anchor_x - 30, int(zx1 * img_w) - 10))
    x2 = min(img_w, int(zx2 * img_w) + 10)
    # Vertical: from just above the header row to the standard zone bottom
    y1 = max(0, anchor_y - 10)
    y2 = min(img_h, int(zy2 * img_h) + 20)
    return img.crop((x1, y1, x2, y2))


def _ocr_region(img_region) -> str:
    """OCR a Pillow image region with contrast enhancement."""
    if not HAS_TESSERACT:
        return ""
    region = ImageEnhance.Contrast(img_region.convert("L")).enhance(2.0)
    region = region.filter(ImageFilter.SHARPEN)
    return pytesseract.image_to_string(region, config="--psm 6 --oem 3")


def dynamic_scan(img_path: str) -> dict:
    """Detect the layout of an engineering drawing image.

    Returns a dict with:

    * ``standard`` (bool) — True when all detected section anchors sit at their
      expected positions.  The caller should then use the normal full-page OCR.
    * ``anchors`` (dict) — ``{section: [x_px, y_px]}`` for every detected header.
    * ``<section>_text`` (str) — per-section OCR text (present only when the
      layout is non-standard and the section header was found).

    Returns an **empty dict** when PIL or Tesseract is not available, or when the
    image cannot be opened — in that case the caller falls back to full-page OCR.
    """
    if not (HAS_PIL and HAS_TESSERACT):
        return {}
    try:
        img = Image.open(img_path).convert("L")
    except Exception as e:
        print(f"    [dynamic scan] could not open image: {e}")
        return {}

    img_w, img_h = img.size
    print(f"    [dynamic scan] image size: {img_w}×{img_h} px")

    enhanced = ImageEnhance.Contrast(img).enhance(2.0).filter(ImageFilter.SHARPEN)

    # Step 1 — locate section headers via word bounding boxes
    words = _get_word_data(enhanced)
    anchors = _find_section_anchors(words)
    print(f"    [dynamic scan] sections detected: {list(anchors.keys()) or 'none'}")

    # Step 2 — check whether anchors sit at their standard positions
    if _is_standard_layout(anchors, img_w, img_h):
        print("    [dynamic scan] standard layout — using full-page OCR")
        return {"standard": True, "anchors": {k: list(v) for k, v in anchors.items()}}

    # Step 3 — non-standard: crop each detected section and OCR individually
    print("    [dynamic scan] non-standard layout — running per-section OCR")
    section_texts = {}
    for section, (ax, ay) in anchors.items():
        crop = _crop_section_region(enhanced, ax, ay, img_w, img_h, section)
        text = _ocr_region(crop)
        section_texts[section] = text
        print(f"    [dynamic scan] '{section}': {len(text)} chars")

    result = {"standard": False, "anchors": {k: list(v) for k, v in anchors.items()}}
    for section in _STANDARD_ZONES:
        result[f"{section}_text"] = section_texts.get(section, "")
    return result


# ─────────────────────────────────────────────
# Regex-based structured parser
# ─────────────────────────────────────────────

def parse_drawing_metadata(text: str) -> dict:
    meta = {
        "title": "", "drawing_id": "", "current_revision": "",
        "drawn_by": "", "checked_by": "", "approved_by": "",
        "date": "", "scale": "", "sheet": "", "finish": "",
        "material": "", "company": "", "other_fields": {}
    }
    t = text.upper()

    patterns = {
        "drawing_id":       r"(?:DWG|DRAWING|DRG)[.\s#:-]*NO[.\s#:-]*([A-Z0-9\-]+)",
        "current_revision": r"(?:REV(?:ISION)?|CURR(?:ENT)?\s+REV)[.\s:]*([A-Z0-9]+)",
        "drawn_by":         r"(?:DRAWN|DRN|DWN)\s*(?:BY)?[.\s:]*([A-Z][A-Z.\s]+?)(?:\n|DATE|CHK)",
        "checked_by":       r"(?:CHECKED|CHK|CHKD)\s*(?:BY)?[.\s:]*([A-Z][A-Z.\s]+?)(?:\n|DATE|APPR)",
        "approved_by":      r"(?:APPROVED|APPR)\s*(?:BY)?[.\s:]*([A-Z][A-Z.\s]+?)(?:\n|DATE|$)",
        "date":             r"DATE[.\s:]*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})",
        "scale":            r"SCALE[.\s:]*([0-9:./]+(?:\s*:\s*[0-9]+)?)",
        "sheet":            r"SHEET[.\s:]*(\d+\s*(?:OF|/)\s*\d+)",
        "finish":           r"FINISH[.\s:]*([A-Z0-9\s\-]+?)(?:\n|$)",
        "material":         r"MATERIAL[.\s:]*([A-Z0-9\s\-]+?)(?:\n|$)",
    }
    for field, pattern in patterns.items():
        m = re.search(pattern, t)
        if m:
            meta[field] = m.group(1).strip()

    # Title heuristic: first uppercase line of reasonable length
    skip_kw = {"DATE", "DRAWN", "SCALE", "SHEET", "REV", "PAGE", "NOTE"}
    for line in text.splitlines():
        line = line.strip()
        if (len(line) > 8 and line.isupper()
                and not any(kw in line for kw in skip_kw)):
            meta["title"] = line
            break

    for company in ["THERMO KING", "TRANE TECHNOLOGIES", "TRANE"]:
        if company in t:
            meta["company"] = company
            break

    return meta


def parse_revision_history(text: str) -> list:
    revisions = []
    lines = text.upper().splitlines()
    in_table = False
    for line in lines:
        if re.search(r"REV(ISION)?\s*(DESCRIPTION|HISTORY|CHANGE)", line):
            in_table = True
            continue
        if not in_table:
            continue
        if re.search(r"(COMPONENT|LIST OF MATERIAL|BILL OF MATERIAL|TITLE BLOCK)", line):
            break
        m = re.match(
            r"^\s*([A-Z0-9]{1,3})\s{2,}(.{5,40}?)\s{2,}(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\s{2,}([A-Z.\s]{2,20})",
            line
        )
        if m:
            revisions.append({
                "rev": m.group(1).strip(), "description": m.group(2).strip(),
                "date": m.group(3).strip(), "approved_by": m.group(4).strip(),
            })
        elif in_table and re.match(r"^\s*[A-Z]\s+\S", line):
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 2:
                revisions.append({
                    "rev": parts[0], "description": parts[1] if len(parts) > 1 else "",
                    "date": parts[2] if len(parts) > 2 else "",
                    "approved_by": parts[3] if len(parts) > 3 else "",
                })
    return revisions


def parse_component_list(text: str) -> list:
    components = []
    lines = text.upper().splitlines()
    in_table = False
    for line in lines:
        if re.search(r"(LIST OF MATERIAL|BILL OF MATERIAL|COMPONENT LIST|PARTS LIST)", line):
            in_table = True
            continue
        if not in_table:
            continue
        if re.search(r"(REVISION|DRAWING METADATA|TITLE BLOCK|NOTES)", line):
            break
        m = re.match(
            r"^\s*(\d{1,3})\s{2,}([A-Z0-9\-]+)\s{2,}(.{5,50}?)\s{2,}(\d+(?:\.\d+)?)\s*(.*)?$",
            line
        )
        if m:
            components.append({
                "item_no": m.group(1).strip(), "part_number": m.group(2).strip(),
                "description": m.group(3).strip(), "quantity": m.group(4).strip(),
                "material": "", "notes": m.group(5).strip() if m.group(5) else "",
            })
        elif in_table:
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) >= 3 and parts[0].isdigit():
                components.append({
                    "item_no": parts[0], "part_number": parts[1] if len(parts) > 1 else "",
                    "description": parts[2] if len(parts) > 2 else "",
                    "quantity": parts[3] if len(parts) > 3 else "",
                    "material": parts[4] if len(parts) > 4 else "", "notes": "",
                })
    return components


def parse_other_data(text: str) -> dict:
    notes, tolerances = [], ""
    t = text.upper()
    m = re.search(r"(\d+\.?\d*\s*\+\d+\.?\d*/\-\d+\.?\d*)", t)
    if m:
        tolerances = m.group(1).strip()
    for line in text.splitlines():
        s = line.strip()
        if re.match(r"^(NOTE\s*\d*[:\.\-]?|^\d+\.?\s+[A-Z])", s.upper()):
            notes.append(s)
    raw_blocks = [l.strip() for l in text.splitlines() if len(l.strip()) > 4]
    return {
        "notes": notes, "tables": [], "tolerances": tolerances,
        "raw_text_blocks": raw_blocks[:50],
    }


def extract_from_text(raw_text: str) -> dict:
    return {
        "drawing_metadata":    parse_drawing_metadata(raw_text),
        "revision_history":    parse_revision_history(raw_text),
        "component_list":      parse_component_list(raw_text),
        "other_data":          parse_other_data(raw_text),
        "extraction_confidence": {
            "overall": "medium", "metadata": "medium",
            "revision_history": "medium", "component_list": "medium",
        },
        "raw_text": raw_text,
    }


def extract_from_image(img_path: str, use_dynamic_scan: bool = True) -> dict:
    """Extract structured data from a single image file.

    When *use_dynamic_scan* is True (default) the function first checks whether
    the drawing sections (metadata, revision history, component list, notes) sit
    at their standard positions on the image.  If they do, regular full-page OCR
    is used (existing behaviour, unchanged).  If they are at non-standard
    positions the image is cropped per-section and each crop is OCR-ed
    independently, which yields significantly higher accuracy.
    """
    print(f"    Tesseract OCR → {Path(img_path).name}")

    if use_dynamic_scan:
        scan = dynamic_scan(img_path)
        # Non-standard layout detected: use per-section OCR texts
        if scan and not scan.get("standard", True):
            # For any section whose OCR text is empty (header not found),
            # fall back to the full-page text so we never lose data.
            missing = [s for s in _STANDARD_ZONES
                       if not scan.get(f"{s}_text", "").strip()]
            raw_full = ocr_image(img_path) if missing else ""

            def _sec(key):
                t = scan.get(f"{key}_text", "").strip()
                return t if t else raw_full

            result = {
                "drawing_metadata":  parse_drawing_metadata(_sec("metadata")),
                "revision_history":  parse_revision_history(_sec("revision_history")),
                "component_list":    parse_component_list(_sec("component_list")),
                "other_data":        parse_other_data(_sec("other_data")),
                "extraction_confidence": {
                    "overall": "high", "metadata": "high",
                    "revision_history": "high", "component_list": "high",
                },
                "raw_text": raw_full or "\n".join(
                    scan.get(f"{s}_text", "") for s in _STANDARD_ZONES),
                "source_image": img_path,
                "layout_scan": scan,
            }
            return result

    # Standard layout or dynamic scan disabled: use full-page OCR
    raw_text = ocr_image(img_path)
    result = extract_from_text(raw_text)
    result["source_image"] = img_path
    return result


# ─────────────────────────────────────────────
# Merge pages
# ─────────────────────────────────────────────

def merge_pages(pages: list) -> dict:
    merged = {
        "drawing_metadata": {},
        "revision_history": [],
        "component_list": [],
        "other_data": {"notes": [], "tables": [], "tolerances": "", "raw_text_blocks": []},
        "extraction_confidence": {},
        "parse_errors": []
    }
    for i, page in enumerate(pages):
        if page.get("parse_error"):
            merged["parse_errors"].append({"page": i+1, "raw": page.get("raw_response","")})
            continue
        meta = page.get("drawing_metadata", {})
        for k, v in meta.items():
            if v and k not in merged["drawing_metadata"]:
                merged["drawing_metadata"][k] = v
            elif k == "other_fields" and isinstance(v, dict):
                merged["drawing_metadata"].setdefault("other_fields", {}).update(v)
        seen_rev = {json.dumps(r, sort_keys=True) for r in merged["revision_history"]}
        for row in page.get("revision_history", []):
            key = json.dumps(row, sort_keys=True)
            if key not in seen_rev:
                merged["revision_history"].append(row)
                seen_rev.add(key)
        seen_comp = {json.dumps(r, sort_keys=True) for r in merged["component_list"]}
        for row in page.get("component_list", []):
            key = json.dumps(row, sort_keys=True)
            if key not in seen_comp:
                merged["component_list"].append(row)
                seen_comp.add(key)
        od = page.get("other_data", {})
        merged["other_data"]["notes"].extend(od.get("notes", []))
        merged["other_data"]["tables"].extend(od.get("tables", []))
        if od.get("tolerances") and not merged["other_data"]["tolerances"]:
            merged["other_data"]["tolerances"] = od["tolerances"]
        merged["other_data"]["raw_text_blocks"].extend(od.get("raw_text_blocks", []))
        merged["extraction_confidence"] = page.get("extraction_confidence", {})
    return merged


# ─────────────────────────────────────────────
# Accuracy Metrics
# ─────────────────────────────────────────────

def compute_accuracy_metrics(result: dict) -> dict:
    meta = result.get("drawing_metadata", {})
    required = ["title", "drawing_id", "current_revision", "drawn_by", "checked_by", "date"]
    filled = sum(1 for k in required if meta.get(k))
    meta_c = round(filled / len(required), 2)

    def row_c(rows, keys):
        if not rows: return 0.0
        return round(sum(sum(1 for k in keys if r.get(k)) / len(keys) for r in rows) / len(rows), 2)

    rev_rows  = result.get("revision_history", [])
    comp_rows = result.get("component_list", [])
    rev_c     = row_c(rev_rows,  ["rev", "description", "date"])
    comp_c    = row_c(comp_rows, ["item_no", "part_number", "description"])

    return {
        "metadata_completeness": meta_c,
        "revision_history_row_completeness": rev_c,
        "component_list_row_completeness": comp_c,
        "revision_history_rows_extracted": len(rev_rows),
        "component_list_rows_extracted": len(comp_rows),
        "parse_errors": len(result.get("parse_errors", [])),
        "overall_score": round((meta_c + rev_c + comp_c) / 3, 2),
    }


# ─────────────────────────────────────────────
# Save outputs
# ─────────────────────────────────────────────

def save_outputs(result: dict, metrics: dict, source_name: str, out_dir: str):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = Path(source_name).stem

    clean = {k: v for k, v in result.items() if k != "raw_text"}
    with open(out_dir / f"{stem}_{ts}_extracted.json", "w") as f:
        json.dump(clean, f, indent=2)
    with open(out_dir / f"{stem}_{ts}_metrics.json", "w") as f:
        json.dump(metrics, f, indent=2)
    with open(out_dir / f"{stem}_{ts}_raw_text.txt", "w", encoding="utf-8") as f:
        f.write(result.get("raw_text", ""))

    print(f"  → {out_dir}/{stem}_{ts}_extracted.json")
    print(f"  → {out_dir}/{stem}_{ts}_metrics.json")
    print(f"  → {out_dir}/{stem}_{ts}_raw_text.txt")


# ─────────────────────────────────────────────
# Main pipeline
# ─────────────────────────────────────────────

def process_file(file_path: str, out_dir: str = "output",
                 use_dynamic_scan: bool = True):
    file_path = Path(file_path)
    print(f"\n{'='*60}\n Processing: {file_path.name}\n{'='*60}")
    start = time.time()

    page_results = []
    suffix = file_path.suffix.lower()

    if suffix == ".pdf":
        print("\n[1/4] Trying native PDF text (PyMuPDF)...")
        native = extract_native_text(str(file_path))
        has_text = any(len(p) > 100 for p in native)
        if has_text:
            print(f"  Native text found.")
            for i, text in enumerate(native):
                page_results.append(extract_from_text(text))
        else:
            print("  No text layer — using Tesseract OCR...")
            imgs = pdf_to_images(str(file_path))
            for img in imgs:
                page_results.append(extract_from_image(img, use_dynamic_scan))
    elif suffix in (".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"):
        page_results.append(extract_from_image(str(file_path), use_dynamic_scan))
    else:
        raise ValueError(f"Unsupported: {suffix}")

    print("\n[2/4] Merging pages...")
    merged = merge_pages(page_results)
    merged.update({
        "source_file": str(file_path),
        "processed_at": datetime.now().isoformat(),
        "pages_processed": len(page_results),
        "processing_time_seconds": round(time.time() - start, 2),
        "raw_text": "\n\n--- PAGE BREAK ---\n\n".join(
            p.get("raw_text", "") for p in page_results),
    })

    print("\n[3/4] Computing accuracy metrics...")
    metrics = compute_accuracy_metrics(merged)
    metrics["processing_time_seconds"] = merged["processing_time_seconds"]

    print("\n[4/4] Saving outputs...")
    save_outputs(merged, metrics, file_path.name, out_dir)

    print(f"\n{'─'*60}")
    print(f"  Metadata    : {metrics['metadata_completeness']*100:.0f}% complete")
    print(f"  Rev rows    : {metrics['revision_history_rows_extracted']}")
    print(f"  Comp rows   : {metrics['component_list_rows_extracted']}")
    print(f"  Score       : {metrics['overall_score']*100:.0f}%")
    print(f"  Time        : {metrics['processing_time_seconds']}s")
    print(f"{'─'*60}\n")
    return merged, metrics


def process_folder(folder_path: str, out_dir: str = "output",
                   use_dynamic_scan: bool = True):
    folder = Path(folder_path)
    exts = {".pdf", ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
    files = [f for f in folder.iterdir() if f.suffix.lower() in exts]
    if not files:
        print(f"No files found in {folder_path}")
        return []
    results = []
    for f in files:
        try:
            r, m = process_file(str(f), out_dir, use_dynamic_scan)
            results.append({"file": str(f), "result": r, "metrics": m})
        except Exception as e:
            print(f"  ERROR {f.name}: {e}")
            results.append({"file": str(f), "error": str(e)})
    ok = [r for r in results if "metrics" in r]
    if ok:
        avg = sum(r["metrics"]["overall_score"] for r in ok) / len(ok)
        print(f"\nBATCH: {len(ok)}/{len(files)} OK | Avg score: {avg*100:.1f}%\n")
    return results


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Engineering Drawing OCR (FREE)")
    parser.add_argument("input", help="PDF/image file or folder")
    parser.add_argument("--out", default="output")
    parser.add_argument("--no-dynamic-scan", action="store_true",
                        help="Disable dynamic layout scan (use full-page OCR only)")
    args = parser.parse_args()
    p = Path(args.input)
    use_dynamic_scan = not args.no_dynamic_scan
    if p.is_dir():
        process_folder(str(p), args.out, use_dynamic_scan)
    else:
        process_file(str(p), args.out, use_dynamic_scan)
