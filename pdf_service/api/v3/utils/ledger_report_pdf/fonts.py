import os

from django.conf import settings
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

from . import constants
from .constants import logger


# ============================================================================
# FONT REGISTRATION
# ============================================================================
def register_fonts():
    """Register fonts with fallback to default if custom fonts are unavailable."""
    try:
        # Try to find font files
        font_paths = []
        for font_name in ["Inter-Regular.ttf", "Inter-SemiBold.ttf"]:
            path = None
            # Check static directories
            if hasattr(settings, 'STATICFILES_DIRS'):
                for static_dir in settings.STATICFILES_DIRS:
                    font_path = os.path.join(static_dir, "fonts", font_name)
                    if os.path.exists(font_path):
                        path = font_path
                        break

            # Check STATIC_ROOT
            if not path and hasattr(settings, 'STATIC_ROOT'):
                font_path = os.path.join(settings.STATIC_ROOT, "fonts", font_name)
                if os.path.exists(font_path):
                    path = font_path

            font_paths.append(path)

        if all(font_paths):
            pdfmetrics.registerFont(TTFont('Inter-Regular', font_paths[0]))
            pdfmetrics.registerFont(TTFont('Inter-SemiBold', font_paths[1]))
            constants.BASE_FONT = 'Inter-Regular'
            constants.BOLD_FONT = 'Inter-SemiBold'
        else:
            raise FileNotFoundError("Inter fonts not found")
    except Exception as e:
        logger.warning(f"Failed to register Inter fonts, using fallback: {e}")
        constants.BASE_FONT = 'Helvetica'
        constants.BOLD_FONT = 'Helvetica-Bold'


# Register fonts on module load
try:
    register_fonts()
except Exception as e:
    logger.error(f"Font registration failed: {e}")
