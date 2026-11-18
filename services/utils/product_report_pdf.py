# product_report_pdf.py
import os
import logging
import requests
from io import BytesIO
from decimal import Decimal
from typing import Optional, Dict, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

from django.conf import settings
from django.contrib.staticfiles import finders
from django.utils.timezone import localtime
from django.utils import timezone

from ERP.utils.number_format import format_indian_number, format_inr

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph, 
    Spacer, Image, Flowable
)
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from reportlab.graphics.shapes import Drawing, Rect
from reportlab.graphics import renderPDF
import collections
from PIL import Image as PILImage, ImageOps

# SVG support
try:
    from svglib.svglib import svg2rlg
    SVG_AVAILABLE = True
except ImportError:
    SVG_AVAILABLE = False
    logging.warning("svglib not installed. SVG backgrounds will not be supported.")

logger = logging.getLogger("api_requests")

# ============================================================================
# GLOBAL CACHES
# ============================================================================
_STYLE_CACHE = {}
_SVG_CACHE = {}
_PLACEHOLDER_CACHE = {}

# GLOBAL CACHE modifications
_image_cache_lock = threading.Lock()
# bounded LRU cache, store resized image bytes
_IMAGE_CACHE_MAX_ITEMS = getattr(settings, 'PDF_IMAGE_CACHE_MAX_ITEMS', 1024)
_image_cache = collections.OrderedDict()

# Thumbnail size (width, height) - configurable via settings
_THUMBNAIL_SIZE = getattr(settings, 'PDF_THUMBNAIL_SIZE', (120, 120))


# ============================================================================
# FONT DEFINITIONS (Will be registered later)
# ============================================================================
BASE_FONT = 'Helvetica'
BOLD_FONT = 'Helvetica-Bold'
EXTRA_BOLD_FONT = 'Helvetica-Bold'

# ============================================================================
# COLOR CONSTANTS
# ============================================================================
CUSTOM_COLOR = colors.HexColor("#6C5CE7")
GREY_COLOR = colors.HexColor("#D9D9D9")
DARK_GREY = colors.HexColor("#666666")
LIGHT_GREY = colors.HexColor("#F9FAFB")
BLACK = colors.black
WHITE = colors.white
RED = colors.HexColor("#EF4444")


# ============================================================================
# CACHED STYLES
# ============================================================================
def get_cached_style(name, **kwargs):
    """Get or create cached paragraph style."""
    cache_key = f"{name}_{hash(frozenset(kwargs.items()))}"
    if cache_key not in _STYLE_CACHE:
        styles = getSampleStyleSheet()
        
        if name == 'title':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'Title',
                parent=styles['Normal'],
                fontName=EXTRA_BOLD_FONT,
                fontSize=20,
                leading=24,
                textColor=CUSTOM_COLOR,
                alignment=1,
            )
        elif name == 'shop_name':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'ShopName',
                parent=styles['Normal'],
                fontName=BOLD_FONT,
                fontSize=14,
                leading=18,
                textColor=BLACK,
            )
        elif name == 'normal':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'Normal',
                parent=styles['Normal'],
                fontName=BASE_FONT,
                fontSize=10,
                textColor=colors.HexColor("#374151"),
                **kwargs
            )
        elif name == 'bold':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'Bold',
                parent=styles['Normal'],
                fontName=BOLD_FONT,
                fontSize=10,
                textColor=BLACK,
                **kwargs
            )
        elif name == 'header_cell':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'HeaderCell',
                parent=styles['Normal'],
                fontName=BOLD_FONT,
                fontSize=9,
                textColor=BLACK,
                alignment=1,
            )
        elif name == 'shop_style':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                "ShopStyle",
                parent=styles['Normal'],
                fontName=BASE_FONT,
                fontSize=11,
                leading=16,
                textColor=BLACK,
            )
        elif name == 'center':
            _STYLE_CACHE[cache_key] = ParagraphStyle(
                'Center',
                parent=styles['Normal'],
                fontName=BASE_FONT,
                fontSize=9,
                alignment=1,
            )
    
    return _STYLE_CACHE[cache_key]


# ============================================================================
# SVG BACKGROUND CACHE
# ============================================================================
def get_cached_svg(svg_path, first_page=False):
    """Get cached SVG drawing."""
    cache_key = f"{svg_path}_{first_page}"
    
    if cache_key not in _SVG_CACHE:
        if not SVG_AVAILABLE or not os.path.exists(svg_path):
            _SVG_CACHE[cache_key] = None
        else:
            try:
                drawing = svg2rlg(svg_path)
                if drawing:
                    page_width, page_height = A4
                    scale_x = page_width / drawing.width
                    scale_y = page_height / drawing.height
                    drawing.width = page_width
                    drawing.height = page_height
                    drawing.scale(scale_x, scale_y)
                    _SVG_CACHE[cache_key] = drawing
                else:
                    _SVG_CACHE[cache_key] = None
            except Exception as e:
                logger.error(f"Failed to cache SVG: {e}")
                _SVG_CACHE[cache_key] = None
    
    return _SVG_CACHE[cache_key]


def draw_svg_background(c: canvas.Canvas, doc, first_page=False):
    """Draw SVG background with caching."""
    page_width, page_height = A4
    
    if not SVG_AVAILABLE:
        c.setFillColor(colors.HexColor("#F8F9FA"))
        c.rect(0, 0, page_width, page_height, fill=1, stroke=0)
        return
    
    # Get SVG path
    bg_svg_rel = "images/report_first_page.svg" if first_page else "images/other_pages.svg"
    bg_svg_path = None
    
    # Try to find static file
    if hasattr(settings, 'STATICFILES_DIRS'):
        for static_dir in settings.STATICFILES_DIRS:
            path = os.path.join(static_dir, bg_svg_rel)
            if os.path.exists(path):
                bg_svg_path = path
                break
    
    if not bg_svg_path and hasattr(settings, 'STATIC_ROOT'):
        path = os.path.join(settings.STATIC_ROOT, bg_svg_rel)
        if os.path.exists(path):
            bg_svg_path = path
    
    if bg_svg_path:
        drawing = get_cached_svg(bg_svg_path, first_page)
        if drawing:
            try:
                renderPDF.draw(drawing, c, 0, 0)
                return
            except Exception as e:
                logger.error(f"Failed to draw cached SVG: {e}")
    
    # Fallback background
    c.setFillColor(colors.HexColor("#F8F9FA"))
    c.rect(0, 0, page_width, page_height, fill=1, stroke=0)


# ============================================================================
# OPTIMIZED IMAGE DOWNLOADING
# ============================================================================
def _lru_cache_set(key, value):
    with _image_cache_lock:
        if key in _image_cache:
            _image_cache.move_to_end(key)
        _image_cache[key] = value
        while len(_image_cache) > _IMAGE_CACHE_MAX_ITEMS:
            _image_cache.popitem(last=False)


def download_image_optimized(url: str, timeout: int = 4, retries: int = 1) -> Optional[BytesIO]:
    """Download raw image bytes (no large memory ops)."""
    if not url or not isinstance(url, str):
        return None
    
    url = url.strip()
    if not url.startswith('http'):
        return None
    
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
            
            content = BytesIO()
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    content.write(chunk)
            content.seek(0)
            return content
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError):
            if attempt < retries:
                continue
        except Exception as e:
            break
    return None


