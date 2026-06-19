import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = Path("recently_viewed.json")
DEFAULT_OUTPUT = Path("recently_viewed_formatted.json")

def timestamp_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    if ts > 1e11:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def format_viewed_json(input_path: Path, output_path: Path) -> None:
    print(f"Reading: {input_path.resolve()}")
    
    with input_path.open("r", encoding="utf-8") as fh:
        raw_data = json.load(fh)

    # Provide the proper list name to extract the categories (e.g., "recently_viewed" or "viewed_items")
    categories = raw_data.get("recently_viewed", [])
    formatted_views = []
    seen_identifiers = set()
    total_raw_entries = 0
    skipped_duplicate = 0
    skipped_invalid = 0

    def process_entries(entries_list, category_label):
        nonlocal total_raw_entries, skipped_duplicate, skipped_invalid
        
        for entry in entries_list:
            total_raw_entries += 1
            data = entry.get("data", {})
            
            uri = data.get("uri")
            name = data.get("name")
            value = data.get("value")
            watch_time = data.get("watch_time")
            watch_pos = data.get("watch_position_seconds")
            
            unique_identifier = uri if uri else value
            
            if not unique_identifier:
                skipped_invalid += 1
                continue

            if unique_identifier in seen_identifiers:
                skipped_duplicate += 1
                continue
                
            seen_identifiers.add(unique_identifier)
            ts = entry.get("timestamp")
            
            visit_record = {
                "index": len(formatted_views) + 1,
                "category": category_label,
                "name": name,
                "url": uri,
                "action_value": value,
                "watch_time_seconds": watch_time,
                "watch_position_seconds": watch_pos,
                "timestamp_unix": ts,
                "timestamp_iso": timestamp_to_iso(ts)
            }
            formatted_views.append(visit_record)

    for category in categories:
        main_cat_name = category.get("name", "Unknown Category")
        
        if "children" in category:
            for child in category["children"]:
                sub_cat_name = child.get("name", "Unknown Sub-Category")
                process_entries(child.get("entries", []), f"{main_cat_name} -> {sub_cat_name}")
                
        if "entries" in category:
            process_entries(category["entries"], main_cat_name)

    output = {
        "meta": {
            "source_file": input_path.name,
            "total_raw_entries": total_raw_entries,
            "total_unique_views": len(formatted_views),
            "skipped_invalid_no_data": skipped_invalid,
            "skipped_duplicate": skipped_duplicate,
            "generated_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "views": formatted_views
    }

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=4)

    print("\nSummary:")
    print("-" * 35)
    print(f"Total raw entries   : {total_raw_entries}")
    print(f"Unique views written: {len(formatted_views)}")
    print(f"Skipped (No Data)   : {skipped_invalid}")
    print(f"Skipped (Duplicate) : {skipped_duplicate}")
    print("-" * 35)
    print(f"\nOutput saved to -> {output_path.resolve()}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    format_viewed_json(args.input, args.output)