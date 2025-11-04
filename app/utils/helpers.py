"""
Utility helper functions for the LinkedIn Content Agent.
"""

import logging
import asyncio
import json
import uuid
import re
from typing import Dict, Any, List, Optional, Union
from datetime import datetime, timedelta
from pathlib import Path
import hashlib
import random
import string

logger = logging.getLogger(__name__)


class ContentHelper:
    """Helper functions for content processing and validation"""
    
    @staticmethod
    def extract_hashtags(text: str) -> List[str]:
        """
        Extract hashtags from text content.
        
        Args:
            text: Text content to extract hashtags from
            
        Returns:
            List of unique hashtags
        """
        try:
            hashtags = re.findall(r'#(\w+)', text)
            # Remove duplicates and return
            return list(set(hashtags))
        except Exception as e:
            logger.error(f"Hashtag extraction failed: {e}")
            return []
    
    @staticmethod
    def validate_content_length(content: str, min_length: int = 100, max_length: int = 3000) -> Dict[str, Any]:
        """
        Validate content length for LinkedIn.
        
        Args:
            content: Content text to validate
            min_length: Minimum allowed length
            max_length: Maximum allowed length
            
        Returns:
            Validation results
        """
        content_length = len(content)
        
        return {
            "valid": min_length <= content_length <= max_length,
            "length": content_length,
            "within_limits": content_length <= max_length,
            "too_short": content_length < min_length,
            "too_long": content_length > max_length,
            "recommended_max": 1300  # LinkedIn's optimal length
        }
    
    @staticmethod
    def truncate_content(content: str, max_length: int = 1300, append_ellipsis: bool = True) -> str:
        """
        Truncate content to maximum length while preserving sentences.
        
        Args:
            content: Content to truncate
            max_length: Maximum length allowed
            append_ellipsis: Whether to add "..." at the end
            
        Returns:
            Truncated content
        """
        if len(content) <= max_length:
            return content
        
        # Try to truncate at sentence boundary
        truncated = content[:max_length]
        last_period = truncated.rfind('. ')
        last_exclamation = truncated.rfind('! ')
        last_question = truncated.rfind('? ')
        
        # Find the last sentence boundary
        last_boundary = max(last_period, last_exclamation, last_question)
        
        if last_boundary > max_length * 0.7:  # Only use if it's not too early
            truncated = truncated[:last_boundary + 1]
        
        if append_ellipsis and len(truncated) < len(content):
            truncated = truncated.rstrip() + "..."
        
        return truncated
    
    @staticmethod
    def calculate_reading_time(content: str, words_per_minute: int = 200) -> Dict[str, Any]:
        """
        Calculate estimated reading time for content.
        
        Args:
            content: Content text to analyze
            words_per_minute: Average reading speed
            
        Returns:
            Reading time information
        """
        try:
            # Count words (simple approach)
            words = len(re.findall(r'\w+', content))
            minutes = words / words_per_minute
            
            # Round up to nearest minute, but minimum 1 minute
            reading_minutes = max(1, round(minutes))
            
            return {
                "words": words,
                "minutes": reading_minutes,
                "seconds": int(minutes * 60),
                "estimated": f"{reading_minutes} min read"
            }
        except Exception as e:
            logger.error(f"Reading time calculation failed: {e}")
            return {"words": 0, "minutes": 1, "seconds": 60, "estimated": "1 min read"}
    
    @staticmethod
    def generate_content_preview(content: str, preview_length: int = 150) -> str:
        """
        Generate a preview snippet of content.
        
        Args:
            content: Content to preview
            preview_length: Length of preview in characters
            
        Returns:
            Preview text
        """
        if len(content) <= preview_length:
            return content
        
        preview = content[:preview_length]
        
        # Try to end at a word boundary
        last_space = preview.rfind(' ')
        if last_space > preview_length * 0.8:
            preview = preview[:last_space]
        
        return preview.strip() + "..."
    
    @staticmethod
    def sanitize_content(content: str) -> str:
        """
        Sanitize content for LinkedIn posting.
        
        Args:
            content: Content to sanitize
            
        Returns:
            Sanitized content
        """
        # Remove excessive whitespace
        content = re.sub(r'\n\s*\n', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        
        # Remove any problematic characters
        content = content.replace('\x00', '')  # Remove null bytes
        
        # Ensure proper encoding
        content = content.encode('utf-8', 'ignore').decode('utf-8')
        
        return content.strip()


class ImageHelper:
    """Helper functions for image processing and management"""
    
    @staticmethod
    def generate_image_filename(original_filename: str, prefix: str = "img") -> str:
        """
        Generate a unique filename for image storage.
        
        Args:
            original_filename: Original filename
            prefix: Prefix for the generated filename
            
        Returns:
            Unique filename
        """
        extension = Path(original_filename).suffix.lower() if original_filename else ".jpg"
        unique_id = uuid.uuid4().hex[:12]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        return f"{prefix}_{timestamp}_{unique_id}{extension}"
    
    @staticmethod
    def get_file_extension_from_mime(mime_type: str) -> str:
        """
        Get file extension from MIME type.
        
        Args:
            mime_type: MIME type string
            
        Returns:
            File extension with dot
        """
        mime_to_extension = {
            "image/jpeg": ".jpg",
            "image/jpg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
            "image/gif": ".gif"
        }
        
        return mime_to_extension.get(mime_type.lower(), ".jpg")
    
    @staticmethod
    def validate_image_dimensions(width: int, height: int) -> Dict[str, Any]:
        """
        Validate image dimensions for LinkedIn.
        
        Args:
            width: Image width in pixels
            height: Image height in pixels
            
        Returns:
            Validation results
        """
        # LinkedIn recommended aspect ratios
        aspect_ratio = width / height if height > 0 else 0
        
        valid_aspect_ratios = [
            (1.91, 1),  # Horizontal
            (1, 1),     # Square
            (4, 5),     # Vertical
            (1.91, 1)   # Shared image
        ]
        
        is_valid_aspect = any(
            abs(aspect_ratio - target[0]/target[1]) < 0.1
            for target in valid_aspect_ratios
        )
        
        min_dimension = 200
        max_dimension = 12000
        
        return {
            "valid": (min_dimension <= width <= max_dimension and 
                     min_dimension <= height <= max_dimension and
                     is_valid_aspect),
            "width": width,
            "height": height,
            "aspect_ratio": round(aspect_ratio, 2),
            "valid_aspect": is_valid_aspect,
            "recommended": "1:1, 1.91:1, or 4:5"
        }


class SecurityHelper:
    """Security and validation helper functions"""
    
    @staticmethod
    def generate_api_key(length: int = 32) -> str:
        """
        Generate a secure API key.
        
        Args:
            length: Length of the API key
            
        Returns:
            Generated API key
        """
        characters = string.ascii_letters + string.digits
        return ''.join(random.choice(characters) for _ in range(length))
    
    @staticmethod
    def hash_content(content: str, algorithm: str = "sha256") -> str:
        """
        Generate hash of content for verification.
        
        Args:
            content: Content to hash
            algorithm: Hash algorithm to use
            
        Returns:
            Hash string
        """
        if algorithm == "sha256":
            return hashlib.sha256(content.encode()).hexdigest()
        elif algorithm == "md5":
            return hashlib.md5(content.encode()).hexdigest()
        else:
            raise ValueError(f"Unsupported algorithm: {algorithm}")
    
    @staticmethod
    def validate_email(email: str) -> bool:
        """
        Validate email address format.
        
        Args:
            email: Email address to validate
            
        Returns:
            True if valid, False otherwise
        """
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email))
    
    @staticmethod
    def sanitize_filename(filename: str) -> str:
        """
        Sanitize filename to prevent path traversal and other attacks.
        
        Args:
            filename: Original filename
            
        Returns:
            Sanitized filename
        """
        # Remove path components
        filename = Path(filename).name
        
        # Remove potentially dangerous characters
        filename = re.sub(r'[^\w\-. ]', '', filename)
        
        # Limit length
        if len(filename) > 255:
            name, ext = os.path.splitext(filename)
            filename = name[:255 - len(ext)] + ext
        
        return filename


