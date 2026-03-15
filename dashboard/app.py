"""
Flask Dashboard — Engineering Drawing OCR
Trane Technologies / Thermo King

Run:
    cd dashboard
    python app.py
Then open: http://127.0.0.1:5000
"""

import sys
import os
import json
from pathlib import Path
from datetime import datetime
from werkzeug.utils import secure_filename

from flask import (Flask, render_template, request, redirect,
                   url_for, flash, jsonify)

# Allow importing from ../src
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from database import (get_all_drawings, get_drawing_detail,
                      search_by_part_number, find_duplicate_parts,
                      store_result, DB_TYPE)
from extractor import process_file, compute_accuracy_metrics

# ─────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "trane_ocr_secret_2024")

UPLOAD_FOLDER = Path(__file__).parent.parent / "sample_input"
OUTPUT_FOLDER = Path(__file__).parent.parent / "output"
UPLOAD_FOLDER.mkdir(exist_ok=True)
OUTPUT_FOLDER.mkdir(exist_ok=True)
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg", "tiff", "bmp"}

app.config["UPLOAD_FOLDER"] = str(UPLOAD_FOLDER)
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB max upload


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# ─────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────

@app.route("/")
def index():
    drawings = get_all_drawings()
    dups = find_duplicate_parts()

    # Summary stats
    total = len(drawings)
    avg_score = (sum(d["overall_score"] or 0 for d in drawings) / total * 100) if total else 0

    # Enrich with counts
    for d in drawings:
        detail = get_drawing_detail(d["id"])
        d["rev_count"]  = len(detail.get("revision_history", []))
        d["comp_count"] = len(detail.get("component_list", []))
        d["score_pct"]  = round((d["overall_score"] or 0) * 100)

    return render_template("index.html",
        drawings=drawings, dups=dups,
        total=total, avg_score=round(avg_score, 1),
        dup_count=len(dups), db_type=DB_TYPE.upper()
    )


@app.route("/drawing/<int:drawing_id>")
def drawing_detail(drawing_id):
    detail = get_drawing_detail(drawing_id)
    if not detail:
        flash("Drawing not found.", "error")
        return redirect(url_for("index"))
    detail["score_pct"] = round((detail.get("overall_score") or 0) * 100)
    return render_template("detail.html", d=detail)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file selected.", "error")
            return redirect(request.url)
        file = request.files["file"]
        if file.filename == "":
            flash("No file selected.", "error")
            return redirect(request.url)
        if not allowed_file(file.filename):
            flash("Unsupported file type. Use PDF, PNG, JPG, TIFF.", "error")
            return redirect(request.url)

        filename = secure_filename(file.filename)
        save_path = Path(app.config["UPLOAD_FOLDER"]) / filename
        file.save(str(save_path))

        try:
            flash(f"Processing '{filename}'... this may take a moment.", "info")
            use_dynamic_scan = request.form.get("dynamic_scan") == "1"
            result, metrics = process_file(str(save_path), str(OUTPUT_FOLDER),
                                           use_dynamic_scan=use_dynamic_scan)
            drawing_id = store_result(result, metrics)
            flash(f"✓ '{filename}' extracted successfully! Drawing ID: {drawing_id}", "success")
            return redirect(url_for("drawing_detail", drawing_id=drawing_id))
        except Exception as e:
            flash(f"Error processing file: {e}", "error")
            return redirect(request.url)

    return render_template("upload.html")


@app.route("/search")
def search():
    query = request.args.get("q", "").strip()
    results = []
    if query:
        results = search_by_part_number(query)
    return render_template("search.html", query=query, results=results)


@app.route("/duplicates")
def duplicates():
    dups = find_duplicate_parts()
    return render_template("duplicates.html", dups=dups)


# ── API endpoints (JSON) ──────────────────────

@app.route("/api/drawings")
def api_drawings():
    return jsonify(get_all_drawings())


@app.route("/api/drawing/<int:drawing_id>")
def api_drawing(drawing_id):
    return jsonify(get_drawing_detail(drawing_id))


@app.route("/api/stats")
def api_stats():
    drawings = get_all_drawings()
    total = len(drawings)
    avg   = (sum(d["overall_score"] or 0 for d in drawings) / total) if total else 0
    dups  = find_duplicate_parts()
    return jsonify({
        "total_drawings": total,
        "avg_score": round(avg, 3),
        "duplicate_parts": len(dups),
        "db_type": DB_TYPE,
    })


# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  Engineering Drawing OCR — Flask Dashboard")
    print("  Open: http://127.0.0.1:5000\n")
    app.run(debug=True, port=5000)
