from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from typing import List, Optional
import logging
import uuid
from datetime import datetime

from app.models.schemas import (
    ContentGenerationRequest,
    ContentResponse,
    ErrorResponse,
    create_error_response
)
from app.models.database import DatabaseManager, DatabaseUtils
from app.agents.content_agent import ContentGenerationAgent
from app.services.telegram_service import TelegramService
from app.services.image_service import ImageService
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize services
content_agent = ContentGenerationAgent()
telegram_service = TelegramService()
image_service = ImageService()
db_manager = DatabaseManager(settings.DATABASE_URL)


@router.post(
    "/generate",
    response_model=ContentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Generate LinkedIn Content",
    description="Generate LinkedIn content using AI agent with human-in-the-loop approval workflow",
    responses={
        201: {"description": "Content generated successfully"},
        400: {"description": "Invalid request parameters"},
        500: {"description": "Internal server error"}
    }
)
async def generate_content(
    request: ContentGenerationRequest,
    background_tasks: BackgroundTasks,
    user_id: str = "default_user"  # In production, get from auth token
):
    """
    Generate LinkedIn content based on company info and topic.
    
    This endpoint:
    - Uses LangGraph agent to generate professional content
    - Optionally generates or selects images
    - Sends content to Telegram for human approval
    - Stores content in database for tracking
    """
    try:
        logger.info(f"Generating content for user {user_id}, topic: {request.topic}")
        
        # Generate content using LangGraph agent
        agent_result = await content_agent.generate_content(
            company_info=request.company_info,
            topic=request.topic,
            style=request.style,
            target_audience=request.target_audience,
            content_length=request.content_length
        )
        
        if not agent_result or not agent_result.final_content:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to generate content"
            )
        
        # Generate image if required
        image_urls = None
        if request.image_required:
            try:
                image_result = await image_service.generate_images(
                    theme=request.topic,
                    style=request.style,
                    count=3
                )
                image_urls = [img.url for img in image_result]
            except Exception as img_error:
                logger.warning(f"Image generation failed: {img_error}")
                # Continue without images
        
        # Generate content ID and prepare response
        content_id = DatabaseUtils.generate_content_id()
        
        # Store content in database
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB, ImageSourceDB
            
            content_db = Content(
                content_id=content_id,
                user_id=user_id,  # In production, use actual user ID
                company_info=request.company_info,
                topic=request.topic,
                style=request.style,
                content_text=agent_result.final_content,
                hashtags=agent_result.hashtags,
                image_prompt=agent_result.image_prompt,
                image_url=image_urls[0] if image_urls else None,
                image_source=ImageSourceDB.GENERATED if image_urls else None,
                status=ContentStatusDB.PENDING_APPROVAL
            )
            session.add(content_db)
            session.commit()
        
        # Prepare response
        response = ContentResponse(
            content_id=content_id,
            text=agent_result.final_content,
            hashtags=agent_result.hashtags,
            image_prompt=agent_result.image_prompt,
            image_urls=image_urls,
            status="pending_approval",
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        
        # Send to Telegram for approval in background
        background_tasks.add_task(
            telegram_service.send_content_for_approval,
            user_id=user_id,
            content_id=content_id,
            content=agent_result.final_content,
            image_urls=image_urls
        )
        
        logger.info(f"Content generated successfully: {content_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Content generation failed: {str(e)}"
        )


@router.get(
    "/{content_id}",
    response_model=ContentResponse,
    summary="Get Content by ID",
    description="Retrieve generated content by its unique identifier",
    responses={
        200: {"description": "Content retrieved successfully"},
        404: {"description": "Content not found"},
        500: {"description": "Internal server error"}
    }
)
async def get_content(content_id: str, user_id: str = "default_user"):
    """
    Retrieve specific content by ID.
    
    Returns the content with its current status and details.
    """
    try:
        logger.info(f"Retrieving content: {content_id} for user: {user_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content
            
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
            
            # Convert database model to response schema
            response = ContentResponse(
                content_id=content.content_id,
                text=content.content_text,
                hashtags=content.hashtags,
                image_prompt=content.image_prompt,
                image_urls=[content.image_url] if content.image_url else None,
                status=content.status.value,
                created_at=content.created_at,
                updated_at=content.updated_at
            )
            
            return response
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve content: {str(e)}"
        )


@router.get(
    "/",
    response_model=List[ContentResponse],
    summary="List User Content",
    description="Retrieve all content for the authenticated user with optional filtering",
    responses={
        200: {"description": "Content list retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
async def list_content(
    status: Optional[str] = None,
    limit: int = 10,
    offset: int = 0,
    user_id: str = "default_user"
):
    """
    List all content for the user with optional status filtering.
    
    Supports pagination and status-based filtering.
    """
    try:
        logger.info(f"Listing content for user: {user_id}, status: {status}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content
            
            query = session.query(Content).filter(Content.user_id == user_id)
            
            # Apply status filter if provided
            if status:
                query = query.filter(Content.status == DatabaseUtils.content_status_to_db_enum(status))
            
            # Apply pagination
            content_list = query.order_by(
                Content.created_at.desc()
            ).offset(offset).limit(limit).all()
            
            # Convert to response models
            response = []
            for content in content_list:
                response.append(ContentResponse(
                    content_id=content.content_id,
                    text=content.content_text,
                    hashtags=content.hashtags,
                    image_prompt=content.image_prompt,
                    image_urls=[content.image_url] if content.image_url else None,
                    status=content.status.value,
                    created_at=content.created_at,
                    updated_at=content.updated_at
                ))
            
            return response
            
    except Exception as e:
        logger.error(f"Error listing content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list content: {str(e)}"
        )


@router.delete(
    "/{content_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Content",
    description="Delete specific content by ID",
    responses={
        200: {"description": "Content deleted successfully"},
        404: {"description": "Content not found"},
        500: {"description": "Internal server error"}
    }
)
async def delete_content(content_id: str, user_id: str = "default_user"):
    """
    Delete content by ID.
    
    Only allows deletion of content that hasn't been posted to LinkedIn.
    """
    try:
        logger.info(f"Deleting content: {content_id} for user: {user_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import Content, ContentStatusDB
            
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
            
            # Prevent deletion of posted content
            if content.status == ContentStatusDB.POSTED:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete content that has been posted to LinkedIn"
                )
            
            session.delete(content)
            session.commit()
            
            logger.info(f"Content deleted successfully: {content_id}")
            return {
                "message": "Content deleted successfully",
                "content_id": content_id
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete content: {str(e)}"
        )


@router.post(
    "/{content_id}/regenerate",
    response_model=ContentResponse,
    summary="Regenerate Content",
    description="Regenerate content with the same parameters but different output",
    responses={
        200: {"description": "Content regenerated successfully"},
        404: {"description": "Content not found"},
        500: {"description": "Internal server error"}
    }
)
async def regenerate_content(
    content_id: str,
    background_tasks: BackgroundTasks,
    user_id: str = "default_user"
):
    """
    Regenerate content with different variations.
    
    Uses the same parameters but generates new content variations.
    """
    try:
        logger.info(f"Regenerating content: {content_id} for user: {user_id}")
        
        # Get original content
        with db_manager.get_session() as session:
            from app.models.database import Content
            
            original_content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not original_content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
        
        # Regenerate content using agent
        agent_result = await content_agent.generate_content(
            company_info=original_content.company_info,
            topic=original_content.topic,
            style=original_content.style,
            content_length="medium"  # Use same length or make configurable
        )
        
        if not agent_result or not agent_result.final_content:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to regenerate content"
            )
        
        # Update content in database
        with db_manager.get_session() as session:
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            content.content_text = agent_result.final_content
            content.hashtags = agent_result.hashtags
            content.image_prompt = agent_result.image_prompt
            content.updated_at = datetime.now()
            session.commit()
        
        # Prepare response
        response = ContentResponse(
            content_id=content_id,
            text=agent_result.final_content,
            hashtags=agent_result.hashtags,
            image_prompt=agent_result.image_prompt,
            image_urls=[content.image_url] if content.image_url else None,
            status=content.status.value,
            created_at=content.created_at,
            updated_at=content.updated_at
        )
        
        # Update Telegram message if needed
        background_tasks.add_task(
            telegram_service.update_content_approval,
            user_id=user_id,
            content_id=content_id,
            content=agent_result.final_content
        )
        
        logger.info(f"Content regenerated successfully: {content_id}")
        return response
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Content regeneration failed: {str(e)}"
        )