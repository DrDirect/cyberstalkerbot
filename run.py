#!/usr/bin/env python3
"""
TikTok Monitor Bot - Main entry point
"""

import asyncio
import sys
import logging
from bot import TikTokMonitorBot

def main():
    """Main function to run the bot"""
    try:
        # Create and run the bot
        bot = TikTokMonitorBot()
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nBot stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"Error running bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
