import asyncio
import logging
import os
import re
import yt_dlp
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone
from TikTokApi import TikTokApi

logger = logging.getLogger(__name__)

class SimpleTikTokParser:
    def __init__(self):
        self.download_dir = "downloads"
        os.makedirs(self.download_dir, exist_ok=True)
        self.tiktok_api = None

    def _timestamp_to_iso(self, value) -> Optional[str]:
        """Convert TikTok/yt-dlp timestamp values to ISO UTC."""
        if not value:
            return None
        try:
            if isinstance(value, (int, float)):
                timestamp = int(value)
            else:
                value_str = str(value).strip()
                if len(value_str) == 8 and value_str.isdigit():
                    return datetime.strptime(value_str, '%Y%m%d').replace(
                        tzinfo=timezone.utc
                    ).isoformat()
                timestamp = int(float(value_str))

            # Some APIs return milliseconds.
            if timestamp > 10_000_000_000:
                timestamp = timestamp // 1000
            return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (TypeError, ValueError, OSError):
            return None

    def _created_at_from_video_id(self, video_id: str) -> str:
        """TikTok video IDs embed upload time in the high 32 bits."""
        try:
            timestamp = int(str(video_id)) >> 32
            if 1_400_000_000 <= timestamp <= 2_200_000_000:
                return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
        except (TypeError, ValueError):
            pass
        return datetime.now(timezone.utc).isoformat()

    def _created_at_from_tiktok_item(self, item: dict, video_id: str) -> str:
        for key in ('createTime', 'create_time', 'created_at', 'timestamp'):
            created_at = self._timestamp_to_iso(item.get(key))
            if created_at:
                return created_at
        return self._created_at_from_video_id(video_id)

    def _created_at_from_ytdlp_entry(self, entry: dict, video_id: str) -> str:
        for key in ('timestamp', 'release_timestamp', 'upload_date', 'modified_timestamp'):
            created_at = self._timestamp_to_iso(entry.get(key))
            if created_at:
                return created_at
        return self._created_at_from_video_id(video_id)
    
    async def __aenter__(self):
        # TikTokApi requires Playwright which needs system libraries
        # We'll use HTML parsing instead
        self.tiktok_api = None
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass
    
    async def get_user_info(self, username: str) -> Dict:
        """Get user information"""
        logger.info(f"Getting user info for @{username}")
        return {
            'username': username,
            'display_name': f'@{username}',
            'follower_count': 1000,
            'user_id': f'user_{username}'
        }
    
    async def get_user_videos(self, username: str, max_count: Optional[int] = 10) -> List[Dict]:
        """Get user's videos using multiple extraction methods. max_count=None fetches all available."""
        logger.info(f"Getting real videos for @{username}" + ("" if max_count is None else f" (max {max_count})"))

        if max_count is None:
            try:
                videos = await self._get_real_tiktok_videos(username, max_count=None)
                if videos:
                    logger.info(f"✅ Got {len(videos)} videos for @{username} using yt-dlp (all)")
                    return videos
            except Exception as e:
                logger.error(f"yt-dlp failed for @{username} when fetching all videos: {e}")
            return []

        # Method 1: Try HTML parsing first (most reliable)
        try:
            videos = await self._get_videos_from_html(username, max_count)
            if videos and len(videos) > 0:
                logger.info(f"✅ Got {len(videos)} real videos for @{username} using HTML parsing")
                return videos
        except Exception as e:
            logger.error(f"HTML parsing failed for @{username}: {e}", exc_info=True)

        # Method 2: Fallback to yt-dlp with improved settings
        try:
            videos = await self._get_real_tiktok_videos(username, max_count)
            if videos and len(videos) > 0:
                logger.info(f"✅ Got {len(videos)} real videos for @{username} using yt-dlp")
                return videos
        except Exception as e:
            logger.error(f"yt-dlp failed for @{username}: {e}")

        # Method 3: Return empty list - no videos found
        # Don't create fake fallback entries - they're not useful
        logger.warning(f"⚠️ No videos found for @{username} after all extraction methods")
        logger.info(f"Returning empty list for @{username} - check if username is correct or account exists")
        
        # Return empty list instead of fake fallback
        # This allows the bot to properly handle the "no videos" case
        return []
    
    async def _get_videos_with_tiktokapi(self, username: str, max_count: int) -> List[Dict]:
        """Get videos using TikTokApi library or direct HTML parsing"""
        if not self.tiktok_api:
            # Try direct HTML parsing as fallback
            return await self._get_videos_from_html(username, max_count)
        
        try:
            logger.info(f"Fetching videos for @{username} using TikTokApi")
            
            # Get user videos
            user_videos = await self.tiktok_api.user(username).videos(count=max_count)
            
            videos = []
            for i, video_data in enumerate(user_videos):
                try:
                    video = await self._process_tiktokapi_video(video_data, username, i)
                    if video:
                        videos.append(video)
                except Exception as e:
                    logger.error(f"Error processing TikTokApi video {i}: {e}")
                    continue
            
            return videos
            
        except Exception as e:
            logger.error(f"TikTokApi error: {e}, trying HTML parsing")
            return await self._get_videos_from_html(username, max_count)
    
    async def _get_videos_from_html(self, username: str, max_count: int) -> List[Dict]:
        """Get videos by parsing TikTok HTML page with improved extraction methods"""
        try:
            from bs4 import BeautifulSoup
            import json
            import re

            url = f"https://www.tiktok.com/@{username}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Referer': 'https://www.tiktok.com/',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'same-origin',
            }

            logger.info(f"Fetching HTML from {url}")
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            # Check if we got redirected or blocked
            if 'captcha' in response.text.lower() or 'verify' in response.text.lower():
                logger.warning(f"Possible captcha/block detected for @{username}")
            
            soup = BeautifulSoup(response.text, 'html.parser')
            videos = []

            # Method 1: Extract from __UNIVERSAL_DATA_FOR_REHYDRATION__ with improved paths
            scripts = soup.find_all('script', {'id': '__UNIVERSAL_DATA_FOR_REHYDRATION__'})
            if scripts:
                try:
                    data_text = scripts[0].string
                    if data_text:
                        data = json.loads(data_text)
                        logger.info("Found __UNIVERSAL_DATA_FOR_REHYDRATION__ script")

                        # Try multiple new paths for TikTok's updated structure
                        paths_to_try = [
                            # New TikTok structure paths
                            ['__DEFAULT_SCOPE__', 'webapp.user-detail', 'userInfo', 'user', 'itemList'],
                            ['__DEFAULT_SCOPE__', 'webapp.user-detail', 'userInfo', 'itemList'],
                            ['__DEFAULT_SCOPE__', 'webapp.user-detail', 'itemList'],
                            ['webapp', 'user-detail', 'userInfo', 'user', 'itemList'],
                            # Alternative paths
                            ['__DEFAULT_SCOPE__', 'webapp.user-detail', 'userInfo'],
                            ['__DEFAULT_SCOPE__', 'webapp.user-detail'],
                            # Try without __DEFAULT_SCOPE__
                            ['webapp.user-detail', 'userInfo', 'user', 'itemList'],
                            ['webapp.user-detail', 'userInfo', 'itemList'],
                        ]

                        item_list = []
                        for path in paths_to_try:
                            current = data
                            try:
                                for key in path:
                                    if isinstance(current, dict):
                                        current = current.get(key)
                                    else:
                                        break
                                if isinstance(current, list) and len(current) > 0:
                                    item_list = current
                                    logger.info(f"Found itemList with {len(item_list)} items using path: {path}")
                                    break
                            except (KeyError, TypeError, AttributeError):
                                continue

                        if item_list:
                            for i, item in enumerate(item_list[:max_count]):
                                try:
                                    video = await self._process_html_video(item, username, i)
                                    if video:
                                        videos.append(video)
                                except Exception as e:
                                    logger.error(f"Error processing HTML video {i}: {e}")
                                    continue
                except Exception as e:
                    logger.error(f"Error parsing __UNIVERSAL_DATA_FOR_REHYDRATION__: {e}")

            # Method 2: Search ALL script tags for any JSON containing video data
            if not videos:
                logger.info("Searching all script tags for video data")
                all_scripts = soup.find_all('script')
                
                for script_idx, script in enumerate(all_scripts):
                    if not script.string or len(script.string) < 500:
                        continue

                    text = script.string
                    
                    # Look for video-related patterns
                    if 'itemList' in text or '"aweme"' in text or '"video"' in text or '"desc"' in text:
                        try:
                            # Try to find JSON objects in the script
                            # Look for { ... } patterns that might contain video data
                            json_starts = [i for i, c in enumerate(text) if c == '{']
                            json_ends = [i for i, c in enumerate(text) if c == '}']
                            
                            # Try to extract and parse JSON chunks
                            for start in json_starts[:10]:  # Limit attempts
                                for end in json_ends:
                                    if end > start:
                                        try:
                                            json_str = text[start:end+1]
                                            if len(json_str) > 1000 and '"itemList"' in json_str:
                                                data = json.loads(json_str)
                                                if 'itemList' in data and isinstance(data['itemList'], list):
                                                    item_list = data['itemList']
                                                    logger.info(f"Found itemList in script {script_idx} with {len(item_list)} items")
                                                    for i, item in enumerate(item_list[:max_count]):
                                                        try:
                                                            video = await self._process_html_video(item, username, i)
                                                            if video:
                                                                videos.append(video)
                                                        except Exception as e:
                                                            logger.error(f"Error processing video {i}: {e}")
                                                            continue
                                                    if videos:
                                                        break
                                        except json.JSONDecodeError:
                                            continue
                                if videos:
                                    break
                        except Exception as e:
                            logger.debug(f"Script {script_idx} parsing error: {e}")
                            continue
                    
                    if videos:
                        break

            # Method 3: Extract video data from embedded JSON in the page
            if not videos:
                logger.info("Trying to extract video data from page structure")
                try:
                    # Look for data in meta tags or other embedded data
                    # TikTok sometimes embeds video info in meta tags
                    meta_tags = soup.find_all('meta', attrs={'content': True})
                    video_urls = set()
                    
                    for meta in meta_tags:
                        content = meta.get('content', '')
                        # Look for TikTok video URLs in meta content
                        video_matches = re.findall(r'https://www\.tiktok\.com/@[\w\.]+/video/\d+', content)
                        for match in video_matches:
                            if match not in video_urls:
                                video_urls.add(match)
                    
                    # Also search for video links in the page
                    links = soup.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        if '/video/' in href:
                            if not href.startswith('http'):
                                href = f"https://www.tiktok.com{href}"
                            video_urls.add(href)
                    
                    if video_urls:
                        logger.info(f"Found {len(video_urls)} video URLs in page")
                        for i, video_url in enumerate(list(video_urls)[:max_count]):
                            video_id_match = re.search(r'/video/(\d+)', video_url)
                            if video_id_match:
                                video_id = video_id_match.group(1)
                                video = {
                                    'id': video_id,
                                    'video_url': video_url,
                                    'description': f'Video from @{username}',
                                    'created_at': self._created_at_from_video_id(video_id),
                                    'thumbnail_url': '',
                                    'view_count': 0,
                                    'like_count': 0,
                                    'comment_count': 0
                                }
                                videos.append(video)
                except Exception as e:
                    logger.error(f"Error extracting from page structure: {e}")

            # Method 4: Try to use browser-like request with cookies
            if not videos:
                logger.info("Trying enhanced request with cookies")
                try:
                    # Create a session to maintain cookies
                    session = requests.Session()
                    
                    # First request to get cookies
                    session.get('https://www.tiktok.com/', headers=headers, timeout=10)
                    
                    # Then request the user page
                    user_response = session.get(url, headers=headers, timeout=30)
                    
                    if user_response.status_code == 200:
                        soup2 = BeautifulSoup(user_response.text, 'html.parser')
                        
                        # Try to find video data in the new response
                        scripts = soup2.find_all('script', {'id': '__UNIVERSAL_DATA_FOR_REHYDRATION__'})
                        if scripts and scripts[0].string:
                            try:
                                data = json.loads(scripts[0].string)
                                # Try the most common path
                                item_list = data.get('__DEFAULT_SCOPE__', {}).get('webapp.user-detail', {}).get('userInfo', {}).get('user', {}).get('itemList', [])
                                
                                if item_list:
                                    logger.info(f"Session request found {len(item_list)} videos")
                                    for i, item in enumerate(item_list[:max_count]):
                                        try:
                                            video = await self._process_html_video(item, username, i)
                                            if video:
                                                videos.append(video)
                                        except Exception as e:
                                            logger.error(f"Error processing session video {i}: {e}")
                            except Exception as e:
                                logger.error(f"Session data parse error: {e}")
                except Exception as e:
                    logger.error(f"Session request error: {e}")

            # Method 5: Direct API call with better parameters
            if not videos:
                logger.info("Trying enhanced TikTok API endpoint")
                try:
                    # First get user info to get secUid
                    user_api_url = f"https://www.tiktok.com/api/user/detail/?aid=1988&app_name=tiktok_web&device_platform=web&os=windows&browser_language=en-US&browser_platform=Win32&browser_name=Mozilla&browser_version=5.0&uniqueId={username}"
                    
                    user_response = requests.get(user_api_url, headers=headers, timeout=15)
                    
                    if user_response.status_code == 200:
                        user_data = user_response.json()
                        
                        if 'userInfo' in user_data:
                            user_info = user_data['userInfo'].get('user', {})
                            user_id = user_info.get('id', '')
                            sec_uid = user_info.get('secUid', '')
                            
                            if sec_uid:
                                # Use secUid for video list API
                                videos_api_url = f"https://www.tiktok.com/api/post/item_list/?aid=1988&app_name=tiktok_web&device_platform=web&count={max_count}&secUid={sec_uid}&userId={user_id}&maxCursor=0&minCursor=0"
                                
                                videos_response = requests.get(videos_api_url, headers=headers, timeout=15)
                                
                                if videos_response.status_code == 200:
                                    videos_data = videos_response.json()
                                    
                                    if 'itemList' in videos_data and videos_data['itemList']:
                                        item_list = videos_data['itemList']
                                        logger.info(f"API endpoint found {len(item_list)} videos")
                                        
                                        for i, item in enumerate(item_list[:max_count]):
                                            try:
                                                video = await self._process_html_video(item, username, i)
                                                if video:
                                                    videos.append(video)
                                            except Exception as e:
                                                logger.error(f"Error processing API video {i}: {e}")
                except Exception as e:
                    logger.error(f"Enhanced API endpoint error: {e}")

            logger.info(f"HTML parsing completed: found {len(videos)} videos for @{username}")
            return videos

        except Exception as e:
            logger.error(f"HTML parsing error: {e}", exc_info=True)
            return []
    
    async def _process_html_video(self, item: dict, username: str, index: int) -> Optional[Dict]:
        """Process video from HTML JSON data"""
        try:
            video_id = item.get('id', f'{username}_video_{index}')
            desc = item.get('desc', '') or item.get('description', '')
            stats = item.get('stats', {})
            
            # Get video URL - TikTok video URLs are in format
            video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"
            
            video = {
                'id': str(video_id),
                'video_url': video_url,
                'description': desc[:500] if desc else '',
                'created_at': self._created_at_from_tiktok_item(item, str(video_id)),
                'thumbnail_url': item.get('video', {}).get('cover', '') or item.get('cover', ''),
                'view_count': stats.get('playCount', 0) or item.get('playCount', 0),
                'like_count': stats.get('diggCount', 0) or item.get('diggCount', 0),
                'comment_count': stats.get('commentCount', 0) or item.get('commentCount', 0)
            }
            
            return video
            
        except Exception as e:
            logger.error(f"Error processing HTML video: {e}")
            return None
    
    async def _process_tiktokapi_video(self, video_data: dict, username: str, index: int) -> Optional[Dict]:
        """Process a video from TikTokApi"""
        try:
            # Extract video information
            video_id = video_data.get('id', f'{username}_video_{index}')
            
            # Get video URL - TikTokApi provides video URLs
            video_url = None
            if 'video' in video_data:
                video_url = video_data['video'].get('downloadAddr') or video_data['video'].get('playAddr')
            elif 'videoUrl' in video_data:
                video_url = video_data['videoUrl']
            elif 'downloadAddr' in video_data:
                video_url = video_data['downloadAddr']
            
            # Get description
            description = video_data.get('desc', '') or video_data.get('description', '') or f'Video from @{username}'
            
            # Get stats
            stats = video_data.get('stats', {})
            view_count = stats.get('playCount', 0) or video_data.get('playCount', 0)
            like_count = stats.get('diggCount', 0) or video_data.get('diggCount', 0)
            comment_count = stats.get('commentCount', 0) or video_data.get('commentCount', 0)
            
            # Get creation time
            created_at = self._created_at_from_tiktok_item(video_data, str(video_id))
            
            # Get thumbnail
            thumbnail_url = None
            if 'video' in video_data and 'cover' in video_data['video']:
                thumbnail_url = video_data['video']['cover']
            elif 'cover' in video_data:
                thumbnail_url = video_data['cover']
            elif 'thumbnail' in video_data:
                thumbnail_url = video_data['thumbnail']
            
            if not video_url:
                logger.warning(f"No video URL found in TikTokApi data for entry {index}")
                return None
            
            video = {
                'id': str(video_id),
                'video_url': video_url,
                'description': description[:500] if description else '',
                'created_at': created_at,
                'thumbnail_url': thumbnail_url or '',
                'view_count': int(view_count) if view_count else 0,
                'like_count': int(like_count) if like_count else 0,
                'comment_count': int(comment_count) if comment_count else 0
            }
            
            return video
            
        except Exception as e:
            logger.error(f"Error processing TikTokApi video entry {index}: {e}", exc_info=True)
            return None
    
    async def _get_real_tiktok_videos(self, username: str, max_count: Optional[int]) -> List[Dict]:
        """Try to get real TikTok videos using yt-dlp with extract_flat mode"""
        try:
            # Create a TikTok URL
            tiktok_url = f"https://www.tiktok.com/@{username}"

            # Configure yt-dlp options - minimal options for reliability
            # Using extract_flat mode which works better with TikTok's anti-bot
            ydl_opts = {
                'quiet': True,
                'no_warnings': True,
                'extract_flat': 'in_playlist',  # Only extract URLs, don't download
                'ignoreerrors': True,
                'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
            }
            if max_count is not None:
                ydl_opts['playlistend'] = max_count

            videos = []

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    logger.info(f"Extracting info from {tiktok_url} using yt-dlp")
                    info = ydl.extract_info(tiktok_url, download=False)

                    if not info:
                        logger.error(f"No info extracted from {tiktok_url}")
                        return []

                    # Get entries from playlist
                    entries = []
                    if 'entries' in info:
                        entries = info['entries']
                    elif info.get('_type') != 'playlist':
                        # Single video
                        entries = [info]

                    if not entries:
                        logger.warning(f"No entries found for {tiktok_url}")
                        return []

                    entry_list = list(entries) if entries else []
                    logger.info(f"yt-dlp found {len(entry_list)} entries for @{username}")

                    limited_entries = entry_list if max_count is None else entry_list[:max_count]
                    for i, entry in enumerate(limited_entries):
                        if not entry:
                            continue

                        # Handle string entries (URLs)
                        if isinstance(entry, str):
                            video_id_match = re.search(r'/video/(\d+)', entry)
                            if video_id_match:
                                video_id = video_id_match.group(1)
                                videos.append({
                                    'id': video_id,
                                    'video_url': entry if entry.startswith('http') else f"https://www.tiktok.com{entry}",
                                    'description': f'Video from @{username}',
                                    'created_at': self._created_at_from_video_id(video_id),
                                    'thumbnail_url': '',
                                    'view_count': 0,
                                    'like_count': 0,
                                    'comment_count': 0
                                })
                            continue

                        # Handle dict entries
                        if isinstance(entry, dict):
                            video_id = entry.get('id', f'{username}_video_{i}')
                            video_url = entry.get('url') or entry.get('webpage_url') or f"https://www.tiktok.com/@{username}/video/{video_id}"

                            # Extract additional info if available
                            video = {
                                'id': str(video_id),
                                'video_url': video_url,
                                'description': entry.get('title') or entry.get('description') or f'Video from @{username}',
                                'created_at': self._created_at_from_ytdlp_entry(entry, str(video_id)),
                                'thumbnail_url': entry.get('thumbnail', ''),
                                'view_count': entry.get('view_count', 0) or 0,
                                'like_count': entry.get('like_count', 0) or 0,
                                'comment_count': entry.get('comment_count', 0) or 0
                            }
                            videos.append(video)
                            logger.info(f"Processed yt-dlp entry {i}: {video_id}")

                    if videos:
                        logger.info(f"✅ yt-dlp successfully extracted {len(videos)} videos for @{username}")
                    else:
                        logger.warning(f"yt-dlp found entries but couldn't process any for @{username}")

                except Exception as e:
                    logger.error(f"Failed to extract info from {tiktok_url}: {e}", exc_info=True)
                    return []

            return videos

        except Exception as e:
            logger.error(f"Error in _get_real_tiktok_videos: {e}", exc_info=True)
            return []
    
    async def _process_video_entry(self, entry: dict, username: str, index: int) -> Optional[Dict]:
        """Process a single video entry from yt-dlp"""
        try:
            # Get video ID - try multiple fields
            video_id = (entry.get('id') or 
                       entry.get('display_id') or 
                       entry.get('webpage_url', '').split('/')[-1].split('?')[0] or 
                       f'{username}_video_{index}')
            
            # Get video URL - prefer direct URL, then formats, then webpage URL
            video_url = None
            
            # Try to get direct video URL from formats
            if entry.get('formats'):
                # Find the best video format
                for fmt in entry['formats']:
                    if fmt.get('vcodec') != 'none' and fmt.get('url'):  # Has video
                        video_url = fmt['url']
                        break
            
            # Fallback to direct URL or webpage URL
            if not video_url:
                video_url = entry.get('url') or entry.get('webpage_url') or ''
            
            # If we have a video ID but no URL, construct TikTok URL
            if not video_url and video_id and video_id != f'{username}_video_{index}':
                # Extract numeric ID if it's in the format
                numeric_id = re.search(r'(\d+)', str(video_id))
                if numeric_id:
                    video_url = f"https://www.tiktok.com/@{username}/video/{numeric_id.group(1)}"
                else:
                    video_url = f"https://www.tiktok.com/@{username}/video/{video_id}"
            
            # Get title/description
            title = (entry.get('title') or 
                    entry.get('description') or 
                    entry.get('fulltitle') or 
                    entry.get('alt_title') or 
                    f'Video from @{username}')
            
            # Get metadata
            duration = entry.get('duration', 0)
            view_count = entry.get('view_count') or entry.get('play_count', 0)
            like_count = entry.get('like_count') or entry.get('repost_count', 0)
            comment_count = entry.get('comment_count', 0)
            
            # Get upload date
            upload_date = entry.get('upload_date') or entry.get('timestamp')
            if upload_date:
                try:
                    if isinstance(upload_date, (int, float)):
                        created_at = datetime.fromtimestamp(upload_date).isoformat()
                    else:
                        created_at = datetime.strptime(str(upload_date), '%Y%m%d').isoformat()
                except:
                    created_at = datetime.now().isoformat()
            else:
                created_at = datetime.now().isoformat()
            
            # Get thumbnail
            thumbnail_url = entry.get('thumbnail') or (entry.get('thumbnails', [{}])[0].get('url', '') if entry.get('thumbnails') else '')
            
            # If we have a webpage URL but no direct video URL, use the webpage URL
            if not video_url and entry.get('webpage_url'):
                video_url = entry['webpage_url']
            
            # Validate that we have at least a URL
            if not video_url:
                logger.warning(f"No video URL found for entry {index}, entry keys: {list(entry.keys())[:10]}")
                return None
            
            # Ensure URL is complete
            if video_url and not video_url.startswith('http'):
                if video_url.startswith('/'):
                    video_url = f"https://www.tiktok.com{video_url}"
                else:
                    video_url = f"https://www.tiktok.com/@{username}/video/{video_url}"
            
            video = {
                'id': str(video_id),
                'video_url': video_url,
                'description': title[:500] if title else '',  # Limit description length
                'created_at': created_at,
                'thumbnail_url': thumbnail_url or '',
                'view_count': int(view_count) if view_count else 0,
                'like_count': int(like_count) if like_count else 0,
                'comment_count': int(comment_count) if comment_count else 0
            }
            
            logger.info(f"Processed video entry {index}: {video_id} -> {video_url}")
            return video
            
        except Exception as e:
            logger.error(f"Error processing video entry {index}: {e}", exc_info=True)
            return None
    
    async def _download_video(self, video_url: str, username: str, index: int, video_id: str) -> Optional[str]:
        """Download video using yt-dlp"""
        try:
            if not video_url:
                return None
            
            # Create safe filename
            safe_username = "".join(c for c in username if c.isalnum() or c in ('-', '_'))[:20]
            safe_video_id = str(video_id)[:30]
            filename = f"{safe_username}_{safe_video_id}.mp4"
            filepath = os.path.join(self.download_dir, filename)
            
            # Skip if file already exists
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                logger.info(f"Video already exists: {filepath}")
                return filepath
            
            # Configure yt-dlp options for downloading
            ydl_opts = {
                'outtmpl': filepath,
                'format': 'best[ext=mp4]/best[height<=720]/best',  # Prefer MP4, max 720p
                'quiet': False,  # Show progress
                'no_warnings': False,
                'max_duration': 300,  # Limit to 5 minutes
                'noplaylist': True,
                'extract_flat': False,
            }
            
            logger.info(f"Downloading video from {video_url} to {filepath}")
            
            # Download video in executor to avoid blocking
            import asyncio
            loop = asyncio.get_event_loop()
            
            def download():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([video_url])
            
            await loop.run_in_executor(None, download)
            
            # Check if file was downloaded
            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                file_size = os.path.getsize(filepath)
                logger.info(f"✅ Successfully downloaded video: {filepath} ({file_size} bytes)")
                return filepath
            else:
                logger.error(f"❌ Downloaded file is empty or doesn't exist: {filepath}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Error downloading video {video_url}: {e}", exc_info=True)
            return None
    
    def _get_fallback_videos(self, username: str, max_count: int) -> List[Dict]:
        """DEPRECATED: Fallback method removed - no longer using test videos"""
        logger.warning(f"Fallback videos requested for @{username} - this should not happen!")
        return []

# Main API class
class HybridTikTokAPI(SimpleTikTokParser):
    pass