class DateTimeHelper:
    """Date and time helper functions"""
    
    @staticmethod
    def format_timestamp(timestamp: datetime, format_type: str = "human") -> str:
        """
        Format timestamp for display.
        
        Args:
            timestamp: Datetime object to format
            format_type: Format type ('human', 'iso', 'short')
            
        Returns:
            Formatted timestamp string
        """
        if format_type == "human":
            now = datetime.now()
            diff = now - timestamp
            
            if diff.days == 0:
                if diff.seconds < 60:
                    return "just now"
                elif diff.seconds < 3600:
                    minutes = diff.seconds // 60
                    return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
                else:
                    hours = diff.seconds // 3600
                    return f"{hours} hour{'s' if hours > 1 else ''} ago"
            elif diff.days == 1:
                return "yesterday"
            elif diff.days < 7:
                return f"{diff.days} days ago"
            else:
                return timestamp.strftime("%b %d, %Y")
        
        elif format_type == "iso":
            return timestamp.isoformat()
        
        elif format_type == "short":
            return timestamp.strftime("%Y-%m-%d %H:%M")
        
        else:
            return timestamp.strftime("%Y-%m-%d %H:%M:%S")
    
    @staticmethod
    def calculate_next_post_time(
        base_time: datetime = None,
        min_delay: int = 30,
        max_delay: int = 120
    ) -> datetime:
        """
        Calculate optimal time for next post.
        
        Args:
            base_time: Base time to calculate from (default: now)
            min_delay: Minimum delay in minutes
            max_delay: Maximum delay in minutes
            
        Returns:
            Calculated post time
        """
        if base_time is None:
            base_time = datetime.now()
        
        # Add random delay between min and max
        delay_minutes = random.randint(min_delay, max_delay)
        next_time = base_time + timedelta(minutes=delay_minutes)
        
        # Ensure it's within business hours (9 AM - 5 PM)
        hour = next_time.hour
        if hour < 9 or hour >= 17:
            # Move to next business day at 9 AM
            days_to_add = 1 if hour < 17 else 0
            next_time = next_time.replace(
                day=next_time.day + days_to_add,
                hour=9,
                minute=0,
                second=0,
                microsecond=0
            )
        
        return next_time
    
    @staticmethod
    def is_business_hours(timestamp: datetime = None) -> bool:
        """
        Check if timestamp is within business hours.
        
        Args:
            timestamp: Timestamp to check (default: now)
            
        Returns:
            True if within business hours
        """
        if timestamp is None:
            timestamp = datetime.now()
        
        # Monday to Friday, 9 AM to 5 PM
        return (
            timestamp.weekday() < 5 and  # Monday (0) to Friday (4)
            9 <= timestamp.hour < 17
        )


