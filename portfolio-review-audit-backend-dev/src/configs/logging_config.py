"""
Logging configuration for the application
"""
import logging.config
import os

# Root level: DEBUG, INFO, WARNING, ERROR — default INFO so app and API access logs are visible.
_ROOT_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()
if _ROOT_LEVEL not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
    _ROOT_LEVEL = "INFO"

class ColoredFormatter(logging.Formatter):
    """Custom formatter with colors"""
    
    # Color codes
    grey = "\x1b[38;21m"
    blue = "\x1b[38;5;39m"
    yellow = "\x1b[38;5;226m"
    red = "\x1b[38;5;196m"
    green = "\x1b[38;5;40m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"

    # Format for different levels
    FORMATS = {
        logging.DEBUG: blue + "[%(asctime)s +0530] [%(process)d] [%(levelname)s] %(message)s" + reset,
        logging.INFO: green + "[%(asctime)s +0530] [%(process)d] [%(levelname)s] %(message)s" + reset,
        logging.WARNING: yellow + "[%(asctime)s +0530] [%(process)d] [%(levelname)s] %(message)s" + reset,
        logging.ERROR: red + "[%(asctime)s +0530] [%(process)d] [%(levelname)s] %(message)s" + reset,
        logging.CRITICAL: bold_red + "[%(asctime)s +0530] [%(process)d] [%(levelname)s] %(message)s" + reset
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, datefmt='%Y-%m-%d %H:%M:%S')
        return formatter.format(record)

def setup_logging():
    """Configure logging for the application"""
    logging_config = {
        'version': 1,
        'disable_existing_loggers': False,
        'formatters': {
            'colored': {
                '()': ColoredFormatter
            }
        },
        'handlers': {
            'console': {
                # Handler must not filter out INFO; logger levels control verbosity.
                'level': 'DEBUG',
                'formatter': 'colored',
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stdout',
            },
            'scheduler_handler': {
                'level': 'DEBUG',
                'formatter': 'colored',
                'class': 'logging.StreamHandler',
                'stream': 'ext://sys.stdout',
            }
        },
        'loggers': {
            '': {  # root logger
                'handlers': ['console'],
                'level': _ROOT_LEVEL,
                'propagate': True
            },
            # HTTP access lines (method, path, status, duration)
            'src.http': {
                'handlers': ['console'],
                'level': 'INFO',
                'propagate': False,
            },
            'src.scheduler': {
                'handlers': ['scheduler_handler'],
                'level': 'WARNING',
                'propagate': False
            },
            'apscheduler': {
                'handlers': ['console'],
                'level': 'WARNING',
                'propagate': False
            }
            ,
            # Silence per-statement SQL / transaction chatter
            'sqlalchemy.engine': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            'sqlalchemy.pool': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            'sqlalchemy.dialects': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            'asyncpg': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            # Silence boto/botocore debug noise (metadata service, endpoint resolution, retries)
            'boto3': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            'botocore': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
            # Uvicorn access (optional duplicate; we also log in AccessLoggingMiddleware)
            'uvicorn.access': {'handlers': ['console'], 'level': _ROOT_LEVEL, 'propagate': False},
            'uvicorn.error': {'handlers': ['console'], 'level': _ROOT_LEVEL, 'propagate': False},
        }
    }
    
    logging.config.dictConfig(logging_config) 