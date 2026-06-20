import json, os, logging, argparse 
import requests
from pathlib import Path


logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

DEFAULT_INPUT = Path("viewed_metadata.json")
DEFAULT_DIRECTORY = Path("downloaded_media")  

class FacebookMediaDownloader:
    def __init__(self, base_download_dir: str | Path = DEFAULT_DIRECTORY):
        self.base_dir = Path(base_download_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36"
        }
        logging.info(f"Initialized downloader. Saving files to: {self.base_dir.absolute()}")

    def download_file(self, url: str, filepath: Path) -> bool:
        """Streams the file download chunk by chunk to efficiently handle large videos."""
        if filepath.exists():
            logging.info(f"File already exists, skipping: {filepath.name}")
            return True

        try:
            with requests.get(url, headers=self.headers, stream=True, timeout=20) as response:
                response.raise_for_status() 
                with open(filepath, 'wb') as file:
                    for chunk in response.iter_content(chunk_size=8192):
                        file.write(chunk)
            return True
        except requests.exceptions.RequestException as e:
            logging.error(f"Network error while downloading {filepath.name}: {e}")
            return False
        except Exception as e:
            logging.error(f"Unexpected error saving {filepath.name}: {e}")
            return False

    def process_post(self, metadata: dict):
        """Processes a single metadata dictionary and downloads its associated media."""
        folder_name = metadata.get("media_id") or metadata.get("username")
        
        if not folder_name or folder_name == "UNKNOWN_ID":
            logging.warning("Skipping entry: No valid media_id or username found.")
            return

        safe_folder_name = "".join([c for c in str(folder_name) if c.isalpha() or c.isdigit() or c in ('-', '_')]).rstrip()
        target_dir = self.base_dir / safe_folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

        logging.info(f"Processing media for ID: {safe_folder_name}")

        # Extract URLs
        video_url = metadata.get("video_url")
        thumbnail_url = metadata.get("thumbnail_url")
        profile_pic_url = metadata.get("profile_picture_url")

        if video_url:
            video_path = target_dir / "video.mp4"
            logging.info(f"[{safe_folder_name}] Downloading video...")
            self.download_file(video_url, video_path)

        if thumbnail_url:
            thumb_path = target_dir / "thumbnail.jpg"
            logging.info(f"[{safe_folder_name}] Downloading thumbnail...")
            self.download_file(thumbnail_url, thumb_path)

        if profile_pic_url:
            ext = ".png" if ".png" in profile_pic_url.lower() else ".jpg"
            profile_path = target_dir / f"profile_pic{ext}"
            logging.info(f"[{safe_folder_name}] Downloading profile picture...")
            self.download_file(profile_pic_url, profile_path)

    def run_bulk_download(self, json_filepath: Path):
        """Reads the JSON file and iterates over all valid results."""
        if not json_filepath.exists():
            logging.error(f"Could not find JSON file at: {json_filepath}")
            return

        try:
            with open(json_filepath, 'r', encoding='utf-8') as file:
                data = json.load(file)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON file at: {json_filepath}")
            return

        results = data.get("results", [])
        if not results:
            logging.warning("No 'results' array found in the JSON file.")
            return

        logging.info(f"Found {len(results)} posts to process.")
        
        for item in results:
            metadata = item.get("metadata")
            status = item.get("status")
            
            if metadata and status == "success":
                self.process_post(metadata)
            else:
                post_id = item.get('post_url') or item.get('scraped_url')
                logging.warning(f"Skipping post {post_id} due to missing metadata or failed status.")

        logging.info("Bulk download complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download Facebook media from parsed JSON metadata.")
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT, help="Path to input JSON file")
    parser.add_argument("-dir", "--directory", type=Path, default=DEFAULT_DIRECTORY, help="Media download destination directory")
    args = parser.parse_args()

    downloader = FacebookMediaDownloader(base_download_dir=args.directory)
    downloader.run_bulk_download(args.input)
