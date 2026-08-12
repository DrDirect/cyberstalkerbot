import os
from typing import List
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Telegram Bot Configuration
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

# TikTok Configuration
TIKTOK_API_BASE_URL = os.getenv('TIKTOK_API_BASE_URL', 'https://www.tiktok.com/api/user/detail/')
TIKTOK_VIDEOS_API_URL = os.getenv('TIKTOK_VIDEOS_API_URL', 'https://www.tiktok.com/api/post/item_list/')

# Database Configuration
DATABASE_PATH = os.getenv('DATABASE_PATH', 'tiktok_monitor.db')

# Monitoring Configuration
CHECK_INTERVAL = int(os.getenv('CHECK_INTERVAL', '900'))  # 15 minutes in seconds
MAX_RETRIES = int(os.getenv('MAX_RETRIES', '3'))
RETRY_DELAY = int(os.getenv('RETRY_DELAY', '60'))  # 1 minute

# Admin Configuration
ADMIN_IDS_STR = os.getenv('ADMIN_IDS', '')
ADMIN_IDS: List[int] = [int(id.strip()) for id in ADMIN_IDS_STR.split(',') if id.strip()]

# Logging Configuration
LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
LOG_FILE = os.getenv('LOG_FILE', 'tiktok_monitor.log')

# TikTok API Configuration (if using official API)
TIKTOK_CLIENT_KEY = os.getenv('TIKTOK_CLIENT_KEY', '')
TIKTOK_CLIENT_SECRET = os.getenv('TIKTOK_CLIENT_SECRET', '')
TIKTOK_ACCESS_TOKEN = os.getenv('TIKTOK_ACCESS_TOKEN', '')