"""
Dependency injections for FastAPI routes.
"""

from fastapi import Header, HTTPException, Depends, status
from typing import Optional
import logging

from app.core.config import settings
from app.models.database import DatabaseManager

logger = logging.getLogger(__name__)

# Database dependency
db_manager = DatabaseManager(settings.DATABASE_URL)


async def get_database():
    """Dependency for database session"""
    try:
        with db_manager.get_session() as session:
            yield session
    except Exception as e:
        logger.error(f"Database session error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database connection failed"
        )


async def verify_api_key(api_key: str = Header(None, alias="X-API-Key")):
    """
    Verify API key for protected endpoints.
    
    In production, this would validate against a database of API keys.
    """
    if not settings.DEBUG and not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key required"
        )
    
    # In production, validate against stored API keys
    # For now, accept any non-empty key in development
    if settings.DEBUG and api_key:
        return api_key
    
    # TODO: Implement proper API key validation
    valid_keys = []  # Would be loaded from database
    
    if api_key not in valid_keys:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key"
        )
    
    return api_key


async def get_current_user(
    user_id: str = Header(None, alias="User-ID"),
    api_key: str = Depends(verify_api_key)
):
    """
    Get current user from request headers.
    
    In production, this would validate JWT tokens or session cookies.
    """
    if settings.DEBUG and not user_id:
        # Default user for development
        return "dev_user_123"
    
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User authentication required"
        )
    
    # TODO: Implement proper user validation
    # For now, accept any user ID in development
    return user_id


async def validate_content_length(content: str):
    """Validate content length before processing"""
    from app.utils.helpers import ContentHelper
    
    validation = ContentHelper.validate_content_length(content)
    
    if not validation["valid"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Content length must be between 100 and 3000 characters. Current: {validation['length']}"
        )
    
    return content


async def rate_limit_check(
    user_id: str = Depends(get_current_user),
    db=Depends(get_database)
):
    """
    Basic rate limiting check.
    
    In production, this would use Redis or similar for distributed rate limiting.
    """
    from datetime import datetime, timedelta
    
    # TODO: Implement proper rate limiting
    # For now, simple in-memory check (not suitable for production)
    
    # This would check if the user has exceeded their rate limit
    # and raise HTTPException if they have
    
    return user_id