"""Create a clearly synthetic invoice image for upload and OCR smoke checks."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    output = Path("presentation_data/synthetic_invoices/upload-smoke-invoice.png")
    output.parent.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (1275, 1650), "white")
    draw = ImageDraw.Draw(image)
    regular = ImageFont.load_default(size=30)
    bold = ImageFont.load_default(size=38)
    lines = [
        (70, "SYNTHETIC TEST DOCUMENT — NOT FOR PAYMENT", bold),
        (170, "INVOICE", bold),
        (250, "Invoice Number: PT-SMOKE-2026-001", regular),
        (310, "Invoice Date: 2026-11-04", regular),
        (390, "Vendor: Northwind Office Supplies (Synthetic)", regular),
        (450, "Customer: Paper Trail Demo (Synthetic)", regular),
        (550, "Description                         Qty     Unit Price       Amount", regular),
        (620, "Printer paper A4                     2       12.50           25.00", regular),
        (680, "Blue ballpoint pens                   3        4.00           12.00", regular),
        (800, "Subtotal: EUR 37.00", regular),
        (860, "Tax (10%): EUR 3.70", regular),
        (920, "Total: EUR 40.70", bold),
        (1100, "Synthetic fixture for local upload smoke testing only.", regular),
    ]
    for y, value, font in lines:
        draw.text((80, y), value, fill="#111827", font=font)
    for y in (140, 500, 760, 990):
        draw.line((70, y, 1200, y), fill="#9ca3af", width=2)
    image.save(output)
    print(output.resolve())


if __name__ == "__main__":
    main()
