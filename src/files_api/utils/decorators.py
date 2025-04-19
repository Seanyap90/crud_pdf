"""Decorator utilities for cross-cutting concerns."""
import time
import logging
import functools
import asyncio
from typing import Any, Callable, Optional, TypeVar, cast

# Setup logging
logger = logging.getLogger(__name__)

F = TypeVar('F', bound=Callable[..., Any])

def log_execution_time(func: F) -> F:
    """Decorator to log function execution time.
    
    Args:
        func: The function to decorate
        
    Returns:
        Decorated function that logs execution time
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = func(*args, **kwargs)
            duration = time.time() - start_time
            logger.info(f"{func.__name__} completed in {duration:.2f}s")
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"{func.__name__} failed after {duration:.2f}s: {str(e)}")
            raise
    return cast(F, wrapper)

def async_log_execution_time(func: F) -> F:
    """Decorator to log async function execution time.
    
    Args:
        func: The async function to decorate
        
    Returns:
        Decorated async function that logs execution time
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            duration = time.time() - start_time
            logger.info(f"{func.__name__} completed in {duration:.2f}s")
            return result
        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"{func.__name__} failed after {duration:.2f}s: {str(e)}")
            raise
    return cast(F, wrapper)

def retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, 
          exceptions: tuple = (Exception,), logger_name: Optional[str] = None):
    """Decorator for retrying functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier (e.g., 2.0 means delay doubles each retry)
        exceptions: Tuple of exceptions to catch for retry
        logger_name: Optional logger name (defaults to module logger)
        
    Returns:
        Decorator function
    """
    retry_logger = logging.getLogger(logger_name) if logger_name else logger
    
    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 1
            current_delay = delay
            
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        retry_logger.error(f"All {max_attempts} attempts failed for {func.__name__}: {str(e)}")
                        raise
                    
                    retry_logger.warning(
                        f"Attempt {attempt}/{max_attempts} for {func.__name__} failed: {str(e)}. "
                        f"Retrying in {current_delay:.2f}s"
                    )
                    
                    time.sleep(current_delay)
                    attempt += 1
                    current_delay *= backoff
        
        return cast(F, wrapper)
    
    return decorator

def async_retry(max_attempts: int = 3, delay: float = 1.0, backoff: float = 2.0, 
                exceptions: tuple = (Exception,), logger_name: Optional[str] = None):
    """Decorator for retrying async functions with exponential backoff.
    
    Args:
        max_attempts: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Backoff multiplier (e.g., 2.0 means delay doubles each retry)
        exceptions: Tuple of exceptions to catch for retry
        logger_name: Optional logger name (defaults to module logger)
        
    Returns:
        Decorator function
    """
    retry_logger = logging.getLogger(logger_name) if logger_name else logger
    
    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 1
            current_delay = delay
            
            while attempt <= max_attempts:
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts:
                        retry_logger.error(f"All {max_attempts} attempts failed for {func.__name__}: {str(e)}")
                        raise
                    
                    retry_logger.warning(
                        f"Attempt {attempt}/{max_attempts} for {func.__name__} failed: {str(e)}. "
                        f"Retrying in {current_delay:.2f}s"
                    )
                    
                    await asyncio.sleep(current_delay)
                    attempt += 1
                    current_delay *= backoff
        
        return cast(F, wrapper)
    
    return decorator

def memoize(func: F) -> F:
    """Simple memoization decorator to cache function results.
    
    Args:
        func: The function to decorate
        
    Returns:
        Decorated function with caching
    """
    cache = {}
    
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Create a cache key from args and kwargs
        key_args = args
        key_kwargs = frozenset(kwargs.items())
        cache_key = (key_args, key_kwargs)
        
        if cache_key not in cache:
            cache[cache_key] = func(*args, **kwargs)
        
        return cache[cache_key]
    
    # Add function to clear cache
    wrapper.clear_cache = lambda: cache.clear()
    
    return cast(F, wrapper)