import logging
import colorlog
from app.config import get_settings

settings = get_settings()

def setup_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    
    if logger.handlers:
        return logger
    
    logger.setLevel(settings.LOG_LEVEL)
    
    handler = logging.StreamHandler()
    formatter = colorlog.ColoredFormatter(
        '%(log_color)s[%(asctime)s]%(reset)s %(name)s - %(levelname)s - %(message)s',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        }
    )
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger