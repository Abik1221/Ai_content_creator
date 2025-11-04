from pydantic import BaseModel, Field, validator, HttpUrl
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class ContentStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EDITED_APPROVED = "edited_approved"
    REJECTED = "rejected"
    POSTED = "posted"
    FAILED = "failed"


class ImageSource(str, Enum):
    UPLOAD = "upload"
    GENERATED = "generated"
    STOCK = "stock"


# Request Schemas
class ContentGenerationRequest(BaseModel):
    """Schema for content generation request"""
    company_info: str = Field(..., min_length=10, max_length=1000, description="Company information and context")
    topic: str = Field(..., min_length=3, max_length=200, description="Content topic or theme")
    style: str = Field(default="professional", description="Writing style (professional, casual, inspirational, etc.)")
    include_hashtags: bool = Field(default=True, description="Include relevant hashtags")
    target_audience: Optional[str] = Field(None, description="Target audience description")
    content_length: str = Field(default="medium", description="Content length (short, medium, long)")
    image_required: bool = Field(default=True, description="Whether to include an image")

    @validator('style')
    def validate_style(cls, v):
        allowed_styles = ['professional', 'casual', 'inspirational', 'technical', 'storytelling']
        if v.lower() not in allowed_styles:
            raise ValueError(f"Style must be one of: {', '.join(allowed_styles)}")
        return v.lower()

    @validator('content_length')
    def validate_content_length(cls, v):
        allowed_lengths = ['short', 'medium', 'long']
        if v.lower() not in allowed_lengths:
            raise ValueError(f"Content length must be one of: {', '.join(allowed_lengths)}")
        return v.lower()


class ImageGenerationRequest(BaseModel):
    """Schema for image generation request"""
    theme: str = Field(..., min_length=3, max_length=200, description="Image theme or description")
    style: str = Field(default="professional", description="Image style (professional, abstract, realistic, etc.)")
    count: int = Field(default=3, ge=1, le=5, description="Number of images to generate (1-5)")
    size: str = Field(default="1024x1024", description="Image dimensions")


class ContentApprovalRequest(BaseModel):
    """Schema for content approval request"""
    content_id: str = Field(..., description="Unique content identifier")
    approved: bool = Field(..., description="Whether the content is approved")
    edits: Optional[str] = Field(None, max_length=2000, description="User edits to the content")
    image_choice: Optional[str] = Field(None, description="Selected image URL or ID")


class TelegramWebhookRequest(BaseModel):
    """Schema for Telegram webhook updates"""
    update_id: int
    message: Optional[Dict[str, Any]] = None
    callback_query: Optional[Dict[str, Any]] = None


# Response Schemas
class ContentResponse(BaseModel):
    """Schema for content response"""
    content_id: str = Field(..., description="Unique content identifier")
    text: str = Field(..., description="Generated content text")
    hashtags: Optional[List[str]] = Field(None, description="Suggested hashtags")
    image_prompt: Optional[str] = Field(None, description="Generated image prompt")
    image_urls: Optional[List[str]] = Field(None, description="Generated or selected image URLs")
    status: ContentStatus = Field(..., description="Current content status")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: Optional[datetime] = Field(None, description="Last update timestamp")


class ImageResponse(BaseModel):
    """Schema for image response"""
    image_id: str = Field(..., description="Unique image identifier")
    url: str = Field(..., description="Image URL or path")
    source: ImageSource = Field(..., description="Image source type")
    description: Optional[str] = Field(None, description="Image description or prompt")
    created_at: datetime = Field(..., description="Creation timestamp")


class ApprovalResponse(BaseModel):
    """Schema for approval response"""
    content_id: str = Field(..., description="Unique content identifier")
    status: ContentStatus = Field(..., description="Updated content status")
    message: str = Field(..., description="Response message")
    posted_url: Optional[str] = Field(None, description="LinkedIn post URL if posted")
    posted_at: Optional[datetime] = Field(None, description="Posting timestamp")


class TelegramMessageResponse(BaseModel):
    """Schema for Telegram message response"""
    success: bool = Field(..., description="Whether the message was sent successfully")
    message_id: Optional[int] = Field(None, description="Telegram message ID")
    error: Optional[str] = Field(None, description="Error message if failed")


# Internal Schemas
class AgentState(BaseModel):
    """Schema for LangGraph agent state"""
    company_info: str
    topic: str
    style: str
    draft_content: Optional[str] = None
    final_content: Optional[str] = None
    image_prompt: Optional[str] = None
    hashtags: Optional[List[str]] = None
    status: str = "initialized"
    error: Optional[str] = None


class LinkedInPostRequest(BaseModel):
    """Schema for LinkedIn post request"""
    content: str = Field(..., min_length=10, max_length=3000, description="Post content")
    image_url: Optional[str] = Field(None, description="Image URL to attach")
    visibility: str = Field(default="PUBLIC", description="Post visibility (PUBLIC, CONNECTIONS)")
    author_urn: Optional[str] = Field(None, description="LinkedIn author URN")


class ErrorResponse(BaseModel):
    """Schema for error responses"""
    error: str = Field(..., description="Error message")
    code: str = Field(..., description="Error code")
    details: Optional[Dict[str, Any]] = Field(None, description="Additional error details")
    timestamp: datetime = Field(default_factory=datetime.now)


class HealthResponse(BaseModel):
    """Schema for health check response"""
    status: str = Field(..., description="Overall health status")
    timestamp: float = Field(..., description="Unix timestamp")
    services: Dict[str, str] = Field(..., description="Individual service statuses")


# Database Models (if using ORM, these would be SQLAlchemy models)
class ContentBase(BaseModel):
    """Base content model for database"""
    user_id: str
    company_info: str
    topic: str
    content_text: str
    status: ContentStatus
    image_url: Optional[str] = None
    linkedin_post_id: Optional[str] = None

    class Config:
        from_attributes = True


class ContentCreate(ContentBase):
    """Schema for creating content in database"""
    pass


class ContentUpdate(BaseModel):
    """Schema for updating content in database"""
    content_text: Optional[str] = None
    status: Optional[ContentStatus] = None
    image_url: Optional[str] = None
    linkedin_post_id: Optional[str] = None


class ContentDB(ContentBase):
    """Schema for content from database"""
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Utility function for error responses
def create_error_response(error: str, code: str, details: Optional[Dict[str, Any]] = None) -> ErrorResponse:
    """Helper function to create standardized error responses"""
    return ErrorResponse(
        error=error,
        code=code,
        details=details,
        timestamp=datetime.now()
    )