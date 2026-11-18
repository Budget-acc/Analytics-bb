import logging
import requests
from io import BytesIO
from typing import Optional
from urllib.parse import urlparse

from django.conf import settings
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.lib import colors

from PIL import Image as PILImage, ImageOps

# SVG support
try:
    from svglib.svglib import svg2rlg
    SVG_AVAILABLE = True
except ImportError:
    SVG_AVAILABLE = False
    logging.warning("svglib not installed. SVG backgrounds will not be supported.")

from .constants import logger, CUSTOM_PURPLE

# ============================================================================
# GLOBAL CACHES
# ============================================================================
_SVG_CACHE = {}
_PLACEHOLDER_CACHE = {}
_IMAGE_CACHE = {}


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================
def get_placeholder(width: int, height: int, is_logo: bool = False) -> Drawing:
    """Get cached placeholder drawing."""
    cache_key = f"{width}x{height}_{is_logo}"

    if cache_key not in _PLACEHOLDER_CACHE:
        d = Drawing(width, height)

        if is_logo:
            # Shop logo placeholder
            d.add(Rect(
                0, 0, width, height,
                strokeColor=colors.grey,
                fillColor=colors.whitesmoke,
                rx=8, ry=8
            ))
            d.add(Rect(
                width/4, height/4, width/2, height/2,
                strokeColor=CUSTOM_PURPLE,
                fillColor=CUSTOM_PURPLE
            ))
        else:
            # Generic placeholder
            d.add(Rect(
                0, 0, width, height,
                strokeColor=colors.grey,
                fillColor=colors.whitesmoke,
                rx=8, ry=8
            ))

        _PLACEHOLDER_CACHE[cache_key] = d

    return _PLACEHOLDER_CACHE[cache_key]


def download_image_optimized(url: str, timeout: int = 4, retries: int = 1, max_size_mb: int = 5) -> Optional[BytesIO]:
    """Download raw image bytes with security validation."""
    if not url or not isinstance(url, str):
        return None

    url = url.strip()
    if not url.startswith('http'):
        return None

    # Whitelist allowed domains for security (SSRF prevention)
    try:
        parsed = urlparse(url)
        ALLOWED_DOMAINS = []

        # Add AWS S3 domain if configured
        if hasattr(settings, 'AWS_S3_CUSTOM_DOMAIN') and settings.AWS_S3_CUSTOM_DOMAIN:
            ALLOWED_DOMAINS.append(settings.AWS_S3_CUSTOM_DOMAIN)

        # Add AWS S3 bucket domain
        if hasattr(settings, 'AWS_STORAGE_BUCKET_NAME') and settings.AWS_STORAGE_BUCKET_NAME:
            ALLOWED_DOMAINS.append(f"{settings.AWS_STORAGE_BUCKET_NAME}.s3.amazonaws.com")
            if hasattr(settings, 'AWS_S3_REGION_NAME') and settings.AWS_S3_REGION_NAME:
                ALLOWED_DOMAINS.append(f"{settings.AWS_STORAGE_BUCKET_NAME}.s3.{settings.AWS_S3_REGION_NAME}.amazonaws.com")

        # Add any additional trusted CDN domains from settings
        if hasattr(settings, 'ALLOWED_IMAGE_DOMAINS'):
            ALLOWED_DOMAINS.extend(settings.ALLOWED_IMAGE_DOMAINS)

        # Filter out None values
        ALLOWED_DOMAINS = [d for d in ALLOWED_DOMAINS if d]

        # Check if domain is allowed (only if whitelist is configured)
        if ALLOWED_DOMAINS and parsed.netloc not in ALLOWED_DOMAINS:
            logger.warning(f"Blocked image download from untrusted domain: {parsed.netloc}")
            return None
    except Exception as e:
        logger.warning(f"Failed to parse URL {url}: {e}")
        return None

    max_size_bytes = max_size_mb * 1024 * 1024

    for attempt in range(retries + 1):
        try:
            response = requests.get(
                url,
                timeout=timeout,
                stream=True,
                headers={'User-Agent': 'Mozilla/5.0'},
                allow_redirects=True,
                verify=True
            )
            response.raise_for_status()

            # Check content length before downloading (DOS prevention)
            content_length = int(response.headers.get('Content-Length', 0))
            if content_length > 0 and content_length > max_size_bytes:
                logger.warning(f"Image too large: {content_length} bytes (max {max_size_bytes})")
                return None

            # Download with size limit enforcement
            content = BytesIO()
            downloaded = 0
            for chunk in response.iter_content(chunk_size=8192):
                downloaded += len(chunk)
                if downloaded > max_size_bytes:
                    logger.warning(f"Image download exceeded size limit: {downloaded} bytes")
                    return None
                content.write(chunk)

            content.seek(0)
            return content
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < retries:
                continue
        except Exception as e:
            logger.warning(f"Failed to download image {url}: {e}")
            break
    return None


def _resize_image(image_bytes: BytesIO, size: tuple = (60, 60), maintain_aspect: bool = True,
                  quality: int = 90, format: str = "JPEG") -> Optional[BytesIO]:
    """
    Resize image with configurable options.
    """
    try:
        image_bytes.seek(0)
        with PILImage.open(image_bytes) as im:
            # Auto-rotate based on EXIF and convert to RGB
            im = ImageOps.exif_transpose(im)

            # Flatten transparency onto a white background (handles RGBA, LA, P modes).
            # A bare .convert('RGB') on an image with an alpha channel drops the alpha
            # and fills transparent pixels with black - composite onto white first.
            if im.mode == 'P':
                im = im.convert('RGBA')
            if im.mode in ('RGBA', 'LA'):
                background = PILImage.new('RGB', im.size, (255, 255, 255))
                background.paste(im, mask=im.split()[-1])  # alpha channel as mask
                im = background
            elif im.mode != 'RGB':
                im = im.convert('RGB')

            if maintain_aspect:
                # Maintain aspect ratio with centered composition
                original_width, original_height = im.size
                target_width, target_height = size

                # Calculate scaling to fit within target size
                width_ratio = target_width / original_width
                height_ratio = target_height / original_height
                scaling_factor = min(width_ratio, height_ratio)

                # Calculate new dimensions
                new_width = int(original_width * scaling_factor)
                new_height = int(original_height * scaling_factor)

                # Resize the image
                im = im.resize((new_width, new_height), PILImage.LANCZOS)

                # Create canvas with white background
                canvas = PILImage.new('RGB', size, (255, 255, 255))

                # Center the resized image
                left = (target_width - new_width) // 2
                top = (target_height - new_height) // 2
                canvas.paste(im, (left, top))

                im = canvas
            else:
                # Fast thumbnail resize (may change aspect ratio)
                im.thumbnail(size, PILImage.LANCZOS)

            # Save to BytesIO
            out = BytesIO()
            im.save(out, format=format, quality=quality, optimize=True)
            out.seek(0)
            return out
    except Exception as e:
        logger.warning(f"Failed to resize image: {e}")
        return None
