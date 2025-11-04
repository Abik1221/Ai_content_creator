import logging
import asyncio
from typing import Dict, List, Optional, Any
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes
from telegram.error import TelegramError, NetworkError
import httpx
from datetime import datetime
import json

from app.core.config import settings
from app.models.schemas import TelegramMessageResponse, ContentApprovalRequest
from app.models.database import DatabaseManager, DatabaseUtils

logger = logging.getLogger(__name__)


class TelegramService:
    """
    Telegram bot service for human-in-the-loop content approval workflow.
    
    Handles:
    - Sending content for approval with interactive buttons
    - Receiving user responses (approve/reject/edit)
    - Managing conversation state
    - Sending notifications and confirmations
    """
    
    def __init__(self):
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.webhook_url = settings.TELEGRAM_WEBHOOK_URL
        self.bot = Bot(token=self.bot_token)
        self.application = None
        self.db_manager = DatabaseManager(settings.DATABASE_URL)
        
        # In-memory state (in production, use Redis)
        self.user_sessions = {}
        self.pending_approvals = {}
        
        # Initialize bot handlers
        self._setup_handlers()
    
    def _setup_handlers(self):
        """Setup Telegram bot command and callback handlers"""
        try:
            self.application = Application.builder().token(self.bot_token).build()
            
            # Command handlers
            self.application.add_handler(CommandHandler("start", self._start_command))
            self.application.add_handler(CommandHandler("help", self._help_command))
            self.application.add_handler(CommandHandler("status", self._status_command))
            self.application.add_handler(CommandHandler("pending", self._pending_command))
            
            # Callback query handler for inline buttons
            self.application.add_handler(CallbackQueryHandler(self._handle_callback_query))
            
            # Message handler for text responses (edits)
            self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_message))
            
            logger.info("Telegram bot handlers setup successfully")
            
        except Exception as e:
            logger.error(f"Failed to setup Telegram handlers: {e}")
            raise
    
    async def _start_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        try:
            user = update.effective_user
            chat_id = update.effective_chat.id
            
            welcome_message = """
🤖 *Welcome to LinkedIn Content Agent!*

I help you create and schedule professional LinkedIn content with AI assistance.

*Available Commands:*
/start - Show this welcome message
/help - Get help and instructions  
/status - Check your content status
/pending - Show pending approvals

*How it works:*
1. AI generates content based on your company info
2. I send it here for your approval
3. You can Approve, Edit, or Reject
4. Approved content gets posted to LinkedIn

Ready to create amazing content? 🚀
            """
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=welcome_message,
                parse_mode='Markdown'
            )
            
            # Store user session
            self.user_sessions[chat_id] = {
                'user_id': user.id,
                'username': user.username,
                'first_name': user.first_name,
                'last_used': datetime.now()
            }
            
            logger.info(f"New user session started: {user.username} (ID: {chat_id})")
            
        except Exception as e:
            logger.error(f"Start command failed: {e}")
    
    async def _help_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        try:
            help_message = """
*📖 LinkedIn Content Agent Help*

*Approval Workflow:*
- ✅ *Approve*: Content will be posted to LinkedIn immediately
- ✏️ *Edit*: You can modify the content before posting  
- ❌ *Reject*: Content will be discarded and not posted

*Content Editing:*
When you choose to edit, simply type your new version of the content and send it. I'll use your edited version for posting.

*Image Selection:*
If multiple images are generated, you can choose which one to use with the content.

*Need Help?*
Contact support if you encounter any issues or have questions about the content generation process.

*Commands:*
/start - Welcome message
/help - This help message
/status - Check content status
/pending - Show pending approvals
            """
            
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=help_message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Help command failed: {e}")
    
    async def _status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command - show user's content status"""
        try:
            chat_id = update.effective_chat.id
            
            with self.db_manager.get_session() as session:
                from app.models.database import Content, ContentStatusDB
                
                # Get user's content statistics
                total_content = session.query(Content).filter(
                    Content.user_id == str(chat_id)  # Using chat_id as user_id for simplicity
                ).count()
                
                pending_count = session.query(Content).filter(
                    Content.user_id == str(chat_id),
                    Content.status == ContentStatusDB.PENDING_APPROVAL
                ).count()
                
                posted_count = session.query(Content).filter(
                    Content.user_id == str(chat_id),
                    Content.status == ContentStatusDB.POSTED
                ).count()
            
            status_message = f"""
*📊 Your Content Status*

- 📝 Total Content: {total_content}
- ⏳ Pending Approval: {pending_count}
- ✅ Posted to LinkedIn: {posted_count}

Use /pending to see content waiting for your approval.
            """
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=status_message,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Status command failed: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Could not retrieve your status. Please try again later."
            )
    
    async def _pending_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /pending command - show pending approvals"""
        try:
            chat_id = update.effective_chat.id
            
            with self.db_manager.get_session() as session:
                from app.models.database import Content, ContentStatusDB
                
                pending_content = session.query(Content).filter(
                    Content.user_id == str(chat_id),
                    Content.status == ContentStatusDB.PENDING_APPROVAL
                ).order_by(Content.created_at.desc()).limit(5).all()
            
            if not pending_content:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="🎉 No pending approvals! All caught up."
                )
                return
            
            message = f"*📋 Pending Approvals ({len(pending_content)})*\n\n"
            
            for i, content in enumerate(pending_content, 1):
                preview = content.content_text[:100] + "..." if len(content.content_text) > 100 else content.content_text
                message += f"*{i}. {content.topic}*\n"
                message += f"   {preview}\n"
                message += f"   _Created: {content.created_at.strftime('%Y-%m-%d %H:%M')}_\n\n"
            
            message += "I'll send each one for your approval individually."
            
            await context.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='Markdown'
            )
            
            # Send the first pending content for approval
            if pending_content:
                await self.send_content_for_approval(
                    user_id=str(chat_id),
                    content_id=pending_content[0].content_id,
                    content=pending_content[0].content_text,
                    image_urls=[pending_content[0].image_url] if pending_content[0].image_url else None
                )
            
        except Exception as e:
            logger.error(f"Pending command failed: {e}")
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ Could not retrieve pending approvals. Please try again later."
            )
    
    async def _handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle callback queries from inline buttons"""
        try:
            query = update.callback_query
            await query.answer()  # Acknowledge the callback
            
            data = json.loads(query.data)
            action = data.get('action')
            content_id = data.get('content_id')
            user_id = str(query.from_user.id)
            
            logger.info(f"Callback received: {action} for content {content_id}")
            
            if action == 'approve':
                await self._handle_approval(user_id, content_id, query)
            elif action == 'reject':
                await self._handle_rejection(user_id, content_id, query)
            elif action == 'edit':
                await self._handle_edit_request(user_id, content_id, query)
            elif action == 'regenerate':
                await self._handle_regenerate_request(user_id, content_id, query)
            elif action.startswith('image_'):
                await self._handle_image_selection(user_id, content_id, action, query)
            else:
                await query.edit_message_text("❌ Unknown action. Please try again.")
                
        except Exception as e:
            logger.error(f"Callback handling failed: {e}")
            try:
                await query.edit_message_text("❌ An error occurred. Please try again.")
            except:
                pass
    
    async def _handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle text messages (primarily for content edits)"""
        try:
            user_id = str(update.effective_user.id)
            message_text = update.message.text
            
            # Check if user is in edit mode
            if user_id in self.user_sessions and self.user_sessions[user_id].get('editing_content_id'):
                content_id = self.user_sessions[user_id]['editing_content_id']
                await self._handle_edited_content(user_id, content_id, message_text, update)
            else:
                # Regular message - provide guidance
                await update.message.reply_text(
                    "💡 I'm here to help with LinkedIn content approval! "
                    "Use /help to see available commands or wait for content approval requests."
                )
                
        except Exception as e:
            logger.error(f"Message handling failed: {e}")
            await update.message.reply_text("❌ An error occurred. Please try again.")
    
    async def _handle_approval(self, user_id: str, content_id: str, query):
        """Handle content approval"""
        try:
            # Process approval through API
            approval_request = ContentApprovalRequest(
                content_id=content_id,
                approved=True,
                edits=None
            )
            
            # In production, this would call the FastAPI endpoint
            # For now, simulate the approval
            success = await self._process_approval_via_api(approval_request, user_id)
            
            if success:
                await query.edit_message_text(
                    "✅ *Content Approved!* \n\n"
                    "Your content has been approved and is being posted to LinkedIn. "
                    "You'll receive a confirmation when it's live.",
                    parse_mode='Markdown'
                )
                
                # Clear any editing session
                if user_id in self.user_sessions:
                    self.user_sessions[user_id].pop('editing_content_id', None)
                    
            else:
                await query.edit_message_text(
                    "❌ *Approval Failed* \n\n"
                    "There was an error processing your approval. Please try again or contact support.",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Approval handling failed: {e}")
            await query.edit_message_text("❌ Approval processing failed. Please try again.")
    
    async def _handle_rejection(self, user_id: str, content_id: str, query):
        """Handle content rejection"""
        try:
            # Process rejection through API
            approval_request = ContentApprovalRequest(
                content_id=content_id,
                approved=False,
                edits=None
            )
            
            success = await self._process_approval_via_api(approval_request, user_id)
            
            if success:
                await query.edit_message_text(
                    "❌ *Content Rejected* \n\n"
                    "The content has been rejected and will not be posted to LinkedIn.",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    "❌ *Rejection Failed* \n\n"
                    "There was an error processing your rejection. Please try again.",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Rejection handling failed: {e}")
            await query.edit_message_text("❌ Rejection processing failed. Please try again.")
    
    async def _handle_edit_request(self, user_id: str, content_id: str, query):
        """Handle request to edit content"""
        try:
            # Store editing session
            if user_id not in self.user_sessions:
                self.user_sessions[user_id] = {}
            self.user_sessions[user_id]['editing_content_id'] = content_id
            
            await query.edit_message_text(
                "✏️ *Edit Mode* \n\n"
                "Please send your edited version of the content. \n"
                "I'll use your edited text for posting to LinkedIn. \n\n"
                "_Send your edited content now..._",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Edit request handling failed: {e}")
            await query.edit_message_text("❌ Failed to enter edit mode. Please try again.")
    
    async def _handle_edited_content(self, user_id: str, content_id: str, edited_text: str, update: Update):
        """Handle submitted edited content"""
        try:
            # Process approval with edits
            approval_request = ContentApprovalRequest(
                content_id=content_id,
                approved=True,
                edits=edited_text
            )
            
            success = await self._process_approval_via_api(approval_request, user_id)
            
            if success:
                await update.message.reply_text(
                    "✅ *Edited Content Approved!* \n\n"
                    "Your edited content has been approved and is being posted to LinkedIn.",
                    parse_mode='Markdown'
                )
                
                # Clear editing session
                if user_id in self.user_sessions:
                    self.user_sessions[user_id].pop('editing_content_id', None)
            else:
                await update.message.reply_text(
                    "❌ *Approval Failed* \n\n"
                    "There was an error processing your edited content. Please try again.",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Edited content handling failed: {e}")
            await update.message.reply_text("❌ Failed to process edited content. Please try again.")
    
    async def _handle_regenerate_request(self, user_id: str, content_id: str, query):
        """Handle request to regenerate content"""
        try:
            await query.edit_message_text(
                "🔄 *Regenerating Content* \n\n"
                "I'm creating a new version of this content. Please wait...",
                parse_mode='Markdown'
            )
            
            # In production, call the regenerate API endpoint
            # For now, simulate regeneration
            success = await self._process_regeneration_via_api(content_id, user_id)
            
            if success:
                await query.edit_message_text(
                    "✅ *Content Regenerated!* \n\n"
                    "I've created a new version. Check your pending approvals with /pending",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    "❌ *Regeneration Failed* \n\n"
                    "There was an error regenerating the content. Please try again.",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Regeneration handling failed: {e}")
            await query.edit_message_text("❌ Regeneration failed. Please try again.")
    
    async def _handle_image_selection(self, user_id: str, content_id: str, action: str, query):
        """Handle image selection"""
        try:
            image_index = int(action.split('_')[1])
            
            # Process approval with selected image
            approval_request = ContentApprovalRequest(
                content_id=content_id,
                approved=True,
                edits=None,
                image_choice=f"image_{image_index}"  # In production, use actual image URL
            )
            
            success = await self._process_approval_via_api(approval_request, user_id)
            
            if success:
                await query.edit_message_text(
                    f"✅ *Content Approved with Image #{image_index + 1}!* \n\n"
                    "Your content has been approved with the selected image and is being posted to LinkedIn.",
                    parse_mode='Markdown'
                )
            else:
                await query.edit_message_text(
                    "❌ *Approval Failed* \n\n"
                    "There was an error processing your approval. Please try again.",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Image selection handling failed: {e}")
            await query.edit_message_text("❌ Image selection failed. Please try again.")
    
    async def send_content_for_approval(
        self, 
        user_id: str, 
        content_id: str, 
        content: str,
        image_urls: Optional[List[str]] = None
    ) -> TelegramMessageResponse:
        """
        Send content to user for approval via Telegram
        
        Args:
            user_id: Telegram user ID
            content_id: Unique content identifier
            content: Generated content text
            image_urls: Optional list of image URLs
        
        Returns:
            TelegramMessageResponse with success status
        """
        try:
            # Create approval message with inline keyboard
            message_text = f"""
