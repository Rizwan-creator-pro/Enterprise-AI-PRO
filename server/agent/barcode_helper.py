"""
Enterprise AI Hub - Barcode & QR Code Scanning Engine
Detects, decodes, and parses 1D and 2D barcodes (QR Code, DataMatrix, Aztec, EAN-13, Code 128, etc.)
from uploaded images using zxing-cpp.
"""

import io
from typing import List, Dict, Any

def scan_barcodes_and_qr(image_bytes: bytes) -> List[Dict[str, Any]]:
    """
    Scans and decodes all barcodes / QR codes from image bytes.
    Returns list of detected codes with format and text.
    """
    if not image_bytes:
        return []

    try:
        import zxingcpp
        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes))
        results = zxingcpp.read_barcodes(img)
        if not results:
            # Try rotating 90, 180, 270 if not detected directly
            for rot in [Image.ROTATE_90, Image.ROTATE_180, Image.ROTATE_270]:
                rotated = img.transpose(rot)
                results = zxingcpp.read_barcodes(rotated)
                if results:
                    break

        detected = []
        for r in results:
            fmt = str(r.format.name)
            txt = str(r.text).strip()
            if txt:
                detected.append({
                    "format": fmt,
                    "text": txt,
                    "is_url": txt.startswith("http://") or txt.startswith("https://")
                })
        return detected
    except Exception as e:
        print(f"[Barcode/QR Scanner] Error: {e}")
        return []
