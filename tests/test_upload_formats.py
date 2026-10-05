"""Tests for PDF / image uploads.  Run:  python -m unittest discover -s tests -v
OCR tests are skipped automatically when the Tesseract binary isn't installed."""
import io, os, sys, tempfile, unittest
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="inkify-up-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
import app as appmod  # noqa: E402
from utils import extractors  # noqa: E402

HAVE_OCR = extractors.ocr_status()[0]
need_ocr = unittest.skipUnless(HAVE_OCR, "tesseract not installed")
SAMPLE = Path(__file__).resolve().parent.parent / "sample.docx"
TEXT = ["The quick brown fox jumps over the lazy dog while the sun sets slowly behind distant green hills.",
        "Handwriting conversion keeps every paragraph, heading and line break from the original document."]


def text_image(fmt="PNG", w=1240, h=700, size=34):
    im = Image.new("RGB", (w, h), "white"); d = ImageDraw.Draw(im); f = ImageFont.load_default(size=size); y = 80
    for p in TEXT:
        cur = ""
        for word in p.split():
            t = (cur + " " + word).strip()
            if d.textlength(t, font=f) > w - 160: d.text((80, y), cur, font=f, fill="black"); y += int(size * 1.6); cur = word
            else: cur = t
        d.text((80, y), cur, font=f, fill="black"); y += int(size * 2.4)
    b = io.BytesIO(); im.save(b, fmt); return b.getvalue(), im


def text_pdf(pages=2):
    b = io.BytesIO(); c = canvas.Canvas(b, pagesize=A4); W, H = A4
    for n in range(1, pages + 1):
        c.setFont("Helvetica-Bold", 22); c.drawString(60, H - 80, f"Chapter {n}")
        c.setFont("Helvetica", 12); y = H - 120
        for p in TEXT:
            words, cur = p.split(), ""
            for w in words:
                t = (cur + " " + w).strip()
                if c.stringWidth(t, "Helvetica", 12) > W - 120: c.drawString(60, y, cur); y -= 18; cur = w
                else: cur = t
            c.drawString(60, y, cur); y -= 30
        c.showPage()
    c.save(); return b.getvalue()


class UploadBase(unittest.TestCase):
    def setUp(self): self.c = appmod.app.test_client()

    def up(self, name, data, ctype=None):
        return self.c.post("/api/upload", data={"file": (io.BytesIO(data), name, ctype) if ctype else (io.BytesIO(data), name)},
                           content_type="multipart/form-data")

    def ok(self, name, data, ctype=None):
        r = self.up(name, data, ctype); self.assertEqual(r.status_code, 200, r.get_json()); return r.get_json()

    def convert(self, did, options=None):
        r = self.c.post("/api/render", json={"id": did, "options": options or {}, "final": True})
        self.assertEqual(r.status_code, 200, r.get_json()); j = r.get_json()
        d = self.c.get(j["download"]); self.assertEqual(d.status_code, 200); data = d.data; d.close()
        self.assertTrue(data.startswith(b"%PDF")); self.assertTrue(data.rstrip().endswith(b"%%EOF"))
        pg = self.c.get(f"/api/page/{did}/1"); self.assertEqual(pg.status_code, 200); pg.close()
        return j, data

    def text(self, did): return self.c.get(f"/api/doc/{did}").get_json()["text"]

    def assertNoTempFiles(self):
        self.assertEqual([p.name for p in appmod.UPLOADS.iterdir() if p.name != ".gitkeep"], [], "upload temp files left behind")


class DocxStillWorks(UploadBase):
    def test_docx_flow_and_response_shape(self):
        j = self.ok("sample.docx", SAMPLE.read_bytes())
        self.assertTrue({"id", "words", "paragraphs"} <= set(j)); self.assertEqual(j["type"], "Word")
        self.convert(j["id"]); self.assertNoTempFiles()

    def test_docx_with_odd_mime_still_accepted(self):
        self.ok("sample.docx", SAMPLE.read_bytes(), "application/zip")


class PdfTests(UploadBase):
    def test_text_pdf_multipage_structure_and_conversion(self):
        j = self.ok("notes.pdf", text_pdf(3), "application/pdf")
        self.assertEqual((j["type"], j["pages"]), ("PDF", 3)); self.assertNotIn("ocr", j)
        t = self.text(j["id"])
        for n in (1, 2, 3): self.assertIn(f"Chapter {n}", t)                  # every page extracted
        self.assertIn("# Chapter 1", t)                                         # headings detected
        self.assertIn("quick brown fox jumps over the lazy dog while the sun sets slowly behind distant green hills.", t)
        self.assertNotIn("fox\njumps", t)                                       # wrapped lines re-joined into a paragraph
        self.assertNoTempFiles()
        r = self.c.post("/api/doc", json={"id": j["id"], "text": t + "\n\nEdited line"}); self.assertEqual(r.status_code, 200)
        self.convert(j["id"], {"style": "neat", "paper": "plain", "ink": "#c01e2a", "thickness": "bold"})

    def test_corrupted_pdf(self):
        r = self.up("bad.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj garbage garbage"); self.assertEqual(r.status_code, 422)
        self.assertIn("corrupted", r.get_json()["error"]); self.assertNoTempFiles()

    def test_renamed_non_pdf(self):
        r = self.up("fake.pdf", b"just some text"); self.assertEqual(r.status_code, 422); self.assertIn("not a valid PDF", r.get_json()["error"])

    def test_empty_file_and_blank_pdf(self):
        r = self.up("empty.pdf", b""); self.assertEqual(r.status_code, 422)
        b = io.BytesIO(); c = canvas.Canvas(b); c.showPage(); c.save()
        r = self.up("blank.pdf", b.getvalue()); self.assertEqual(r.status_code, 422); self.assertNoTempFiles()

    def test_mime_mismatch_rejected(self):
        r = self.up("a.pdf", text_pdf(1), "image/png"); self.assertEqual(r.status_code, 400); self.assertIn("doesn't match", r.get_json()["error"])

    @need_ocr
    def test_scanned_pdf_uses_ocr(self):
        _, im = text_image(); b = io.BytesIO(); im.save(b, "PDF", resolution=150)
        j = self.ok("scan.pdf", b.getvalue()); self.assertTrue(j.get("ocr"))
        self.assertIn("brown fox", self.text(j["id"])); self.convert(j["id"])

    def test_scanned_pdf_without_ocr_engine_gives_clear_error(self):
        _, im = text_image(); b = io.BytesIO(); im.save(b, "PDF", resolution=150)
        old = dict(extractors._ocr_state); extractors._ocr_state.update(ok=False, why="test")
        try:
            r = self.up("scan.pdf", b.getvalue())
        finally:
            extractors._ocr_state.clear(); extractors._ocr_state.update(old)
        self.assertEqual(r.status_code, 503); self.assertIn("OCR", r.get_json()["error"]); self.assertNoTempFiles()


