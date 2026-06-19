import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import instaloader

DEFAULT_INPUT = Path("posts_formatted.json")
DEFAULT_OUTPUT = Path("posts_meta_output.json")
REQUEST_DELAY_SECONDS = 2.0

class ScraperResult:
    def __init__(self, post_index, post_url, shortcode, success, metadata=None, error_type=None, error_message=None):
        self.post_index = post_index
        self.post_url = post_url
        self.shortcode = shortcode
        self.success = success
        self.metadata = metadata or {}
        self.error_type = error_type
        self.error_message = error_message

    def to_dict(self):
        base = {
            "post_index": self.post_index,
            "post_url": self.post_url,
            "shortcode": self.shortcode,
            "status": "success" if self.success else "error",
        }
        if self.success:
            base["metadata"] = self.metadata
        else:
            base["error"] = {
                "type": self.error_type,
                "message": self.error_message,
            }
        return base

class InstagramSession:
    def __init__(self, username=None):
        self._username = username
        self._loader = None

    def __enter__(self):
        self._loader = instaloader.Instaloader(
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            download_geotags=False,
            download_comments=False,
            save_metadata=False,
            compress_json=False,
            quiet=True,
        )

        if self._username:
            print(f"Loading saved session for @{self._username}...")
            try:
                self._loader.load_session_from_file(self._username)
                print("Success: Session loaded.")
            except FileNotFoundError:
                print(f"[ERROR] Session file for @{self._username} not found.", file=sys.stderr)
                print(f"Please run 'instaloader -l {self._username}' in your terminal first.", file=sys.stderr)
                sys.exit(1)
            except Exception as exc:
                print(f"[ERROR] Failed to load session: {exc}", file=sys.stderr)
                sys.exit(1)
        else:
            print("Running in anonymous (unauthenticated) mode.")

        return self

    def __exit__(self, *_):
        if self._loader is not None:
            self._loader.close()

    @property
    def context(self):
        if self._loader is None:
            raise RuntimeError("InstagramSession must be used as a context-manager.")
        return self._loader.context


