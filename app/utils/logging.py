"""
Logging configuration for the LinkedIn Content Agent.
"""

import logging
import logging.config
import sys
import json
from pathlib import Path
from datetime import datetime
import os

from app.core.config import settings


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging"""
    
    def format(self, record):
        log_entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }
        
        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)
        
        # Add extra fields if present
        if hasattr(record, "props"):
            log_entry.update(record.props)
        
        return json.dumps(log_entry)


class ColoredFormatter(logging.Formatter):
    """Colored formatter for console output"""
    
    # ANSI color codes
    COLORS = {
        'DEBUG': '\033[36m',      # Cyan
        'INFO': '\033[32m',       # Green
        'WARNING': '\033[33m',    # Yellow
        'ERROR': '\033[31m',      # Red
        'CRITICAL': '\033[41m',   # Red background
        'RESET': '\033[0m'        # Reset
    }
    
    def format(self, record):
        # Add color to level name
        levelname = record.levelname
        if levelname in self.COLORS:
            levelname_color = f"{self.COLORS[levelname]}{levelname}{self.COLORS['RESET']}"
            record.levelname = levelname_color
        
        return super().format(record)


def setup_logging():
    """Setup logging configuration for the application"""
    
    # Create logs directory
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Log file paths
    debug_log = logs_dir / "debug.log"
    error_log = logs_dir / "error.log"
    application_log = logs_dir / "application.log"
    
    # Log configuration
    log_config = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "verbose": {
                "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
                "style": "{",
            },
            "simple": {
                "format": "{levelname} {message}",
                "style": "{",
            },
            "json": {
                "()": JSONFormatter,
            },
            "colored": {
                "()": ColoredFormatter,
                "format": "{levelname} {asctime} {name} - {message}",
                "datefmt": "%Y-%m-%d %H:%M:%S",
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "stream": sys.stdout,
                "formatter": "colored" if settings.DEBUG else "simple",
                "level": "DEBUG" if settings.DEBUG else "INFO",
            },
            "debug_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": debug_log,
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
                "formatter": "verbose",
                "level": "DEBUG",
            },
            "error_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": error_log,
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
                "formatter": "verbose",
                "level": "ERROR",
            },
            "application_file": {
                "class": "logging.handlers.RotatingFileHandler",
                "filename": application_log,
                "maxBytes": 10485760,  # 10MB
                "backupCount": 5,
                "formatter": "json",
                "level": "INFO",
            }
        },
        "loggers": {
            "app": {
                "handlers": ["console", "debug_file", "application_file"],
                "level": "DEBUG" if settings.DEBUG else "INFO",
                "propagate": False,
            },
            "uvicorn": {
                "handlers": ["console", "debug_file"],
                "level": "INFO",
                "propagate": False,
            },
            "fastapi": {
                "handlers": ["console", "debug_file"],
                "level": "INFO",
                "propagate": False,
            },
            "telegram": {
                "handlers": ["console", "debug_file"],
                "level": "INFO",
                "propagate": False,
            },
            "httpx": {
                "handlers": ["console", "debug_file"],
                "level": "WARNING",
                "propagate": False,
            },
            "httpcore": {
                "handlers": ["console", "debug_file"],
                "level": "WARNING",
                "propagate": False,
            },
        },
        "root": {
            "handlers": ["console", "error_file"],
            "level": "WARNING",
        }
    }
    
    # Apply logging configuration
    logging.config.dictConfig(log_config)
    
    # Set specific log levels from environment
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.getLogger("app").setLevel(log_level)
    
    # Log startup information
    logger = logging.getLogger("app")
    logger.info("Logging configuration completed")
    logger.info(f"Application started in {'DEBUG' if settings.DEBUG else 'PRODUCTION'} mode")
    logger.info(f"Log level set to: {settings.LOG_LEVEL}")


def get_logger(name: str) -> logging.Logger:
    """
    Get logger instance with the given name.
    
    Args:
        name: Logger name
        
    Returns:
        Logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding context to logs"""
    
    def __init__(self, logger: logging.Logger, **context):
        self.logger = logger
        self.context = context
        self.old_factory = None
    
    def __enter__(self):
        # Store the old factory
        self.old_factory = self.logger.makeRecord
        
        # Create new factory that adds context
        def factory(name, level, fn, lno, msg, args, exc_info, func=None, extra=None, sinfo=None):
            # Create the record using the old factory
            record = self.old_factory(name, level, fn, lno, msg, args, exc_info, func, extra, sinfo)
            
            # Add context as properties
            record.props = self.context
            
            return record
        
        # Replace the factory
        self.logger.makeRecord = factory
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        # Restore the old factory
        self.logger.makeRecord = self.old_factory


def log_execution_time(logger: logging.Logger):
    """
    Decorator to log function execution time.
    
    Args:
        logger: Logger instance
        
    Returns:
        Decorator function
    """
    def decorator(func):
        if asyncio.iscoroutinefunction(func):
            async def async_wrapper(*args, **kwargs):
                start_time = datetime.now()
                try:
                    result = await func(*args, **kwargs)
                    end_time = datetime.now()
                    execution_time = (end_time - start_time).total_seconds()
                    
                    logger.debug(
                        f"Async function {func.__name__} executed in {execution_time:.2f}s",
                        extra={"execution_time": execution_time, "function": func.__name__}
                    )
                    return result
                except Exception as e:
                    end_time = datetime.now()
                    execution_time = (end_time - start_time).total_seconds()
                    
                    logger.error(
                        f"Async function {func.__name__} failed after {execution_time:.2f}s: {str(e)}",
                        extra={"execution_time": execution_time, "function": func.__name__, "error": str(e)}
                    )
                    raise
            
            return async_wrapper
        else:
            def sync_wrapper(*args, **kwargs):
                start_time = datetime.now()
                try:
                    result = func(*args, **kwargs)
                    end_time = datetime.now()
                    execution_time = (end_time - start_time).total_seconds()
                    
                    logger.debug(
                        f"Function {func.__name__} executed in {execution_time:.2f}s",
                        extra={"execution_time": execution_time, "function": func.__name__}
                    )
                    return result
                except Exception as e:
                    end_time = datetime.now()
                    execution_time = (end_time - start_time).total_seconds()
                    
                    logger.error(
                        f"Function {func.__name__} failed after {execution_time:.2f}s: {str(e)}",
                        extra={"execution_time": execution_time, "function": func.__name__, "error": str(e)}
                    )
                    raise
            
            return sync_wrapper
    
    return decorator


# Convenience logger instances
app_logger = get_logger("app")
api_logger = get_logger("app.api")
agent_logger = get_logger("app.agents")
service_logger = get_logger("app.services")