class ImageTests(UploadBase):
    @need_ocr
    def test_png_jpg_jpeg_ocr_and_conversion(self):
        for fmt, name, ct in (("PNG", "a.png", "image/png"), ("JPEG", "b.jpg", "image/jpeg"), ("JPEG", "c.jpeg", "image/jpeg")):
            data, _ = text_image(fmt); j = self.ok(name, data, ct)
            self.assertEqual(j["type"], "Image"); self.assertTrue(j["ocr"])
            self.assertIn("quick brown fox", self.text(j["id"])); self.convert(j["id"])
        self.assertNoTempFiles()

    @need_ocr
    def test_dark_background_and_transparent_png(self):
        _, im = text_image(); inv = Image.eval(im, lambda p: 255 - p); b = io.BytesIO(); inv.save(b, "PNG")
        self.assertIn("quick brown fox", self.text(self.ok("dark.png", b.getvalue())["id"]))
        t = Image.new("RGBA", (900, 200), (0, 0, 0, 0)); ImageDraw.Draw(t).text((30, 50), "Transparent sample text", font=ImageFont.load_default(size=46), fill=(0, 0, 0, 255))
        b = io.BytesIO(); t.save(b, "PNG"); self.assertIn("Transparent", self.text(self.ok("t.png", b.getvalue())["id"]))

    @need_ocr
    def test_image_without_text(self):
        b = io.BytesIO(); Image.new("RGB", (800, 600), "white").save(b, "PNG")
        r = self.up("blank.png", b.getvalue()); self.assertEqual(r.status_code, 422); self.assertIn("No text", r.get_json()["error"])
        b = io.BytesIO(); Image.effect_noise((600, 400), 90).convert("RGB").save(b, "PNG")
        self.assertEqual(self.up("noise.png", b.getvalue()).status_code, 422); self.assertNoTempFiles()

    def test_corrupt_and_truncated_images(self):
        for name, data in (("c.png", b"\x89PNG\r\n\x1a\n" + b"\0" * 40), ("t.jpg", text_image("JPEG")[0][:2500])):
            r = self.up(name, data); self.assertEqual(r.status_code, 422, name); self.assertIn("corrupted", r.get_json()["error"])
        self.assertEqual(self.up("x.png", b"not an image at all").status_code, 422); self.assertNoTempFiles()

    def test_tiny_and_huge_dimension_images(self):
        b = io.BytesIO(); Image.new("RGB", (10, 10), "white").save(b, "PNG"); self.assertEqual(self.up("tiny.png", b.getvalue()).status_code, 422)
        old = extractors.MAX_IMAGE_PIXELS; extractors.MAX_IMAGE_PIXELS = 1000
        try: r = self.up("big.png", text_image()[0])
        finally: extractors.MAX_IMAGE_PIXELS = old
        self.assertEqual(r.status_code, 422); self.assertIn("too large", r.get_json()["error"])

    def test_image_without_ocr_engine_gives_clear_error(self):
        old = dict(extractors._ocr_state); extractors._ocr_state.update(ok=False, why="test")
        try: r = self.up("a.png", text_image()[0])
        finally: extractors._ocr_state.clear(); extractors._ocr_state.update(old)
        self.assertEqual(r.status_code, 503); self.assertIn("OCR", r.get_json()["error"]); self.assertNoTempFiles()


class ValidationTests(UploadBase):
    def test_unsupported_extensions(self):
        for n in ("a.txt", "a.exe", "a.gif", "a.webp", "a.doc", "noext", "a.docx.exe"):
            r = self.up(n, b"x"); self.assertEqual(r.status_code, 400, n); self.assertIn(".docx, .pdf, .png, .jpg", r.get_json()["error"])

    def test_size_limit_still_10mb(self):
        for n in ("a.docx", "a.pdf", "a.png"):
            r = self.up(n, b"0" * (11 * 1024 * 1024)); self.assertEqual(r.status_code, 413); self.assertIn("10 MB", r.get_json()["error"])

    def test_no_file(self):
        self.assertEqual(self.c.post("/api/upload", data={}, content_type="multipart/form-data").status_code, 400)

    def test_extension_is_case_insensitive_and_path_safe(self):
        self.ok("../../Notes.PDF", text_pdf(1))


if __name__ == "__main__":
    unittest.main()