*📝 Content Ready for Approval*

*Topic:* {content_id}  # In production, use actual topic

{content}

*Please choose an action:*
            """
            
            # Create inline keyboard
            keyboard = [
                [
                    InlineKeyboardButton("✅ Approve", callback_data=json.dumps({
                        'action': 'approve', 
                        'content_id': content_id
                    })),
                    InlineKeyboardButton("✏️ Edit", callback_data=json.dumps({
                        'action': 'edit', 
                        'content_id': content_id
                    })),
                    InlineKeyboardButton("❌ Reject", callback_data=json.dumps({
                        'action': 'reject', 
                        'content_id': content_id
                    }))
                ]
            ]
            
            # Add image selection if multiple images available
            if image_urls and len(image_urls) > 1:
                image_buttons = []
                for i, image_url in enumerate(image_urls[:3]):  # Max 3 images
                    image_buttons.append(
                        InlineKeyboardButton(
                            f"🖼️ Image {i+1}", 
                            callback_data=json.dumps({
                                'action': f'image_{i}', 
                                'content_id': content_id
                            })
                        )
                    )
                keyboard.append(image_buttons)
            
            # Add regenerate button
            keyboard.append([
                InlineKeyboardButton("🔄 Regenerate", callback_data=json.dumps({
                    'action': 'regenerate', 
                    'content_id': content_id
                }))
            ])
            
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            # Send message
            message = await self.bot.send_message(
                chat_id=user_id,
                text=message_text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
            
            # Store approval context
            self.pending_approvals[content_id] = {
                'user_id': user_id,
                'message_id': message.message_id,
                'sent_at': datetime.now()
            }
            
            # Store in database
            with self.db_manager.get_session() as session:
                from app.models.database import ApprovalWorkflow
                
                workflow = ApprovalWorkflow(
                    content_id=content_id,
                    user_id=int(user_id),  # Convert to int for database
                    telegram_message_id=str(message.message_id),
                    telegram_chat_id=user_id,
                    sent_for_approval_at=datetime.now(),
                    original_content=content
                )
                session.add(workflow)
                session.commit()
            
            logger.info(f"Content sent for approval: {content_id} to user {user_id}")
            
            return TelegramMessageResponse(
                success=True,
                message_id=message.message_id
            )
            
        except TelegramError as e:
            logger.error(f"Telegram API error sending approval: {e}")
            return TelegramMessageResponse(
                success=False,
                error=f"Telegram API error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Error sending approval request: {e}")
            return TelegramMessageResponse(
                success=False,
                error=f"Unexpected error: {str(e)}"
            )
    
    async def send_approval_confirmation(
        self, 
        user_id: str, 
        content_id: str, 
        approved: bool,
        message: str
    ):
        """Send confirmation message after approval/rejection"""
        try:
            if approved:
                emoji = "✅"
                status = "approved"
            else:
                emoji = "❌" 
                status = "rejected"
            
            confirmation_text = f"""
{emoji} *Content {status.capitalize()}*

