import asyncio
import logging
import os
from typing import List, Dict
from datetime import datetime, timezone
import config
from database import Database
from simple_tiktok_parser import HybridTikTokAPI

logger = logging.getLogger(__name__)

class TikTokMonitor:
    def __init__(self, db: Database, tiktok_api: HybridTikTokAPI):
        self.db = db
        self.tiktok_api = tiktok_api
        self.is_running = False
        self.monitoring_task = None
        self.bot_application = None
    
    async def start_monitoring(self):
        """Start monitoring TikTok accounts"""
        if self.is_running:
            logger.warning("Monitoring is already running")
            return
        
        self.is_running = True
        self.monitoring_task = asyncio.create_task(self._monitoring_loop())
        logger.info("TikTok monitoring started")
    
    def set_bot_application(self, application):
        """Set bot application for sending notifications"""
        self.bot_application = application
    
    def stop_monitoring(self):
        """Stop monitoring TikTok accounts"""
        self.is_running = False
        if self.monitoring_task:
            self.monitoring_task.cancel()
            self.monitoring_task = None
        logger.info("TikTok monitoring stopped")
    
    async def _monitoring_loop(self):
        """Main monitoring loop"""
        while self.is_running:
            try:
                await self._check_accounts()
                await asyncio.sleep(config.CHECK_INTERVAL)
            except asyncio.CancelledError:
                logger.info("Monitoring loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in monitoring loop: {e}")
                await asyncio.sleep(config.RETRY_DELAY)
    
    async def _check_accounts(self):
        """Check all monitored accounts for new posts"""
        try:
            accounts = await self.db.get_tiktok_accounts()
            
            if not accounts:
                logger.info("No accounts to monitor")
                return
            
            logger.info(f"Checking {len(accounts)} accounts for new posts")
            
            for account in accounts:
                try:
                    await self._check_account(account)
                except Exception as e:
                    logger.error(f"Error checking account {account['username']}: {e}")
                    continue
            
            # Update last check time
            await self.db.set_monitoring_state(True)
            
        except Exception as e:
            logger.error(f"Error checking accounts: {e}")
    
    async def _check_account(self, account: Dict):
        """Check a single account for new posts"""
        username = account['username']
        account_id = account['id']
        last_post_id = account.get('last_post_id')

        logger.info(f"Checking account @{username}")

        try:
            # Get recent videos from TikTok
            async with self.tiktok_api as api:
                videos = await api.get_user_videos(username, max_count=10)

            if not videos:
                logger.warning(f"⚠️ No videos found for @{username} - account may be private, deleted, or parsing failed")
                return

            sent_post_ids = await self.db.get_sent_post_ids(account_id)
            logger.info(f"Already sent {len(sent_post_ids)} posts for @{username}")

            # Filter new videos - check both last_post_id and sent posts
            new_videos = []
            
            if last_post_id:
                # Find videos newer than the last known post
                for video in videos:
                    video_id = video['id']
                    # Skip if already sent OR if we reached the last known post
                    if video_id in sent_post_ids:
                        logger.debug(f"Skipping already sent video: {video_id}")
                        continue
                    if video_id == last_post_id:
                        # Stop at the last known post (all videos after this are new)
                        break
                    new_videos.append(video)
            else:
                # No last_post_id - check which videos are already sent
                for video in videos:
                    video_id = video['id']
                    if video_id not in sent_post_ids:
                        new_videos.append(video)
                    else:
                        logger.debug(f"Skipping already sent video: {video_id}")

            if new_videos:
                logger.info(f"✅ Found {len(new_videos)} new videos for @{username}")

                # Process new videos in reverse order (oldest first)
                for video in reversed(new_videos):
                    await self._process_new_video(account_id, video, username)

                # Update last_post_id to the most recent video
                if videos:
                    await self.db.update_last_post_id(account_id, videos[0]['id'])
            else:
                logger.info(f"✓ No new videos for @{username}")

        except Exception as e:
            logger.error(f"Error checking account @{username}: {e}")
    
    async def _process_new_video(self, account_id: int, video: Dict, username: str):
        """Process a new video and send notifications"""
        try:
            if await self.db.is_post_sent(account_id, video['id']):
                logger.debug(f"Video {video['id']} from @{username} already sent, skipping")
                return

            sent = await self._send_video_notification(video, username, self.bot_application)
            if sent:
                await self.db.add_post(
                    tiktok_account_id=account_id,
                    post_id=video['id'],
                    video_url=video['video_url'],
                    description=video['description'],
                    created_at=video['created_at']
                )
                logger.info(f"Processed new video {video['id']} from @{username}")
            else:
                logger.warning(f"Failed to send video {video['id']} from @{username}, will retry later")
            
        except Exception as e:
            logger.error(f"Error processing video {video['id']}: {e}")

    def _delete_local_file(self, filepath: str):
        """Remove downloaded video file after sending"""
        try:
            if filepath and os.path.exists(filepath):
                os.remove(filepath)
                logger.info(f"Deleted local video file: {filepath}")
        except OSError as e:
            logger.warning(f"Failed to delete file {filepath}: {e}")

    def _format_video_time(self, created_at: str) -> str:
        """Format TikTok upload time for notification messages."""
        if not created_at:
            return "неизвестно"
        try:
            value = str(created_at).replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        except (TypeError, ValueError):
            return str(created_at)
    
    async def _send_video_notification(self, video: Dict, username: str, bot_application=None) -> bool:
        """Send video notification to all admins. Returns True if at least one admin received it."""
        local_file = None
        sent_successfully = False
        try:
            # Get all admins
            admins = await self.db.get_admins()
            
            if not admins:
                logger.warning("No admins found to send notification to")
                return False
            
            # Create notification message
            message = f"🎥 Новое видео от @{username}!\n\n"
            message += f"📝 Описание: {video['description'][:200]}...\n" if len(video['description']) > 200 else f"📝 Описание: {video['description']}\n"
            message += f"⏰ Загружено в TikTok: {self._format_video_time(video.get('created_at'))}\n"
            if video.get('view_count', 0) > 0:
                message += f"👀 Просмотры: {video['view_count']:,}\n"
            if video.get('like_count', 0) > 0:
                message += f"❤️ Лайки: {video['like_count']:,}\n"
            if video.get('comment_count', 0) > 0:
                message += f"💬 Комментарии: {video['comment_count']:,}\n"
            
            video_url = video.get('video_url', '')
            local_file = video.get('local_file')
            
            # Try to download video if it's a TikTok URL and we don't have local file
            if 'tiktok.com' in video_url and '/video/' in video_url:
                if not local_file or not os.path.exists(local_file):
                    logger.info(f"🔄 Attempting to download video from {video_url}")
                    try:
                        from simple_tiktok_parser import SimpleTikTokParser
                        parser = SimpleTikTokParser()
                        video_id = video.get('id', 'unknown')
                        if not video_id or video_id == 'unknown':
                            # Extract video ID from URL
                            import re
                            match = re.search(r'/video/(\d+)', video_url)
                            if match:
                                video_id = match.group(1)
                        
                        logger.info(f"Downloading video ID: {video_id}")
                        downloaded = await parser._download_video(video_url, username, 0, str(video_id))
                        if downloaded and os.path.exists(downloaded):
                            local_file = downloaded
                            video['local_file'] = downloaded
                            logger.info(f"✅ Video downloaded successfully: {downloaded}")
                        else:
                            logger.warning(f"⚠️ Download returned None or file doesn't exist")
                    except Exception as download_error:
                        logger.error(f"❌ Error downloading video: {download_error}", exc_info=True)
                else:
                    logger.info(f"✅ Using existing local file: {local_file}")
            
            # Send to all admins
            for admin in admins:
                try:
                    if bot_application:
                        # Check if we have a local file - send as video file
                        if local_file and os.path.exists(local_file):
                            try:
                                logger.info(f"Sending local video file to admin {admin['user_id']}")
                                with open(local_file, 'rb') as video_file:
                                    await bot_application.bot.send_video(
                                        chat_id=admin['user_id'],
                                        video=video_file,
                                        caption=message,
                                        parse_mode='HTML',
                                        supports_streaming=True
                                    )
                                logger.info(f"✅ Successfully sent local video to admin {admin['user_id']}")
                                sent_successfully = True
                                continue
                            except Exception as video_error:
                                logger.warning(f"❌ Failed to send local video to admin {admin['user_id']}: {video_error}")
                        
                        # Fallback: send as text message with link
                        try:
                            link_text = f"\n\n🔗 <a href='{video_url}'>Смотреть видео на TikTok</a>" if video_url else ""
                            await bot_application.bot.send_message(
                                chat_id=admin['user_id'],
                                text=message + link_text,
                                parse_mode='HTML',
                                disable_web_page_preview=False
                            )
                            logger.info(f"📝 Sent text notification with link to admin {admin['user_id']}")
                            sent_successfully = True
                        except Exception as msg_error:
                            logger.error(f"❌ Failed to send message to admin {admin['user_id']}: {msg_error}")
                    else:
                        # Log notification if no bot application available
                        logger.info(f"Would send video notification to admin {admin['user_id']}: {message}")
                except Exception as e:
                    logger.error(f"Error sending notification to admin {admin['user_id']}: {e}")
        
        except Exception as e:
            logger.error(f"Error sending video notification: {e}")
        finally:
            if local_file:
                self._delete_local_file(local_file)
                video['local_file'] = None

        return sent_successfully
    
    async def get_monitoring_stats(self) -> Dict:
        """Get monitoring statistics"""
        try:
            accounts = await self.db.get_tiktok_accounts()
            is_monitoring = await self.db.get_monitoring_state()
            
            return {
                'is_monitoring': is_monitoring,
                'total_accounts': len(accounts),
                'active_accounts': len([acc for acc in accounts if acc.get('is_active', True)]),
                'last_check': datetime.now().isoformat()
            }
        
        except Exception as e:
            logger.error(f"Error getting monitoring stats: {e}")
            return {
                'is_monitoring': False,
                'total_accounts': 0,
                'active_accounts': 0,
                'last_check': None
            }
