"""
Unit Tests for Engineering Drawing OCR Extractor
Run: python -m pytest tests/ -v
"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

import pytest
from extractor import (merge_pages, compute_accuracy_metrics,
                       _find_section_anchors, _is_standard_layout,
                       _STANDARD_ZONES, dynamic_scan)
from database import get_connection, store_result, get_drawing_detail, find_duplicate_parts


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

SAMPLE_PAGE = {
    "drawing_metadata": {
        "title": "SENSOR ASSY BATTERY TEMPERATURE",
        "drawing_id": "DN-12345",
        "current_revision": "D",
        "drawn_by": "J. Smith",
        "checked_by": "A. Brown",
        "approved_by": "M. Jones",
        "date": "2024-01-15",
        "scale": "1:1",
        "sheet": "1 OF 3",
        "finish": "TIN PLATED",
        "material": "COPPER",
        "company": "THERMO KING",
        "other_fields": {}
    },
    "revision_history": [
        {"rev": "A", "description": "INITIAL RELEASE", "date": "2023-01-01", "approved_by": "M. Jones"},
        {"rev": "B", "description": "UPDATED DIMENSIONS", "date": "2023-06-01", "approved_by": "M. Jones"},
        {"rev": "C", "description": "UPDATED SPECIFICATIONS", "date": "2023-12-01", "approved_by": "M. Jones"},
        {"rev": "D", "description": "UPDATED DIMENSIONS SHEET 1&2", "date": "2024-01-15", "approved_by": "M. Jones"},
    ],
    "component_list": [
        {"item_no": "1", "part_number": "19220240", "description": "SHELL MOLLY COPPER TIN PLATED", "quantity": "1", "material": "COPPER", "notes": ""},
        {"item_no": "2", "part_number": "UL4413", "description": "18AWG BLACK 125C 300V LEAD WIRES", "quantity": "2", "material": "", "notes": ""},
    ],
    "other_data": {
        "notes": ["PLASTIC WIRE TIE", "BLACK SHRINKTUDIE"],
        "tables": [{"type": "TEMP TABLE", "rows": [{"TEMP": "-40", "NOM_VAL_RES": "842.71"}]}],
        "tolerances": "415.0 +10/-0",
        "raw_text_blocks": []
    },
    "extraction_confidence": {
        "overall": "high",
        "metadata": "high",
        "revision_history": "high",
        "component_list": "high"
    }
}

SAMPLE_METRICS = {
    "metadata_completeness": 1.0,
    "revision_history_row_completeness": 1.0,
    "component_list_row_completeness": 1.0,
    "revision_history_rows_extracted": 4,
    "component_list_rows_extracted": 2,
    "parse_errors": 0,
    "overall_score": 1.0,
    "processing_time_seconds": 5.2
}


# ─────────────────────────────────────────────
# Tests: merge_pages
# ─────────────────────────────────────────────

class TestMergePages:
    def test_single_page(self):
        result = merge_pages([SAMPLE_PAGE])
        assert result["drawing_metadata"]["title"] == "SENSOR ASSY BATTERY TEMPERATURE"
        assert len(result["revision_history"]) == 4
        assert len(result["component_list"]) == 2

    def test_deduplication(self):
        # Same page twice — rows should not double
        result = merge_pages([SAMPLE_PAGE, SAMPLE_PAGE])
        assert len(result["revision_history"]) == 4
        assert len(result["component_list"]) == 2

    def test_metadata_merge_prefers_first(self):
        page2 = json.loads(json.dumps(SAMPLE_PAGE))
        page2["drawing_metadata"]["title"] = "DIFFERENT TITLE"
        result = merge_pages([SAMPLE_PAGE, page2])
        # First non-empty value wins
        assert result["drawing_metadata"]["title"] == "SENSOR ASSY BATTERY TEMPERATURE"

    def test_parse_error_page_is_skipped(self):
        error_page = {"parse_error": True, "raw_response": "some garbage"}
        result = merge_pages([SAMPLE_PAGE, error_page])
        assert len(result["parse_errors"]) == 1
        assert len(result["revision_history"]) == 4

    def test_empty_pages(self):
        result = merge_pages([])
        assert result["drawing_metadata"] == {}
        assert result["revision_history"] == []
        assert result["component_list"] == []


# ─────────────────────────────────────────────
# Tests: compute_accuracy_metrics
# ─────────────────────────────────────────────

class TestAccuracyMetrics:
    def test_perfect_result(self):
        result = merge_pages([SAMPLE_PAGE])
        result["parse_errors"] = []
        metrics = compute_accuracy_metrics(result)
        assert metrics["metadata_completeness"] == 1.0
        assert metrics["revision_history_rows_extracted"] == 4
        assert metrics["component_list_rows_extracted"] == 2
        assert metrics["overall_score"] == 1.0

    def test_empty_result(self):
        empty = {"drawing_metadata": {}, "revision_history": [],
                 "component_list": [], "other_data": {}, "parse_errors": []}
        metrics = compute_accuracy_metrics(empty)
        assert metrics["metadata_completeness"] == 0.0
        assert metrics["revision_history_rows_extracted"] == 0
        assert metrics["overall_score"] == 0.0

    def test_partial_metadata(self):
        partial = json.loads(json.dumps(SAMPLE_PAGE))
        partial["drawing_metadata"]["title"] = ""
        partial["drawing_metadata"]["drawing_id"] = ""
        result = merge_pages([partial])
        result["parse_errors"] = []
        metrics = compute_accuracy_metrics(result)
        assert metrics["metadata_completeness"] < 1.0
        assert metrics["metadata_completeness"] > 0.0


# ─────────────────────────────────────────────
# Tests: database
# ─────────────────────────────────────────────

@pytest.fixture
def temp_db(tmp_path):
    return str(tmp_path / "test.db")


class TestDatabase:
    def test_schema_creation(self, temp_db):
        conn = get_connection(temp_db)
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        expected = {"drawings", "revision_history", "component_list",
                    "other_data", "accuracy_metrics"}
        assert expected.issubset(tables)
        conn.close()

    def test_store_and_retrieve(self, temp_db):
        result = merge_pages([SAMPLE_PAGE])
        result["source_file"] = "test_drawing.pdf"
        drawing_id = store_result(result, SAMPLE_METRICS, temp_db)
        assert drawing_id > 0

        detail = get_drawing_detail(drawing_id, temp_db)
        assert detail["title"] == "SENSOR ASSY BATTERY TEMPERATURE"
        assert detail["drawing_id"] == "DN-12345"
        assert len(detail["revision_history"]) == 4
        assert len(detail["component_list"]) == 2

    def test_duplicate_part_detection(self, temp_db):
        # Store two drawings with the same part number
        result1 = merge_pages([SAMPLE_PAGE])
        result1["source_file"] = "drawing1.pdf"
        store_result(result1, SAMPLE_METRICS, temp_db)

        result2 = json.loads(json.dumps(result1))
        result2["source_file"] = "drawing2.pdf"
        result2["drawing_metadata"]["title"] = "ANOTHER DRAWING"
        store_result(result2, SAMPLE_METRICS, temp_db)

        dups = find_duplicate_parts(temp_db)
        part_numbers = [d["part_number"] for d in dups]
        assert "19220240" in part_numbers

    def test_metrics_stored(self, temp_db):
        result = merge_pages([SAMPLE_PAGE])
        result["source_file"] = "test.pdf"
        drawing_id = store_result(result, SAMPLE_METRICS, temp_db)
        detail = get_drawing_detail(drawing_id, temp_db)
        assert detail["metrics"]["overall_score"] == 1.0
        assert detail["metrics"]["revision_rows_extracted"] == 4


# ─────────────────────────────────────────────
# Tests: dynamic layout scan
# ─────────────────────────────────────────────

class TestDynamicScan:
    """Tests for the pure-Python layout detection helpers.

    These tests do NOT require PIL, Tesseract, or a real image file because
    they exercise only the functions that operate on already-parsed word lists
    and numeric coordinates.
    """

    # ── _find_section_anchors ─────────────────

    def test_find_section_anchors_empty_words(self):
        """Empty word list → no anchors detected."""
        assert _find_section_anchors([]) == {}

    def test_find_section_anchors_revision(self):
        """'REVISION HISTORY' header words are detected correctly."""
        words = [
            {"text": "REVISION", "left": 600, "top": 50, "width": 80, "height": 12},
            {"text": "HISTORY",  "left": 690, "top": 50, "width": 70, "height": 12},
        ]
        anchors = _find_section_anchors(words)
        assert "revision_history" in anchors
        ax, ay = anchors["revision_history"]
        assert ax == 600          # leftmost word in the row
        assert ay == 40           # bucket: (50 // 20) * 20

    def test_find_section_anchors_component_list(self):
        """'LIST OF MATERIAL' is mapped to component_list."""
        words = [
            {"text": "LIST",     "left": 30,  "top": 200, "width": 40, "height": 12},
            {"text": "OF",       "left": 75,  "top": 200, "width": 20, "height": 12},
            {"text": "MATERIAL", "left": 100, "top": 200, "width": 80, "height": 12},
        ]
        anchors = _find_section_anchors(words)
        assert "component_list" in anchors

    def test_find_section_anchors_metadata(self):
        """'DRAWING NO' is mapped to metadata."""
        words = [
            {"text": "DRAWING", "left": 550, "top": 700, "width": 70, "height": 12},
            {"text": "NO",      "left": 625, "top": 700, "width": 25, "height": 12},
        ]
        anchors = _find_section_anchors(words)
        assert "metadata" in anchors

    def test_find_section_anchors_multiple_sections(self):
        """Multiple sections in one word list are all found."""
        words = [
            # revision_history at top-right
            {"text": "REVISION",  "left": 600, "top": 50,  "width": 80, "height": 12},
            {"text": "HISTORY",   "left": 690, "top": 50,  "width": 70, "height": 12},
            # component_list on left
            {"text": "LIST",      "left": 30,  "top": 200, "width": 40, "height": 12},
            {"text": "OF",        "left": 75,  "top": 200, "width": 20, "height": 12},
            {"text": "MATERIAL",  "left": 100, "top": 200, "width": 80, "height": 12},
            # metadata at bottom-right
            {"text": "DRAWING",   "left": 550, "top": 700, "width": 70, "height": 12},
            {"text": "NO",        "left": 625, "top": 700, "width": 25, "height": 12},
        ]
        anchors = _find_section_anchors(words)
        assert "revision_history" in anchors
        assert "component_list"   in anchors
        assert "metadata"         in anchors

    def test_find_section_anchors_first_match_wins(self):
        """When the same keyword appears twice, the topmost row is used."""
        words = [
            {"text": "REVISION", "left": 600, "top": 50,  "width": 80, "height": 12},
            {"text": "HISTORY",  "left": 690, "top": 50,  "width": 70, "height": 12},
            {"text": "REVISION", "left": 600, "top": 400, "width": 80, "height": 12},
            {"text": "HISTORY",  "left": 690, "top": 400, "width": 70, "height": 12},
        ]
        anchors = _find_section_anchors(words)
        _, ay = anchors["revision_history"]
        assert ay == 40   # first (topmost) occurrence wins

    # ── _is_standard_layout ──────────────────

    def test_is_standard_layout_empty_anchors(self):
        """No anchors found → treated as standard (no evidence of non-standard)."""
        assert _is_standard_layout({}, 1000, 800) is True

    def test_is_standard_layout_revision_at_standard_position(self):
        """Revision header in the expected upper-right zone → standard."""
        # Standard zone for revision_history: x: 55-100%, y: 0-55%
        # Anchor at x=600 (60%), y=80 (10%) on a 1000×800 image → in zone.
        anchors = {"revision_history": (600, 80)}
        assert _is_standard_layout(anchors, 1000, 800) is True

    def test_is_standard_layout_revision_at_nonstandard_position(self):
        """Revision header at bottom-left → not standard."""
        anchors = {"revision_history": (50, 700)}
        assert _is_standard_layout(anchors, 1000, 800) is False

    def test_is_standard_layout_metadata_at_standard_position(self):
        """Metadata header in expected bottom-right zone → standard."""
        # Standard zone for metadata: x: 45-100%, y: 65-100%
        # Anchor at x=550 (55%), y=700 (87.5%) on 1000×800 → in zone.
        anchors = {"metadata": (550, 700)}
        assert _is_standard_layout(anchors, 1000, 800) is True

    def test_is_standard_layout_metadata_at_top_is_nonstandard(self):
        """Metadata header at the very top → not at its standard bottom position."""
        anchors = {"metadata": (550, 10)}
        assert _is_standard_layout(anchors, 1000, 800) is False

    def test_is_standard_layout_all_standard(self):
        """All four sections at expected positions → standard."""
        anchors = {
            "metadata":         (550, 700),   # bottom-right
            "revision_history": (600, 80),    # upper-right
            "component_list":   (30,  50),    # left
            "other_data":       (30,  600),   # lower-left
        }
        assert _is_standard_layout(anchors, 1000, 800) is True

    def test_is_standard_layout_one_section_misplaced(self):
        """If even one section is out of place, the layout is non-standard."""
        anchors = {
            "metadata":         (550, 700),
            "revision_history": (50,  700),   # bottom-left ← non-standard
        }
        assert _is_standard_layout(anchors, 1000, 800) is False

    # ── _STANDARD_ZONES completeness ─────────

    def test_standard_zones_cover_all_parseable_sections(self):
        """Every section that has a parser also has a standard zone defined."""
        parseable = {"metadata", "revision_history", "component_list", "other_data"}
        assert parseable == set(_STANDARD_ZONES.keys())

    # ── dynamic_scan dependency check ────────

    def test_dynamic_scan_returns_empty_when_no_dependencies(self, monkeypatch):
        """dynamic_scan returns {} when PIL or Tesseract is unavailable."""
        import extractor
        monkeypatch.setattr(extractor, "HAS_PIL", False)
        result = extractor.dynamic_scan("dummy_path.png")
        assert result == {}

    def test_dynamic_scan_returns_empty_when_image_not_found(self, monkeypatch):
        """dynamic_scan returns {} when the image file cannot be opened."""
        import extractor
        # Ensure PIL is reported available so we reach the Image.open() call
        monkeypatch.setattr(extractor, "HAS_PIL", True)
        monkeypatch.setattr(extractor, "HAS_TESSERACT", True)
        result = extractor.dynamic_scan("/nonexistent/path/drawing.png")
        assert result == {}
