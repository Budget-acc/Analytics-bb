# services/utils/pdf_merger.py
from io import BytesIO
import logging
from PyPDF2 import PdfMerger, PdfReader
from typing import List, Tuple
import gc

logger = logging.getLogger("api_requests")

class PDFChunkMerger:
    """Handles merging of multiple PDF chunks with memory management."""
    
    @staticmethod
    def merge_pdf_chunks(pdf_chunks: List[bytes]) -> bytes:
        """
        Merge multiple PDF byte arrays into a single PDF.
        Uses streaming approach to minimize memory usage.
        
        Args:
            pdf_chunks: List of PDF byte arrays to merge
            
        Returns:
            bytes: Merged PDF as bytes
        """
        if not pdf_chunks:
            raise ValueError("No PDF chunks to merge")
        
        if len(pdf_chunks) == 1:
            return pdf_chunks[0]
        
        merger = PdfMerger()
        
        try:
            logger.info(f"[PDF Merger] Starting merge of {len(pdf_chunks)} chunks")
            
            for idx, chunk_bytes in enumerate(pdf_chunks):
                # Process one chunk at a time to manage memory
                chunk_buffer = BytesIO(chunk_bytes)
                merger.append(chunk_buffer)
                
                # Free memory after appending
                del chunk_bytes
                chunk_buffer.close()
                
                if (idx + 1) % 5 == 0:
                    logger.info(f"[PDF Merger] Merged {idx + 1}/{len(pdf_chunks)} chunks")
                    gc.collect()  # Force garbage collection every 5 chunks
            
            # Write merged PDF
            output_buffer = BytesIO()
            merger.write(output_buffer)
            merger.close()
            
            output_buffer.seek(0)
            merged_pdf = output_buffer.getvalue()
            output_buffer.close()
            
            # Final cleanup
            gc.collect()
            
            logger.info(f"[PDF Merger] Successfully merged {len(pdf_chunks)} chunks ({len(merged_pdf)} bytes)")
            return merged_pdf
            
        except Exception as e:
            logger.error(f"[PDF Merger] Error merging PDFs: {e}", exc_info=True)
            merger.close()
            gc.collect()
            raise
    
    @staticmethod
    def get_chunk_size(total_products: int) -> int:
        """
        Determine optimal chunk size based on total products.
        Balances parallelism vs memory usage.
        """
        if total_products <= 500:
            return total_products  # No chunking needed
        elif total_products <= 1000:
            return 200  # 5 chunks for 1000 products
        elif total_products <= 2000:
            return 250  # 8 chunks for 2000 products
        elif total_products <= 3500:
            return 250  # 14 chunks for 3500 products
        else:
            return 200  # More chunks for very large datasets
    
    @staticmethod
    def calculate_chunks(total_products: int, chunk_size: int) -> List[Tuple[int, int]]:
        """Calculate chunk ranges for parallel processing."""
        chunks = []
        for start in range(0, total_products, chunk_size):
            end = min(start + chunk_size, total_products)
            chunks.append((start, end))
        
        logger.info(f"[PDF Chunking] Created {len(chunks)} chunks (size={chunk_size}) for {total_products} products")
        return chunks