import os, json, re, time, random, argparse
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


class ExtractorHelper:

    @staticmethod
    def get_meta(soup, prop):
        tag = soup.find("meta", property=prop) or soup.find("meta", attrs={"name": prop})
        return tag.get("content") if tag else None

    @staticmethod
    def parse_int(val_str):
        if not val_str: return None
        val_str = str(val_str).upper().replace(',', '')
        multiplier = 1

        if 'K' in val_str or 'T' in val_str: multiplier = 1000
        elif 'M' in val_str: multiplier = 1000000
        elif 'L' in val_str: multiplier = 100000     
        elif 'CR' in val_str: multiplier = 10000000  
        
        try: return int(float(re.sub(r'[A-Z]', '', val_str)) * multiplier)
        except ValueError: return None

    @staticmethod
    def get_primary_metric(patterns, html_text):

        for p in patterns:
            for match in re.findall(p, html_text, re.IGNORECASE):
                val = ExtractorHelper.parse_int(match if isinstance(match, str) else match[0])
                if val is not None: 
                    return val
        return None

    @staticmethod
    def is_auto_generated(text, owner_name=""):

        if not text: return True
        text_lower = text.lower()
        if owner_name and owner_name.lower() in text_lower and "posted" in text_lower: return True
        if re.search(r'posted an? (episode|video)|explore more in video', text_lower): return True
        return False

    @staticmethod
    def get_clean_text(html_text, soup, owner_name=""):
        caption, description = None, None
        
        # Parse GraphQL Cache (Authenticated SPA payload)
        for script in soup.find_all("script", type="application/json"):
            try:
                def hunt_text(obj):
                    nonlocal caption, description
                    if isinstance(obj, dict):
                        if "title" in obj and isinstance(obj["title"], dict) and "text" in obj["title"]:
                            t = obj["title"]["text"]
                            if not caption and not ExtractorHelper.is_auto_generated(t, owner_name): caption = t
                        if "message" in obj and isinstance(obj["message"], dict) and "text" in obj["message"]:
                            d = obj["message"]["text"]
                            if not description and not ExtractorHelper.is_auto_generated(d, owner_name): description = d
                        for v in obj.values(): hunt_text(v)
                    elif isinstance(obj, list):
                        for item in obj: hunt_text(item)
                hunt_text(json.loads(script.string))
            except: continue

        if not caption:
            og_title = ExtractorHelper.get_meta(soup, "og:title") or ""
            if "views" in og_title and "|" in og_title:
                parts = [p.strip() for p in og_title.split("|")]
                caption = parts[0] if len(parts) >= 2 else og_title
            else: caption = og_title

        if not description: description = ExtractorHelper.get_meta(soup, "og:description") or ""

        if ExtractorHelper.is_auto_generated(caption, owner_name): caption = ""
        if ExtractorHelper.is_auto_generated(description, owner_name): description = ""

        return caption, description

    @staticmethod
    def extract_text_blocks(soup, exclude_strings=None):
        valid_blocks = []
        exclusions = [x.lower() for x in (exclude_strings or []) if x and isinstance(x, str)]
        main_area = soup.find(attrs={"role": "main"}) or soup
        
        for el in main_area.find_all(['h1', 'h2', 'span', 'div']):
            if el.name not in ['h1', 'h2'] and el.get('dir') != 'auto': continue
                
            text = el.get_text(separator=" ", strip=True)
            text_lower = text.lower()
            
            if len(text) < 4: continue
            if re.match(r'^\d+[mhdw]$', text_lower): continue 
            
            # Rejects timestamp lines (e.g. "1 Dec 2020 · Public")
            if ('·' in text or '•' in text) and len(text) < 60: continue
            
            # Strict Metric Stripper
            clean_str = re.sub(r'^[\d,.]+[a-z]*\s*(comments?|reactions?|likes?|shares?|views?|plays?)$', '', text_lower).strip()
            if not clean_str: continue
            
            if exclusions and text_lower in exclusions: continue
            if ExtractorHelper.is_auto_generated(text_lower): continue
            
            is_ui_or_comment = False
            p = el
            for _ in range(6):
                if p:
                    role = p.get('role', '').lower()
                    aria = p.get('aria-label', '').lower()
                    tag_name = p.name.lower()
                    
                    # SEMANTIC UI FILTER
                    if role in ['button', 'link', 'navigation', 'tab', 'toolbar', 'dialog', 'textbox', 'search', 'form', 'combobox', 'menu', 'banner'] or tag_name in ['a', 'button', 'nav', 'form', 'input', 'textarea']:
                        is_ui_or_comment = True
                        break
                    
                    # SEMANTIC COMMENT FILTER
                    if role == 'article' or aria.startswith('comment from') or aria == 'comment':
                        is_ui_or_comment = True
                        break
                        
                    if role in ['main', 'complementary']: break
                    p = p.parent
                else: break
            
            if not is_ui_or_comment and text not in valid_blocks:
                valid_blocks.append(text)
                
        return valid_blocks

    @staticmethod
    def get_metrics(soup, html, media_id=None):
        """The Enterprise Metric Scraper: Bulletproofed against the Infinite Scroll Bleed."""
        likes, comments = None, None
        
        # STRATEGY 1: Targeted GraphQL Search (100% accurate, no cross-video bleed)
        if media_id:
            for script in soup.find_all("script", type="application/json"):
                text = script.string
                if not text or media_id not in text: continue
                try:
                    data = json.loads(text)
                    def hunt_metrics(obj):
                        nonlocal likes, comments
                        if isinstance(obj, dict):
                            # Target nodes that contain the media_id in their serialized string
                            if media_id in json.dumps(obj):
                                if likes is None:
                                    if "reaction_count" in obj and isinstance(obj["reaction_count"], dict):
                                        likes = ExtractorHelper.parse_int(str(obj["reaction_count"].get("count", "")))
                                    elif "i18n_reaction_count" in obj:
                                        likes = ExtractorHelper.parse_int(str(obj["i18n_reaction_count"]))
                                        
                                if comments is None:
                                    if "comment_count" in obj and isinstance(obj["comment_count"], dict):
                                        comments = ExtractorHelper.parse_int(str(obj["comment_count"].get("total_count", "")))
                                    elif "total_comment_count" in obj:
                                        comments = ExtractorHelper.parse_int(str(obj["total_comment_count"]))
                                    elif "i18n_comment_count" in obj:
                                        comments = ExtractorHelper.parse_int(str(obj["i18n_comment_count"]))
                            for v in obj.values(): hunt_metrics(v)
                        elif isinstance(obj, list):
                            for item in obj: hunt_metrics(item)
                    hunt_metrics(data)
                except: continue

        # STRATEGY 2: Visual Scoped Fallback (Restricted to role="main")
        if likes is None or comments is None:
            main_area = soup.find(attrs={"role": "main"})
            if main_area:
                main_html = str(main_area)
                
                if comments is None:
                    c_match = re.search(r'aria-label="([\d,.]+[a-zA-Z]*)\s*comments?"', main_html, re.I) or \
                              re.search(r'>([\d,.]+[a-zA-Z]*)\s*comments?<', main_html, re.I)
                    if c_match: comments = ExtractorHelper.parse_int(c_match.group(1))
                    
                if likes is None:
                    l_match = re.search(r'aria-label="([\d,.]+[a-zA-Z]*)\s*likes?"', main_html, re.I) or \
                              re.search(r'aria-label="([\d,.]+[a-zA-Z]*)\s*reactions?"', main_html, re.I) or \
                              re.search(r'>([\d,.]+[a-zA-Z]*)\s*likes?<', main_html, re.I)
                    if l_match: 
                        likes = ExtractorHelper.parse_int(l_match.group(1))
                    else:
                        # Safe visual fallback requiring a metric suffix (K, M, T, L, CR) to avoid plain numbers
                        backup_likes = re.findall(r'>([\d]+[.,]?[\d]*[KkMmTtLlCcRr]+)<', main_html)
                        if backup_likes:
                            likes = ExtractorHelper.parse_int(backup_likes[0])

        # STRATEGY 3: Global Fallback (Last resort)
        if likes is None:
            likes = ExtractorHelper.get_primary_metric([r'"i18n_reaction_count"\s*:\s*"([\d,.]+[a-zA-Z]*)"', r'"total_reaction_count"\s*:\s*(\d+)'], html)
        if comments is None:
            comments = ExtractorHelper.get_primary_metric([r'"i18n_comment_count"\s*:\s*"([\d,.]+[a-zA-Z]*)"', r'"total_comment_count"\s*:\s*(\d+)'], html)
            
        return likes, comments

    @staticmethod
    def extract_comments(html_text, limit=6):
        extracted = []        
        soup = BeautifulSoup(html_text, 'html.parser')
        for script in soup.find_all("script", type="application/json"):
            try:
                def hunt(obj):
                    if isinstance(obj, dict):
                        if obj.get("__typename") == "Comment":
                            body = obj.get("body", {})                    
                            txt = body.get("text") if isinstance(body, dict) else (body if isinstance(body, str) else None)
                            if txt and txt not in extracted: extracted.append(txt)
                        for v in obj.values(): hunt(v)
                    elif isinstance(obj, list):
                        for item in obj: hunt(item)
                hunt(json.loads(script.string))
            except: continue
        return extracted[:limit]

    @staticmethod
    def get_media_elements(html, soup, is_video, media_id=None):
        video_url = ExtractorHelper.get_meta(soup, "og:video") or ExtractorHelper.get_meta(soup, "og:video:url")
        thumbnail_url = ExtractorHelper.get_meta(soup, "og:image")

        if is_video:
            if media_id:
                for script in soup.find_all("script", type="application/json"):
                    if media_id in script.text and ("browser_native_hd_url" in script.text or "playable_url" in script.text):
                        m = re.search(r'"(browser_native_hd_url|playable_url_quality_hd|playable_url)"\s*:\s*"([^"]+)"', script.text)
                        if m:
                            try: video_url = json.loads(f'"{m.group(2)}"')
                            except: video_url = m.group(2).replace('\\/', '/')
                            break
            
            if not video_url:
                for p in [r'"browser_native_hd_url"\s*:\s*"([^"]+)"', r'"playable_url"\s*:\s*"([^"]+)"']:
                    m = re.search(p, html)
                    if m:
                        try: video_url = json.loads(f'"{m.group(1)}"')
                        except: video_url = m.group(1).replace('\\/', '/')
                        break
            
            video_tag = soup.find('video')
            if video_tag:
                if not video_url: video_url = video_tag.get('src')
                if not thumbnail_url: thumbnail_url = video_tag.get('poster')

        if not thumbnail_url or "fbcdn" not in thumbnail_url:
            for img in soup.find_all('img', src=re.compile(r'fbcdn\.net')):
                src = img.get('src')
                if 'pico' not in src and '16x16' not in src and '32x32' not in src:
                    thumbnail_url = src
                    break
        
        return video_url, thumbnail_url

    @staticmethod
    def get_post_context(html):
        posted_at_unix = None
        for ts_pat in [r'"publish_time"\s*:\s*(\d{10})', r'"creation_time"\s*:\s*(\d{10})', r'"story_publish_time"\s*:\s*(\d{10})', r'data-utime="(\d{10})"']:
            m = re.search(ts_pat, html)
            if m:
                posted_at_unix = int(m.group(1))
                break
                
        sidecar_count = 0
        for sc_pat in [r'"subattachments"\s*:\s*\{[^{}]*?"count"\s*:\s*(\d+)', r'"album"\s*:\s*\{[^{}]*?"media_count"\s*:\s*(\d+)']:
            m = re.search(sc_pat, html)
            if m:
                sidecar_count = int(m.group(1))
                break

        return {
            "posted_at_unix": posted_at_unix,
            "posted_at_utc": datetime.fromtimestamp(posted_at_unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if posted_at_unix else None,
            "sidecar_count": sidecar_count,
            "is_pinned": bool(re.search(r'"is_pinned"\s*:\s*true', html, re.IGNORECASE))
        }

def parse_profile_page(html, soup, url, category, index):
    main_area = soup.find(attrs={"role": "main"}) or soup
    
    full_name = ExtractorHelper.get_meta(soup, "og:title")
    if not full_name:
        for h1 in main_area.find_all('h1'):
            text = h1.get_text(strip=True)
            if text and text.lower() not in ['notifications', 'facebook', 'search', 'home', 'menu']:
                full_name = text
                break

    follower_count = ExtractorHelper.get_primary_metric([
        r'([\d,.]+[a-zA-Z]*)\s*followers?', 
        r'"follower_count"\s*:\s*(\d+)'
    ], html)
    
    following_count = ExtractorHelper.get_primary_metric([
        r'([\d,.]+[a-zA-Z]*)\s*following', 
        r'"following_count"\s*:\s*(\d+)'
    ], html)

    pic_url = None
    for img_tag in main_area.find_all('image'):
        href = img_tag.get('xlink:href') or img_tag.get('href')
        if href and 'fbcdn' in href:
            if '40x40' not in href and 'pico' not in href:
                pic_url = href
                break
                
    if not pic_url:
        pic_url = ExtractorHelper.get_meta(soup, "og:image")
        
    if pic_url:
        pic_url = re.sub(r's\d+x\d+(_tt\d+)?/', '', pic_url) 
        pic_url = re.sub(r'p\d+x\d+/', '', pic_url)

    profile_category = None
    known_categories = {
        "digital creator", "tv programme", "gaming video creator", "page", "public figure", 
        "video creator", "comedian", "actor", "entertainment website", 
        "musician", "band", "personal blog", "product", "service", "artist", 
        "entrepreneur", "creator", "media", "news company", "brand"
    }
    
    for el in main_area.find_all(['span', 'div', 'a'], attrs={'role': ['button', 'link']}):
        text = el.get_text(strip=True)
        if text.lower() in known_categories:
            profile_category = text.title()
            break

    bio_text = ""
    valid_blocks = ExtractorHelper.extract_text_blocks(soup, exclude_strings=[full_name, profile_category])
    
    for block in valid_blocks:
        block_lower = block.lower()
        if re.search(r'followers?|following|likes?', block_lower): continue
        if len(block) > 5:
            bio_text = block
            break

    if not bio_text:
        bio_text = ExtractorHelper.get_meta(soup, "og:description") or ""
        if "is on Facebook" in bio_text or "Join Facebook to connect" in bio_text:
            bio_text = ""

    return {
        "post_index": index, "scraped_url": url, "facebook_category": category, "status": "success",
        "metadata": {
            "typename": "GraphProfile",
            "username": url.split('facebook.com/')[-1].split('?')[0].strip('/'),
            "full_name": full_name or "",
            "category": profile_category,
            "follower_count": follower_count, "following_count": following_count,
            "profile_picture_url": pic_url, 
            "bio_text": bio_text,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }

def parse_reel(html, soup, url, category, index):
    owner = None
    title_text = soup.title.string if soup.title else ""
    title_parts = [p.strip() for p in title_text.replace(" | Facebook", "").replace("Facebook", "").split("-")]
    if len(title_parts) >= 2: owner = title_parts[0]

    if not owner:
        for a in soup.find_all('a', href=True):
            if 'facebook.com/' in a['href'] and '/reel/' not in a['href']:
                if a.parent.get('role') != 'button': 
                    m = re.search(r'facebook\.com/([^/?]+)', a['href'])
                    if m: 
                        owner = m.group(1)
                        break
                    
    caption, description = ExtractorHelper.get_clean_text(html, soup, owner)
    
    if not caption:
        valid_blocks = ExtractorHelper.extract_text_blocks(soup, exclude_strings=[owner])
        if len(valid_blocks) > 0: 
            caption = valid_blocks.pop(0)

    shortcode_match = re.search(r'(pfbid[a-zA-Z0-9]+)', url) or re.search(r'/(\d+)/?$', url)
    media_id = shortcode_match.group(1) if shortcode_match else None

    video_url, thumbnail_url = ExtractorHelper.get_media_elements(html, soup, True, media_id)
    context = ExtractorHelper.get_post_context(html)
    
    likes, comments = ExtractorHelper.get_metrics(soup, html, media_id)
    
    comment_data = ExtractorHelper.extract_comments(html)
    if description in comment_data: comment_data.remove(description)

    return {
        "post_index": index, "post_url": url, "facebook_category": category, "status": "success",
        "metadata": {
            "media_id": media_id or "UNKNOWN_ID",
            "typename": "GraphVideo", "is_video": True,
            "video_url": video_url, "thumbnail_url": thumbnail_url,
            "like_count": likes,
            "comment_count": comments,
            "relevant_comments": comment_data,
            "caption": caption or "", "description": description or "", "caption_hashtags": re.findall(r"#(\w+)", str(description or caption)),
            "owner_username": owner, 
            **context,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }

def parse_post_or_video(html, soup, url, category, index):
    owner_name = ""
    og_title = ExtractorHelper.get_meta(soup, "og:title") or ""
    
    if "views" in og_title and "|" in og_title:
        parts = [p.strip() for p in og_title.split("|")]
        if len(parts) >= 2:
            owner_name = parts[-1]

    caption, description = ExtractorHelper.get_clean_text(html, soup, owner_name)
    
    if not caption:
        main_area = soup.find(attrs={"role": "main"}) or soup
        for h1 in main_area.find_all('h1'):
            text = h1.get_text(strip=True)
            if text and not ExtractorHelper.is_auto_generated(text):
                caption = text
                break
        
        if not caption:
            valid_blocks = ExtractorHelper.extract_text_blocks(soup, exclude_strings=[owner_name])
            if len(valid_blocks) > 0:
                caption = valid_blocks[0]

    is_video = any(x in url for x in ['/watch/', '/videos/'])
    shortcode_match = re.search(r'(pfbid[a-zA-Z0-9]+)', url) or re.search(r'/(\d+)/?$', url)
    media_id = shortcode_match.group(1) if shortcode_match else None

    video_url, thumbnail_url = ExtractorHelper.get_media_elements(html, soup, is_video, media_id)
    context = ExtractorHelper.get_post_context(html)

    likes, comments = ExtractorHelper.get_metrics(soup, html, media_id)

    comment_data = ExtractorHelper.extract_comments(html)
    if description in comment_data: comment_data.remove(description)
    if caption in comment_data: comment_data.remove(caption)

    return {
        "post_index": index, 
        "post_url": url, 
        "facebook_category": category, 
        "status": "success",
        "metadata": {
            "media_id": media_id or "UNKNOWN_ID",
            "typename": "GraphVideo" if is_video else "GraphImage",
            "is_video": is_video, 
            "video_url": video_url, 
            "thumbnail_url": thumbnail_url,
            "like_count": likes,
            "comment_count": comments,
            "relevant_comments": comment_data,
            "caption": caption or "", 
            "description": description or "", 
            "caption_hashtags": re.findall(r"#(\w+)", str(description or caption)),
            "owner_username": owner_name or (url.split('/')[3] if len(url.split('/')) > 3 else None),
            **context,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }


def parse_facebook_url(html_content, target_url, category, post_index):
    soup = BeautifulSoup(html_content, 'html.parser')
    
    if "/reel/" in target_url: url_type = "REEL"
    elif any(x in target_url for x in ['/watch/', '/videos/', '/posts/', '/photo/', '/story.php']): url_type = "POST_OR_VIDEO"
    elif "/groups/" in target_url: url_type = "GROUP"
    else: url_type = "PROFILE"

    switch = {
        "PROFILE": parse_profile_page,
        "GROUP": parse_profile_page,
        "REEL": parse_reel,
        "POST_OR_VIDEO": parse_post_or_video
    }

    return switch.get(url_type, parse_post_or_video)(html_content, soup, target_url, category, post_index)


def scrape_visited_history(input_json, output_json, limit=None):
    script_start_time = time.time()
    
    with open(input_json, "r", encoding="utf-8") as f: history_data = json.load(f)

    targets = [{"uri": r.get("uri") or r.get("url"), "category": r.get("category", "Unknown")} 
               for r in (history_data.get("visits", []) or history_data.get("views", [])) if r.get("uri") or r.get("url")]

    scraped_data, scraped_urls = [], set()
    attempt_count, success_count, fail_count = 0, 0, 0
    
    if os.path.exists(output_json):
        try:
            with open(output_json, "r", encoding="utf-8") as f:
                existing_output = json.load(f)
                if "results" in existing_output:
                    scraped_data = existing_output.get("results", [])
                    old_summary = existing_output.get("run_summary", {})
                    attempt_count, success_count, fail_count = old_summary.get("total_posts_attempted", 0), old_summary.get("successfully_fetched", 0), old_summary.get("failed_to_fetch", 0)
                else: scraped_data = existing_output if isinstance(existing_output, list) else []
                scraped_urls = {item.get("post_url", item.get("scraped_url")) for item in scraped_data}
        except json.JSONDecodeError: pass

    remaining_targets = [t for t in targets if t["uri"] not in scraped_urls]
    if limit and limit > 0: remaining_targets = remaining_targets[:limit]

    if not remaining_targets:
        print("All URLs scraped. Exiting.")
        return

    chrome_options = Options()
    chrome_options.add_argument(f"user-data-dir={os.path.join(os.getcwd(), 'ChromeScraperProfile')}")
    for arg in ["--disable-gpu", "--no-sandbox", "--disable-dev-shm-usage", "--headless=new", "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"]:
        chrome_options.add_argument(arg)

    print(f"Launching scraper for {len(remaining_targets)} URLs...")
    driver = webdriver.Chrome(options=chrome_options)

    def save_state():
        elapsed_time = time.time() - script_start_time
        is_logged_in = True
        try:
            if "login_form" in driver.page_source or "/login/" in driver.current_url: is_logged_in = False
        except Exception: pass 
            
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump({
                "run_summary": {
                    "input_file": os.path.abspath(input_json), "output_file": os.path.abspath(output_json),
                    "total_posts_in_file": len(targets), "scrape_limit_applied": limit,
                    "authenticated": is_logged_in, "total_posts_attempted": attempt_count,
                    "successfully_fetched": success_count, "failed_to_fetch": fail_count,
                    "success_rate_percent": round((success_count / attempt_count * 100), 2) if attempt_count > 0 else 0.0,
                    "total_time_seconds": round(elapsed_time, 3), "total_time_human": f"{int(elapsed_time // 60)}m {int(elapsed_time % 60):02d}s",
                    "completed_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                },
                "results": scraped_data
            }, f, indent=2, ensure_ascii=False)

    try:
        for index, target in enumerate(remaining_targets, 1):
            url, category = target["uri"], target["category"]
            global_index = len(scraped_data) + 1
            print(f"[{index}/{len(remaining_targets)}] {category} -> {url}")
            attempt_count += 1
            
            try:
                driver.get(url)
                time.sleep(9)
                post_data = parse_facebook_url(driver.page_source, url, category, global_index)
                scraped_data.append(post_data)
                success_count += 1
                save_state()
                if index < len(remaining_targets): time.sleep(random.uniform(6.0, 15.0))
            except Exception as e:
                fail_count += 1
                scraped_data.append({"post_index": global_index, "post_url": url, "status": "error", "error_message": str(e)})
                save_state()
    finally:
        driver.quit()
        print(f"\nProgress safely stored in '{output_json}'!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", default="viewed_formatted.json")
    parser.add_argument("-o", "--output", default="scraped_viewed_metadata.json")
    parser.add_argument("-l", "--limit", type=int, default=None)
    args = parser.parse_args()
    scrape_visited_history(args.input, args.output, args.limit)