{message}

_Content ID: {content_id}_
            """
            
            await self.bot.send_message(
                chat_id=user_id,
                text=confirmation_text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Failed to send approval confirmation: {e}")
    
    async def send_post_success_notification(self, user_id: str, content_id: str, post_url: str):
        """Send notification when content is successfully posted to LinkedIn"""
        try:
            notification_text = f"""
🎉 *Content Posted Successfully!*

Your content has been published on LinkedIn.

[View Post on LinkedIn]({post_url})

_Content ID: {content_id}_
            """
            
            await self.bot.send_message(
                chat_id=user_id,
                text=notification_text,
                parse_mode='Markdown',
                disable_web_page_preview=False
            )
            
        except Exception as e:
            logger.error(f"Failed to send post success notification: {e}")
    
    async def send_post_failure_notification(self, user_id: str, content_id: str, error_message: str):
        """Send notification when LinkedIn posting fails"""
        try:
            notification_text = f"""
❌ *LinkedIn Posting Failed*

There was an error posting your content to LinkedIn.

_Error: {error_message}_

_Content ID: {content_id}_

Please try again or contact support.
            """
            
            await self.bot.send_message(
                chat_id=user_id,
                text=notification_text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Failed to send post failure notification: {e}")
    
    async def send_approval_reminder(self, user_id: str, content_id: str, content_text: str):
        """Send reminder for pending approval"""
        try:
            reminder_text = f"""
