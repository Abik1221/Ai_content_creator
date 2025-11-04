from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, status
from fastapi.responses import JSONResponse, FileResponse
from typing import List, Optional
import logging
import os
import uuid
from datetime import datetime
import aiofiles

from app.models.schemas import (
    ImageGenerationRequest,
    ImageResponse,
    ErrorResponse,
    create_error_response
)
from app.models.database import DatabaseManager, DatabaseUtils
from app.services.image_service import ImageService
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# Initialize services
image_service = ImageService()
db_manager = DatabaseManager(settings.DATABASE_URL)


@router.post(
    "/generate",
    response_model=List[ImageResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Generate AI Images",
    description="Generate images using AI based on theme and style specifications",
    responses={
        201: {"description": "Images generated successfully"},
        400: {"description": "Invalid request parameters"},
        500: {"description": "Image generation failed"}
    }
)
async def generate_images(
    request: ImageGenerationRequest,
    user_id: str = "default_user"
):
    """
    Generate AI images for LinkedIn content.
    
    Supports multiple AI models and styles:
    - Professional business images
    - Abstract concepts
    - Brand-themed visuals
    - Custom styles and dimensions
    """
    try:
        logger.info(f"Generating images for user {user_id}, theme: {request.theme}")
        
        # Validate image size
        allowed_sizes = ["256x256", "512x512", "1024x1024", "1024x1792", "1792x1024"]
        if request.size not in allowed_sizes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid image size. Allowed sizes: {', '.join(allowed_sizes)}"
            )
        
        # Generate images using image service
        generated_images = await image_service.generate_images(
            theme=request.theme,
            style=request.style,
            count=request.count,
            size=request.size
        )
        
        if not generated_images:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No images were generated"
            )
        
        # Store image metadata in database
        image_responses = []
        with db_manager.get_session() as session:
            from app.models.database import ImageAsset, ImageSourceDB
            
            for img in generated_images:
                image_id = DatabaseUtils.generate_image_id()
                
                # Create database record
                image_asset = ImageAsset(
                    image_id=image_id,
                    user_id=user_id,
                    file_url=img.url,
                    file_size=img.size if hasattr(img, 'size') else 0,
                    mime_type="image/png",  # Default, adjust based on actual type
                    prompt=request.theme,
                    source=ImageSourceDB.GENERATED,
                    theme=request.theme,
                    style=request.style,
                    created_at=datetime.now()
                )
                session.add(image_asset)
                
                # Create response object
                image_response = ImageResponse(
                    image_id=image_id,
                    url=img.url,
                    source="generated",
                    description=request.theme,
                    created_at=datetime.now()
                )
                image_responses.append(image_response)
            
            session.commit()
        
        logger.info(f"Successfully generated {len(image_responses)} images")
        return image_responses
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating images: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Image generation failed: {str(e)}"
        )


@router.post(
    "/upload",
    response_model=ImageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload Image",
    description="Upload custom images for use in LinkedIn content",
    responses={
        201: {"description": "Image uploaded successfully"},
        400: {"description": "Invalid file type or size"},
        500: {"description": "Upload failed"}
    }
)
async def upload_image(
    file: UploadFile = File(..., description="Image file to upload"),
    description: Optional[str] = Form(None, description="Image description"),
    user_id: str = "default_user"
):
    """
    Upload custom images for content.
    
    Supported formats: JPEG, PNG, WebP
    Maximum file size: 10MB
    """
    try:
        logger.info(f"Uploading image for user {user_id}, filename: {file.filename}")
        
        # Validate file type
        if file.content_type not in settings.ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid file type. Allowed types: {', '.join(settings.ALLOWED_IMAGE_TYPES)}"
            )
        
        # Read file content to validate size
        file_content = await file.read()
        file_size = len(file_content)
        
        if file_size > settings.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"File too large. Maximum size: {settings.MAX_FILE_SIZE // 1024 // 1024}MB"
            )
        
        # Reset file pointer
        await file.seek(0)
        
        # Generate unique filename
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4().hex}{file_extension}"
        
        # Create upload directory if it doesn't exist
        upload_dir = os.path.join(settings.UPLOAD_DIR, user_id)
        os.makedirs(upload_dir, exist_ok=True)
        
        # Save file
        file_path = os.path.join(upload_dir, unique_filename)
        async with aiofiles.open(file_path, 'wb') as f:
            content = await file.read()
            await f.write(content)
        
        # Generate file URL (in production, this would be a CDN URL)
        file_url = f"/api/v1/images/file/{user_id}/{unique_filename}"
        
        # Store metadata in database
        image_id = DatabaseUtils.generate_image_id()
        with db_manager.get_session() as session:
            from app.models.database import ImageAsset, ImageSourceDB
            
            image_asset = ImageAsset(
                image_id=image_id,
                user_id=user_id,
                file_path=file_path,
                file_url=file_url,
                file_size=file_size,
                mime_type=file.content_type,
                prompt=description,
                source=ImageSourceDB.UPLOAD,
                theme=description or "Uploaded image",
                style="custom",
                created_at=datetime.now()
            )
            session.add(image_asset)
            session.commit()
        
        logger.info(f"Image uploaded successfully: {image_id}")
        
        return ImageResponse(
            image_id=image_id,
            url=file_url,
            source="upload",
            description=description,
            created_at=datetime.now()
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error uploading image: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Image upload failed: {str(e)}"
        )


