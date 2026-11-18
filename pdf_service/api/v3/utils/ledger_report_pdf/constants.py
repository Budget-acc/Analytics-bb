import logging

from reportlab.lib import colors

logger = logging.getLogger("api_requests")

# ============================================================================
# FONT DEFINITIONS
# ============================================================================
BASE_FONT = 'Helvetica'
BOLD_FONT = 'Helvetica-Bold'

# ============================================================================
# COLOR CONSTANTS (Updated based on reference image)
# ============================================================================
HEADER_BG_COLOR = colors.HexColor("#7C61A0")  # Purple background for headers
SUMMARY_BG_COLOR = colors.HexColor("#FEFBFE")  # Light pink background for summary
TABLE_HEADER_BG = colors.HexColor("#7C61A0")  # Purple for table header
TABLE_HEADER_TEXT = colors.white  # White text for table header
TABLE_ROW_ALT_COLOR = colors.HexColor("#F7F3FE")  # Alternate row color
TABLE_ROW_MAIN_COLOR = colors.white  # Main row color
CUSTOM_PURPLE = colors.HexColor("#7C61A0")  # Main purple color
BLACK = colors.black
WHITE = colors.white
RED = colors.HexColor("#EF4444")
LIGHT_GREY = colors.HexColor("#F9FAFB")
DARK_GREY = colors.HexColor("#666666")
SUMMARY_BORDER_COLOR = colors.HexColor("#E5D8EF")  # Light purple border for summary table
