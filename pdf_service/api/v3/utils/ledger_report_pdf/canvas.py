from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from . import constants


# ============================================================================
# PAGE TEMPLATE WITH PAGE NUMBERS
# ============================================================================
class PageNumberCanvas(canvas.Canvas):
    """Custom canvas for adding page numbers with background color."""

    def __init__(self, *args, **kwargs):
        canvas.Canvas.__init__(self, *args, **kwargs)
        self.pages = []

    def showPage(self):
        self.pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        page_count = len(self.pages)
        for page in self.pages:
            self.__dict__.update(page)
            self.draw_page_number(page_count)
            canvas.Canvas.showPage(self)
        canvas.Canvas.save(self)

    def draw_page_number(self, page_count):
        """Draw page number without background color."""
        page_width, page_height = A4

        # Draw page number text only (no background)
        page_num_text = f"Page {self._pageNumber} of {page_count}"

        # Draw page number text
        self.setFont(constants.BASE_FONT, 10)
        self.setFillColor(colors.black)  # Black text
        self.drawCentredString(page_width / 2, 30, page_num_text)
