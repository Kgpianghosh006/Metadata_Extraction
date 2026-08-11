import os, json, re, time, random, argparse, csv
from datetime import datetime, timezone
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait

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
        clean_html = str(html_text).replace('\n', '')
        for p in patterns:
            for match in re.findall(p, clean_html, re.I):
                val = ExtractorHelper.parse_int(match if isinstance(match, str) else match[0])
                if val is not None: 
                    return val
        return None

    @staticmethod
    def is_auto_generated(text, owner_name=""):
        if not text: return True
        text_lower = text.lower()
        if owner_name and owner_name.lower() in text_lower and "posted" in text_lower: return True
        if re.search(r"posted an? (episode|video)|explore more in video", text_lower): return True
        if re.search(r"\d+[,.]?\d*\s*(likes?|followers?)\s*[·•]\s*\d", text_lower): return True
        if owner_name and text_lower.strip() == owner_name.lower().strip(): return True
        # Note: "'s post" pattern intentionally NOT filtered here
        return False

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
            if ('·' in text or '•' in text) and len(text) < 60: continue
            
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
                    
                    if role in ['button', 'link', 'navigation', 'tab', 'toolbar', 'dialog', 'textbox', 'search', 'form', 'combobox', 'menu', 'banner'] or tag_name in ['a', 'button', 'nav', 'form', 'input', 'textarea']:
                        is_ui_or_comment = True
                        break
                    
                    if role == 'article' or aria.startswith('comment from') or aria == 'comment':
                        is_ui_or_comment = True
                        break
                        
                    if role in ['main', 'complementary', 'dialog']: break
                    p = p.parent
                else: break
            
            if not is_ui_or_comment and text not in valid_blocks:
                valid_blocks.append(text)
                
        return valid_blocks

    @staticmethod
    def get_facebook_ids(url, html):
        # Use ordered lists per priority tier to preserve ranking
        tier1 = []  # direct post/story numeric IDs from JSON (most reliable)
        tier2 = []  # pfbid / storyID (URL or JSON encoded)
        tier3 = []  # numeric IDs decoded from Base64 storyIDs (least reliable — may include owner ID)
        
        # 1. Grab the ID or pfbid directly from the URL parameters
        shortcode_match = re.search(r'(pfbid[a-zA-Z0-9]+)', url) or re.search(r'/(\d+)/?$', url)
        if shortcode_match:
            val = shortcode_match.group(1)
            (tier1 if re.match(r'^\d{10,}$', val) else tier2).append(val)
            
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(url).query)
        # `id=` in permalink.php is the page-owner's ID, not the post ID — exclude it
        for param in ["story_fbid", "fbid", "v"]:
            if qs.get(param): tier1.append(qs.get(param)[0])
        if qs.get("set"): 
            set_match = re.search(r'a\.(\d+)', qs.get("set")[0])
            if set_match: tier1.append(set_match.group(1))
            
        # 2. Grab the numeric IDs from tracking HTML — these are post-specific
        for p in [r'"top_level_post_id"\s*:\s*"(\d+)"', r'"post_id"\s*:\s*"(\d+)"',
                  r'"story_fbid"\s*:\s*\[?"(\d+)"', r'"target_id"\s*:\s*(\d+)']:
            m = re.search(p, html)
            if m and m.group(1) not in tier1:
                tier1.append(m.group(1))

        url_pfbid = next((t for t in tier2 if str(t).startswith('pfbid')), None)
        if url_pfbid:
            try:
                import base64
                padding = "=" * ((4 - len(url_pfbid) % 4) % 4)
                decoded = base64.b64decode(url_pfbid + padding).decode('utf-8', errors='ignore')
                for num in re.findall(r'\d{10,}', decoded):
                    if num not in tier1 and num not in tier3:
                        tier3.append(num)
            except Exception:
                pass
                
        seen = set()
        result = []
        for tid in tier1 + tier2 + tier3:
            if tid not in seen:
                seen.add(tid)
                result.append(tid)
        return result

    @staticmethod
    def pick_best_media_id(target_ids):
        if not target_ids:
            return "UNKNOWN_ID"
        # First preference: pure numeric IDs — these come first in the ordered list
        for tid in target_ids:
            if re.match(r'^\d{10,}$', str(tid)):
                return str(tid)
        # Second preference: Base64-encoded IDs (UzpfS...)
        for tid in target_ids:
            if str(tid).startswith('Uzpf'):
                return str(tid)
        # Fallback: first ID
        return str(target_ids[0])

    @staticmethod
    def get_search_area(soup):
        dialog = soup.find(attrs={"role": "dialog"})
        if dialog: return dialog
        
        main = soup.find(attrs={"role": "main"})
        if main: return main
        
        return soup

    @staticmethod
    def isolate_post_node(obj, target_ids):
        import base64 as _b64
        valid_targets = [str(tid) for tid in target_ids if tid]
        if not valid_targets: return {}

        # Primary story typenames → +3 boost; sidebar containers → -2 penalty.
        # Ensures the real Story node beats sidebar reference nodes that merely
        # contain the target ID as a recommendation link.
        _PRIMARY_TYPENAMES = {
            "Story", "Video", "Photo", "Reel",
            "CometReelsVideo", "CometShowcaseVideo", "CometFeedUnit",
            "CometUFIReactionsAndCommentsCount", "CometUFIComments",
            "GraphVideo", "GraphImage",
        }
        _SIDEBAR_TYPENAMES = {
            "StoryCard", "RecommendedStories", "CometRightColumn",
            "SidebarSection", "RecommendedContent", "AdUnit",
            "CometNewsfeedEdge",
        }

        # (composite_score, direct_score, node)
        scored_nodes = []

        def _decode_b64_id(node_id):
            if not node_id or len(node_id) < 8: return []
            try:
                padding = "=" * ((4 - len(node_id) % 4) % 4)
                decoded = _b64.b64decode(node_id + padding).decode('utf-8', errors='ignore')
                return re.findall(r'\d{10,}', decoded)
            except Exception:
                return []

        def traverse(node):
            if isinstance(node, dict):
                node_id = str(node.get("id", ""))
                node_post_id = str(node.get("post_id", ""))
                node_legacy_id = str(node.get("legacy_fbid", ""))
                node_typename = node.get("__typename", "")

                # Score this node's OWN identity fields — not its children.
                direct_score = 0
                for t in valid_targets:
                    if t and (t == node_id or t == node_post_id or t == node_legacy_id):
                        direct_score = 2
                        break
                if not direct_score:
                    for t in valid_targets:
                        if t and (t in node_id or t in node_post_id or t in node_legacy_id):
                            direct_score = 1
                            break
                if not direct_score and node_id:
                    for num in _decode_b64_id(node_id):
                        if any(t == num for t in valid_targets):
                            direct_score = 1
                            break

                child_has_target = False
                for k, v in node.items():
                    if traverse(v):
                        child_has_target = True

                contains_target = bool(direct_score) or child_has_target

                if contains_target:
                    is_valid = any(key in node for key in [
                        "message", "feedback", "comments", "reaction_count", "comment_count",
                        "likers", "unified_reactors", "total_comment_count"
                    ])
                    if not is_valid and node_typename in _PRIMARY_TYPENAMES:
                        is_valid = True

                    if is_valid:
                        if node_typename in _PRIMARY_TYPENAMES: typename_boost = 3
                        elif node_typename in _SIDEBAR_TYPENAMES: typename_boost = -2
                        else: typename_boost = 0
                        composite = direct_score + typename_boost
                        scored_nodes.append((composite, direct_score, node))

                return contains_target

            elif isinstance(node, list):
                child_has_target = False
                for item in node:
                    if traverse(item): child_has_target = True
                return child_has_target

            elif isinstance(node, str) and any(t in node for t in valid_targets):
                return True
            elif isinstance(node, (int, float)) and any(str(node) == t for t in valid_targets):
                return True

            return False

        traverse(obj)
        if not scored_nodes: return {}
        best_composite = max(c for c, _, _ in scored_nodes)
        best_direct    = max(d for c, d, _ in scored_nodes if c == best_composite)
        best_nodes     = [n for c, d, n in scored_nodes if c == best_composite and d == best_direct]
        return best_nodes[-1]

    _CAPTION_SENTINELS = {
        "notifications", "facebook", "log in or sign up", "home", "menu",
        "watch", "reels", "groups", "marketplace", "search",
    }

    @staticmethod
    def get_clean_text(html_text, soup, owner_name="", target_ids=None):
        og_title = ExtractorHelper.get_meta(soup, "og:title") or ""
        og_desc  = ExtractorHelper.get_meta(soup, "og:description") or ""
        browser_title = soup.title.string if soup.title else ""
        caption, description = None, None

        if og_title:
            if "views" in og_title and "|" in og_title:
                parts = [p.strip() for p in og_title.split("|")]
                if len(parts) >= 3: caption = " | ".join(parts[1:-1])
                elif len(parts) == 2: caption = parts[1]
            elif "|" in og_title:
                parts = [p.strip() for p in og_title.split("|")]
                candidate = parts[0] if len(parts) >= 2 else og_title
                if candidate and candidate.lower() != (owner_name or "").lower():
                    caption = candidate
            elif og_title and og_title.strip().lower() != (owner_name or "").lower():
                caption = og_title
        elif browser_title:
            _bt = re.sub(r'^\(\d+\)\s*', '', browser_title).strip()
            _bt = re.sub(r'\s*\|\s*Facebook\s*$', '', _bt, flags=re.I).strip()
            if " - " in _bt:
                _candidate = re.sub(r'\s*\.\.\.\s*$', '', _bt.split(" - ", 1)[1]).strip()
                if _candidate and _candidate.lower() not in ExtractorHelper._CAPTION_SENTINELS:
                    if not ExtractorHelper.is_auto_generated(_candidate, owner_name):
                        caption = _candidate

        if caption and caption.strip().lower() in ExtractorHelper._CAPTION_SENTINELS:
            caption = None

        og_desc_truncated = og_desc.rstrip().endswith("...")
        description = og_desc if not og_desc_truncated else None

        if ExtractorHelper.is_auto_generated(caption, owner_name):     caption = None
        if ExtractorHelper.is_auto_generated(description, owner_name): description = None

        valid_targets = target_ids or []
        needs_json_desc = (not description) or og_desc_truncated
        if (not caption or needs_json_desc) and valid_targets:
            def hunt_text(obj):
                nonlocal caption, description
                if isinstance(obj, dict):
                    if "title" in obj and isinstance(obj["title"], dict) and "text" in obj["title"]:
                        t = obj["title"]["text"]
                        if not caption and not ExtractorHelper.is_auto_generated(t, owner_name): caption = t
                    if "message" in obj and isinstance(obj["message"], dict) and "text" in obj["message"]:
                        d = obj["message"]["text"]
                        if d and not ExtractorHelper.is_auto_generated(d, owner_name):
                            if needs_json_desc or not description: description = d
                            if not caption: caption = d
                    for v in obj.values(): hunt_text(v)
                elif isinstance(obj, list):
                    for item in obj: hunt_text(item)

            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    target_node = ExtractorHelper.isolate_post_node(data, valid_targets)
                    hunt_text(target_node)
                    if caption and description: break
                except Exception: continue

        if not description and og_desc and not ExtractorHelper.is_auto_generated(og_desc, owner_name):
            description = og_desc

        return caption or "", description or ""

    @staticmethod
    def get_metrics(soup, html, target_ids=None):
        likes, comments = None, None
        valid_targets = target_ids or []

        def hunt_metrics(obj, primary_only=False):
            nonlocal likes, comments
            if isinstance(obj, dict):
                if likes is None:
                    if "reaction_count" in obj and isinstance(obj["reaction_count"], dict):
                        likes = ExtractorHelper.parse_int(str(obj["reaction_count"].get("count", "")))
                    elif "i18n_reaction_count" in obj:
                        likes = ExtractorHelper.parse_int(str(obj["i18n_reaction_count"]))
                    elif not primary_only:
                        if "likers" in obj and isinstance(obj["likers"], dict):
                            likes = ExtractorHelper.parse_int(str(obj["likers"].get("count", "")))
                        elif "unified_reactors" in obj and isinstance(obj["unified_reactors"], dict):
                            likes = ExtractorHelper.parse_int(str(obj["unified_reactors"].get("count", "")))

                if comments is None:
                    if "comment_count" in obj and isinstance(obj["comment_count"], dict):
                        comments = ExtractorHelper.parse_int(str(obj["comment_count"].get("total_count", "")))
                    elif "comments" in obj and isinstance(obj["comments"], dict):
                        comments = ExtractorHelper.parse_int(str(obj["comments"].get("total_count", "")))
                    elif "i18n_comment_count" in obj:
                        comments = ExtractorHelper.parse_int(str(obj["i18n_comment_count"]))
                    elif not primary_only:
                        if "total_comment_count" in obj:
                            comments = ExtractorHelper.parse_int(str(obj["total_comment_count"]))
                        elif "feedback" in obj and isinstance(obj["feedback"], dict):
                            tc = obj["feedback"].get("total_comment_count")
                            if tc is not None:
                                comments = ExtractorHelper.parse_int(str(tc))

                for v in obj.values(): hunt_metrics(v, primary_only)
            elif isinstance(obj, list):
                for item in obj: hunt_metrics(item, primary_only)

        isolated_node = {}
        if valid_targets:
            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    target_node = ExtractorHelper.isolate_post_node(data, valid_targets)
                    if target_node: isolated_node = target_node 
                    hunt_metrics(target_node, primary_only=True)
                    if likes is not None and likes > 0 and comments is not None: break
                except Exception: continue

        needs_likes = likes is None or likes == 0
        needs_comments = comments is None or comments == 0
        if (needs_likes or needs_comments) and isolated_node:
            old_l, old_c = likes, comments
            if needs_likes: likes = None
            if needs_comments: comments = None
            hunt_metrics(isolated_node, primary_only=False)
            if needs_likes and (likes is None or likes == 0): likes = old_l
            if needs_comments and (comments is None or comments == 0): comments = old_c

        needs_likes = likes is None or likes == 0
        needs_comments = comments is None or comments == 0
        if (needs_likes or needs_comments) and valid_targets:
            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    old_likes, old_comments = likes, comments
                    if needs_likes: likes = None
                    if needs_comments: comments = None
                    hunt_metrics(data, primary_only=True)
                    if needs_likes and (likes is None or likes == 0): likes = old_likes
                    if needs_comments and (comments is None or comments == 0): comments = old_comments
                    needs_likes = likes is None or likes == 0
                    needs_comments = comments is None or comments == 0
                    if not needs_likes and not needs_comments: break
                except Exception: continue

        needs_likes = likes is None or likes == 0
        needs_comments = comments is None or comments == 0
        if (needs_likes or needs_comments) and valid_targets:
            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    old_likes, old_comments = likes, comments
                    if needs_likes: likes = None
                    if needs_comments: comments = None
                    hunt_metrics(data, primary_only=False)
                    if needs_likes and (likes is None or likes == 0): likes = old_likes
                    if needs_comments and (comments is None or comments == 0): comments = old_comments
                    needs_likes = likes is None or likes == 0
                    needs_comments = comments is None or comments == 0
                    if not needs_likes and not needs_comments: break
                except Exception: continue

        if likes is None or likes == 0:
            likes = ExtractorHelper.get_primary_metric([
                r'aria-label="([\d,.]+[a-zA-Z]*)\s*likes?"',
                r'aria-label="([\d,.]+[a-zA-Z]*)\s*reactions?"',
                r'"reaction_count"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
                r'"i18n_reaction_count"\s*:\s*"([\d,.]+[a-zA-Z]*)"',
                r'"likers"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
                r'"unified_reactors"\s*:\s*\{\s*"count"\s*:\s*(\d+)'
            ], html)

        if comments is None or comments == 0:
            comments = ExtractorHelper.get_primary_metric([
                r'aria-label="([\d,.]+[a-zA-Z]*)\s*comments?"',
                r'"comment_count"\s*:\s*\{\s*"total_count"\s*:\s*(\d+)',
                r'"i18n_comment_count"\s*:\s*"([\d,.]+[a-zA-Z]*)"',
                r'"total_comment_count"\s*:\s*(\d+)'
            ], html)

        return likes, comments

    @staticmethod
    def extract_comments(html_text, target_ids=None, limit=6):
        extracted = []        
        soup = BeautifulSoup(html_text, 'html.parser')
        valid_targets = target_ids or []

        if not valid_targets: return extracted

        def hunt(obj):
            if isinstance(obj, dict):
                if obj.get("__typename") == "Comment":
                    body = obj.get("body", {})                    
                    txt = body.get("text") if isinstance(body, dict) else (body if isinstance(body, str) else None)
                    if txt and txt not in extracted: extracted.append(txt)
                for v in obj.values(): hunt(v)
            elif isinstance(obj, list):
                for item in obj: hunt(item)

        for script in soup.find_all("script", type=["application/json"]):
            text = script.string
            if not text or not any(t in text for t in valid_targets): continue
            try:
                data = json.loads(text)
                target_node = ExtractorHelper.isolate_post_node(data, valid_targets)
                hunt(target_node)
                if len(extracted) >= limit: break
            except Exception: continue

        return extracted[:limit]

    @staticmethod
    def get_media_elements(html, soup, is_video, target_ids=None):
        video_url = ExtractorHelper.get_meta(soup, "og:video") or ExtractorHelper.get_meta(soup, "og:video:url")
        thumbnail_url = None
        valid_targets = target_ids or []
        search_area = ExtractorHelper.get_search_area(soup)

        if valid_targets:
            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    target_node = ExtractorHelper.isolate_post_node(data, valid_targets)
                    
                    def hunt_media(obj):
                        nonlocal video_url, thumbnail_url
                        if isinstance(obj, dict):
                            if not thumbnail_url and "photo_image" in obj and isinstance(obj["photo_image"], dict) and "uri" in obj["photo_image"]:
                                thumbnail_url = obj["photo_image"]["uri"]
                            if not thumbnail_url and "image" in obj and isinstance(obj["image"], dict) and "uri" in obj["image"]:
                                thumbnail_url = obj["image"]["uri"]
                            if is_video and not video_url:
                                for k in ['browser_native_hd_url', 'playable_url_quality_hd', 'browser_native_sd_url', 'playable_url']:
                                    if k in obj and obj[k]:
                                        video_url = obj[k]
                                        break
                            for v in obj.values(): hunt_media(v)
                        elif isinstance(obj, list):
                            for item in obj: hunt_media(item)
                            
                    hunt_media(target_node)
                    if thumbnail_url or video_url: break
                except Exception: continue

        if is_video and not video_url:
            keys = ['browser_native_hd_url', 'playable_url_quality_hd', 'browser_native_sd_url', 'playable_url']
            for key in keys:
                if video_url: break
                matches = re.findall(rf'{key}[\\"]*\s*:\s*[\\"]*(https:[^"\'<>\s]+)', html)
                for match in matches:
                    clean_url = match.replace('\\/', '/').replace('\\\\', '')
                    if ('fbcdn' in clean_url or 'akamai' in clean_url) and 'static.xx' not in clean_url and 'emoji.php' not in clean_url:
                        video_url = clean_url
                        break

        if not thumbnail_url:
            feed_img = search_area.find('img', attrs={'data-imgperflogname': 'feedImage'})
            if feed_img: thumbnail_url = feed_img.get('src')

        if is_video and not video_url:
            video_tag = search_area.find('video')
            if video_tag:
                temp_src = video_tag.get('src')
                if temp_src and not temp_src.startswith('blob:'):
                    if not video_url: video_url = temp_src
                if not thumbnail_url: thumbnail_url = video_tag.get('poster')

        if not thumbnail_url:
            og_img = ExtractorHelper.get_meta(soup, "og:image") or ""
            if og_img and 't1.30497' not in og_img and 'emoji.php' not in og_img and 'static.xx' not in og_img:
                thumbnail_url = og_img

        return video_url, thumbnail_url

    @staticmethod
    def get_post_context(soup, html, target_ids=None):
        posted_at_unix = None
        location = None
        sidecar_count = 0
        is_pinned = False
        valid_targets = target_ids or []

        def hunt_context(obj):
            nonlocal posted_at_unix, location, sidecar_count, is_pinned
            if isinstance(obj, dict):
                if not posted_at_unix:
                    for key in ["publish_time", "creation_time", "story_publish_time"]:
                        if key in obj and isinstance(obj[key], (int, float)):
                            posted_at_unix = int(obj[key])
                            break
                if not location:
                    if "place" in obj and isinstance(obj["place"], dict) and "name" in obj["place"]:
                        location = obj["place"]["name"]
                    elif "checkin_info" in obj and isinstance(obj["checkin_info"], dict) and "name" in obj["checkin_info"]:
                        location = obj["checkin_info"]["name"]
                if not sidecar_count:
                    if "subattachments" in obj and isinstance(obj["subattachments"], dict):
                        sidecar_count = int(obj["subattachments"].get("count", 0))
                    elif "album" in obj and isinstance(obj["album"], dict):
                        sidecar_count = int(obj["album"].get("media_count", 0))
                if not is_pinned and "is_pinned" in obj:
                    is_pinned = bool(obj["is_pinned"])
                
                for v in obj.values(): hunt_context(v)
            elif isinstance(obj, list):
                for item in obj: hunt_context(item)

        if valid_targets:
            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    target_node = ExtractorHelper.isolate_post_node(data, valid_targets)
                    hunt_context(target_node)
                    if posted_at_unix: break
                except Exception: continue

        return {
            "posted_at_unix": posted_at_unix,
            "posted_at_utc": datetime.fromtimestamp(posted_at_unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if posted_at_unix else None,
            "location": location,
            "sidecar_count": sidecar_count,
            "is_pinned": is_pinned or bool(re.search(r'"is_pinned"\s*:\s*true', html, re.IGNORECASE))
        }

    @staticmethod
    def get_accessibility_caption(soup, html, thumbnail_url=None, target_ids=None):
        search_area = ExtractorHelper.get_search_area(soup)
        valid_targets = target_ids or []
        
        if thumbnail_url:
            for img in search_area.find_all('img'):
                src = img.get('src', '')
                alt = img.get('alt', '')
                if alt and src == thumbnail_url: return alt
                if alt and ('fbcdn' in src or 'akamai' in src) and 't1.30497' not in src and 'static.xx' not in src and 'emoji.php' not in src:
                    clean_thumb = thumbnail_url.split('?')[0].split('/')[-1]
                    clean_src  = src.split('?')[0].split('/')[-1]
                    if clean_thumb and clean_thumb == clean_src: return alt

        if valid_targets:
            for script in soup.find_all("script", type=["application/json"]):
                text = script.string
                if not text or "accessibility_caption" not in text: continue
                if not any(t in text for t in valid_targets): continue
                try:
                    data = json.loads(text)
                    def find_acc_cap(obj, depth=0):
                        if depth > 40: return None
                        if isinstance(obj, dict):
                            if obj.get("accessibility_caption") and isinstance(obj["accessibility_caption"], str):
                                return obj["accessibility_caption"]
                            for v in obj.values():
                                r = find_acc_cap(v, depth + 1)
                                if r: return r
                        elif isinstance(obj, list):
                            for item in obj:
                                r = find_acc_cap(item, depth + 1)
                                if r: return r
                        return None
                    cap = find_acc_cap(data)
                    if cap: return cap
                except Exception: continue

        return None

    @staticmethod
    def get_hashtags(caption, description, soup):
        text_source = str(description or caption or "")
        tags = re.findall(r"#(\w+)", text_source)
        if not tags:
            seen = set()
            search_area = ExtractorHelper.get_search_area(soup)
            for a in search_area.find_all("a", href=re.compile(r"facebook\.com/hashtag/")):
                tag_text = a.get_text(strip=True).lstrip("#")
                if tag_text and tag_text not in seen:
                    tags.append(tag_text)
                    seen.add(tag_text)
        return tags

def parse_page(html, soup, url, category, index, input_name=""):
    import html as _html
    main_area = soup.find(attrs={"role": "main"}) or soup
    
    # Strip URL cleanly to get the exact username or ID
    raw_username = url.split('facebook.com/')[-1].split('?')[0].strip('/')
    page_username = raw_username.split('/')[0] if raw_username else ""
    
    full_name = ExtractorHelper.get_meta(soup, "og:title")
    if not full_name:
        for h1 in main_area.find_all('h1'):
            text = h1.get_text(separator=" ", strip=True)
            if text and text.lower() not in ['notifications', 'facebook', 'search', 'home', 'menu']:
                full_name = re.sub(r'(?i)\s*verified account\s*', '', text).strip()
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
    profile_category = None
    full_name_json = None
    cover_photo_url = None
    member_count_text = None

    # Walk JSON scripts for structured profile data 
    for script in soup.find_all("script", type=["application/json"]):
        text = script.string
        if not text: continue
        
        if "profile_picture" in text or "cover_photo" in text \
                or "group_member_profiles" in text or "category_name" in text \
                or "overall_category_name" in text:
            try:
                data = json.loads(text)
                
                def _hunt_profile_data(obj, depth=0):
                    nonlocal pic_url, profile_category, full_name_json, cover_photo_url, member_count_text
                    if depth > 40: return
                    if isinstance(obj, dict):
                        node_id = str(obj.get("id", ""))
                        node_uname = str(obj.get("username", ""))
                        
                        # Match node to URL's page identifier
                        is_target = page_username and page_username in (node_id, node_uname)
                        
                        if is_target:
                            if not full_name_json and obj.get("name"):
                                full_name_json = obj["name"]
                            
                            # Profile picture URI (usually tiny s50x50 — upgraded later)
                            if not pic_url and "profile_picture" in obj \
                                    and isinstance(obj["profile_picture"], dict):
                                uri = obj["profile_picture"].get("uri")
                                if uri and "fbcdn" in uri:
                                    pic_url = uri
                            
                            # Category from multiple possible field names
                            if not profile_category:
                                for cat_field in ["category_name", "overall_category_name",
                                                  "page_category", "category_display_name"]:
                                    if obj.get(cat_field) and isinstance(obj[cat_field], str):
                                        profile_category = obj[cat_field]
                                        break
                                if not profile_category and "page_categories" in obj:
                                    cats = obj["page_categories"]
                                    if isinstance(cats, list) and cats:
                                        first_cat = cats[0]
                                        if isinstance(first_cat, dict):
                                            profile_category = first_cat.get("name") or first_cat.get("category_name")
                                        elif isinstance(first_cat, str):
                                            profile_category = first_cat
                        
                        # Cover photo (groups/pages — checked on all nodes, not just target)
                        for key in ["cover_photo", "profile_cover_photo"]:
                            if not cover_photo_url and key in obj and isinstance(obj[key], dict):
                                photo = obj[key].get("photo", {})
                                if isinstance(photo, dict):
                                    img = photo.get("image", {})
                                    if isinstance(img, dict) and img.get("uri"):
                                        cover_photo_url = img["uri"]
                                if not cover_photo_url:
                                    img = obj[key].get("image", {})
                                    if isinstance(img, dict) and img.get("uri"):
                                        cover_photo_url = img["uri"]
                        
                        # Group member count
                        if not member_count_text and "group_member_profiles" in obj \
                                and isinstance(obj["group_member_profiles"], dict):
                            fct = obj["group_member_profiles"].get("formatted_count_text")
                            if fct:
                                member_count_text = fct

                        for v in obj.values(): _hunt_profile_data(v, depth + 1)
                    elif isinstance(obj, list):
                        for item in obj: _hunt_profile_data(item, depth + 1)

                _hunt_profile_data(data)
            except Exception: continue

    if full_name_json and not full_name:
        full_name = full_name_json

    def _pick_best_profile_pic(json_pic_url):
        if not json_pic_url:
            return None
        filename = json_pic_url.split('?')[0].split('/')[-1]
        if not filename:
            return None

        candidates = []
        seen_urls = set()

        def _add(url):
            if not url: return
            url = _html.unescape(url)
            if filename not in url or 'fbcdn' not in url: return
            if url in seen_urls: return
            seen_urls.add(url)
            m = re.search(r'ctp=s(\d+)x\d+', url)
            candidates.append((int(m.group(1)) if m else 0, url))

        _add(ExtractorHelper.get_meta(soup, "og:image"))
        for img in main_area.find_all('image'):
            _add(img.get('xlink:href') or img.get('href', ''))
        for img in main_area.find_all('img'):
            _add(img.get('src', ''))
        for m in re.finditer(
            r'https://[^\s"\'\\]+' + re.escape(filename) + r'[^\s"\'\\]*', html
        ):
            _add(m.group(0))

        if not candidates:
            return json_pic_url
        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    if pic_url:
        pic_url = _pick_best_profile_pic(pic_url) or pic_url
    if not pic_url:
        og_img = ExtractorHelper.get_meta(soup, "og:image")
        if og_img:
            pic_url = _html.unescape(og_img)
    if not pic_url and cover_photo_url:
        pic_url = cover_photo_url
    if pic_url:
        pic_url = _html.unescape(pic_url)

    bio_text = ""
    NAV_LABELS = {'posts', 'about', 'friends', 'photos', 'videos', 'reels',
                  'mentions', 'reviews', 'followers', 'following', 'groups',
                  'events', 'more', 'intro'}

    valid_blocks = ExtractorHelper.extract_text_blocks(
        soup, exclude_strings=[full_name, profile_category] + list(NAV_LABELS)
    )
    for block in valid_blocks:
        block_lower = block.lower()
        if re.search(r'followers?|following|likes?|verified account', block_lower): continue
        if full_name and full_name.lower() in block_lower and len(block_lower) < len(full_name) + 20: continue
        if len(block) > 4 and block_lower not in NAV_LABELS:
            bio_text = block
            break

    if not bio_text:
        bio_text = ExtractorHelper.get_meta(soup, "og:description") or ""
        if "is on Facebook" in bio_text or "Join Facebook to connect" in bio_text:
            bio_text = ""

    if bio_text:
        bio_lower = bio_text.strip().lower()
        if (full_name and bio_lower == full_name.strip().lower()) \
                or (profile_category and bio_lower == profile_category.strip().lower()) \
                or (full_name and len(bio_text.strip()) <= len(full_name.strip()) + 5 and full_name.strip().lower() in bio_lower):
            bio_text = ""

    # Remaining fallbacks
    if follower_count is None and member_count_text:
        follower_count = ExtractorHelper.parse_int(member_count_text.split()[0])

    if not profile_category:
        for m in re.finditer(r'"category_name"\s*:\s*"([^"]+)"', html):
            cat_val = m.group(1)
            if cat_val and cat_val not in ['null', 'undefined', 'GROUP', 'PERSON']:
                profile_category = cat_val
                break
        if not profile_category:
            for m in re.finditer(r'"overall_category_name"\s*:\s*"([^"]+)"', html):
                profile_category = m.group(1)
                break

    return {
        "post_index": index, "scraped_url": url, "facebook_category": category, "status": "success",
        "metadata": {
            "typename": "GraphProfile",
            "username": page_username,
            "full_name": full_name or "",
            "category": profile_category,
            "follower_count": follower_count, "following_count": following_count,
            "profile_picture_url": pic_url, 
            "bio_text": bio_text,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }


def parse_reel(html, soup, url, category, index, input_name=""):
    search_area = ExtractorHelper.get_search_area(soup)
    target_ids = ExtractorHelper.get_facebook_ids(url, html)
    
    owner = None
    profile_name_el = search_area.find(attrs={'data-ad-rendering-role': 'profile_name'})
    if profile_name_el:
        owner = re.sub(r'\s*verified account\s*', '', profile_name_el.get_text(separator=' ', strip=True), flags=re.I).strip() or None

    if not owner:
        h2_el = search_area.find('h2')
        if h2_el:
            candidate = re.sub(r'\s*verified account\s*', '', h2_el.get_text(separator=' ', strip=True), flags=re.I).strip()
            if candidate and candidate.lower() not in ('facebook', 'reels', 'video', ''): owner = candidate

    if not owner and target_ids:
        for script in soup.find_all("script", type=["application/json"]):
            text = script.string
            if not text or not any(t in text for t in target_ids): continue
            try:
                data = json.loads(text)
                def _hunt_reel_owner(obj, depth=0):
                    if depth > 30 or not isinstance(obj, (dict, list)): return None
                    if isinstance(obj, dict):
                        if obj.get('__typename') in ('User', 'Page', 'Group') and obj.get('name'): return obj['name']
                        if 'actors' in obj and isinstance(obj['actors'], list) and obj['actors']:
                            actor = obj['actors'][0]
                            if isinstance(actor, dict) and actor.get('name'): return actor['name']
                        for v in obj.values():
                            r = _hunt_reel_owner(v, depth + 1)
                            if r: return r
                    elif isinstance(obj, list):
                        for item in obj:
                            r = _hunt_reel_owner(item, depth + 1)
                            if r: return r
                    return None
                found = _hunt_reel_owner(data)
                if found:
                    owner = re.sub(r'\s*verified account\s*', '', found, flags=re.I).strip() or None
                    if owner: break
            except Exception: continue

    if not owner:
        og_title = ExtractorHelper.get_meta(soup, "og:title") or ""
        if og_title and not re.search(r'[|·•]', og_title): owner = og_title.strip() or None

    caption, description = ExtractorHelper.get_clean_text(html, soup, owner, target_ids)
    # input_name (from CSV) is always preferred as caption — it's the exact FB post heading.
    # The h2[dir=auto] is the fallback for when input_name is absent.
    if input_name:
        caption = input_name
    elif not caption:
        h2_el = search_area.find('h2', attrs={'dir': 'auto'})
        if h2_el:
            h2_text = h2_el.get_text(separator=' ', strip=True)
            if h2_text and h2_text.strip().lower() not in ExtractorHelper._CAPTION_SENTINELS:
                caption = h2_text
    if not caption:
        valid_blocks = ExtractorHelper.extract_text_blocks(soup, exclude_strings=[owner])
        if len(valid_blocks) > 0: caption = valid_blocks.pop(0)

    video_url, thumbnail_url = ExtractorHelper.get_media_elements(html, soup, True, target_ids)
    accessibility_caption = ExtractorHelper.get_accessibility_caption(soup, html, thumbnail_url, target_ids)
    context = ExtractorHelper.get_post_context(soup, html, target_ids)
    likes, comments = ExtractorHelper.get_metrics(soup, html, target_ids)
    
    comment_data = ExtractorHelper.extract_comments(html, target_ids)
    if description in comment_data: comment_data.remove(description)

    return {
        "post_index": index, "post_url": url, "facebook_category": category, "status": "success",
        "metadata": {
            "media_id": ExtractorHelper.pick_best_media_id(target_ids), "typename": "GraphVideo", "is_video": True,
            "video_url": video_url, "thumbnail_url": thumbnail_url, "accessibility_caption": accessibility_caption,
            "like_count": likes, "comment_count": comments, "relevant_comments": comment_data,
            "caption": caption or "", "description": description or "", "caption_hashtags": ExtractorHelper.get_hashtags(caption, description, soup),
            "owner_username": owner, **context,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }

def parse_post_or_video(html, soup, url, category, index, input_name=""):
    search_area = ExtractorHelper.get_search_area(soup)
    target_ids = ExtractorHelper.get_facebook_ids(url, html)
    owner_name = ""

    if target_ids:
        for script in soup.find_all("script", type=["application/json"]):
            text = script.string
            if not text or not any(t in text for t in target_ids): continue
            try:
                data = json.loads(text)
                target_node = ExtractorHelper.isolate_post_node(data, target_ids)
                def _hunt_owner(obj, depth=0):
                    if depth > 30 or not isinstance(obj, (dict, list)): return None
                    if isinstance(obj, dict):
                        if obj.get('__typename') in ('User', 'Page', 'Group') and obj.get('name'): return obj['name']
                        if 'actors' in obj and isinstance(obj['actors'], list) and obj['actors']:
                            actor = obj['actors'][0]
                            if isinstance(actor, dict) and actor.get('name'): return actor['name']
                        if 'owner' in obj and isinstance(obj['owner'], dict) and obj['owner'].get('name'): return obj['owner']['name']
                        for v in obj.values():
                            r = _hunt_owner(v, depth + 1)
                            if r: return r
                    elif isinstance(obj, list):
                        for item in obj:
                            r = _hunt_owner(item, depth + 1)
                            if r: return r
                    return None
                found = _hunt_owner(target_node)
                if found:
                    owner_name = re.sub(r'\s*verified account\s*', '', found, flags=re.I).strip()
                    if owner_name: break
            except Exception: continue

    if not owner_name:
        profile_name_el = search_area.find(attrs={'data-ad-rendering-role': 'profile_name'})
        if profile_name_el: owner_name = re.sub(r'\s*verified account\s*', '', profile_name_el.get_text(separator=' ', strip=True), flags=re.I).strip()

    if not owner_name:
        og_title = ExtractorHelper.get_meta(soup, "og:title") or ""
        if og_title and not re.search(r'[|·•]', og_title): owner_name = og_title.strip()

    is_video = any(x in url for x in ['/watch/', '/videos/'])
    caption, description = ExtractorHelper.get_clean_text(html, soup, owner_name, target_ids)

    # input_name (from CSV) is always preferred as caption — it's the exact FB post heading.
    # The h2[dir=auto] is the fallback for when input_name is absent.
    if input_name:
        caption = input_name
    elif not caption:
        h2_el = search_area.find('h2', attrs={'dir': 'auto'})
        if h2_el:
            h2_text = h2_el.get_text(separator=' ', strip=True)
            if h2_text and h2_text.strip().lower() not in ExtractorHelper._CAPTION_SENTINELS:
                caption = h2_text
    if not caption:
        for h1 in search_area.find_all('h1'):
            text = h1.get_text(separator=" ", strip=True)
            if text and not ExtractorHelper.is_auto_generated(text, owner_name):
                caption = text
                break

    video_url, thumbnail_url = ExtractorHelper.get_media_elements(html, soup, is_video, target_ids)
    accessibility_caption = ExtractorHelper.get_accessibility_caption(soup, html, thumbnail_url, target_ids)
    context = ExtractorHelper.get_post_context(soup, html, target_ids)
    likes, comments = ExtractorHelper.get_metrics(soup, html, target_ids)

    comment_data = ExtractorHelper.extract_comments(html, target_ids)
    if description in comment_data: comment_data.remove(description)
    if caption in comment_data: comment_data.remove(caption)

    return {
        "post_index": index, "post_url": url, "facebook_category": category, "status": "success",
        "metadata": {
            "media_id": ExtractorHelper.pick_best_media_id(target_ids),
            "typename": "GraphVideo" if is_video else "GraphImage",
            "is_video": is_video, "video_url": video_url, "thumbnail_url": thumbnail_url,
            "accessibility_caption": accessibility_caption, "like_count": likes, "comment_count": comments,
            "relevant_comments": comment_data, "caption": caption or "", "description": description or "", 
            "caption_hashtags": ExtractorHelper.get_hashtags(caption, description, soup),
            "owner_username": owner_name or (url.split('/')[3] if len(url.split('/')) > 3 else None),
            **context,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
    }

def parse_facebook_url(html_content, target_url, category, post_index, input_name=""):
    soup = BeautifulSoup(html_content, 'html.parser')

    BLOCKED_SIGNALS = [
        "this content isn't available", "this content is no longer available",
        "this page isn't available", "you must log in to continue",
    ]
    page_text_lower = soup.get_text(separator=" ", strip=True).lower()
    is_blocked = any(sig in page_text_lower for sig in BLOCKED_SIGNALS)
    
    og_title_val = ExtractorHelper.get_meta(soup, "og:title")
    page_title = (soup.title.string or "").strip()
    if not og_title_val and page_title.lower() in ("facebook", ""):
        is_blocked = True

    if is_blocked:
        reason = "Post is unavailable — it may be deleted, private, or geo-restricted."
        for sig in BLOCKED_SIGNALS:
            if sig in page_text_lower:
                reason = sig.capitalize() + "."
                break
        return {
            "post_index": post_index, "post_url": target_url, "facebook_category": category,
            "status": "blocked", "error_message": reason, "metadata": None,
            "fetched_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        }

    if "/reel/" in target_url:
        return parse_reel(html_content, soup, target_url, category, post_index, input_name)
    elif any(x in target_url for x in ['/watch/', '/videos/', '/posts/', '/photo/', '/story.php']) or ("permalink.php" in target_url and "story_fbid=" in target_url):
        return parse_post_or_video(html_content, soup, target_url, category, post_index, input_name)
    else:
        return parse_page(html_content, soup, target_url, category, post_index, input_name)

def scrape_visited_history(input_csv, output_json, limit=None):
    script_start_time = time.time()
    
    with open(input_csv, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        targets = [{"uri": r["url"], "category": r.get("category", "Unknown"), "name": r.get("name", "")}
                   for r in reader if r.get("url")]

    scraped_data, scraped_urls = [], set()
    attempt_count, success_count, fail_count = 0, 0, 0
    
    if os.path.exists(output_json):
        try:
            with open(output_json, "r", encoding="utf-8") as f:
                existing_output = json.load(f)
                if "results" in existing_output:
                    scraped_data = existing_output.get("results", [])
                    old_summary = existing_output.get("run_summary", {})
                    attempt_count = old_summary.get("total_posts_attempted", 0)
                    success_count = old_summary.get("successfully_fetched", 0)
                    fail_count = old_summary.get("failed_to_fetch", 0)
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
    chrome_options.add_argument("--disable-blink-features=AutomationControlled")
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    for arg in ["--headless", "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"]:
        chrome_options.add_argument(arg)

    print(f"Launching scraper for {len(remaining_targets)} URLs...")
    driver = webdriver.Chrome(options=chrome_options)

    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
        'source': '''
            Object.defineProperty(navigator, 'webdriver', {
              get: () => undefined
            })
        '''
    })

    def save_state():
        elapsed_time = time.time() - script_start_time
        is_logged_in = True
        try:
            if "login_form" in driver.page_source or "/login/" in driver.current_url: is_logged_in = False
        except Exception: pass 
            
        with open(output_json, 'w', encoding='utf-8') as f:
            json.dump({
                "run_summary": {
                    "input_file": os.path.abspath(input_csv), "output_file": os.path.abspath(output_json),
                    "total_posts_in_file": len(targets), "scrape_limit_applied": limit,
                    "authenticated": is_logged_in, "total_posts_attempted": attempt_count,
                    "successfully_fetched": success_count, "failed_to_fetch": fail_count,
                    "success_rate_percent": round((success_count / attempt_count * 100), 2) if attempt_count > 0 else 0.0,
                    "total_time_seconds": round(elapsed_time, 3), "total_time_taken": f"{int(elapsed_time // 60)}m {int(elapsed_time % 60):02d}s",
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

            # post_start_time = time.time()
            
            try:
                driver.get("about:blank")
                try:
                    WebDriverWait(driver, 5).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                        and d.current_url == "about:blank"
                    )
                except Exception:
                    pass

                driver.get(url)

                try:
                    WebDriverWait(driver, 10).until(
                        lambda d: d.execute_script("return document.readyState") == "complete"
                    )
                except Exception:
                    pass

                try:
                    driver.execute_script("window.scrollTo(0, document.body.scrollHeight/2);")
                except Exception:
                    pass

                _url_id_match = (
                    re.search(r'(pfbid[a-zA-Z0-9]+)', url)
                    or re.search(r'story_fbid=(pfbid[a-zA-Z0-9]+)', url)
                    or re.search(r'[/?]([0-9]{10,})(?:[/?]|$)', url)
                )
                _url_id = _url_id_match.group(1) if _url_id_match else None

                if _url_id:
                    try:
                        WebDriverWait(driver, 10).until(lambda d: _url_id in d.page_source)
                    except Exception:
                        pass
                    time.sleep(1.5)  # DOM stability: let React finish injecting sibling scripts
                else:
                    try:
                        WebDriverWait(driver, 6).until(
                            lambda d: (any(mk in d.page_source
                                         for mk in ['"post_id"', '"profile_picture"',
                                                    '"story_node"', '"reaction_count"'])))
                    except Exception:
                        pass

                post_data = parse_facebook_url(driver.page_source, url, category, global_index, target["name"])

                # # For Stress Testing
                # fetch_duration = round(time.time() - post_start_time, 3)
                
                # if post_data.get("metadata"):
                #     post_data["metadata"]["fetch_time_seconds"] = fetch_duration

                scraped_data.append(post_data)
                success_count += 1
                save_state()
                
                if index < len(remaining_targets): time.sleep(random.uniform(4.0, 10.0))
            except Exception as e:
                fail_count += 1
                scraped_data.append({"post_index": global_index, "post_url": url, "status": "error", "error_message": str(e)})
                save_state()

                # BROWSER_RESTART_EVERY = 500  # restart every 500 posts
                # if index > 1 and (index - 1) % BROWSER_RESTART_EVERY == 0:
                #     driver.quit()              # kills the Chrome process
                #     print(f"Launching scraper for {len(remaining_targets)} URLs...")
                #     driver = webdriver.Chrome(options=chrome_options)
    finally:
        driver.quit()
        print(f"\nBrowser closed. Progress safely stored in '{output_json}'!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--input", default="json_data_formatted1.csv")
    parser.add_argument("-o", "--output", default="viewed1_metadata.json")
    parser.add_argument("-l", "--limit", type=int, default=None)
    args = parser.parse_args()
    scrape_visited_history(args.input, args.output, args.limit)
