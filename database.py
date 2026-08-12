import sqlite3
import asyncio
import aiofiles
import os
import re
from typing import List, Dict, Optional, Tuple, Set
from datetime import datetime
import json

VIDEO_FILE_RE = re.compile(r'^(.+)_(\d{15,})\.mp4$', re.IGNORECASE)

class Database:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.init_database()

    @staticmethod
    def normalize_username(username: str) -> str:
        return "".join(c for c in username.lower() if c.isalnum() or c in ('-', '_'))
    
    def init_database(self):
        """Initialize database with required tables"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Create admins table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY,
                user_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create tiktok_accounts table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tiktok_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                user_id TEXT,
                display_name TEXT,
                follower_count INTEGER,
                last_post_id TEXT,
                added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_active BOOLEAN DEFAULT 1
            )
        ''')
        
        # Create posts table for tracking sent posts
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tiktok_account_id INTEGER,
                post_id TEXT UNIQUE NOT NULL,
                video_url TEXT,
                description TEXT,
                created_at TIMESTAMP,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (tiktok_account_id) REFERENCES tiktok_accounts (id)
            )
        ''')
        
        # Create monitoring_state table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS monitoring_state (
                id INTEGER PRIMARY KEY,
                is_monitoring BOOLEAN DEFAULT 0,
                last_check TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
        conn.close()
    
    async def add_admin(self, user_id: int, username: str = None) -> bool:
        """Add admin to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "INSERT OR IGNORE INTO admins (user_id, username) VALUES (?, ?)",
                (user_id, username)
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error adding admin: {e}")
            return False
    
    async def remove_admin(self, user_id: int) -> bool:
        """Remove admin from database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error removing admin: {e}")
            return False
    
    async def get_admins(self) -> List[Dict]:
        """Get all admins"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT user_id, username, added_at FROM admins")
            admins = []
            for row in cursor.fetchall():
                admins.append({
                    'user_id': row[0],
                    'username': row[1],
                    'added_at': row[2]
                })
            conn.close()
            return admins
        except Exception as e:
            print(f"Error getting admins: {e}")
            return []
    
    async def is_admin(self, user_id: int) -> bool:
        """Check if user is admin"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM admins WHERE user_id = ?", (user_id,))
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except Exception as e:
            print(f"Error checking admin status: {e}")
            return False
    
    async def add_tiktok_account(self, username: str, user_id: str = None, 
                               display_name: str = None, follower_count: int = None) -> bool:
        """Add TikTok account to monitoring"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO tiktok_accounts 
                (username, user_id, display_name, follower_count, is_active) 
                VALUES (?, ?, ?, ?, 1)
            ''', (username, user_id, display_name, follower_count))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error adding TikTok account: {e}")
            return False
    
    async def remove_tiktok_account(self, username: str) -> bool:
        """Remove TikTok account from monitoring"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE tiktok_accounts SET is_active = 0 WHERE username = ?", (username,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error removing TikTok account: {e}")
            return False
    
    async def get_tiktok_accounts(self) -> List[Dict]:
        """Get all active TikTok accounts"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                SELECT id, username, user_id, display_name, follower_count, 
                       last_post_id, added_at 
                FROM tiktok_accounts WHERE is_active = 1
            ''')
            accounts = []
            for row in cursor.fetchall():
                accounts.append({
                    'id': row[0],
                    'username': row[1],
                    'user_id': row[2],
                    'display_name': row[3],
                    'follower_count': row[4],
                    'last_post_id': row[5],
                    'added_at': row[6]
                })
            conn.close()
            return accounts
        except Exception as e:
            print(f"Error getting TikTok accounts: {e}")
            return []
    
    async def update_last_post_id(self, account_id: int, post_id: str) -> bool:
        """Update last post ID for account"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE tiktok_accounts SET last_post_id = ? WHERE id = ?",
                (post_id, account_id)
            )
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error updating last post ID: {e}")
            return False
    
    async def add_post(self, tiktok_account_id: int, post_id: str, 
                      video_url: str, description: str, created_at: str) -> bool:
        """Add sent post to database"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO posts 
                (tiktok_account_id, post_id, video_url, description, created_at) 
                VALUES (?, ?, ?, ?, ?)
            ''', (tiktok_account_id, post_id, video_url, description, created_at))
            inserted = cursor.rowcount > 0
            conn.commit()
            conn.close()
            return inserted
        except Exception as e:
            print(f"Error adding post: {e}")
            return False

    async def get_sent_post_ids(self, tiktok_account_id: int) -> Set[str]:
        """Get IDs of posts already sent for an account"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT post_id FROM posts WHERE tiktok_account_id = ?",
                (tiktok_account_id,)
            )
            sent_ids = {row[0] for row in cursor.fetchall()}
            conn.close()
            return sent_ids
        except Exception as e:
            print(f"Error getting sent post IDs: {e}")
            return set()

    async def is_post_sent(self, tiktok_account_id: int, post_id: str) -> bool:
        """Check if a post was already sent"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM posts WHERE tiktok_account_id = ? AND post_id = ?",
                (tiktok_account_id, post_id)
            )
            result = cursor.fetchone() is not None
            conn.close()
            return result
        except Exception as e:
            print(f"Error checking sent post: {e}")
            return False

    def _match_account_for_file_prefix(
        self, file_prefix: str, accounts: List[Dict]
    ) -> Optional[Dict]:
        norm_prefix = self.normalize_username(file_prefix)
        for account in accounts:
            norm_username = self.normalize_username(account['username'])
            if norm_prefix == norm_username:
                return account
        for account in accounts:
            norm_username = self.normalize_username(account['username'])
            if norm_prefix.startswith(norm_username) or norm_username.startswith(norm_prefix):
                return account
        return None

    async def import_downloaded_videos(self, downloads_dir: str) -> Dict[str, int]:
        """Mark videos from downloads/ as already sent and remove local files"""
        stats = {'imported': 0, 'deleted': 0, 'skipped': 0}
        if not os.path.isdir(downloads_dir):
            return stats

        accounts = await self.get_tiktok_accounts()
        if not accounts:
            return stats

        for filename in os.listdir(downloads_dir):
            match = VIDEO_FILE_RE.match(filename)
            if not match:
                stats['skipped'] += 1
                continue

            file_prefix, post_id = match.group(1), match.group(2)
            account = self._match_account_for_file_prefix(file_prefix, accounts)
            if not account:
                stats['skipped'] += 1
                continue

            video_url = f"https://www.tiktok.com/@{account['username']}/video/{post_id}"
            if await self.add_post(
                account['id'], post_id, video_url, '', datetime.now().isoformat()
            ):
                stats['imported'] += 1

            filepath = os.path.join(downloads_dir, filename)
            try:
                os.remove(filepath)
                stats['deleted'] += 1
            except OSError:
                pass

        return stats
    
    async def reset_monitoring_state(self) -> bool:
        """Reset monitoring state - clear last_post_id for all accounts"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("UPDATE tiktok_accounts SET last_post_id = NULL")
            cursor.execute("DELETE FROM posts")
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error resetting monitoring state: {e}")
            return False
    
    async def set_monitoring_state(self, is_monitoring: bool) -> bool:
        """Set monitoring state"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO monitoring_state (id, is_monitoring, last_check) 
                VALUES (1, ?, CURRENT_TIMESTAMP)
            ''', (is_monitoring,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error setting monitoring state: {e}")
            return False
    
    async def get_monitoring_state(self) -> bool:
        """Get current monitoring state"""
        try:
            conn = sqlite3.connect(self.db_path)
            cursor = conn.cursor()
            cursor.execute("SELECT is_monitoring FROM monitoring_state WHERE id = 1")
            result = cursor.fetchone()
            conn.close()
            return result[0] if result else False
        except Exception as e:
            print(f"Error getting monitoring state: {e}")
            return False
