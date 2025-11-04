from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, JSON, Enum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.sql import func
from contextlib import contextmanager
import enum
import logging
from typing import Generator, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# SQLAlchemy setup
Base = declarative_base()

# Enums for database
class ContentStatusDB(enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EDITED_APPROVED = "edited_approved"
    REJECTED = "rejected"
    POSTED = "posted"
    FAILED = "failed"

class ImageSourceDB(enum.Enum):
    UPLOAD = "upload"
    GENERATED = "generated"
    STOCK = "stock"


# Database Models
class User(Base):
    """User model for storing user information"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    telegram_chat_id = Column(String(100), unique=True, index=True)
    linkedin_access_token = Column(Text)
    linkedin_user_id = Column(String(100))
    company_info = Column(Text)
    preferences = Column(JSON, default={})  # Store user preferences
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class Content(Base):
    """Content model for storing generated content"""
    __tablename__ = "content"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(String(100), unique=True, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    
    # Content details
    company_info = Column(Text, nullable=False)
    topic = Column(String(500), nullable=False)
    style = Column(String(100), default="professional")
    content_text = Column(Text, nullable=False)
    hashtags = Column(JSON)  # Store as JSON array
    
    # Image information
    image_prompt = Column(Text)
    image_url = Column(String(500))
    image_source = Column(Enum(ImageSourceDB))
    
    # Status and workflow
    status = Column(Enum(ContentStatusDB), default=ContentStatusDB.DRAFT)
    edits = Column(Text)  # Store user edits
    
    # LinkedIn integration
    linkedin_post_id = Column(String(200))
    linkedin_post_url = Column(String(500))
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    posted_at = Column(DateTime(timezone=True))

    def __repr__(self):
        return f"<Content(content_id={self.content_id}, status={self.status})>"


class ImageAsset(Base):
    """Image asset model for storing image information"""
    __tablename__ = "image_assets"

    id = Column(Integer, primary_key=True, index=True)
    image_id = Column(String(100), unique=True, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    
    # Image details
    file_path = Column(String(500))
    file_url = Column(String(500))
    file_size = Column(Integer)
    mime_type = Column(String(100))
    
    # Generation info
    prompt = Column(Text)
    source = Column(Enum(ImageSourceDB), nullable=False)
    theme = Column(String(300))
    style = Column(String(100))
    
    # Usage tracking
    used_in_content = Column(Boolean, default=False)
    content_id = Column(String(100))  # Reference to content if used
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f"<ImageAsset(image_id={self.image_id}, source={self.source})>"


class ApprovalWorkflow(Base):
    """Approval workflow model for tracking human-in-the-loop process"""
    __tablename__ = "approval_workflows"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(String(100), unique=True, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    
    # Telegram integration
    telegram_message_id = Column(String(100))
    telegram_chat_id = Column(String(100))
    
    # Approval process
    sent_for_approval_at = Column(DateTime(timezone=True))
    approved_at = Column(DateTime(timezone=True))
    approved_by = Column(String(255))
    rejection_reason = Column(Text)
    
    # Edit history
    original_content = Column(Text)
    edited_content = Column(Text)
    
    # Status
    is_completed = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<ApprovalWorkflow(content_id={self.content_id}, is_completed={self.is_completed})>"


class LinkedInPost(Base):
    """LinkedIn post tracking model"""
    __tablename__ = "linkedin_posts"

    id = Column(Integer, primary_key=True, index=True)
    content_id = Column(String(100), unique=True, index=True, nullable=False)
    user_id = Column(Integer, index=True, nullable=False)
    
    # Post details
    linkedin_post_id = Column(String(200), unique=True, index=True)
    post_url = Column(String(500))
    post_content = Column(Text)
    
    # Status
    posted_successfully = Column(Boolean, default=False)
    error_message = Column(Text)
    
    # Metrics (to be updated later)
    impressions = Column(Integer, default=0)
    engagements = Column(Integer, default=0)
    
    # Timestamps
    posted_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def __repr__(self):
        return f"<LinkedInPost(content_id={self.content_id}, posted_successfully={self.posted_successfully})>"


# Database connection and session management
class DatabaseManager:
    """Database manager for handling database connections and sessions"""
    
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.engine = None
        self.SessionLocal = None
        self._setup_database()
    
    def _setup_database(self):
        """Setup database engine and session factory"""
        try:
            # Create engine
            self.engine = create_engine(
                self.database_url,
                pool_pre_ping=True,
                pool_recycle=300,
                echo=settings.DEBUG  # Log SQL queries in debug mode
            )
            
            # Create session factory
            self.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=self.engine
            )
            
            logger.info("Database engine and session factory setup successfully")
            
        except Exception as e:
            logger.error(f"Failed to setup database: {e}")
            raise
    
    def create_tables(self):
        """Create all database tables"""
        try:
            Base.metadata.create_all(bind=self.engine)
            logger.info("Database tables created successfully")
        except Exception as e:
            logger.error(f"Failed to create database tables: {e}")
            raise
    
    @contextmanager
    def get_session(self) -> Generator:
        """Get database session context manager"""
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception as e:
            session.rollback()
            logger.error(f"Database session error: {e}")
            raise
        finally:
            session.close()
    
    def get_db_session(self):
        """Get database session for dependency injection"""
        with self.get_session() as session:
            yield session


# Global database manager instance
db_manager = DatabaseManager(settings.DATABASE_URL)


# Utility functions for common database operations
class DatabaseUtils:
    """Utility class for common database operations"""
    
    @staticmethod
    def generate_content_id() -> str:
        """Generate unique content ID"""
        import uuid
        return f"content_{uuid.uuid4().hex[:12]}"
    
    @staticmethod
    def generate_image_id() -> str:
        """Generate unique image ID"""
        import uuid
        return f"img_{uuid.uuid4().hex[:12]}"
    
    @staticmethod
    def content_status_to_db_enum(status: str) -> ContentStatusDB:
        """Convert string status to database enum"""
        status_map = {
            "draft": ContentStatusDB.DRAFT,
            "pending_approval": ContentStatusDB.PENDING_APPROVAL,
            "approved": ContentStatusDB.APPROVED,
            "edited_approved": ContentStatusDB.EDITED_APPROVED,
            "rejected": ContentStatusDB.REJECTED,
            "posted": ContentStatusDB.POSTED,
            "failed": ContentStatusDB.FAILED
        }
        return status_map.get(status.lower(), ContentStatusDB.DRAFT)
    
    @staticmethod
    def image_source_to_db_enum(source: str) -> ImageSourceDB:
        """Convert string source to database enum"""
        source_map = {
            "upload": ImageSourceDB.UPLOAD,
            "generated": ImageSourceDB.GENERATED,
            "stock": ImageSourceDB.STOCK
        }
        return source_map.get(source.lower(), ImageSourceDB.UPLOAD)


# Initialize database on startup
def init_database():
    """Initialize database on application startup"""
    try:
        db_manager.create_tables()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database: {e}")
        raise