class PostMetadataFetcher:
    def __init__(self, context):
        self._context = context

    def fetch(self, post_index, post_url, shortcode, username=None):
        try:
            post = instaloader.Post.from_shortcode(self._context, shortcode)
            metadata = self._extract_post_metadata(post)
            return ScraperResult(post_index, post_url, shortcode, True, metadata=metadata)
        
        except instaloader.exceptions.InstaloaderException as post_exc:
            # Fallback to Profile if Post fails
            if username:
                try:
                    profile = instaloader.Profile.from_username(self._context, username)
                    metadata = self._extract_profile_metadata(profile)
                    return ScraperResult(post_index, post_url, shortcode, True, metadata=metadata)
                except instaloader.exceptions.InstaloaderException as prof_exc:
                    return ScraperResult(post_index, post_url, shortcode, False, error_type=type(prof_exc).__name__, error_message=f"Post & Profile fetch failed: {prof_exc}")
            
            return ScraperResult(post_index, post_url, shortcode, False, error_type=type(post_exc).__name__, error_message=str(post_exc))

    @staticmethod
    def _extract_post_metadata(post):
        def safe(fn):
            try:
                return fn()
            except Exception:
                return None

        location = safe(lambda: post.location)
        location_dict = None
        if location is not None:
            location_dict = {
                "id": safe(lambda: location.id),
                "name": safe(lambda: location.name),
                "lat": safe(lambda: location.lat),
                "lng": safe(lambda: location.lng),
            }

        date_utc = safe(lambda: post.date_utc)
        date_iso = date_utc.strftime("%Y-%m-%dT%H:%M:%SZ") if date_utc else None

        return {
            "media_id": safe(lambda: post.mediaid),
            "typename": safe(lambda: post.typename),
            "is_video": safe(lambda: post.is_video),
            "video_url": safe(lambda: post.video_url),
            "video_view_count": safe(lambda: post.video_view_count),
            "thumbnail_url": safe(lambda: post.url),
            "like_count": safe(lambda: post.likes),
            "comment_count": safe(lambda: post.comments),
            "caption": safe(lambda: post.caption),
            "caption_hashtags": safe(lambda: list(post.caption_hashtags)),
            "caption_mentions": safe(lambda: list(post.caption_mentions)),
            "tagged_users": safe(lambda: list(post.tagged_users)),
            "accessibility_caption": safe(lambda: post.accessibility_caption),
            "owner_username": safe(lambda: post.owner_username),
            "owner_id": safe(lambda: post.owner_id),
            "owner_full_name": safe(lambda: post.owner_profile.full_name),
            "owner_follower_count": safe(lambda: post.owner_profile.followers),
            "owner_bio": safe(lambda: post.owner_profile.biography),
            "owner_external_url": safe(lambda: post.owner_profile.external_url),
            "owner_profile_pic_url": safe(lambda: post.owner_profile.profile_pic_url),
            "posted_at_utc": date_iso,
            "posted_at_unix": safe(lambda: int(post.date_utc.timestamp())),
            "location": location_dict,
            "sidecar_count": safe(lambda: len(list(post.get_sidecar_nodes())) if post.typename == "GraphSidecar" else 0),
            "is_pinned": safe(lambda: post.is_pinned),
            "pcaption": safe(lambda: post.pcaption),
            "fetched_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

    @staticmethod
    def _extract_profile_metadata(profile):
        def safe(fn):
            try:
                return fn()
            except Exception:
                return None

        return {
            "typename": "GraphProfile",
            "username": safe(lambda: profile.username),
            "full_name": safe(lambda: profile.full_name),
            "category": safe(lambda: profile.business_category_name),
            "follower_count": safe(lambda: profile.followers),
            "following_count": safe(lambda: profile.followees),
            "profile_picture_url": safe(lambda: profile.profile_pic_url),
            "bio_text": safe(lambda: profile.biography),
            "is_private": safe(lambda: profile.is_private),
            "fetched_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }


class OutputSerializer:
    def __init__(self, output_path):
        self._output_path = output_path
        self.existing_results = []
        self.previous_elapsed = 0.0

        if self._output_path.exists():
            try:
                with self._output_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.existing_results = data.get("results", [])
                    self.previous_elapsed = data.get("run_summary", {}).get("total_time_seconds", 0.0)
            except json.JSONDecodeError:
                pass 

    def write_incremental(self, new_results, session_elapsed, run_meta):
        combined_results = self.existing_results + [r.to_dict() for r in new_results]
        
        total_attempted = len(combined_results)
        success_count = sum(1 for r in combined_results if r.get("status") == "success")
        error_count = total_attempted - success_count
        total_time = self.previous_elapsed + session_elapsed

        output = {
            "run_summary": {
                **run_meta,
                "total_posts_attempted": total_attempted,
                "successfully_fetched": success_count,
                "failed_to_fetch": error_count,
                "success_rate_percent": round(100 * success_count / total_attempted, 2) if total_attempted else 0.0,
                "total_time_seconds": round(total_time, 3),
                "total_time_human": format_duration(total_time),
                "completed_at_utc": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "results": combined_results,
        }

        with self._output_path.open("w", encoding="utf-8") as fh:
            json.dump(output, fh, ensure_ascii=False, indent=2)


class MetadataScraperPipeline:
    def __init__(self, session, delay=REQUEST_DELAY_SECONDS):
        self._session = session
        self._delay = delay
        self._fetcher = PostMetadataFetcher(session.context)

    def run(self, posts, limit=None, serializer=None, run_meta=None):
        subset = posts[:limit] if limit else posts
        total = len(subset)
        results = []

        print(f"\nStarting run -- {total} remaining post(s) to process...\n")
        wall_start = time.perf_counter()

        for idx, post_record in enumerate(subset, start=1):
            post_url = post_record.get("post_url", "")
            shortcode = post_record.get("shortcode", "")
            post_index = post_record.get("post_index", idx)
            username = post_record.get("owner", {}).get("username")

            print(f"[{idx}/{total}] Fetching {shortcode}...", end=" ", flush=True)

            result = self._fetcher.fetch(post_index, post_url, shortcode, username)
            results.append(result)

            if result.success:
                if result.metadata.get("typename") == "GraphProfile":
                    fc = result.metadata.get("follower_count", "?")
                    print(f"FALLBACK TO PROFILE (followers={fc})")
                else:
                    lc = result.metadata.get("like_count", "?")
                    cc = result.metadata.get("comment_count", "?")
                    print(f"OK (likes={lc}, comments={cc})")
            else:
                print(f"ERR ({result.error_type}: {result.error_message})")

            if serializer and run_meta:
                current_elapsed = time.perf_counter() - wall_start
                serializer.write_incremental(results, current_elapsed, run_meta)

            if idx < total:
                time.sleep(self._delay)

        elapsed = time.perf_counter() - wall_start
        return results, elapsed


def format_duration(seconds):
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"

def print_final_summary(results, elapsed, out_path):
    success = sum(1 for r in results if r.success)
    errors = len(results) - success
    rate = (100 * success / len(results)) if results else 0.0

    print("\nScrape Completion Summary (Current Batch)")
    print(f"Posts attempted : {len(results)}")
    print(f"Successful      : {success}")
    print(f"Failed          : {errors}")
    print(f"Success rate    : {rate:.1f}%")
    print(f"Output saved to : {out_path.resolve()}")
    print(f"Total time taken: {format_duration(elapsed)} ({elapsed:.3f} seconds)\n")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("-o", "--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("-u", "--username", type=str, default=None, help="Instagram username")
    parser.add_argument("-l", "--limit", type=int, default=None, help="Maximum number of posts to scrape")
    parser.add_argument("-d", "--delay", type=float, default=REQUEST_DELAY_SECONDS, help="Delay between requests")
    args = parser.parse_args()

    with args.input.open("r", encoding="utf-8") as fh:
        formatted_data = json.load(fh)

    all_posts = formatted_data.get("posts", [])
    if not all_posts:
        sys.exit("\n[ERROR] No posts found in the input file.\n")

    serializer = OutputSerializer(args.output)
    
    scraped_shortcodes = {r.get("shortcode") for r in serializer.existing_results if r.get("shortcode")}
    remaining_posts = [p for p in all_posts if p.get("shortcode") not in scraped_shortcodes]

    if not remaining_posts:
        print(f"\nAll {len(all_posts)} URLs have already been scraped. Exiting.")
        sys.exit(0)

    run_meta = {
        "input_file": str(args.input.resolve()),
        "output_file": str(args.output.resolve()),
        "total_posts_in_file": len(all_posts),
        "scrape_limit_applied": args.limit,
        "request_delay_seconds": args.delay,
        "authenticated": bool(args.username),
    }

    with InstagramSession(args.username) as session:
        pipeline = MetadataScraperPipeline(session, delay=args.delay)
        
        results, elapsed = pipeline.run(
            posts=remaining_posts, 
            limit=args.limit, 
            serializer=serializer, 
            run_meta=run_meta
        )

    print_final_summary(results, elapsed, args.output)

if __name__ == "__main__":
    main()