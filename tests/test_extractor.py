"""
Unit Tests for Engineering Drawing OCR Extractor
Run: python -m pytest tests/ -v
"""

import json
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

import pytest
from extractor import merge_pages, compute_accuracy_metrics
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
