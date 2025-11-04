import logging
import asyncio
import aiofiles
import aiohttp
import os
import uuid
from typing import List, Optional, Dict, Any
from datetime import datetime
from pathlib import Path

from app.core.config import settings
from app.models.schemas import ImageResponse, ImageGenerationRequest
from app.models.database import DatabaseManager

logger = logging.getLogger(__name__)


class ImageService:
    """
    Image service for generating and managing images for LinkedIn content.
    
    Supports:
    - AI image generation (OpenAI DALL-E, Stable Diffusion, etc.)
    - Image upload and storage
    - Stock image integration
    - Image optimization and formatting
    """
    
    def __init__(self):
        self.storage_dir = Path(settings.UPLOAD_DIR)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.db_manager = DatabaseManager(settings.DATABASE_URL)
        
        # AI Service configurations
        self.openai_api_key = settings.OPENAI_API_KEY
        self.stability_api_key = settings.STABILITY_API_KEY
        self.huggingface_token = settings.HUGGINGFACE_TOKEN
        
        # Initialize HTTP client
        self.client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60)
        )
    
    async def generate_images(
        self, 
        theme: str, 
        style: str = "professional",
        count: int = 3,
        size: str = "1024x1024"
    ) -> List[ImageResponse]:
        """
        Generate images using AI services.
        
        Args:
            theme: Image theme/description
            style: Image style (professional, abstract, etc.)
            count: Number of images to generate
            size: Image dimensions
            
        Returns:
            List of generated image responses
        """
        try:
            logger.info(f"Generating {count} images with theme: {theme}")
            
            images = []
            
            # Try OpenAI DALL-E first
            if self.openai_api_key:
                dall_e_images = await self._generate_with_dalle(theme, style, count, size)
                images.extend(dall_e_images)
            
            # If we need more images, try other services
            if len(images) < count and self.stability_api_key:
                remaining = count - len(images)
                stability_images = await self._generate_with_stability(theme, style, remaining, size)
                images.extend(stability_images)
            
            # Fallback to placeholder if no AI services available
            if not images:
                placeholder_images = await self._generate_placeholders(theme, count)
                images.extend(placeholder_images)
            
            logger.info(f"Successfully generated {len(images)} images")
            return images[:count]  # Ensure we don't exceed requested count
            
        except Exception as e:
            logger.error(f"Image generation failed: {e}")
            # Return placeholder images as fallback
            return await self._generate_placeholders(theme, count)
    
    async def _generate_with_dalle(
        self, 
        theme: str, 
        style: str,
        count: int,
        size: str
    ) -> List[ImageResponse]:
        """Generate images using OpenAI DALL-E"""
        try:
            prompt = self._build_image_prompt(theme, style)
            
            headers = {
                "Authorization": f"Bearer {self.openai_api_key}",
                "Content-Type": "application/json"
            }
            
            data = {
                "model": "dall-e-3",
                "prompt": prompt,
                "n": min(count, 3),  # DALL-E 3 max is 3
                "size": size,
                "quality": "standard",
                "style": "natural" if style == "professional" else "vivid"
            }
            
            async with self.client.post(
                "https://api.openai.com/v1/images/generations",
                headers=headers,
                json=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    images = []
                    for img_data in result.get("data", []):
                        image_url = img_data.get("url")
                        if image_url:
                            # Download and store image locally
                            local_url = await self._download_and_store_image(image_url, theme)
                            
                            images.append(ImageResponse(
                                image_id=str(uuid.uuid4()),
                                url=local_url,
                                source="generated",
                                description=theme,
                                created_at=datetime.now()
                            ))
                    
                    logger.info(f"DALL-E generated {len(images)} images")
                    return images
                else:
                    error_text = await response.text()
                    logger.error(f"DALL-E API error: {response.status} - {error_text}")
                    return []
                    
        except Exception as e:
            logger.error(f"DALL-E generation failed: {e}")
            return []
    
    async def _generate_with_stability(
        self, 
        theme: str, 
        style: str,
        count: int,
        size: str
    ) -> List[ImageResponse]:
        """Generate images using Stability AI"""
        try:
            if not self.stability_api_key:
                return []
            
            prompt = self._build_image_prompt(theme, style)
            
            headers = {
                "Authorization": f"Bearer {self.stability_api_key}",
                "Content-Type": "application/json"
            }
            
            # Map size to Stability AI format
            width, height = map(int, size.split('x'))
            
            data = {
                "text_prompts": [{"text": prompt}],
                "cfg_scale": 7,
                "height": height,
                "width": width,
                "samples": min(count, 4),
                "steps": 30,
            }
            
            async with self.client.post(
                "https://api.stability.ai/v1/generation/stable-diffusion-xl-1024-v1-0/text-to-image",
                headers=headers,
                json=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    images = []
                    for artifact in result.get("artifacts", []):
                        if artifact.get("base64"):
                            # Save base64 image to file
                            image_url = await self._save_base64_image(
                                artifact["base64"], 
                                theme
                            )
                            
                            images.append(ImageResponse(
                                image_id=str(uuid.uuid4()),
                                url=image_url,
                                source="generated",
                                description=theme,
                                created_at=datetime.now()
                            ))
                    
                    logger.info(f"Stability AI generated {len(images)} images")
                    return images
                else:
                    error_text = await response.text()
                    logger.error(f"Stability AI API error: {response.status} - {error_text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Stability AI generation failed: {e}")
            return []
    
    async def _generate_placeholders(self, theme: str, count: int) -> List[ImageResponse]:
        """Generate placeholder images when AI services are unavailable"""
        try:
            images = []
            
            # Use placeholder service or local generation
            for i in range(count):
                # For now, we'll use a placeholder service
                placeholder_url = f"https://picsum.photos/800/600?random={uuid.uuid4()}"
                
                # Download and store locally
                local_url = await self._download_and_store_image(placeholder_url, theme)
                
                images.append(ImageResponse(
                    image_id=str(uuid.uuid4()),
                    url=local_url,
                    source="generated",
                    description=f"{theme} (placeholder)",
                    created_at=datetime.now()
                ))
            
            logger.info(f"Generated {len(images)} placeholder images")
            return images
            
        except Exception as e:
            logger.error(f"Placeholder generation failed: {e}")
            return []
    
    def _build_image_prompt(self, theme: str, style: str) -> str:
        """Build professional image prompt for AI generation"""
        
        style_descriptions = {
            "professional": "professional business setting, clean, modern, corporate, professional photography, business environment",
            "abstract": "abstract concept, creative, artistic, symbolic, modern art, conceptual",
            "realistic": "photorealistic, detailed, realistic, high quality photography, natural lighting",
            "minimalist": "minimalist, clean, simple, elegant, white space, modern design",
            "inspirational": "inspiring, motivational, uplifting, positive, hopeful, visionary"
        }
        
        style_desc = style_descriptions.get(style, "professional business setting")
        
        prompt = f"""
        Professional LinkedIn post image: {theme}
        
        Style: {style_desc}
        Requirements:
        - Business appropriate
        - Professional quality
        - Clean and modern
        - LinkedIn suitable
        - High resolution
        - No text or watermarks
        
        Create an image that represents this theme in a professional business context.
        """
        
        return prompt.strip()
    
    async def _download_and_store_image(self, image_url: str, theme: str) -> str:
        """Download image from URL and store locally"""
        try:
            async with self.client.get(image_url) as response:
                if response.status == 200:
                    image_data = await response.read()
                    
                    # Generate filename
                    file_extension = self._get_file_extension_from_url(image_url) or ".jpg"
                    filename = f"{uuid.uuid4()}{file_extension}"
                    file_path = self.storage_dir / filename
                    
                    # Save file
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(image_data)
                    
                    # Return relative URL for API access
                    return f"/api/v1/images/file/{filename}"
                else:
                    raise Exception(f"Failed to download image: {response.status}")
                    
        except Exception as e:
            logger.error(f"Image download failed: {e}")
            # Return original URL as fallback
            return image_url
    
    async def _save_base64_image(self, base64_data: str, theme: str) -> str:
        """Save base64 image data to file"""
        try:
            # Remove data URL prefix if present
            if base64_data.startswith('data:image'):
                base64_data = base64_data.split(',', 1)[1]
            
            # Decode base64
            image_data = base64.b64decode(base64_data)
            
            # Generate filename
            filename = f"{uuid.uuid4()}.png"
            file_path = self.storage_dir / filename
            
            # Save file
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(image_data)
            
            # Return relative URL
            return f"/api/v1/images/file/{filename}"
            
        except Exception as e:
            logger.error(f"Base64 image save failed: {e}")
            # Fallback to placeholder
            return f"https://picsum.photos/800/600?random={uuid.uuid4()}"
    
    def _get_file_extension_from_url(self, url: str) -> str:
        """Extract file extension from URL"""
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path
            return os.path.splitext(path)[1] or ".jpg"
        except:
            return ".jpg"
    
    async def get_stock_images(self, theme: str, count: int = 5) -> List[ImageResponse]:
        """
        Get relevant stock images for a theme.
        
        Args:
            theme: Image theme
            count: Number of images to return
            
        Returns:
            List of stock image responses
        """
        try:
            # In production, integrate with actual stock image APIs
            # For now, use placeholder service
            images = []
            
            for i in range(count):
                # Using Unsplash as example (requires API key in production)
                unsplash_url = f"https://source.unsplash.com/800x600/?{theme.replace(' ', ',')}&{uuid.uuid4()}"
                
                images.append(ImageResponse(
                    image_id=str(uuid.uuid4()),
                    url=unsplash_url,
                    source="stock",
                    description=f"Stock image: {theme}",
                    created_at=datetime.now()
                ))
            
            logger.info(f"Retrieved {len(images)} stock images for theme: {theme}")
            return images
            
        except Exception as e:
            logger.error(f"Stock image retrieval failed: {e}")
            return []
    
    async def optimize_image(self, image_path: str, target_size: tuple = (1200, 630)) -> str:
        """
        Optimize image for LinkedIn post.
        
        Args:
            image_path: Path to image file
            target_size: Target dimensions (width, height)
            
        Returns:
            Path to optimized image
        """
        try:
            # In production, use PIL/Pillow for image optimization
            # For now, return original path
            logger.info(f"Image optimization requested for: {image_path}")
            return image_path
            
        except Exception as e:
            logger.error(f"Image optimization failed: {e}")
            return image_path
    
    async def validate_image(self, image_url: str) -> Dict[str, Any]:
        """
        Validate image for LinkedIn requirements.
        
        Args:
            image_url: Image URL to validate
            
        Returns:
            Validation results
        """
        try:
            async with self.client.head(image_url) as response:
                content_type = response.headers.get('content-type', '')
                content_length = int(response.headers.get('content-length', 0))
                
                is_valid = (
                    content_type.startswith('image/') and
                    content_length <= settings.MAX_FILE_SIZE and
                    content_length > 0
                )
                
                return {
                    "valid": is_valid,
                    "content_type": content_type,
                    "file_size": content_length,
                    "supported": content_type in settings.ALLOWED_IMAGE_TYPES
                }
                
        except Exception as e:
            logger.error(f"Image validation failed: {e}")
            return {
                "valid": False,
                "error": str(e)
            }
    
    async def get_image_analysis(self, image_url: str) -> Dict[str, Any]:
        """
        Analyze image content and suitability.
        
        Args:
            image_url: Image URL to analyze
            
        Returns:
            Analysis results
        """
        try:
            # In production, use computer vision API
            # For now, return basic analysis
            return {
                "suitable_for_linkedin": True,
                "estimated_engagement": "medium",
                "recommended_use": "general",
                "brightness": "optimal",
                "contrast": "good"
            }
            
        except Exception as e:
            logger.error(f"Image analysis failed: {e}")
            return {
                "suitable_for_linkedin": True,  # Default to true on error
                "error": str(e)
            }
    
    async def cleanup_old_images(self, older_than_days: int = 30):
        """Clean up old generated images"""
        try:
            cutoff_time = datetime.now().timestamp() - (older_than_days * 24 * 60 * 60)
            deleted_count = 0
            
            for file_path in self.storage_dir.glob("*.*"):
                if file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1
            
            logger.info(f"Cleaned up {deleted_count} old images")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Image cleanup failed: {e}")
            return 0
    
    async def close(self):
        """Close HTTP client connections"""
        await self.client.close()


# Global image service instance
image_service = ImageService()