def _resize_bytes_to_thumbnail(image_bytes: BytesIO, size=_THUMBNAIL_SIZE) -> Optional[BytesIO]:
    """Open bytes with Pillow, resize and return JPEG bytes (in-memory)."""
    try:
        image_bytes.seek(0)
        with PILImage.open(image_bytes) as im:
            # Convert to RGBA or RGB, preserve aspect -> padded square if needed
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail(size, PILImage.LANCZOS)
            out = BytesIO()
            im.save(out, format="JPEG", quality=100, optimize=True)
            out.seek(0)
            return out
    except Exception as e:
        return None


def prefetch_images_batch(image_urls: List[str]) -> Dict[str, Optional[BytesIO]]:
    """
    Prefetch images in parallel with aggressive optimization.
    """
    cache = {}
    
    unique_urls = list(dict.fromkeys([
        url.strip() for url in image_urls 
        if url and isinstance(url, str) and url.strip().startswith('http')
    ]))
    
    if not unique_urls:
        return cache
    
    max_workers = min(
        getattr(settings, 'PDF_MAX_IMAGE_WORKERS', 30),  # Increased from 12 to 30
        len(unique_urls),
        100  
    )
    
    # Reduce timeout for faster failure
    def _fetch_and_resize(url):
        cached = _image_cache.get(url)
        if cached:
            return (url, cached)
        
        # timeout from 4s to 1.5s
        raw_bytes = download_image_optimized(url, timeout=1.5, retries=0)  
        if raw_bytes:
            resized = _resize_bytes_to_thumbnail(raw_bytes)
            if resized:
                _lru_cache_set(url, resized)
                return (url, resized)
        
        return (url, None)
    
    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(_fetch_and_resize, url): url for url in unique_urls}
        for future in as_completed(futures):
            try:
                url, img_data = future.result(timeout=2) 
                cache[url] = img_data
            except Exception as e:
                url = futures[future]
                cache[url] = None
    
    return cache


# ============================================================================
# ROUNDED IMAGE WITH SIMPLIFIED RENDERING
# ============================================================================
class RoundedImage(Flowable):
    """Rounded image flowable with optimized drawing."""
    
    def __init__(self, img_data, width, height, corner_radius=8):
        Flowable.__init__(self)
        self.img_data = img_data
        self.width = width
        self.height = height
        self.corner_radius = corner_radius
        self._cached_img = None
        
        # Pre-create ImageReader if possible
        if isinstance(img_data, BytesIO):
            try:
                self._cached_img = ImageReader(img_data)
            except:
                pass
    
    def wrap(self, availWidth, availHeight):
        return (self.width, self.height)
    
    def draw(self):
        c = self.canv
        c.saveState()
        
        # Simplified clipping path
        x, y = 0, 0
        w, h = self.width, self.height
        r = self.corner_radius
        
        # Create rounded rectangle clipping path
        p = c.beginPath()
        p.roundRect(x, y, w, h, r)
        c.clipPath(p, stroke=0, fill=0)
        
        try:
            if self._cached_img:
                c.drawImage(self._cached_img, x, y, width=w, height=h, 
                           preserveAspectRatio=True, anchor='c')
            elif isinstance(self.img_data, BytesIO):
                img = ImageReader(self.img_data)
                c.drawImage(img, x, y, width=w, height=h, 
                           preserveAspectRatio=True, anchor='c')
            else:
                self.img_data.drawOn(c, x, y)
        except Exception as e:
            # Fallback placeholder
            c.setFillColor(colors.whitesmoke)
            c.setStrokeColor(colors.grey)
            c.roundRect(x, y, w, h, r, fill=1, stroke=1)
        
        c.restoreState()


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
                fillColor=colors.whitesmoke
            ))
            d.add(Rect(
                width/4, height/4, width/2, height/2,
                strokeColor=CUSTOM_COLOR,
                fillColor=CUSTOM_COLOR
            ))
        else:
            # Product image placeholder with rounded corners
            d.add(Rect(
                0, 0, width, height,
                strokeColor=colors.grey,
                fillColor=colors.whitesmoke,
                rx=8, ry=8
            ))
            # Simple camera icon
            camera_size = min(width, height) * 0.4
            d.add(Rect(
                width/2 - camera_size/2, height/2 - camera_size/2,
                camera_size, camera_size,
                strokeColor=colors.grey,
                fillColor=colors.lightgrey
            ))
        
        _PLACEHOLDER_CACHE[cache_key] = d
    
    return _PLACEHOLDER_CACHE[cache_key]

def process_products_in_chunks(queryset, chunk_size=1000):
    """
    Process queryset in memory-efficient chunks.
    """
    # Method 1: Use Django's iterator (most efficient)
    try:
        for product in queryset.iterator(chunk_size=chunk_size):
            yield product
    except Exception as e:
        logger.warning(f"Iterator failed, falling back to manual chunking: {e}")
        # Method 2: Manual chunking as fallback
        pk = 0
        while True:
            # Get chunk of products
            chunk = list(queryset.filter(pk__gt=pk)[:chunk_size])
            if not chunk:
                break
            for product in chunk:
                yield product
            pk = chunk[-1].pk


