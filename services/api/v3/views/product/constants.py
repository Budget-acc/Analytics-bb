# Maximum size of the attributes JSON body
MAX_ATTRIBUTE_JSON_SIZE = 10 * 1024  # 10 KB

# Default product plan limit per shop
PRODUCT_PLAN_DEFAULT_LIMIT = 150

# Allowed image MIME types
ALLOWED_IMAGE_CONTENT_TYPES = frozenset({"image/jpeg", "image/jpg", "image/png", "image/webp"})

# Maximum image file size
MAX_IMAGE_SIZE_BYTES = 3 * 1024 * 1024  # 3 MB

# Thumbnail dimensions (width, height) in pixels
THUMBNAIL_SIZE = (300, 300)

# Image upload pipeline constants
MAX_IMAGE_DIMENSION = 1200                          # Pre-downscale cap before JPEG encode in Celery task
IMAGE_PROCESSING_SENTINEL = "__processing__"        # DB value while Celery task is in flight
IMAGE_FAILED_SENTINEL = "__failed__"                # DB value when all retries exhausted
MAIN_IMAGE_QUALITY = 85                             # JPEG quality for main image (optimize=False)
THUMB_IMAGE_QUALITY = 75                            # JPEG quality for thumbnail (optimize=False)

# Pre signed url
ALLOWED_ENTITY_TYPES = {"product", "booking_document", "booking_thumbnail"}
PRESIGNED_URL_EXPIRY = 300  # seconds