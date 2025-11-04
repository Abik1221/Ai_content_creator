"""
Storage service for file and data management.
"""

import logging
import aiofiles
import json
import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime
from pathlib import Path
import aiohttp

from app.core.config import settings
from app.models.database import DatabaseManager

logger = logging.getLogger(__name__)


class StorageService:
    """
    Storage service for managing files, caching, and data persistence.
    """
    
    def __init__(self):
        self.storage_base = Path("storage")
        self.ensure_directories()
        self.db_manager = DatabaseManager(settings.DATABASE_URL)
    
    def ensure_directories(self):
        """Ensure all required storage directories exist"""
        directories = [
            self.storage_base / "images",
            self.storage_base / "temp",
            self.storage_base / "cache",
            self.storage_base / "exports",
            self.storage_base / "backups"
        ]
        
        for directory in directories:
            directory.mkdir(parents=True, exist_ok=True)
    
    async def store_image(self, image_data: bytes, filename: str, user_id: str) -> str:
        """
        Store image file in user-specific directory.
        
        Args:
            image_data: Image binary data
            filename: Original filename
            user_id: User identifier
            
        Returns:
            Storage path
        """
        try:
            # Create user directory
            user_dir = self.storage_base / "images" / user_id
            user_dir.mkdir(exist_ok=True)
            
            # Generate safe filename
            safe_filename = self._generate_safe_filename(filename)
            file_path = user_dir / safe_filename
            
            # Write file
            async with aiofiles.open(file_path, 'wb') as f:
                await f.write(image_data)
            
            logger.info(f"Image stored: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"Image storage failed: {e}")
            raise
    
    async def retrieve_image(self, file_path: str) -> Optional[bytes]:
        """
        Retrieve image file.
        
        Args:
            file_path: Path to image file
            
        Returns:
            Image data or None if not found
        """
        try:
            path = Path(file_path)
            
            if not path.exists():
                return None
            
            async with aiofiles.open(path, 'rb') as f:
                return await f.read()
                
        except Exception as e:
            logger.error(f"Image retrieval failed: {e}")
            return None
    
    async def cache_data(self, key: str, data: Any, ttl_seconds: int = 3600) -> bool:
        """
        Cache data with TTL.
        
        Args:
            key: Cache key
            data: Data to cache
            ttl_seconds: Time to live in seconds
            
        Returns:
            True if successful
        """
        try:
            cache_entry = {
                "data": data,
                "expires_at": (datetime.now().timestamp() + ttl_seconds),
                "created_at": datetime.now().isoformat()
            }
            
            cache_file = self.storage_base / "cache" / f"{key}.json"
            
            async with aiofiles.open(cache_file, 'w') as f:
                await f.write(json.dumps(cache_entry, default=str))
            
            return True
            
        except Exception as e:
            logger.error(f"Cache storage failed: {e}")
            return False
    
    async def get_cached_data(self, key: str) -> Optional[Any]:
        """
        Retrieve cached data.
        
        Args:
            key: Cache key
            
        Returns:
            Cached data or None if not found/expired
        """
        try:
            cache_file = self.storage_base / "cache" / f"{key}.json"
            
            if not cache_file.exists():
                return None
            
            async with aiofiles.open(cache_file, 'r') as f:
                cache_entry = json.loads(await f.read())
            
            # Check if expired
            if datetime.now().timestamp() > cache_entry["expires_at"]:
                # Delete expired cache
                cache_file.unlink()
                return None
            
            return cache_entry["data"]
            
        except Exception as e:
            logger.error(f"Cache retrieval failed: {e}")
            return None
    
    async def export_user_data(self, user_id: str, data: Dict[str, Any]) -> str:
        """
        Export user data to JSON file.
        
        Args:
            user_id: User identifier
            data: Data to export
            
        Returns:
            Path to export file
        """
        try:
            export_dir = self.storage_base / "exports" / user_id
            export_dir.mkdir(parents=True, exist_ok=True)
            
            filename = f"export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            file_path = export_dir / filename
            
            async with aiofiles.open(file_path, 'w') as f:
                await f.write(json.dumps(data, indent=2, default=str))
            
            logger.info(f"User data exported: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"Data export failed: {e}")
            raise
    
    async def create_backup(self, backup_type: str = "auto") -> str:
        """
        Create system backup.
        
        Args:
            backup_type: Type of backup ('auto', 'full', 'partial')
            
        Returns:
            Path to backup file
        """
        try:
            backup_dir = self.storage_base / "backups"
            backup_dir.mkdir(exist_ok=True)
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"backup_{backup_type}_{timestamp}.json"
            file_path = backup_dir / filename
            
            # Collect backup data
            backup_data = {
                "metadata": {
                    "backup_type": backup_type,
                    "created_at": datetime.now().isoformat(),
                    "version": "1.0"
                },
                "system_info": await self._get_system_info()
            }
            
            async with aiofiles.open(file_path, 'w') as f:
                await f.write(json.dumps(backup_data, indent=2, default=str))
            
            logger.info(f"Backup created: {file_path}")
            return str(file_path)
            
        except Exception as e:
            logger.error(f"Backup creation failed: {e}")
            raise
    
    async def cleanup_temp_files(self, older_than_hours: int = 24):
        """
        Clean up temporary files.
        
        Args:
            older_than_hours: Delete files older than this
        """
        try:
            temp_dir = self.storage_base / "temp"
            cutoff_time = datetime.now().timestamp() - (older_than_hours * 3600)
            
            deleted_count = 0
            for file_path in temp_dir.rglob("*"):
                if file_path.is_file() and file_path.stat().st_mtime < cutoff_time:
                    file_path.unlink()
                    deleted_count += 1
            
            logger.info(f"Cleaned up {deleted_count} temporary files")
            return deleted_count
            
        except Exception as e:
            logger.error(f"Temp file cleanup failed: {e}")
            return 0
    
    def _generate_safe_filename(self, original_filename: str) -> str:
        """Generate safe filename for storage"""
        from app.utils.helpers import SecurityHelper
        return SecurityHelper.sanitize_filename(original_filename)
    
    async def _get_system_info(self) -> Dict[str, Any]:
        """Get system information for backups"""
        import platform
        from app.utils.helpers import PerformanceHelper
        
        return {
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "system_time": datetime.now().isoformat(),
            "memory_usage": PerformanceHelper.get_memory_usage(),
            "storage_stats": await self._get_storage_stats()
        }
    
    async def _get_storage_stats(self) -> Dict[str, Any]:
        """Get storage statistics"""
        try:
            total_size = 0
            file_count = 0
            
            for file_path in self.storage_base.rglob("*"):
                if file_path.is_file():
                    total_size += file_path.stat().st_size
                    file_count += 1
            
            return {
                "total_size_bytes": total_size,
                "total_size_mb": total_size / (1024 * 1024),
                "file_count": file_count,
                "directories": [str(p) for p in self.storage_base.rglob("*") if p.is_dir()]
            }
        except Exception as e:
            logger.error(f"Storage stats failed: {e}")
            return {"error": str(e)}


# Global storage service instance
storage_service = StorageService()