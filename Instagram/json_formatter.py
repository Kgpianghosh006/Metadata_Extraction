import argparse
import csv
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ZIP_INNER_PREFIX = "ads_information/ads_and_topics"

TARGET_FILES = {
    "ads_viewed.json": "ad",
    "posts_viewed.json": "post",
    "videos_watched.json": "video",
}

SHORTCODE_POST_RE = re.compile(r"instagram\.com/p/([A-Za-z0-9_\-]+)/?")
SHORTCODE_REEL_RE = re.compile(r"instagram\.com/reel/([A-Za-z0-9_\-]+)/?")
SHORTCODE_LEN = 11

def clean_instagram_url(raw_url: str) -> str | None:
    if not raw_url:
        return None

    match_post = SHORTCODE_POST_RE.search(raw_url)
    if match_post:
        shortcode = match_post.group(1)[:SHORTCODE_LEN]
        return f"https://www.instagram.com/p/{shortcode}/"

    match_reel = SHORTCODE_REEL_RE.search(raw_url)
    if match_reel:
        shortcode = match_reel.group(1)[:SHORTCODE_LEN]
        return f"https://www.instagram.com/reel/{shortcode}/"

    return None

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

def extract_content_url(label_values: list) -> str | None:
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

def process_zip_to_csv(zip_path: Path, output_csv: Path) -> None:
    print(f"Opening Archive: {zip_path.resolve()}")
    
    formatted_posts = []
    seen_urls = set()
    stats = {"files_scanned": 0, "entries_parsed": 0, "skipped_no_url": 0, "skipped_duplicate": 0}

    with zipfile.ZipFile(zip_path, 'r') as zf:
        all_names = zf.namelist()

        for filename, source_type in TARGET_FILES.items():
            candidates = [
                f"{ZIP_INNER_PREFIX}/{filename}",
                f"ads_information\\ads_and_topics\\{filename}",
            ]
            for name in all_names:
                if name.replace("\\", "/").endswith(f"{ZIP_INNER_PREFIX}/{filename}"):
                    candidates.insert(0, name)

            raw_data = None
            for candidate in candidates:
                if candidate in all_names:
                    try:
                        with zf.open(candidate) as fh:
                            raw_data = json.load(fh)
                            break
                    except json.JSONDecodeError:
                        continue 

            if not isinstance(raw_data, list):
                continue

            stats["files_scanned"] += 1

            for entry in raw_data:
                if not isinstance(entry, dict) or "label_values" not in entry:
                    continue
                    
                stats["entries_parsed"] += 1
                label_values = entry.get("label_values", [])
                content_url = extract_content_url(label_values)

                if not content_url:
                    stats["skipped_no_url"] += 1
                    continue

                if content_url in seen_urls:
                    stats["skipped_duplicate"] += 1
                    continue
                    
                seen_urls.add(content_url)
                ts = entry.get("timestamp")
                owner = extract_owner(label_values)
                
                formatted_posts.append({
                    "source_type": source_type,
                    "content_url": content_url,
                    "shortcode": content_url.rstrip("/").split("/")[-1],
                    "fbid": entry.get("fbid", ""),
                    "owner_name": owner["name"] or "",
                    "owner_username": owner["username"] or "",
                    "owner_profile_url": owner["profile_url"] or "",
                    "timestamp_unix": ts,
                    "timestamp_iso": timestamp_to_iso(ts)
                })

    if not formatted_posts:
        sys.exit("\n[WARNING] No matching post data found in the ZIP archive.\n")

    # Extract dynamic headers from the keys of the first dictionary
    headers = list(formatted_posts[0].keys())

    with output_csv.open("w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=headers)
        writer.writeheader()
        writer.writerows(formatted_posts)

    print("\nSummary:")
    print("-" * 35)
    print(f"JSON Files scanned   : {stats['files_scanned']}")
    print(f"Valid entries parsed : {stats['entries_parsed']}")
    print(f"Unique CSV rows      : {len(formatted_posts)}")
    print(f"Skipped (no URL)     : {stats['skipped_no_url']}")
    print(f"Skipped (duplicate)  : {stats['skipped_duplicate']}")
    print("-" * 35)
    print(f"\nData successfully exported to -> {output_csv.resolve()}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", required=True)
    parser.add_argument("-o", "--output", default="json_data_formatted.csv")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        sys.exit(f"\n[ERROR] Zip folder not found: {input_path.resolve()}\n")

    process_zip_to_csv(input_path, output_path)
