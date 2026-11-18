"""
Cache-Control metadata for newly uploaded images in S3, so browsers can cache
and reuse them. Sent with every image upload (boto3 `CacheControl` /
ExtraArgs["CacheControl"]) and signed into presigned image PUT URLs.

24h because product images can change at the same URL. Once image
replacements use versioned URLs this can be raised to
"public, max-age=31536000, immutable".
"""

# Browser caching for newly uploaded images.
IMAGE_CACHE_CONTROL = "public, max-age=86400"

# Same lifetime for private-ACL uploads, but never stored by shared caches/CDNs.
PRIVATE_IMAGE_CACHE_CONTROL = "private, max-age=86400"


def presigned_image_cache_control():
    """Cache-Control to sign into presigned image PUT URLs, or None.

    Signing it makes S3 reject (403 SignatureDoesNotMatch) any PUT that does
    not send the exact header, so it is gated by
    settings.PRESIGNED_IMAGE_CACHE_CONTROL_ENABLED (default off). Enable it
    only once every client sends the `required_headers` from the response.
    """
    from django.conf import settings

    if getattr(settings, "PRESIGNED_IMAGE_CACHE_CONTROL_ENABLED", False):
        return IMAGE_CACHE_CONTROL
    return None
