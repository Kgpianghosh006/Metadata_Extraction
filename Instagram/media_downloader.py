import json
import os
import requests
import logging
import argparse 
from pathlib import Path
from typing import Dict, Any

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_INPUT = Path("posts_meta_output.json")
DEFAULT_DIRECTORY = Path("downloaded_media")  

class InstagramMediaDownloader:
    def __init__(self, base_download_dir: str = "downloaded_media"):
        self.base_dir = base_download_dir
        os.makedirs(self.base_dir, exist_ok=True)
        logging.info(f"Initialized downloader. Saving files to: {os.path.abspath(self.base_dir)}")

    def download_file(self, url: str, filepath: str) -> bool:
        try:
            # stream=True ensures we download the file in chunks
            with requests.get(url, stream=True, timeout=15) as response:
                response.raise_for_status() # Check for HTTP errors
                with open(filepath, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error while downloading {filepath}: {e}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error saving {filepath}: {e}")
            return False

    def process_post(self, metadata: Dict[str, Any]):
        shortcode = metadata.get("shortcode", "unknown")
        post_dir = os.path.join(self.base_dir, shortcode)
        os.makedirs(post_dir, exist_ok=True)

        thumbnail_url = metadata.get("thumbnail_url")
        if thumbnail_url:
            thumb_path = os.path.join(post_dir, f"{shortcode}_image.jpg")
            if self.download_file(thumbnail_url, thumb_path):
                logging.info(f"[{shortcode}] Image saved.")

        if metadata.get("is_video"):
            video_url = metadata.get("video_url")
            if video_url:
                video_path = os.path.join(post_dir, f"{shortcode}_video.mp4")
                if self.download_file(video_url, video_path):
                    logging.info(f"[{shortcode}] Video saved.")

    def run_bulk_download(self, json_filepath: str):
        try:
            with open(json_filepath, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except FileNotFoundError:
            logging.error(f"Could not find JSON file at: {json_filepath}")
            return

        results = data.get("results", [])
        if not results:
            logging.warning("No 'results' array found in the JSON file.")
            return

        logging.info(f"Found {len(results)} posts to process.")
        
        for item in results:
            metadata = item.get("metadata")
            if metadata and item.get("status") == "success":
                self.process_post(metadata)
            else:
                logging.warning(f"Skipping post {item.get('shortcode')} due to missing metadata or failed status.")

        logging.info("Bulk download complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Instagram media from parsed JSON metadata.")
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT, help="Output JSON file")
    parser.add_argument("-dir", "--directory", type=Path, default=DEFAULT_DIRECTORY, help="Media download directory")
    args = parser.parse_args()

    downloader = InstagramMediaDownloader(base_download_dir=str(args.directory))
    downloader.run_bulk_download(json_filepath=str(args.input))