# ============================================================================
# MAIN PDF SERVICE CLASS
# ============================================================================
class ProductReportPDFService:
    """Optimized PDF generation service with all original features."""
    
    CUSTOM_COLOR = CUSTOM_COLOR
    GREY_COLOR = GREY_COLOR
    DARK_GREY = DARK_GREY
    LIGHT_GREY = LIGHT_GREY
    BLACK = BLACK
    WHITE = WHITE
    RED = RED
    
    @staticmethod
    def estimate_generation_time(product_count: int) -> Tuple[int, str]:
        """Estimate time based on product count."""
        if product_count <= 100:
            estimated_seconds = max(2, product_count * 0.03)
        elif product_count <= 1000:
            estimated_seconds = max(5, product_count * 0.015)
        elif product_count <= 5000:
            estimated_seconds = max(10, product_count * 0.008)
        else:
            estimated_seconds = max(15, product_count * 0.005)
        
        # Convert to human-readable
        if estimated_seconds < 60:
            time_str = f"{int(estimated_seconds)} seconds"
        else:
            minutes = int(estimated_seconds / 60)
            seconds = int(estimated_seconds % 60)
            time_str = f"{minutes}m {seconds}s"
        
        return int(estimated_seconds), time_str
    
    @staticmethod
    def safe_get_attr(obj, attr, default=''):
        """Safely get attribute with fallback."""
        try:
            value = getattr(obj, attr, default)
            return value if value is not None else default
        except Exception:
            return default
    
    @staticmethod
    def format_date_report(dt):
        """Format date for reports."""
        if not dt:
            return ""
        try:
            local_dt = localtime(dt)
            return local_dt.strftime("%d-%m-%Y")
        except Exception:
            return ""
    
    @staticmethod
    def format_count(value):
        """Whole-number count with Indian grouping (12500 -> 12,500)."""
        try:
            return format_indian_number(value or 0, decimals=0)
        except Exception:
            return str(value)

    @staticmethod
    def format_currency(amount):
        """Format currency with rupee symbol."""
        try:
            if amount is None:
                return "-"
            if isinstance(amount, str):
                amount = Decimal(amount)
            elif not isinstance(amount, Decimal):
                amount = Decimal(str(amount))
            return format_inr(amount, decimals=0)
        except:
            return "-"
    
    @staticmethod
    def collect_all_image_urls(products_queryset, shop) -> List[str]:
        """Collect all image URLs for prefetching."""
        image_urls = []
        
        shop_img = ProductReportPDFService.safe_get_attr(shop, 'img', None)
        if shop_img:
            image_urls.append(shop_img)
        
        for product in products_queryset:
            thumbnail = ProductReportPDFService.safe_get_attr(product, 'thumbnail_image', None)
            if thumbnail:
                image_urls.append(thumbnail)
            
            main_image = ProductReportPDFService.safe_get_attr(product, 'image', None)
            if main_image and main_image != thumbnail:
                image_urls.append(main_image)
        
        return image_urls
    
    @staticmethod
    def get_product_image(product, image_cache: Dict[str, Optional[BytesIO]], size: int = 40) -> Flowable:
        """Get product image from cache or create placeholder."""
        # Try thumbnail first
        thumbnail_url = ProductReportPDFService.safe_get_attr(product, 'thumbnail_image', None)
        if thumbnail_url and thumbnail_url in image_cache and image_cache[thumbnail_url]:
            try:
                return RoundedImage(image_cache[thumbnail_url], width=size, height=size, corner_radius=6)
            except Exception as e:
                logger.warning(f"Failed to create thumbnail for {product.name}: {e}")
        
        # Fallback to main image
        main_image_url = ProductReportPDFService.safe_get_attr(product, 'image', None)
        if main_image_url and main_image_url in image_cache and image_cache[main_image_url]:
            try:
                return RoundedImage(image_cache[main_image_url], width=size, height=size, corner_radius=6)
            except Exception as e:
                logger.warning(f"Failed to create main image for {product.name}: {e}")
        
        # Create placeholder
        return get_placeholder(size, size)
    
    @staticmethod
    def get_shop_logo(shop, image_cache: Dict[str, Optional[BytesIO]], size: int = 60) -> Flowable:
        """Get shop logo from cache or create placeholder."""
        shop_img_url = ProductReportPDFService.safe_get_attr(shop, 'img', None)
        if shop_img_url and shop_img_url in image_cache and image_cache[shop_img_url]:
            try:
                return Image(image_cache[shop_img_url], width=size, height=size)
            except Exception as e:
                logger.warning(f"Failed to create shop logo: {e}")
        
        return get_placeholder(size, size, is_logo=True)
    
    @staticmethod
    def get_main_service_category(products_queryset):
        """Get the main service category from the first product."""
        try:
            for product in products_queryset:
                main_category = None
                try:
                    if hasattr(product, 'shop_service') and product.shop_service:
                        if hasattr(product.shop_service, 'general_service') and product.shop_service.general_service:
                            if hasattr(product.shop_service.general_service, 'main_category') and product.shop_service.general_service.main_category:
                                main_category = product.shop_service.general_service.main_category.name
                except AttributeError:
                    pass
                
                if main_category:
                    return main_category.lower()
            
            return "none"
        except Exception as e:
            logger.warning(f"Failed to get main service category: {e}")
            return "none"
    
    @staticmethod
    def calculate_product_stats_for_dresses(products_queryset):
        """Calculate summary statistics for Dresses category."""
        try:
            total_products = 0
            total_variants = 0
            total_stock = 0
            total_value = Decimal(0)
            
            for product in products_queryset:
                total_products += 1
                variants = list(product.variants.all())
                total_variants += len(variants)
                
                for variant in variants:
                    stock = variant.stock or 0
                    total_stock += stock
                    
                    price = variant.price or product.price or 0
                    total_value += Decimal(stock) * Decimal(price)
            
            return {
                'total_products': total_products,
                'total_variants': total_variants,
                'total_stock': total_stock,
                'total_value': total_value
            }
        except Exception as e:
            logger.error(f"Failed to calculate product stats for dresses: {e}")
            return {
                'total_products': 0,
                'total_variants': 0,
                'total_stock': 0,
                'total_value': Decimal(0)
            }
    
    @staticmethod
    def calculate_product_stats_for_materials(products_queryset):
        """Calculate summary statistics for Materials category."""
        try:
            total_products = 0
            total_fabric_length = Decimal(0)
            total_value = Decimal(0)
            
            for product in products_queryset:
                total_products += 1
                variants = list(product.variants.all())
                
                for variant in variants:
                    fabric_length = variant.fabric_length or Decimal(0)
                    total_fabric_length += fabric_length
                    
                    price = variant.price or product.price or 0
                    total_value += fabric_length * Decimal(price)
            
            return {
                'total_products': total_products,
                'total_fabric_length': total_fabric_length,
                'total_value': total_value
            }
        except Exception as e:
            logger.error(f"Failed to calculate product stats for materials: {e}")
            return {
                'total_products': 0,
                'total_fabric_length': Decimal(0),
                'total_value': Decimal(0)
            }
    
    @staticmethod
    def calculate_product_stats_for_other(products_queryset):
        """Calculate summary statistics for other categories."""
        try:
            total_products = 0
            total_stock = 0
            total_value = Decimal(0)
            
            for product in products_queryset:
                total_products += 1
                variants = list(product.variants.all())
                
                for variant in variants:
                    stock = variant.stock or 0
                    total_stock += stock
                    
                    price = variant.price or product.price or 0
                    total_value += Decimal(stock) * Decimal(price)
            
            return {
                'total_products': total_products,
                'total_stock': total_stock,
                'total_value': total_value
            }
        except Exception as e:
            logger.error(f"Failed to calculate product stats for other categories: {e}")
            return {
                'total_products': 0,
                'total_stock': 0,
                'total_value': Decimal(0)
            }
    
    @staticmethod
    def create_rounded_stats_table(stats, main_category, available_width):
        """Create a custom flowable for rounded statistics table."""
        
        class RoundedStatsTable(Flowable):
            """A flowable that draws a statistics table with rounded corners."""
            
            def __init__(self, stats, main_category, width, height=60):
                Flowable.__init__(self)
                self.stats = stats
                self.main_category = main_category
                self.width = width
                self.height = height
                self.corner_radius = 8
                
            def wrap(self, availWidth, availHeight):
                return (self.width, self.height)
            
            def draw(self):
                c = self.canv
                x, y = 0, 0
                w, h = self.width, self.height
                r = self.corner_radius
                
                c.saveState()
                
                # Create clipping path
                p = c.beginPath()
                p.moveTo(x + r, y)
                p.lineTo(x + w - r, y)
                p.curveTo(x + w, y, x + w, y, x + w, y + r)
                p.lineTo(x + w, y + h - r)
                p.curveTo(x + w, y + h, x + w, y + h, x + w - r, y + h)
                p.lineTo(x + r, y + h)
                p.curveTo(x, y + h, x, y + h, x, y + h - r)
                p.lineTo(x, y + r)
                p.curveTo(x, y, x, y, x + r, y)
                p.close()
                
                c.clipPath(p, stroke=0, fill=0)
                
                c.setFillColor(ProductReportPDFService.LIGHT_GREY)
                c.rect(x, y, w, h, fill=1, stroke=0)
                
                c.restoreState()
                
                c.setStrokeColor(ProductReportPDFService.GREY_COLOR)
                c.setLineWidth(1)
                c.roundRect(x, y, w, h, r, stroke=1, fill=0)
                
                main_category_lower = self.main_category.lower()
                
                if "dress" in main_category_lower:
                    stats_numbers = [
                        ProductReportPDFService.format_count(self.stats.get('total_products', 0)),
                        ProductReportPDFService.format_count(self.stats.get('total_variants', 0)),
                        ProductReportPDFService.format_count(self.stats.get('total_stock', 0)),
                        ProductReportPDFService.format_currency(self.stats.get('total_value', 0))
                    ]
                    stats_labels = [
                        "Total Products",
                        "Total Product + Sizes",
                        "Total Stock",
                        "Total Value"
                    ]
                    col_count = 4
                elif "materials" in main_category_lower:
                    stats_numbers = [
                        ProductReportPDFService.format_count(self.stats.get('total_products', 0)),
                        f"{self.stats.get('total_fabric_length', Decimal(0))}m",
                        ProductReportPDFService.format_currency(self.stats.get('total_value', 0))
                    ]
                    stats_labels = [
                        "Total Products",
                        "Total Fabric Length",
                        "Total Value"
                    ]
                    col_count = 3
                else:
                    stats_numbers = [
                        ProductReportPDFService.format_count(self.stats.get('total_products', 0)),
                        ProductReportPDFService.format_count(self.stats.get('total_stock', 0)),
                        ProductReportPDFService.format_currency(self.stats.get('total_value', 0))
                    ]
                    stats_labels = [
                        "Total Products",
                        "Total Stock",
                        "Total Value"
                    ]
                    col_count = 3
                
                col_width = w / col_count
                c.setFillColor(ProductReportPDFService.BLACK)
                c.setFont(BOLD_FONT, 16)
                
                for i in range(col_count):
                    center_x = x + (col_width * i) + (col_width / 2)
                    number_y = y + h - 30
                    
                    text_width = c.stringWidth(stats_numbers[i], BOLD_FONT, 16)
                    c.drawString(center_x - (text_width / 2), number_y, stats_numbers[i])
                    
                    c.setFont(BASE_FONT, 8)
                    label_y = y + 20
                    label_width = c.stringWidth(stats_labels[i], BASE_FONT, 8)
                    c.drawString(center_x - (label_width / 2), label_y, stats_labels[i])
                    
                    c.setFont(BOLD_FONT, 16)
        
        return RoundedStatsTable(stats, main_category, available_width, height=60)
    
    @staticmethod
    def generate_dresses_table(products_list, available_width, styles, elements, header_cell_style, image_cache):
        """Generate products table for Dresses category."""
        col_widths = [30, 180, 160, 80, 80]
        
        table_data = [[
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">NO</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRODUCT</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">VARIANTS<br/>(SIZE: STOCK)</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PURCHASE<br/>PRICE</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRICE</font>', header_cell_style),
        ]]
        
        row_num = 1
        product_img_size = 40
        
        normal_style = get_cached_style('normal', alignment=0)
        center_style = get_cached_style('center')
        
        for product in products_list:
            product_img = ProductReportPDFService.get_product_image(product, image_cache, product_img_size)
            
            product_info_text = []
            product_info_text.append(Paragraph(
                f'<font name="{BOLD_FONT}" size="10" color="#000000">{product.name}</font>',
                normal_style
            ))
            product_info_text.append(Spacer(1, 4))
            
            details = []
            if product.category:
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Category: {product.category}</font>',
                    normal_style
                ))
            if product.color:
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Color: {product.color}</font>',
                    normal_style
                ))
            product_info_text.extend(details)
            
            text_width = col_widths[1] - product_img_size - 10
            
            product_info_table = Table([[element] for element in product_info_text], colWidths=[text_width])
            product_info_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            product_cell = Table(
                [[product_img, product_info_table]],
                colWidths=[product_img_size + 5, text_width]
            )
            product_cell.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            variants = list(product.variants.all())
            if not variants:
                table_data.append([
                    Paragraph(f'<font size="9">{row_num}</font>', center_style),
                    product_cell,
                    Paragraph(f'<font size="9">-</font>', center_style),
                    Paragraph(f'<font size="9">{ProductReportPDFService.format_currency(product.purchase_price)}</font>', center_style),
                    Paragraph(f'<font name="{BOLD_FONT}" size="10">'
                            f'{ProductReportPDFService.format_currency(product.price)}</font>', center_style),
                ])
            else:
                variant_texts = []
                for v in variants:
                    attr_display = v.attribute or "Free Size"
                    stock_color = "#EF4444" if v.stock == 0 else "#000000"
                    variant_texts.append(
                        f'<font size="9" color="{stock_color}">{attr_display}: {v.stock}</font>'
                    )
                
                variants_display = "<br/>".join(variant_texts)
                display_price = variants[0].price if variants[0].price else product.price
                
                table_data.append([
                    Paragraph(f'<font size="9">{row_num}</font>', center_style),
                    product_cell,
                    Paragraph(variants_display, center_style),
                    Paragraph(f'<font size="9">{ProductReportPDFService.format_currency(product.purchase_price)}</font>', center_style),
                    Paragraph(f'<font name="{BOLD_FONT}" size="10">'
                            f'{ProductReportPDFService.format_currency(display_price)}</font>', center_style),
                ])
            row_num += 1
        
        return table_data, col_widths
    
    @staticmethod
    def generate_materials_table(products_list, available_width, styles, elements, header_cell_style, image_cache):
        """Generate products table for Materials category."""
        col_widths = [30, 200, 120, 90, 90]
        
        table_data = [[
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">NO</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRODUCT</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">FABRIC<br/>LENGTH</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PURCHASE<br/>PRICE</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRICE</font>', header_cell_style),
        ]]
        
        row_num = 1
        product_img_size = 50
        
        normal_style = get_cached_style('normal', alignment=0)
        center_style = get_cached_style('center')
        
        for product in products_list:
            product_img = ProductReportPDFService.get_product_image(product, image_cache, product_img_size)
            
            product_info_text = []
            product_info_text.append(Paragraph(
                f'<font name="{BOLD_FONT}" size="10" color="#000000">{product.name}</font>',
                normal_style
            ))
            product_info_text.append(Spacer(1, 4))
            
            details = []
            variants = list(product.variants.all())
            if variants:
                fabric_type = variants[0].attribute if variants[0].attribute else "Unknown"
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Fabric: {fabric_type}</font>',
                    normal_style
                ))
            product_info_text.extend(details)
            
            text_width = col_widths[1] - product_img_size - 10
            
            product_info_table = Table([[element] for element in product_info_text], colWidths=[text_width])
            product_info_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            product_cell = Table(
                [[product_img, product_info_table]],
                colWidths=[product_img_size + 5, text_width]
            )
            product_cell.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            total_fabric_length = Decimal(0)
            if variants:
                for v in variants:
                    fabric_length = v.fabric_length or Decimal(0)
                    total_fabric_length += fabric_length
            
            display_price = variants[0].price if variants and variants[0].price else product.price
            
            table_data.append([
                Paragraph(f'<font size="9">{row_num}</font>', center_style),
                product_cell,
                Paragraph(f'<font size="9">{total_fabric_length}m</font>', center_style),
                Paragraph(f'<font size="9">{ProductReportPDFService.format_currency(product.purchase_price)}</font>', center_style),
                Paragraph(f'<font name="{BOLD_FONT}" size="10">'
                         f'{ProductReportPDFService.format_currency(display_price)}</font>', center_style),
            ])
            row_num += 1
        
        return table_data, col_widths
    
    @staticmethod
    def generate_vehicle_table(products_list, available_width, styles, elements, header_cell_style, image_cache):
        """Generate products table for Vehicle category."""
        col_widths = [30, 200, 80, 110, 110]
        
        table_data = [[
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">NO</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRODUCT</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">STOCK</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PURCHASE<br/>PRICE</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRICE</font>', header_cell_style),
        ]]
        
        row_num = 1
        product_img_size = 40
        
        normal_style = get_cached_style('normal', alignment=0)
        center_style = get_cached_style('center')
        
        for product in products_list:
            product_img = ProductReportPDFService.get_product_image(product, image_cache, product_img_size)
            
            product_info_text = []
            product_info_text.append(Paragraph(
                f'<font name="{BOLD_FONT}" size="10" color="#000000">{product.name}</font>',
                normal_style
            ))
            product_info_text.append(Spacer(1, 4))
            
            details = []
            if product.model:
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Model: {product.model}</font>',
                    normal_style
                ))
            if product.category:
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Brand: {product.category}</font>',
                    normal_style
                ))
            product_info_text.extend(details)
            
            text_width = col_widths[1] - product_img_size - 10
            
            product_info_table = Table([[element] for element in product_info_text], colWidths=[text_width])
            product_info_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            product_cell = Table(
                [[product_img, product_info_table]],
                colWidths=[product_img_size + 5, text_width]
            )
            product_cell.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            variants = list(product.variants.all())
            total_stock = 0
            if variants:
                for v in variants:
                    total_stock += v.stock or 0
            
            display_price = variants[0].price if variants and variants[0].price else product.price
            
            table_data.append([
                Paragraph(f'<font size="9">{row_num}</font>', center_style),
                product_cell,
                Paragraph(f'<font size="9">{ProductReportPDFService.format_count(total_stock)}</font>', center_style),
                Paragraph(f'<font size="9">{ProductReportPDFService.format_currency(product.purchase_price)}</font>', center_style),
                Paragraph(f'<font name="{BOLD_FONT}" size="10">'
                         f'{ProductReportPDFService.format_currency(display_price)}</font>', center_style),
            ])
            row_num += 1
        
        return table_data, col_widths
    
    @staticmethod
    def generate_other_table(products_list, available_width, styles, elements, header_cell_style, main_category, image_cache):
        """Generate products table for other categories."""
        col_widths = [30, 200, 80, 110, 110]
        
        table_data = [[
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">NO</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRODUCT</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">STOCK</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PURCHASE<br/>PRICE</font>', header_cell_style),
            Paragraph('<font name="' + BOLD_FONT + '" color="#000000">PRICE</font>', header_cell_style),
        ]]
        
        row_num = 1
        product_img_size = 40
        
        normal_style = get_cached_style('normal', alignment=0)
        center_style = get_cached_style('center')
        
        for product in products_list:
            product_img = ProductReportPDFService.get_product_image(product, image_cache, product_img_size)
            
            product_info_text = []
            product_info_text.append(Paragraph(
                f'<font name="{BOLD_FONT}" size="10" color="#000000">{product.name}</font>',
                normal_style
            ))
            product_info_text.append(Spacer(1, 4))
            
            details = []
            if product.category:
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Category: {product.category}</font>',
                    normal_style
                ))
            if product.color:
                details.append(Paragraph(
                    f'<font name="{BASE_FONT}" size="8" color="#6B7280">Color: {product.color}</font>',
                    normal_style
                ))
            product_info_text.extend(details)
            
            text_width = col_widths[1] - product_img_size - 10
            
            product_info_table = Table([[element] for element in product_info_text], colWidths=[text_width])
            product_info_table.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            product_cell = Table(
                [[product_img, product_info_table]],
                colWidths=[product_img_size + 5, text_width]
            )
            product_cell.setStyle(TableStyle([
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            
            variants = list(product.variants.all())
            total_stock = 0
            if variants:
                for v in variants:
                    total_stock += v.stock or 0
            
            display_price = variants[0].price if variants and variants[0].price else product.price
            
            table_data.append([
                Paragraph(f'<font size="9">{row_num}</font>', center_style),
                product_cell,
                Paragraph(f'<font size="9">{ProductReportPDFService.format_count(total_stock)}</font>', center_style),
                Paragraph(f'<font size="9">{ProductReportPDFService.format_currency(product.purchase_price)}</font>', center_style),
                Paragraph(f'<font name="{BOLD_FONT}" size="10">'
                         f'{ProductReportPDFService.format_currency(display_price)}</font>', center_style),
            ])
            row_num += 1
        
        return table_data, col_widths
    
    @staticmethod
    def generate_product_report_pdf_with_iterator(shop, products_qs, service_name=None, search_term=None):
        """
        Generate PDF using iterator for memory efficiency.
        Processes products in chunks without loading all into memory at once.
        """
        import time
        from services.tasks import process_products_in_chunks
        
        start_time = time.time()
        
        # Process products in chunks using iterator
        products_generator = process_products_in_chunks(products_qs, chunk_size=1000)
                
        # For now, collect products (this is still memory intensive for large datasets)
        # TODO: Implement true streaming PDF generation
        products_list = []
        for product in products_generator:
            products_list.append(product)
        
        # Now generate PDF with the collected list
        return ProductReportPDFService.generate_product_report_pdf(
            shop=shop,
            products_data=products_list,
            service_name=service_name,
            search_term=search_term
        )

    
    # @staticmethod
    # def generate_product_report_pdf(shop, products_queryset, service_name=None, search_term=None):
    #     """
    #     Generate a PDF report of products with variants.
    #     """
    #     import time
    #     start_time = time.time()
        
    #     # Prefetch images
    #     image_urls = ProductReportPDFService.collect_all_image_urls(products_queryset, shop)
    #     image_cache = prefetch_images_batch(image_urls)
        
    #     buffer = BytesIO()
    #     doc = SimpleDocTemplate(
    #         buffer,
    #         pagesize=A4,
    #         rightMargin=30,
    #         leftMargin=30,
    #         topMargin=80,
    #         bottomMargin=50
    #     )
        
    #     elements = []
    #     available_width = doc.width

    #     # Use cached styles
    #     title_style = get_cached_style('title')
    #     shop_name_style = get_cached_style('shop_name')
    #     normal_style = get_cached_style('normal')
    #     bold_style = get_cached_style('bold')
    #     header_cell_style = get_cached_style('header_cell')
    #     shop_style = get_cached_style('shop_style')        
        
    #     # ===== SHOP INFO SECTION =====
    #     lines = []
        
    #     name = ProductReportPDFService.safe_get_attr(shop, 'name')
    #     if name:
    #         lines.append(f'<font name="{EXTRA_BOLD_FONT}" size="16" color="#6C5CE7"><b>{name}</b></font>')
        
    #     address = ProductReportPDFService.safe_get_attr(shop, 'address')
    #     if address:
    #         lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{address}</font>')
        
    #     city = ProductReportPDFService.safe_get_attr(shop, 'city')
    #     state = ProductReportPDFService.safe_get_attr(shop, 'state')
    #     pincode = ProductReportPDFService.safe_get_attr(shop, 'pincode')
        
    #     location_parts = [p for p in [city, state] if p]
    #     location_text = ", ".join(location_parts)
        
    #     if pincode:
    #         location_text = f"{location_text} - {pincode}" if location_text else pincode
        
    #     if location_text:
    #         lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{location_text}</font>')
        
    #     phone = ProductReportPDFService.safe_get_attr(shop, 'phone')
    #     if phone:
    #         lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{phone}</font>')
        
    #     phone2 = ProductReportPDFService.safe_get_attr(shop, 'phone2')
    #     if phone2:
    #         lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{phone2}</font>')
        
    #     email = ProductReportPDFService.safe_get_attr(shop, 'email')
    #     if email:
    #         lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{email}</font>')
        
    #     shop_info_text = "<br/>".join(lines)
    #     shop_info = Paragraph(shop_info_text, shop_style)
        
    #     # Get shop logo
    #     shop_logo = ProductReportPDFService.get_shop_logo(shop, image_cache, 60)
        
    #     # Create shop info with logo
    #     shop_info_with_logo = Table(
    #         [[shop_logo, shop_info]],
    #         colWidths=[70, available_width - 70]
    #     )
    #     shop_info_with_logo.setStyle(TableStyle([
    #         ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    #         ("LEFTPADDING", (0, 0), (-1, -1), 0),
    #         ("RIGHTPADDING", (0, 0), (0, 0), 10),
    #         ("TOPPADDING", (0, 0), (-1, -1), 0),
    #         ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    #     ]))
        
    #     elements.append(shop_info_with_logo)
    #     elements.append(Spacer(1, 20))
        
    #     # ===== SERVICE AND DATE INFO =====
    #     service_date_cells = []
        
    #     if service_name:
    #         service_cell = Paragraph(
    #             f'<font name="{BOLD_FONT}" size="11" color="#000000">SERVICE: </font>'
    #             f'<font name="{BASE_FONT}" size="11" color="#374151">{service_name}</font>',
    #             normal_style
    #         )
    #     else:
    #         service_cell = Paragraph('', normal_style)
        
    #     date_cell = Paragraph(
    #         f'<font name="{BOLD_FONT}" size="11" color="#000000">DATE: </font>'
    #         f'<font name="{BASE_FONT}" size="11" color="#6B7280">{ProductReportPDFService.format_date_report(timezone.now())}</font>',
    #         get_cached_style('normal', alignment=2)
    #     )
        
    #     service_date_table = Table(
    #         [[service_cell, date_cell]],
    #         colWidths=[available_width * 0.5, available_width * 0.5]
    #     )
    #     service_date_table.setStyle(TableStyle([
    #         ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    #         ("LEFTPADDING", (0, 0), (-1, -1), 0),
    #         ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    #         ("TOPPADDING", (0, 0), (-1, -1), 0),
    #         ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    #     ]))
        
    #     elements.append(service_date_table)
    #     elements.append(Spacer(1, 20))
        
    #     # ===== DETERMINE MAIN SERVICE CATEGORY =====
    #     # Use iterator for memory efficiency
    #     products_list = list(products_queryset.iterator(chunk_size=1000))
        
    #     main_category = ProductReportPDFService.get_main_service_category(products_list)
        
    #     # ===== SUMMARY STATISTICS =====
    #     stats = None
    #     if "dress" in main_category.lower():
    #         stats = ProductReportPDFService.calculate_product_stats_for_dresses(products_list)
    #     elif "material" in main_category.lower():
    #         stats = ProductReportPDFService.calculate_product_stats_for_materials(products_list)
    #     else:
    #         stats = ProductReportPDFService.calculate_product_stats_for_other(products_list)
        
    #     # Create the rounded stats table
    #     rounded_stats = ProductReportPDFService.create_rounded_stats_table(
    #         stats, 
    #         main_category, 
    #         available_width
    #     )
    #     elements.append(rounded_stats)
    #     elements.append(Spacer(1, 30))
        
    #     # ===== PRODUCTS TABLE =====
    #     table_data = None
    #     col_widths = None
        
    #     if "dress" in main_category.lower():
    #         table_data, col_widths = ProductReportPDFService.generate_dresses_table(
    #             products_list, available_width, getSampleStyleSheet(), elements, header_cell_style, image_cache
    #         )
    #     elif "material" in main_category.lower():
    #         table_data, col_widths = ProductReportPDFService.generate_materials_table(
    #             products_list, available_width, getSampleStyleSheet(), elements, header_cell_style, image_cache
    #         )
    #     elif "vehicle" in main_category.lower():
    #         table_data, col_widths = ProductReportPDFService.generate_vehicle_table(
    #             products_list, available_width, getSampleStyleSheet(), elements, header_cell_style, image_cache
    #         )
    #     else:
    #         table_data, col_widths = ProductReportPDFService.generate_other_table(
    #             products_list, available_width, getSampleStyleSheet(), elements, header_cell_style, main_category, image_cache
    #         )
        
    #     # Create table with styling
    #     if table_data and col_widths:
    #         product_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            
    #         table_style = [
    #             ("BACKGROUND", (0, 0), (-1, 0), ProductReportPDFService.GREY_COLOR),
    #             ("TEXTCOLOR", (0, 0), (-1, 0), ProductReportPDFService.BLACK),
    #             ("ALIGN", (0, 0), (-1, 0), "CENTER"),
    #             ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
    #             ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
    #             ("FONTSIZE", (0, 0), (-1, 0), 9),
    #             ("TOPPADDING", (0, 0), (-1, 0), 12),
    #             ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
    #             ("LEFTPADDING", (0, 0), (-1, 0), 5),
    #             ("RIGHTPADDING", (0, 0), (-1, 0), 5),
    #             ("LINEABOVE", (0, 0), (-1, 0), 1, ProductReportPDFService.GREY_COLOR),
    #             ("ALIGN", (0, 1), (0, -1), "CENTER"),
    #             ("VALIGN", (0, 1), (0, -1), "MIDDLE"),
    #             ("ALIGN", (1, 1), (1, -1), "LEFT"),
    #             ("VALIGN", (1, 1), (1, -1), "MIDDLE"),
    #             ("TOPPADDING", (0, 1), (-1, -1), 10),
    #             ("BOTTOMPADDING", (0, 1), (-1, -1), 10),
    #             ("LEFTPADDING", (0, 1), (-1, -1), 5),
    #             ("RIGHTPADDING", (0, 1), (-1, -1), 5),
    #             ("LINEBELOW", (0, 0), (-1, 0), 1, ProductReportPDFService.GREY_COLOR),
    #             ("LINEBELOW", (0, 1), (-1, -2), 0.5, ProductReportPDFService.GREY_COLOR),
    #             ("LINEBELOW", (0, -1), (-1, -1), 1, ProductReportPDFService.GREY_COLOR),
    #             ("LINEBEFORE", (0, 0), (0, -1), 1, ProductReportPDFService.GREY_COLOR),
    #             ("LINEAFTER", (-1, 0), (-1, -1), 1, ProductReportPDFService.GREY_COLOR),
    #         ]
            
    #         # Set column alignments
    #         if main_category == "dresses":
    #             table_style.extend([
    #                 ("ALIGN", (2, 1), (2, -1), "CENTER"),
    #                 ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
    #                 ("ALIGN", (3, 1), (3, -1), "CENTER"),
    #                 ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
    #                 ("ALIGN", (4, 1), (4, -1), "CENTER"),
    #                 ("VALIGN", (4, 1), (4, -1), "MIDDLE"),
    #             ])
    #         elif main_category == "materials":
    #             table_style.extend([
    #                 ("ALIGN", (2, 1), (2, -1), "CENTER"),
    #                 ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
    #                 ("ALIGN", (3, 1), (3, -1), "CENTER"),
    #                 ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
    #                 ("ALIGN", (4, 1), (4, -1), "CENTER"),
    #                 ("VALIGN", (4, 1), (4, -1), "MIDDLE"),
    #             ])
    #         else:
    #             table_style.extend([
    #                 ("ALIGN", (2, 1), (2, -1), "CENTER"),
    #                 ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
    #                 ("ALIGN", (3, 1), (3, -1), "CENTER"),
    #                 ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
    #                 ("ALIGN", (4, 1), (4, -1), "CENTER"),
    #                 ("VALIGN", (4, 1), (4, -1), "MIDDLE"),
    #             ])
            
    #         # Alternate row colors
    #         for row_idx in range(1, len(table_data)):
    #             if row_idx % 2 == 0:
    #                 table_style.append(
    #                     ("BACKGROUND", (0, row_idx), (-1, row_idx), ProductReportPDFService.LIGHT_GREY)
    #                 )
            
    #         # Add rounded corners effect
    #         table_style.extend([
    #             ("TOPPADDING", (0, 0), (0, 0), 15),
    #             ("LEFTPADDING", (0, 0), (0, 0), 10),
    #             ("TOPPADDING", (-1, 0), (-1, 0), 15),
    #             ("RIGHTPADDING", (-1, 0), (-1, 0), 10),
    #             ("BOTTOMPADDING", (0, -1), (0, -1), 15),
    #             ("LEFTPADDING", (0, -1), (0, -1), 10),
    #             ("BOTTOMPADDING", (-1, -1), (-1, -1), 15),
    #             ("RIGHTPADDING", (-1, -1), (-1, -1), 10),
    #         ])
            
    #         product_table.setStyle(TableStyle(table_style))
    #         elements.append(product_table)
        
    #     # ===== BUILD PDF =====
    #     try:
    #         doc.build(
    #             elements,
    #             onFirstPage=lambda c, d: draw_svg_background(c, d, first_page=True),
    #             onLaterPages=lambda c, d: draw_svg_background(c, d, first_page=False),
    #         )
    #     except Exception as e:
    #         logger.error(f"PDF generation failed: {e}", exc_info=True)
    #         raise RuntimeError(f"Failed to generate PDF: {e}")
        
    #     pdf_bytes = buffer.getvalue()
    #     buffer.close()
        
    #     # Clear caches to free memory
    #     with _image_cache_lock:
    #         _image_cache.clear()
        
    #     total_time = time.time() - start_time
        
    #     return pdf_bytes    


# Update the generate_product_report_pdf method:

    @staticmethod
    def generate_product_report_pdf(shop, products_data, service_name=None, search_term=None, request_params=None):
        """
        Generate a PDF report of products with variants.
        products_data can be either a queryset or a list of products.
        """
        import time
        start_time = time.time()

        show_statistics = True
        if request_params:
            in_stock_only = request_params.get('in_stock_only')
            # Hide statistics when filtering for out-of-stock items
            if in_stock_only is False:
                show_statistics = False
        
        # Convert to list if it's a queryset (for compatibility)
        if hasattr(products_data, 'iterator'):
            # It's a queryset, convert to list using iterator for memory efficiency
            products_list = []
            for product in products_data.iterator(chunk_size=1000):
                products_list.append(product)
            products_data = products_list
        
        # Now products_data should be a list
        if not isinstance(products_data, list):
            products_data = list(products_data)
        
        # Prefetch images
        image_urls = ProductReportPDFService.collect_all_image_urls(products_data, shop)
        image_cache = prefetch_images_batch(image_urls)
        
        buffer = BytesIO()
        doc = SimpleDocTemplate(
            buffer,
            pagesize=A4,
            rightMargin=30,
            leftMargin=30,
            topMargin=80,
            bottomMargin=50
        )
        
        elements = []
        available_width = doc.width
        
        # Use cached styles
        title_style = get_cached_style('title')
        shop_name_style = get_cached_style('shop_name')
        normal_style = get_cached_style('normal')
        bold_style = get_cached_style('bold')
        header_cell_style = get_cached_style('header_cell')
        shop_style = get_cached_style('shop_style')
        
        # ===== SHOP INFO SECTION =====
        lines = []
        
        name = ProductReportPDFService.safe_get_attr(shop, 'name')
        if name:
            lines.append(f'<font name="{EXTRA_BOLD_FONT}" size="16" color="#6C5CE7"><b>{name}</b></font>')
        
        address = ProductReportPDFService.safe_get_attr(shop, 'address')
        if address:
            lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{address}</font>')
        
        city = ProductReportPDFService.safe_get_attr(shop, 'city')
        state = ProductReportPDFService.safe_get_attr(shop, 'state')
        pincode = ProductReportPDFService.safe_get_attr(shop, 'pincode')
        
        location_parts = [p for p in [city, state] if p]
        location_text = ", ".join(location_parts)
        
        if pincode:
            location_text = f"{location_text} - {pincode}" if location_text else pincode
        
        if location_text:
            lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{location_text}</font>')
        
        phone = ProductReportPDFService.safe_get_attr(shop, 'phone')
        if phone:
            lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{phone}</font>')
        
        phone2 = ProductReportPDFService.safe_get_attr(shop, 'phone2')
        if phone2:
            lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{phone2}</font>')
        
        email = ProductReportPDFService.safe_get_attr(shop, 'email')
        if email:
            lines.append(f'<font name="{BASE_FONT}" size="11" color="#000000">{email}</font>')
        
        shop_info_text = "<br/>".join(lines)
        shop_info = Paragraph(shop_info_text, shop_style)
        
        # Get shop logo
        shop_logo = ProductReportPDFService.get_shop_logo(shop, image_cache, 60)
        
        # Create shop info with logo
        shop_info_with_logo = Table(
            [[shop_logo, shop_info]],
            colWidths=[70, available_width - 70]
        )
        shop_info_with_logo.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (0, 0), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        
        elements.append(shop_info_with_logo)
        elements.append(Spacer(1, 20))
        
        # ===== SERVICE AND DATE INFO =====
        service_date_cells = []
        
        if service_name:
            service_cell = Paragraph(
                f'<font name="{BOLD_FONT}" size="11" color="#000000">SERVICE: </font>'
                f'<font name="{BASE_FONT}" size="11" color="#374151">{service_name}</font>',
                normal_style
            )
        else:
            service_cell = Paragraph('', normal_style)
        
        date_cell = Paragraph(
            f'<font name="{BOLD_FONT}" size="11" color="#000000">DATE: </font>'
            f'<font name="{BASE_FONT}" size="11" color="#6B7280">{ProductReportPDFService.format_date_report(timezone.now())}</font>',
            get_cached_style('normal', alignment=2)
        )
        
        service_date_table = Table(
            [[service_cell, date_cell]],
            colWidths=[available_width * 0.5, available_width * 0.5]
        )
        service_date_table.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
            ("TOPPADDING", (0, 0), (-1, -1), 0),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
        ]))
        
        elements.append(service_date_table)
        elements.append(Spacer(1, 20))
        
        # ===== DETERMINE MAIN SERVICE CATEGORY =====
        main_category = ProductReportPDFService.get_main_service_category(products_data)
        
        # ===== SUMMARY STATISTICS =====
        # Only show statistics if not filtering for out-of-stock items
        if show_statistics:        
            stats = None
            if "dress" in main_category.lower():
                stats = ProductReportPDFService.calculate_product_stats_for_dresses(products_data)
            elif "material" in main_category.lower():
                stats = ProductReportPDFService.calculate_product_stats_for_materials(products_data)
            else:
                stats = ProductReportPDFService.calculate_product_stats_for_other(products_data)
            
            # Create the rounded stats table
            rounded_stats = ProductReportPDFService.create_rounded_stats_table(
                stats, 
                main_category, 
                available_width
            )
            elements.append(rounded_stats)
            elements.append(Spacer(1, 30))
        else:
            elements.append(Spacer(1, 10))    

        # ===== PRODUCTS TABLE =====
        table_data = None
        col_widths = None
        
        if "dress" in main_category.lower():
            table_data, col_widths = ProductReportPDFService.generate_dresses_table(
                products_data, available_width, getSampleStyleSheet(), elements, header_cell_style, image_cache
            )
        elif "material" in main_category.lower():
            table_data, col_widths = ProductReportPDFService.generate_materials_table(
                products_data, available_width, getSampleStyleSheet(), elements, header_cell_style, image_cache
            )
        elif "vehicle" in main_category.lower():
            table_data, col_widths = ProductReportPDFService.generate_vehicle_table(
                products_data, available_width, getSampleStyleSheet(), elements, header_cell_style, image_cache
            )
        else:
            table_data, col_widths = ProductReportPDFService.generate_other_table(
                products_data, available_width, getSampleStyleSheet(), elements, header_cell_style, main_category, image_cache
            )
        
        # Create table with styling
        if table_data and col_widths:
            product_table = Table(table_data, colWidths=col_widths, repeatRows=1)
            
            table_style = [
                ("BACKGROUND", (0, 0), (-1, 0), ProductReportPDFService.GREY_COLOR),
                ("TEXTCOLOR", (0, 0), (-1, 0), ProductReportPDFService.BLACK),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, 0), "MIDDLE"),
                ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
                ("FONTSIZE", (0, 0), (-1, 0), 9),
                ("TOPPADDING", (0, 0), (-1, 0), 12),
                ("BOTTOMPADDING", (0, 0), (-1, 0), 12),
                ("LEFTPADDING", (0, 0), (-1, 0), 5),
                ("RIGHTPADDING", (0, 0), (-1, 0), 5),
                ("LINEABOVE", (0, 0), (-1, 0), 1, ProductReportPDFService.GREY_COLOR),
                ("ALIGN", (0, 1), (0, -1), "CENTER"),
                ("VALIGN", (0, 1), (0, -1), "MIDDLE"),
                ("ALIGN", (1, 1), (1, -1), "LEFT"),
                ("VALIGN", (1, 1), (1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 1), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 1), (-1, -1), 10),
                ("LEFTPADDING", (0, 1), (-1, -1), 5),
                ("RIGHTPADDING", (0, 1), (-1, -1), 5),
                ("LINEBELOW", (0, 0), (-1, 0), 1, ProductReportPDFService.GREY_COLOR),
                ("LINEBELOW", (0, 1), (-1, -2), 0.5, ProductReportPDFService.GREY_COLOR),
                ("LINEBELOW", (0, -1), (-1, -1), 1, ProductReportPDFService.GREY_COLOR),
                ("LINEBEFORE", (0, 0), (0, -1), 1, ProductReportPDFService.GREY_COLOR),
                ("LINEAFTER", (-1, 0), (-1, -1), 1, ProductReportPDFService.GREY_COLOR),
            ]
            
            # Set column alignments
            if "dress" in main_category.lower():
                table_style.extend([
                    ("ALIGN", (2, 1), (2, -1), "CENTER"),
                    ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),
                    ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),
                    ("VALIGN", (4, 1), (4, -1), "MIDDLE"),
                ])
            elif "material" in main_category.lower():
                table_style.extend([
                    ("ALIGN", (2, 1), (2, -1), "CENTER"),
                    ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),
                    ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),
                    ("VALIGN", (4, 1), (4, -1), "MIDDLE"),
                ])
            else:
                table_style.extend([
                    ("ALIGN", (2, 1), (2, -1), "CENTER"),
                    ("VALIGN", (2, 1), (2, -1), "MIDDLE"),
                    ("ALIGN", (3, 1), (3, -1), "CENTER"),
                    ("VALIGN", (3, 1), (3, -1), "MIDDLE"),
                    ("ALIGN", (4, 1), (4, -1), "CENTER"),
                    ("VALIGN", (4, 1), (4, -1), "MIDDLE"),
                ])
            
            # Alternate row colors
            for row_idx in range(1, len(table_data)):
                if row_idx % 2 == 0:
                    table_style.append(
                        ("BACKGROUND", (0, row_idx), (-1, row_idx), ProductReportPDFService.LIGHT_GREY)
                    )
            
            # Add rounded corners effect
            table_style.extend([
                ("TOPPADDING", (0, 0), (0, 0), 15),
                ("LEFTPADDING", (0, 0), (0, 0), 10),
                ("TOPPADDING", (-1, 0), (-1, 0), 15),
                ("RIGHTPADDING", (-1, 0), (-1, 0), 10),
                ("BOTTOMPADDING", (0, -1), (0, -1), 15),
                ("LEFTPADDING", (0, -1), (0, -1), 10),
                ("BOTTOMPADDING", (-1, -1), (-1, -1), 15),
                ("RIGHTPADDING", (-1, -1), (-1, -1), 10),
            ])
            
            product_table.setStyle(TableStyle(table_style))
            elements.append(product_table)
        
        # ===== BUILD PDF =====
        try:
            doc.build(
                elements,
                onFirstPage=lambda c, d: draw_svg_background(c, d, first_page=True),
                onLaterPages=lambda c, d: draw_svg_background(c, d, first_page=False),
            )
        except Exception as e:
            logger.error(f"PDF generation failed: {e}", exc_info=True)
            raise RuntimeError(f"Failed to generate PDF: {e}")
        
        pdf_bytes = buffer.getvalue()
        buffer.close()
        
        # Clear caches to free memory
        with _image_cache_lock:
            _image_cache.clear()
        
        total_time = time.time() - start_time
        
        return pdf_bytes

# ============================================================================
# FONT REGISTRATION
# ============================================================================
def register_fonts():
    """Register fonts with fallback to default if custom fonts are unavailable."""
    global BASE_FONT, BOLD_FONT, EXTRA_BOLD_FONT
    
    try:
        # Try to find font files
        font_paths = []
        for font_name in ["Inter-Regular.ttf", "Inter-SemiBold.ttf", "Inter-ExtraBold.ttf"]:
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
            pdfmetrics.registerFont(TTFont('Inter-ExtraBold', font_paths[2]))
            BASE_FONT = 'Inter-Regular'
            BOLD_FONT = 'Inter-SemiBold'
            EXTRA_BOLD_FONT = 'Inter-ExtraBold'
        else:
            raise FileNotFoundError("Inter fonts not found")
    except Exception as e:
        logger.warning(f"Failed to register Inter fonts, using fallback: {e}")
        BASE_FONT = 'Helvetica'
        BOLD_FONT = 'Helvetica-Bold'
        EXTRA_BOLD_FONT = 'Helvetica-Bold'


# Register fonts on module load
try:
    register_fonts()
except Exception as e:
    logger.error(f"Font registration failed: {e}")