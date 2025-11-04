"""
Application startup and shutdown events.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI

from app.core.config import settings
from app.utils.logging import setup_logging
from app.models.database import init_database
from app.services.telegram_service import telegram_service
from app.services.linkedin_service import linkedin_service
from app.services.image_service import image_service

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Lifespan events for FastAPI application.
    Handles startup and shutdown of services.
    """
    # Startup
    logger.info("Starting LinkedIn Content Agent application...")
    
    try:
        # Initialize logging
        setup_logging()
        
        # Initialize database
        init_database()
        logger.info("Database initialized successfully")
        
        # Initialize services
        await _initialize_services()
        logger.info("All services initialized successfully")
        
        # Test external connections
        await _test_external_connections()
        logger.info("External connection tests completed")
        
        logger.info("✅ LinkedIn Content Agent started successfully!")
        
        yield
        
    except Exception as e:
        logger.error(f"❌ Application startup failed: {e}")
        raise
    
    finally:
        # Shutdown
        logger.info("Shutting down LinkedIn Content Agent...")
        await _shutdown_services()
        logger.info("LinkedIn Content Agent shutdown completed")


async def _initialize_services():
    """Initialize all external services"""
    try:
        # Test LinkedIn connection
        linkedin_test = await linkedin_service.test_connection()
        if linkedin_test.get("connected"):
            logger.info("✅ LinkedIn API connected successfully")
        else:
            logger.warning("⚠️ LinkedIn API connection failed")
        
        # Test Telegram bot (if token provided)
        if settings.TELEGRAM_BOT_TOKEN:
            logger.info("Telegram bot configured")
        else:
            logger.warning("Telegram bot token not configured")
        
        # Test image services
        if settings.OPENAI_API_KEY:
            logger.info("OpenAI image generation available")
        if settings.STABILITY_API_KEY:
            logger.info("Stability AI image generation available")
        
    except Exception as e:
        logger.error(f"Service initialization failed: {e}")
        raise


async def _test_external_connections():
    """Test connections to external services"""
    try:
        # Test database connection
        from app.models.database import db_manager
        with db_manager.get_session() as session:
            # Simple query to test connection
            session.execute("SELECT 1")
        logger.info("✅ Database connection test passed")
        
    except Exception as e:
        logger.error(f"Database connection test failed: {e}")
        raise


async def _shutdown_services():
    """Shutdown all external service connections"""
    try:
        await linkedin_service.close()
        await image_service.close()
        logger.info("All service connections closed successfully")
        
    except Exception as e:
        logger.error(f"Service shutdown failed: {e}")


def create_app() -> FastAPI:
    """Create and configure FastAPI application"""
    from app.main import app
    
    # Add lifespan context
    app.router.lifespan_context = lifespan
    
    return app