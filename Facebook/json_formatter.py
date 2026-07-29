import argparse
import zipfile
import json
import csv
import io
from pathlib import Path
from datetime import datetime, timezone

class FB_JSON_Formatter:
    def __init__(self):
        # The specific JSON files we want to extract from the zip archive.
        self.target_files = {
            "content_that_has_been_shown_to_you_in_your_feed.json",
            "recently_viewed.json",
            "groups_and_events_you've_visited.json",
            "groups_you've_visited.json",
            "profile_visits.json"
        }
        self.master_dataset = []
        self._seen_urls = set()
        self._skipped_duplicates = 0

    def timestamp_to_iso(self, ts):
        if not ts: return ""
        try:
            ts_int = int(ts)
            if ts_int > 1e11: ts_int /= 1000.0
            return datetime.fromtimestamp(ts_int, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (ValueError, TypeError):
            return str(ts)

    def process_archive(self, zip_path, output_csv):
        print(f"Mounting the Zip file: {zip_path}")        
        try:
            with zipfile.ZipFile(zip_path, 'r') as archive:
                all_files = archive.namelist()
                
                target_paths = [f for f in all_files if Path(f).name in self.target_files]
                
                if not target_paths:
                    print("No target JSON files found inside the archive.")
                    return

                for file_path in target_paths:
                    filename = Path(file_path).name
                    print(f"Processing: {filename}")
                    
                    with archive.open(file_path) as file_stream:
                        text_stream = io.TextIOWrapper(file_stream, encoding='utf-8')
                        try:
                            json_data = json.load(text_stream)
                            self.route_and_parse(filename, json_data)
                        except json.JSONDecodeError:
                            print(f" Error decoding JSON in {filename}. Skipping.")
        
        except FileNotFoundError:
            print(f"Error: The zip file '{zip_path}' was not found.")
            return
        
        self.write_to_csv(output_csv)

    def route_and_parse(self, filename: str, json_data):
        
        if filename in ["content_that_has_been_shown_to_you_in_your_feed.json", "recently_viewed.json"]:
            self.parse_content_viewed(filename, json_data)
            
        elif filename in ["groups_and_events_you've_visited.json", "groups_you've_visited.json"]:
            self.parse_flat_schema(filename, json_data, category="Group/Event", url_prefix="https://www.facebook.com/")
            
        elif filename == "profile_visits.json":
            self.parse_flat_schema(filename, json_data, category="Profile Visit", url_prefix="https://www.facebook.com/")
            
    def parse_content_viewed(self, filename, json_data):
        categories = json_data.get("label_values", [])
        
        for category in categories:
            cat_name = category.get("label", "Viewed Content")
            for entry in category.get("vec", []):
                name, uri, ts = "", "", ""
                
                for item in entry.get("dict", []):
                    item_label = item.get("label")
                    if item_label == "Event": name = item.get("value")
                    elif item_label == "URL": uri = item.get("href") or item.get("value")
                    elif item_label == "Time": ts = item.get("timestamp_value")
                
                self.append_record(filename, cat_name, name, uri, ts)

    def parse_flat_schema(self, filename, json_data, category, url_prefix):
        # Normalize to list safely
        entries = json_data if isinstance(json_data, list) else [json_data]
        
        for entry in entries:
            ts = entry.get("timestamp")
            fbid = entry.get("fbid")
            uri = f"{url_prefix}{fbid}" if fbid else ""
            
            name = ""
            for item in entry.get("label_values", []):
                if item.get("label") == "Name":
                    name = item.get("value")
                    break
            
            self.append_record(filename, category, name, uri, ts)

    def append_record(self, filename, category, name, uri, ts):
        url = uri or ""

        if url:
            if url in self._seen_urls:
                self._skipped_duplicates += 1
                return
            self._seen_urls.add(url)

        self.master_dataset.append({
            "source_file": filename,
            "category": category,
            "name": name.strip() if name else "",
            "url": uri or "",
            "timestamp_unix": ts or "",
            "timestamp_iso": self.timestamp_to_iso(ts)
        })

    def write_to_csv(self, output_csv: str):
        if not self.master_dataset:
            print("\nNo valid data found to be extracted. CSV is not generated.")
            return

        if self._skipped_duplicates:
            print(f"Deduplication: {self._skipped_duplicates} duplicate URL(s) removed.")
            
        print(f"\nWriting {len(self.master_dataset)} records to {output_csv}")
        
        fieldnames = ["source_file", "category", "name", "url", "timestamp_unix", "timestamp_iso"]
        
        with open(output_csv, mode='w', newline='', encoding='utf-8-sig') as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(self.master_dataset)
            
        print("Success : JSON data is fetched and formatted.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", required=True, help="Path to the raw Facebook ZIP file.")
    parser.add_argument("-o", "--output", default="json_data_formatted.csv", help="Output CSV filename.")
    
    args = parser.parse_args()
    
    formatter = FB_JSON_Formatter()
    formatter.process_archive(zip_path=args.input, output_csv=args.output)
