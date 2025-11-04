"""
External service integrations.
"""

from app.services.telegram_service import TelegramService, telegram_service
from app.services.linkedin_service import LinkedInService, linkedin_service
from app.services.image_service import ImageService, image_service
from app.services.storage_service import StorageService, storage_service

__all__ = [
    "TelegramService", "telegram_service",
    "LinkedInService", "linkedin_service", 
    "ImageService", "image_service",
    "StorageService", "storage_service"
]