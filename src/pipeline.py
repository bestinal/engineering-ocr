"""
Full Pipeline: Extract → Store → Report
Run this as the main entry point.
"""

import argparse
import json
from pathlib import Path

from extractor import process_file, process_folder
from database import store_result, get_all_drawings, find_duplicate_parts


def run_pipeline(input_path: str, out_dir: str = "output", store_db: bool = True):
    """End-to-end: extract then optionally store in DB."""
    p = Path(input_path)
    if p.is_dir():
        results = process_folder(str(p), out_dir)
    else:
        result, metrics = process_file(str(p), out_dir)
        results = [{"file": str(p), "result": result, "metrics": metrics}]

    if store_db:
        for item in results:
            if "result" in item and "metrics" in item:
                store_result(item["result"], item["metrics"])

    return results


def print_dashboard():
    """Print a quick summary of all stored drawings."""
    drawings = get_all_drawings()
    if not drawings:
        print("No drawings in database yet.")
        return

    print(f"\n{'='*70}")
    print(f"  ENGINEERING DRAWINGS DATABASE — {len(drawings)} records")
    print(f"{'='*70}")
    print(f"{'ID':<5} {'Title':<30} {'Drawing ID':<15} {'Rev':<6} {'Score':<8} {'Company'}")
    print(f"{'-'*70}")
    for d in drawings:
        print(f"{d['id']:<5} {(d['title'] or 'N/A')[:28]:<30} "
              f"{(d['drawing_id'] or 'N/A')[:13]:<15} "
              f"{(d['current_revision'] or 'N/A'):<6} "
              f"{d['overall_score']:.0%}    "
              f"{d['company'] or 'N/A'}")

    dups = find_duplicate_parts()
    if dups:
        print(f"\n{'─'*70}")
        print(f"  DUPLICATE PARTS DETECTED ({len(dups)} part numbers appear in 2+ drawings)")
        print(f"{'─'*70}")
        for dup in dups[:10]:
            print(f"  Part: {dup['part_number']:20s}  Drawings: {dup['drawing_count']}  IDs: {dup['drawing_ids']}")

    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Engineering Drawing OCR Pipeline")
    parser.add_argument("command", choices=["extract", "dashboard"],
                        help="'extract' to process files, 'dashboard' to view DB")
    parser.add_argument("input", nargs="?", help="File or folder to process")
    parser.add_argument("--out", default="output")
    parser.add_argument("--no-db", action="store_true", help="Skip database storage")
    args = parser.parse_args()

    if args.command == "extract":
        if not args.input:
            print("Please provide an input file or folder.")
        else:
            run_pipeline(args.input, args.out, store_db=not args.no_db)
    elif args.command == "dashboard":
        print_dashboard()