@router.get(
    "/file/{user_id}/{filename}",
    summary="Get Image File",
    description="Retrieve uploaded image file by filename",
    responses={
        200: {"description": "Image file returned successfully"},
        404: {"description": "Image not found"},
        500: {"description": "File retrieval failed"}
    }
)
async def get_image_file(user_id: str, filename: str):
    """
    Serve uploaded image files.
    
    This endpoint serves images that were uploaded by users.
    In production, consider using a CDN or dedicated file server.
    """
    try:
        file_path = os.path.join(settings.UPLOAD_DIR, user_id, filename)
        
        if not os.path.exists(file_path):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Image file not found"
            )
        
        # Determine media type from file extension
        file_extension = os.path.splitext(filename)[1].lower()
        media_types = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.webp': 'image/webp'
        }
        media_type = media_types.get(file_extension, 'image/jpeg')
        
        return FileResponse(
            path=file_path,
            media_type=media_type,
            filename=filename
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving image file: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"File retrieval failed: {str(e)}"
        )


@router.get(
    "/",
    response_model=List[ImageResponse],
    summary="List User Images",
    description="Retrieve all images for the authenticated user with optional filtering",
    responses={
        200: {"description": "Images list retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
async def list_images(
    source: Optional[str] = None,
    theme: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    user_id: str = "default_user"
):
    """
    List all images for the user.
    
    Supports filtering by source and theme, with pagination.
    """
    try:
        logger.info(f"Listing images for user: {user_id}, source: {source}, theme: {theme}")
        
        with db_manager.get_session() as session:
            from app.models.database import ImageAsset, ImageSourceDB
            
            query = session.query(ImageAsset).filter(ImageAsset.user_id == user_id)
            
            # Apply source filter
            if source:
                source_enum = ImageSourceDB[source.upper()]
                query = query.filter(ImageAsset.source == source_enum)
            
            # Apply theme filter
            if theme:
                query = query.filter(ImageAsset.theme.ilike(f"%{theme}%"))
            
            # Apply pagination and ordering
            images = query.order_by(
                ImageAsset.created_at.desc()
            ).offset(offset).limit(limit).all()
            
            # Convert to response models
            image_responses = []
            for img in images:
                image_responses.append(ImageResponse(
                    image_id=img.image_id,
                    url=img.file_url,
                    source=img.source.value,
                    description=img.theme,
                    created_at=img.created_at
                ))
            
            return image_responses
            
    except Exception as e:
        logger.error(f"Error listing images: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list images: {str(e)}"
        )


@router.get(
    "/{image_id}",
    response_model=ImageResponse,
    summary="Get Image by ID",
    description="Retrieve specific image metadata by its unique identifier",
    responses={
        200: {"description": "Image retrieved successfully"},
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"}
    }
)
async def get_image(image_id: str, user_id: str = "default_user"):
    """
    Get specific image metadata by ID.
    
    Returns image details including source, creation date, and usage information.
    """
    try:
        logger.info(f"Retrieving image: {image_id} for user: {user_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import ImageAsset
            
            image = session.query(ImageAsset).filter(
                ImageAsset.image_id == image_id,
                ImageAsset.user_id == user_id
            ).first()
            
            if not image:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Image with ID {image_id} not found"
                )
            
            return ImageResponse(
                image_id=image.image_id,
                url=image.file_url,
                source=image.source.value,
                description=image.theme,
                created_at=image.created_at
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving image: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve image: {str(e)}"
        )


@router.delete(
    "/{image_id}",
    status_code=status.HTTP_200_OK,
    summary="Delete Image",
    description="Delete specific image by ID",
    responses={
        200: {"description": "Image deleted successfully"},
        404: {"description": "Image not found"},
        400: {"description": "Image is used in content"},
        500: {"description": "Internal server error"}
    }
)
async def delete_image(image_id: str, user_id: str = "default_user"):
    """
    Delete image by ID.
    
    Prevents deletion of images that are currently used in content.
    Also removes the physical file from storage.
    """
    try:
        logger.info(f"Deleting image: {image_id} for user: {user_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import ImageAsset
            
            image = session.query(ImageAsset).filter(
                ImageAsset.image_id == image_id,
                ImageAsset.user_id == user_id
            ).first()
            
            if not image:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Image with ID {image_id} not found"
                )
            
            # Check if image is used in content
            if image.used_in_content:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cannot delete image that is used in content"
                )
            
            # Delete physical file if it exists
            if image.file_path and os.path.exists(image.file_path):
                try:
                    os.remove(image.file_path)
                    logger.info(f"Deleted physical file: {image.file_path}")
                except Exception as file_error:
                    logger.warning(f"Failed to delete physical file: {file_error}")
            
            # Delete database record
            session.delete(image)
            session.commit()
            
            logger.info(f"Image deleted successfully: {image_id}")
            return {
                "message": "Image deleted successfully",
                "image_id": image_id
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting image: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete image: {str(e)}"
        )


@router.post(
    "/{image_id}/assign-to-content",
    status_code=status.HTTP_200_OK,
    summary="Assign Image to Content",
    description="Mark image as used in specific content",
    responses={
        200: {"description": "Image assigned successfully"},
        404: {"description": "Image not found"},
        500: {"description": "Internal server error"}
    }
)
async def assign_image_to_content(
    image_id: str,
    content_id: str = Form(..., description="Content ID to assign image to"),
    user_id: str = "default_user"
):
    """
    Assign image to specific content.
    
    Marks the image as used and creates association with content.
    """
    try:
        logger.info(f"Assigning image {image_id} to content {content_id}")
        
        with db_manager.get_session() as session:
            from app.models.database import ImageAsset, Content
            
            # Verify image exists
            image = session.query(ImageAsset).filter(
                ImageAsset.image_id == image_id,
                ImageAsset.user_id == user_id
            ).first()
            
            if not image:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Image with ID {image_id} not found"
                )
            
            # Verify content exists
            content = session.query(Content).filter(
                Content.content_id == content_id,
                Content.user_id == user_id
            ).first()
            
            if not content:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Content with ID {content_id} not found"
                )
            
            # Update image assignment
            image.used_in_content = True
            image.content_id = content_id
            
            # Update content with image
            content.image_url = image.file_url
            content.image_source = image.source
            content.updated_at = datetime.now()
            
            session.commit()
            
            return {
                "message": "Image assigned to content successfully",
                "image_id": image_id,
                "content_id": content_id
            }
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error assigning image to content: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to assign image to content: {str(e)}"
        )


@router.get(
    "/stock/{theme}",
    response_model=List[ImageResponse],
    summary="Get Stock Images",
    description="Retrieve relevant stock images based on theme",
    responses={
        200: {"description": "Stock images retrieved successfully"},
        500: {"description": "Internal server error"}
    }
)
async def get_stock_images(
    theme: str,
    count: int = 5,
    user_id: str = "default_user"
):
    """
    Get stock images for content theme.
    
    Returns relevant stock images that can be used for LinkedIn posts.
    In production, this would integrate with stock image APIs.
    """
    try:
        logger.info(f"Getting stock images for theme: {theme}")
        
        # This would integrate with actual stock image APIs in production
        # For now, return placeholder response
        stock_images = await image_service.get_stock_images(theme, count)
        
        # Convert to response models
        image_responses = []
        for img in stock_images:
            image_id = DatabaseUtils.generate_image_id()
            image_responses.append(ImageResponse(
                image_id=image_id,
                url=img.url,
                source="stock",
                description=theme,
                created_at=datetime.now()
            ))
        
        return image_responses
        
    except Exception as e:
        logger.error(f"Error getting stock images: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get stock images: {str(e)}"
        )