class PerformanceHelper:
    """Performance monitoring and optimization helpers"""
    
    @staticmethod
    async def measure_execution_time(async_func, *args, **kwargs) -> Dict[str, Any]:
        """
        Measure execution time of an async function.
        
        Args:
            async_func: Async function to measure
            *args: Function arguments
            **kwargs: Function keyword arguments
            
        Returns:
            Execution results with timing
        """
        start_time = datetime.now()
        
        try:
            result = await async_func(*args, **kwargs)
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            return {
                "success": True,
                "result": result,
                "execution_time_seconds": execution_time,
                "start_time": start_time,
                "end_time": end_time
            }
        except Exception as e:
            end_time = datetime.now()
            execution_time = (end_time - start_time).total_seconds()
            
            return {
                "success": False,
                "error": str(e),
                "execution_time_seconds": execution_time,
                "start_time": start_time,
                "end_time": end_time
            }
    
    @staticmethod
    def get_memory_usage() -> Dict[str, float]:
        """
        Get current memory usage.
        
        Returns:
            Memory usage in MB
        """
        try:
            import psutil
            process = psutil.Process()
            memory_info = process.memory_info()
            
            return {
                "rss_mb": memory_info.rss / 1024 / 1024,  # Resident Set Size
                "vms_mb": memory_info.vms / 1024 / 1024,  # Virtual Memory Size
                "percent": process.memory_percent()
            }
        except ImportError:
            return {"rss_mb": 0, "vms_mb": 0, "percent": 0}
    
    @staticmethod
    def optimize_content_batch(content_list: List[Dict[str, Any]], batch_size: int = 5) -> List[List[Dict[str, Any]]]:
        """
        Optimize content processing by batching.
        
        Args:
            content_list: List of content items to process
            batch_size: Size of each batch
            
        Returns:
            List of batches
        """
        batches = []
        for i in range(0, len(content_list), batch_size):
            batch = content_list[i:i + batch_size]
            batches.append(batch)
        
        return batches


