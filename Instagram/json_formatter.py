import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_INPUT = Path("posts_viewed.json")
DEFAULT_OUTPUT = Path("posts_formatted.json")

_SHORTCODE_RE = re.compile(r"instagram\.com/p/([A-Za-z0-9_\-]+)/?")
_SHORTCODE_LEN = 11

def clean_instagram_url(raw_url: str) -> str | None:
    if not raw_url:
        return None
    match = _SHORTCODE_RE.search(raw_url)
    if not match:
        return None
    
    shortcode = match.group(1)[:_SHORTCODE_LEN]
    return f"https://www.instagram.com/p/{shortcode}/"

def decode_instagram_name(text: str) -> str:
    if not text:
        return text
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text

def timestamp_to_iso(ts: int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def extract_post_url(label_values: list) -> str | None:
    for item in label_values:
        if item.get("label") == "URL":
            raw = item.get("value") or item.get("href", "")
            return clean_instagram_url(raw)
    return None

def extract_owner(label_values: list) -> dict:
    owner = {"name": None, "username": None, "profile_url": None}
    
    for item in label_values:
        if item.get("title") != "Owner":
            continue
        for inner in item.get("dict", []):
            field_map = {f["label"]: f.get("value", "") for f in inner.get("dict", []) if "label" in f}
            owner["name"] = decode_instagram_name(field_map.get("Name", "")) or None
            owner["username"] = field_map.get("Username") or None
            owner["profile_url"] = field_map.get("URL") or None
        break 
        
    return owner

def format_json(input_path: Path, output_path: Path) -> None:
    print(f"Reading: {input_path.resolve()}")
    
    with input_path.open("r", encoding="utf-8") as fh:
        raw_data = json.load(fh)

    formatted_posts = []
    seen_urls = set()
    skipped_no_url = 0
    skipped_duplicate = 0

    for entry in raw_data:
        label_values = entry.get("label_values", [])
        post_url = extract_post_url(label_values)

        if not post_url:
            skipped_no_url += 1
            continue

        if post_url in seen_urls:
            skipped_duplicate += 1
            continue
            
        seen_urls.add(post_url)
        ts = entry.get("timestamp")
        
        formatted_posts.append({
            "post_index": len(formatted_posts) + 1,
            "post_url": post_url,
            "shortcode": post_url.rstrip("/").split("/")[-1],
            "timestamp_unix": ts,
            "timestamp_iso": timestamp_to_iso(ts),
            "fbid": entry.get("fbid"),
            "owner": extract_owner(label_values),
        })

    output = {
        "meta": {
            "source_file": input_path.name,
            "total_raw_entries": len(raw_data),
            "total_unique_posts": len(formatted_posts),
            "skipped_no_url": skipped_no_url,
            "skipped_duplicate": skipped_duplicate,
            "generated_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        },
        "posts": formatted_posts,
    }

    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)

    print("\nSummary:")
    print("-" * 35)
    print(f"Total raw entries    : {len(raw_data)}")
    print(f"Unique posts written : {len(formatted_posts)}")
    print(f"Skipped (no URL)     : {skipped_no_url}")
    print(f"Skipped (duplicate)  : {skipped_duplicate}")
    print("-" * 35)
    print(f"\nOutput saved to -> {output_path.resolve()}\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert raw Instagram posts_viewed.json into a scraper-ready JSON.")
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT, help="Path to raw JSON file")
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT, help="Path to formatted JSON output")
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"\n[ERROR] Input file not found: {args.input.resolve()}\n")

    format_json(args.input, args.output)