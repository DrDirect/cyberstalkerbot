import asyncio
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
import config
from database import Database
from simple_tiktok_parser import HybridTikTokAPI
from monitoring import TikTokMonitor

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class TikTokMonitorBot:
    def __init__(self):
        self.db = Database(config.DATABASE_PATH)
        self.monitor = TikTokMonitor(self.db, HybridTikTokAPI())
        self.application = None
        self.send_all_task = None
        self.stop_send_all_requested = False

    async def _sync_monitoring_with_accounts(self):
        """Start monitoring when there are subscriptions; stop when there are none."""
        accounts = await self.db.get_tiktok_accounts()
        self.monitor.set_bot_application(self.application)

        if accounts:
            if not self.monitor.is_running:
                await self.db.set_monitoring_state(True)
                await self.monitor.start_monitoring()
                logger.info(
                    "Monitoring auto-started for %d account(s)",
                    len(accounts),
                )
        else:
            if self.monitor.is_running:
                self.monitor.stop_monitoring()
            await self.db.set_monitoring_state(False)
            logger.info("Monitoring is off (no active subscriptions)")
    
    async def start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        user_id = update.effective_user.id
        username = update.effective_user.username or "Unknown"
        logger.info(f"Received /start command from user {user_id} (@{username})")
        
        # Check if user is admin
        is_admin = await self.db.is_admin(user_id)
        logger.info(f"User {user_id} (@{username}) is admin: {is_admin}")
        
        # Log all admins for debugging
        admins = await self.db.get_admins()
        logger.info(f"Current admins in database: {[admin['user_id'] for admin in admins]}")
        logger.info(f"Admins from config: {config.ADMIN_IDS}")
        
        if is_admin:
            await update.message.reply_text(
                "🤖 Добро пожаловать в TikTok Monitor Bot!\n\n"
                "Доступные команды:\n"
                "/add_account - Добавить аккаунт для мониторинга\n"
                "/remove_account - Удалить аккаунт\n"
                "/list_accounts - Список отслеживаемых аккаунтов\n"
                "/reset_state - Сбросить состояние мониторинга\n"
                "/add_admin - Добавить администратора\n"
                "/remove_admin - Удалить администратора\n"
                "/list_admins - Список администраторов\n"
                "/start_monitoring - Запустить мониторинг\n"
                "/stop_monitoring - Остановить мониторинг\n"
                "/status - Статус мониторинга\n"
                "/test_video - Отправить тестовое видео\n"
                "/send_all - Отправить все неотправленные видео с аккаунтов\n"
                "/stop_send_all - Временно остановить массовую отправку"
            )
        else:
            await update.message.reply_text(
                "❌ У вас нет прав администратора для использования этого бота.\n"
                "Обратитесь к администратору для получения доступа."
            )
    
    async def add_account_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /add_account command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "Использование: /add_account <username>\n"
                "Пример: /add_account username123"
            )
            return
        
        username = context.args[0].replace('@', '')  # Remove @ if present
        
        try:
            # Get user info from TikTok
            async with HybridTikTokAPI() as tiktok_api:
                user_info = await tiktok_api.get_user_info(username)
                
                if not user_info:
                    await update.message.reply_text(f"❌ Не удалось найти аккаунт @{username}")
                    return
                
                # Add account to database
                success = await self.db.add_tiktok_account(
                    username=username,
                    user_id=user_info.get('user_id'),
                    display_name=user_info.get('display_name'),
                    follower_count=user_info.get('follower_count')
                )
                
                if success:
                    await self._sync_monitoring_with_accounts()
                    await update.message.reply_text(
                        f"✅ Аккаунт @{username} успешно добавлен для мониторинга!\n"
                        f"📊 Подписчиков: {user_info.get('follower_count', 'N/A')}"
                    )
                else:
                    await update.message.reply_text(f"❌ Ошибка при добавлении аккаунта @{username}")
        
        except Exception as e:
            logger.error(f"Error adding account {username}: {e}")
            await update.message.reply_text(f"❌ Произошла ошибка при добавлении аккаунта: {str(e)}")
    
    async def remove_account_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /remove_account command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "Использование: /remove_account <username>\n"
                "Пример: /remove_account username123"
            )
            return
        
        username = context.args[0].replace('@', '')
        
        try:
            success = await self.db.remove_tiktok_account(username)
            
            if success:
                await self._sync_monitoring_with_accounts()
                await update.message.reply_text(f"✅ Аккаунт @{username} удален из мониторинга.")
            else:
                await update.message.reply_text(f"❌ Аккаунт @{username} не найден в списке отслеживаемых.")
        
        except Exception as e:
            logger.error(f"Error removing account {username}: {e}")
            await update.message.reply_text(f"❌ Произошла ошибка при удалении аккаунта: {str(e)}")
    
    async def list_accounts_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_accounts command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            accounts = await self.db.get_tiktok_accounts()
            
            if not accounts:
                await update.message.reply_text("📝 Список отслеживаемых аккаунтов пуст.")
                return
            
            message = "📝 Отслеживаемые аккаунты:\n\n"
            for account in accounts:
                message += f"• @{account['username']}\n"
                if account['display_name']:
                    message += f"  Имя: {account['display_name']}\n"
                if account['follower_count']:
                    message += f"  Подписчиков: {account['follower_count']}\n"
                message += f"  Добавлен: {account['added_at']}\n\n"
            
            await update.message.reply_text(message)
        
        except Exception as e:
            logger.error(f"Error listing accounts: {e}")
            await update.message.reply_text("❌ Произошла ошибка при получении списка аккаунтов.")
    
    async def reset_state_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reset_state command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            success = await self.db.reset_monitoring_state()
            
            if success:
                await update.message.reply_text(
                    "✅ Состояние мониторинга сброшено!\n"
                    "Все аккаунты будут отслеживаться с момента сброса."
                )
            else:
                await update.message.reply_text("❌ Ошибка при сбросе состояния мониторинга.")
        
        except Exception as e:
            logger.error(f"Error resetting state: {e}")
            await update.message.reply_text("❌ Произошла ошибка при сбросе состояния.")
    
    async def add_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /add_admin command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "Использование: /add_admin <user_id>\n"
                "Пример: /add_admin 123456789"
            )
            return
        
        try:
            new_admin_id = int(context.args[0])
            username = update.effective_user.username or "Unknown"
            
            success = await self.db.add_admin(new_admin_id, username)
            
            if success:
                await update.message.reply_text(f"✅ Пользователь {new_admin_id} добавлен как администратор.")
            else:
                await update.message.reply_text(f"❌ Ошибка при добавлении администратора.")
        
        except ValueError:
            await update.message.reply_text("❌ Неверный формат user_id. Используйте числовой ID.")
        except Exception as e:
            logger.error(f"Error adding admin: {e}")
            await update.message.reply_text("❌ Произошла ошибка при добавлении администратора.")
    
    async def remove_admin_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /remove_admin command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        if not context.args:
            await update.message.reply_text(
                "Использование: /remove_admin <user_id>\n"
                "Пример: /remove_admin 123456789"
            )
            return
        
        try:
            admin_id = int(context.args[0])
            
            success = await self.db.remove_admin(admin_id)
            
            if success:
                await update.message.reply_text(f"✅ Администратор {admin_id} удален.")
            else:
                await update.message.reply_text(f"❌ Ошибка при удалении администратора.")
        
        except ValueError:
            await update.message.reply_text("❌ Неверный формат user_id. Используйте числовой ID.")
        except Exception as e:
            logger.error(f"Error removing admin: {e}")
            await update.message.reply_text("❌ Произошла ошибка при удалении администратора.")
    
    async def list_admins_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /list_admins command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            admins = await self.db.get_admins()
            
            if not admins:
                await update.message.reply_text("📝 Список администраторов пуст.")
                return
            
            message = "👥 Администраторы:\n\n"
            for admin in admins:
                message += f"• ID: {admin['user_id']}\n"
                if admin['username']:
                    message += f"  Username: @{admin['username']}\n"
                message += f"  Добавлен: {admin['added_at']}\n\n"
            
            await update.message.reply_text(message)
        
        except Exception as e:
            logger.error(f"Error listing admins: {e}")
            await update.message.reply_text("❌ Произошла ошибка при получении списка администраторов.")
    
    async def start_monitoring_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start_monitoring command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            accounts = await self.db.get_tiktok_accounts()
            if not accounts:
                await update.message.reply_text(
                    "❌ Нет отслеживаемых аккаунтов. Добавьте аккаунт через /add_account."
                )
                return

            if self.monitor.is_running:
                await update.message.reply_text("ℹ️ Мониторинг уже запущен.")
                return

            await self._sync_monitoring_with_accounts()
            await update.message.reply_text("✅ Мониторинг запущен!")
        
        except Exception as e:
            logger.error(f"Error starting monitoring: {e}")
            await update.message.reply_text("❌ Произошла ошибка при запуске мониторинга.")
    
    async def stop_monitoring_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop_monitoring command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            if not self.monitor.is_running:
                await update.message.reply_text("ℹ️ Мониторинг уже остановлен.")
                return

            self.monitor.stop_monitoring()
            await self.db.set_monitoring_state(False)
            await update.message.reply_text("⏹️ Мониторинг остановлен!")
        
        except Exception as e:
            logger.error(f"Error stopping monitoring: {e}")
            await update.message.reply_text("❌ Произошла ошибка при остановке мониторинга.")
    
    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            accounts = await self.db.get_tiktok_accounts()
            is_monitoring = self.monitor.is_running
            
            status_text = "📊 Статус мониторинга:\n\n"
            if not accounts:
                status_text += "🔄 Мониторинг: ⚪ Выключен (нет подписок)\n"
            else:
                status_text += f"🔄 Мониторинг: {'🟢 Активен' if is_monitoring else '🔴 Остановлен'}\n"
            status_text += f"📝 Отслеживаемых аккаунтов: {len(accounts)}\n"
            status_text += f"⏱️ Интервал проверки: {config.CHECK_INTERVAL} сек\n"
            
            if accounts:
                status_text += "\n📋 Аккаунты:\n"
                for account in accounts[:5]:  # Show first 5 accounts
                    status_text += f"• @{account['username']}\n"
                if len(accounts) > 5:
                    status_text += f"... и еще {len(accounts) - 5} аккаунтов"
            
            await update.message.reply_text(status_text)
        
        except Exception as e:
            logger.error(f"Error getting status: {e}")
            await update.message.reply_text("❌ Произошла ошибка при получении статуса.")
    
    async def test_video_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /test_video command"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        try:
            # Create test video data
            test_video = {
                'id': f'test_video_{int(datetime.now().timestamp())}',
                'video_url': 'https://commondatastorage.googleapis.com/gtv-videos-bucket/sample/ForBiggerBlazes.mp4',
                'description': '🎵 Тестовое видео для проверки отправки! Это демонстрация работы бота TikTok Monitor. #test #bot #tiktok',
                'created_at': datetime.now().isoformat(),
                'thumbnail_url': 'https://picsum.photos/720/1280?random=999'
            }
            
            # Send test video notification
            await self.monitor._send_video_notification(test_video, "test_user", self.application)
            
            await update.message.reply_text("✅ Тестовое видео отправлено! Проверьте уведомления.")
        
        except Exception as e:
            logger.error(f"Error in test_video command: {e}")
            await update.message.reply_text(f"❌ Произошла ошибка при отправке тестового видео: {str(e)}")
    
    async def send_all_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /send_all command - start sending all unsent videos in background"""
        user_id = update.effective_user.id
        
        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return
        
        if self.send_all_task and not self.send_all_task.done():
            await update.message.reply_text(
                "ℹ️ Массовая отправка уже идет. Используйте /stop_send_all для остановки."
            )
            return

        self.stop_send_all_requested = False
        chat_id = update.effective_chat.id
        self.send_all_task = asyncio.create_task(self._send_all_videos(chat_id))
        await update.message.reply_text(
            "🔄 Начинаю отправку всех неотправленных видео с аккаунтов.\n"
            "Для временной остановки используйте /stop_send_all."
        )

    async def stop_send_all_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop_send_all command"""
        user_id = update.effective_user.id

        if not await self.db.is_admin(user_id):
            await update.message.reply_text("❌ У вас нет прав администратора.")
            return

        if not self.send_all_task or self.send_all_task.done():
            await update.message.reply_text("ℹ️ Массовая отправка сейчас не запущена.")
            return

        self.stop_send_all_requested = True
        await update.message.reply_text(
            "⏸️ Останавливаю массовую отправку после текущего видео.\n"
            "Позже запустите /send_all снова — бот продолжит с неотправленных видео."
        )

    async def _send_all_videos(self, chat_id: int):
        """Send all unsent videos from monitored accounts."""
        try:
            # Get all monitored accounts
            accounts = await self.db.get_tiktok_accounts()
            
            if not accounts:
                await self.application.bot.send_message(
                    chat_id=chat_id,
                    text="📝 Нет отслеживаемых аккаунтов для отправки постов.",
                )
                return

            total_sent = 0
            total_skipped = 0
            stopped = False
            total_accounts = len(accounts)
            
            for account in accounts:
                if self.stop_send_all_requested:
                    stopped = True
                    break

                try:
                    username = account['username']
                    account_id = account['id']
                    logger.info(f"Getting all videos for account @{username}")

                    sent_post_ids = await self.db.get_sent_post_ids(account_id)

                    # Get all videos from TikTok
                    async with HybridTikTokAPI() as tiktok_api:
                        videos = await tiktok_api.get_user_videos(username, max_count=None)

                    if not videos:
                        logger.warning(f"No videos found for @{username}")
                        continue

                    # Filter out already sent videos, send oldest first
                    new_videos = [v for v in reversed(videos) if v['id'] not in sent_post_ids]
                    total_skipped += len(videos) - len(new_videos)
                    
                    if not new_videos:
                        logger.info(f"All videos from @{username} already sent")
                        continue

                    logger.info(f"Found {len(new_videos)} new videos out of {len(videos)} total for @{username}")

                    # Send each new video
                    for i, video in enumerate(new_videos):
                        if self.stop_send_all_requested:
                            stopped = True
                            break

                        try:
                            sent = await self.monitor._send_video_notification(
                                video, username, self.application
                            )
                            if sent:
                                await self.db.add_post(
                                    tiktok_account_id=account_id,
                                    post_id=video['id'],
                                    video_url=video['video_url'],
                                    description=video['description'],
                                    created_at=video['created_at']
                                )
                                total_sent += 1
                            else:
                                logger.warning(
                                    f"Skipped marking video {video['id']} from @{username} as sent"
                                )

                            # Add small delay between videos to avoid rate limiting
                            await asyncio.sleep(1)

                            logger.info(f"Sent video {i+1}/{len(new_videos)} from @{username}")

                        except Exception as video_error:
                            logger.error(f"Error sending video {i+1} from @{username}: {video_error}")
                            continue

                    if stopped:
                        break

                    # Update last_post_id to the most recent video
                    if videos:
                        await self.db.update_last_post_id(account_id, videos[0]['id'])

                    # Add delay between accounts
                    await asyncio.sleep(2)

                except Exception as account_error:
                    logger.error(f"Error processing account @{account['username']}: {account_error}")
                    continue
            
            # Send completion message
            account_names = ', '.join([f'@{acc["username"]}' for acc in accounts])
            status = "⏸️ Отправка остановлена." if stopped else "✅ Отправка завершена!"
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=(
                f"{status}\n\n"
                f"📊 Статистика:\n"
                f"• Обработано аккаунтов: {total_accounts}\n"
                f"• Отправлено видео: {total_sent}\n"
                f"• Пропущено (уже в БД): {total_skipped}\n"
                f"• Аккаунты: {account_names}"
                )
            )
            
        except Exception as e:
            logger.error(f"Error in send_all command: {e}")
            await self.application.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Произошла ошибка при отправке постов: {str(e)}",
            )
        finally:
            self.stop_send_all_requested = False
    
    async def unknown_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle unknown messages"""
        user_id = update.effective_user.id
        
        # Check if user is admin
        is_admin = await self.db.is_admin(user_id)
        
        if is_admin:
            await update.message.reply_text(
                "❓ Неизвестная команда. Используйте /start для просмотра доступных команд."
            )
        else:
            await update.message.reply_text(
                "❌ У вас нет прав администратора для использования этого бота.\n"
                "Обратитесь к администратору для получения доступа."
            )
    
    async def run(self):
        """Run the bot"""
        # Create application
        self.application = Application.builder().token(config.TELEGRAM_BOT_TOKEN).build()
        
        # Add command handlers
        self.application.add_handler(CommandHandler("start", self.start_command))
        self.application.add_handler(CommandHandler("add_account", self.add_account_command))
        self.application.add_handler(CommandHandler("remove_account", self.remove_account_command))
        self.application.add_handler(CommandHandler("list_accounts", self.list_accounts_command))
        self.application.add_handler(CommandHandler("reset_state", self.reset_state_command))
        self.application.add_handler(CommandHandler("add_admin", self.add_admin_command))
        self.application.add_handler(CommandHandler("remove_admin", self.remove_admin_command))
        self.application.add_handler(CommandHandler("list_admins", self.list_admins_command))
        self.application.add_handler(CommandHandler("start_monitoring", self.start_monitoring_command))
        self.application.add_handler(CommandHandler("stop_monitoring", self.stop_monitoring_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("test_video", self.test_video_command))
        self.application.add_handler(CommandHandler("send_all", self.send_all_command))
        self.application.add_handler(CommandHandler("stop_send_all", self.stop_send_all_command))
        
        # Add message handler for unknown commands
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.unknown_message))
        
        # Start the bot
        logger.info("Starting TikTok Monitor Bot...")
        await self.application.initialize()
        await self.application.start()
        
        # Initialize admins from config after bot is started
        for admin_id in config.ADMIN_IDS:
            await self.db.add_admin(admin_id)
            logger.info(f"Initialized admin: {admin_id}")

        import_stats = await self.db.import_downloaded_videos("downloads")
        if any(import_stats.values()):
            logger.info(
                "Imported sent videos from downloads: %d new in DB, %d files removed, %d skipped",
                import_stats['imported'],
                import_stats['deleted'],
                import_stats['skipped'],
            )
        
        await self.application.updater.start_polling()

        await self._sync_monitoring_with_accounts()
        
        # Keep the bot running
        try:
            await asyncio.Event().wait()
        except KeyboardInterrupt:
            logger.info("Bot stopped by user")
        finally:
            if self.monitor.is_running:
                self.monitor.stop_monitoring()
            await self.application.stop()

if __name__ == "__main__":
    bot = TikTokMonitorBot()
    asyncio.run(bot.run())