class ErrorHelper:
    """Error handling and reporting helpers"""
    
    @staticmethod
    def create_error_context(
        error: Exception,
        context: Dict[str, Any] = None,
        user_id: str = None,
        content_id: str = None
    ) -> Dict[str, Any]:
        """
        Create comprehensive error context for logging.
        
        Args:
            error: Exception that occurred
            context: Additional context data
            user_id: User ID related to error
            content_id: Content ID related to error
            
        Returns:
            Error context dictionary
        """
        error_context = {
            "timestamp": datetime.now().isoformat(),
            "error_type": type(error).__name__,
            "error_message": str(error),
            "user_id": user_id,
            "content_id": content_id,
        }
        
        if context:
            error_context.update(context)
        
        # Add stack trace for debugging
        import traceback
        error_context["stack_trace"] = traceback.format_exc()
        
        return error_context
    
    @staticmethod
    def should_retry_error(error: Exception, retry_count: int) -> bool:
        """
        Determine if an error should be retried.
        
        Args:
            error: Exception that occurred
            retry_count: Number of retries already attempted
            
        Returns:
            True if should retry, False otherwise
        """
        max_retries = 3
        
        if retry_count >= max_retries:
            return False
        
        # Retry on network-related errors
        retryable_errors = [
            "Timeout",
            "Connection",
            "Network",
            "RateLimit",
            "Temporary"
        ]
        
        error_str = str(error)
        return any(retryable in error_str for retryable in retryable_errors)
    
    @staticmethod
    def format_error_for_user(error: Exception, include_details: bool = False) -> str:
        """
        Format error message for user display.
        
        Args:
            error: Exception that occurred
            include_details: Whether to include technical details
            
        Returns:
            User-friendly error message
        """
        base_message = "An error occurred while processing your request."
        
        if include_details:
            return f"{base_message} Details: {str(error)}"
        else:
            return base_message


# Utility functions for common operations
def generate_unique_id(prefix: str = "id") -> str:
    """Generate a unique identifier with prefix"""
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def safe_json_parse(json_string: str, default: Any = None) -> Any:
    """Safely parse JSON string with default fallback"""
    try:
        return json.loads(json_string)
    except (json.JSONDecodeError, TypeError):
        return default


def format_file_size(size_bytes: int) -> str:
    """Format file size in human-readable format"""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024.0
    return f"{size_bytes:.1f} TB"


def clean_phone_number(phone: str) -> str:
    """Clean and format phone number"""
    # Remove all non-digit characters
    cleaned = re.sub(r'\D', '', phone)
    return cleaned


def is_valid_url(url: str) -> bool:
    """Validate URL format"""
    pattern = re.compile(
        r'^(https?://)?'  # http:// or https://
        r'([a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+'
        r'[a-zA-Z]{2,}'  # domain
        r'(:\d+)?'  # port
        r'(/.*)?$'  # path
    )
    return bool(pattern.match(url))