⏰ *Approval Reminder*

You have content waiting for your approval:

{content_text[:200]}...

Please check your pending approvals with /pending
            """
            
            await self.bot.send_message(
                chat_id=user_id,
                text=reminder_text,
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Failed to send approval reminder: {e}")
    
    async def update_content_approval(self, user_id: str, content_id: str, content: str):
        """Update existing approval message with new content (for regeneration)"""
        try:
            # This would update the existing message with new content
            # For simplicity, we'll send a new message
            await self.send_content_for_approval(user_id, content_id, content)
            
        except Exception as e:
            logger.error(f"Failed to update content approval: {e}")
    
    async def _process_approval_via_api(self, approval_request: ContentApprovalRequest, user_id: str) -> bool:
        """Process approval through FastAPI (simulated for now)"""
        try:
            # In production, make HTTP request to FastAPI
            # For now, simulate API call
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://localhost:{settings.PORT}{settings.API_V1_STR}/approval/approve",
                    json=approval_request.dict(),
                    headers={"User-ID": user_id}  # Simplified auth
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"API call failed: {e}")
            return False
    
    async def _process_regeneration_via_api(self, content_id: str, user_id: str) -> bool:
        """Process regeneration through FastAPI (simulated for now)"""
        try:
            # In production, make HTTP request to FastAPI
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"http://localhost:{settings.PORT}{settings.API_V1_STR}/content/{content_id}/regenerate",
                    headers={"User-ID": user_id}
                )
                return response.status_code == 200
        except Exception as e:
            logger.error(f"Regeneration API call failed: {e}")
            return False
    
    async def start_bot(self):
        """Start the Telegram bot (for polling mode)"""
        try:
            logger.info("Starting Telegram bot...")
            await self.application.run_polling()
        except Exception as e:
            logger.error(f"Failed to start Telegram bot: {e}")
    
    async def set_webhook(self):
        """Set Telegram webhook (for webhook mode)"""
        try:
            await self.bot.set_webhook(url=f"{self.webhook_url}/{self.bot_token}")
            logger.info("Telegram webhook set successfully")
        except Exception as e:
            logger.error(f"Failed to set Telegram webhook: {e}")
    
    async def process_webhook_update(self, update_data: Dict[str, Any]):
        """Process webhook update from Telegram"""
        try:
            update = Update.de_json(update_data, self.bot)
            await self.application.process_update(update)
        except Exception as e:
            logger.error(f"Failed to process webhook update: {e}")


# Global Telegram service instance
telegram_service = TelegramService()