"""Backend tests for "My Own Handwriting" (Part 1).  Run:  python -m unittest discover -s tests -v"""
import io, os, sys, tempfile, unittest
from pathlib import Path

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="inkify-test-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from PIL import Image
import app as appmod  # noqa: E402


def png(w=400, h=400, fmt="PNG"):
    b = io.BytesIO(); Image.new("RGB", (w, h), "white").save(b, fmt); return b.getvalue()


class Base(unittest.TestCase):
    def setUp(self):
        self.c = appmod.app.test_client()                       # one cookie jar = one visitor

    def mk(self, c=None, name=None):
        return (c or self.c).post("/api/hw/profiles", json={"name": name} if name else {}).get_json()

    def up(self, pid, name, data, ctype="application/octet-stream", c=None):
        return (c or self.c).post(f"/api/hw/profiles/{pid}/samples", data={"file": (io.BytesIO(data), name, ctype)},
                                  content_type="multipart/form-data")


class TemplateTests(Base):
    def test_template_downloads_as_pdf(self):
        r = self.c.get("/api/hw/template.pdf")
        self.assertEqual(r.status_code, 200); self.assertEqual(r.mimetype, "application/pdf")
        self.assertTrue(r.data.startswith(b"%PDF-")); self.assertIn("attachment", r.headers["Content-Disposition"])
        self.assertGreater(len(r.data), 5000)

    def test_template_inline_for_printing(self):
        self.assertIn("inline", self.c.get("/api/hw/template.pdf?inline=1").headers["Content-Disposition"])

    def test_template_contains_all_characters(self):
        from utils.template_pdf import UPPER, LOWER, DIGITS, PUNCT, SYMBOLS, SENTENCES
        self.assertEqual("".join(c for c, _ in UPPER + LOWER + DIGITS), "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789")
        self.assertGreaterEqual(len(PUNCT), 15); self.assertGreaterEqual(len(SYMBOLS), 15)
        self.assertIn("The quick brown fox jumps over the lazy dog.", SENTENCES)
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(self.c.get("/api/hw/template.pdf").data)
        self.assertGreaterEqual(len(pdf), 3)
        text = "".join(pdf[i].get_textpage().get_text_range() for i in range(len(pdf)))
        for needle in ("The quick brown fox jumps over the lazy dog.", "ABCDEFGHIJKLMNOPQRSTUVWXYZ", "0123456789", "Symbols", "Punctuation"):
            self.assertIn(needle, text)


class UploadTests(Base):
    def test_multiple_uploads_png_jpg_jpeg_pdf(self):
        pid = self.mk()["id"]
        pdf = self.c.get("/api/hw/template.pdf").data
        for name, data in (("a.png", png()), ("b.jpg", png(fmt="JPEG")), ("c.JPEG", png(fmt="JPEG")), ("d.pdf", pdf)):
            r = self.up(pid, name, data); self.assertEqual(r.status_code, 201, (name, r.get_json()))
            j = r.get_json(); self.assertEqual(j["name"], name); self.assertTrue(j["thumb"].startswith("/api/hw/"))
        p = self.c.get(f"/api/hw/profiles/{pid}").get_json()
        self.assertEqual(p["sample_count"], 4)
        for s in p["samples"]:
            self.assertEqual(self.c.get(s["thumb"]).mimetype, "image/jpeg")
        self.assertEqual(self.c.get(p["samples"][3]["url"]).mimetype, "application/pdf")

    def rejects(self, name, data, status, word=None):
        pid = self.mk()["id"]; r = self.up(pid, name, data)
        self.assertEqual(r.status_code, status, r.get_json()); self.assertIn("error", r.get_json())
        if word: self.assertIn(word, r.get_json()["error"].lower())
        self.assertEqual(self.c.get(f"/api/hw/profiles/{pid}").get_json()["sample_count"], 0)

    def test_rejects_wrong_extension(self): self.rejects("x.gif", png(fmt="GIF"), 415, "unsupported")
    def test_rejects_docx_exe_txt(self):
        for n in ("a.docx", "a.exe", "a.txt", "noext"): self.rejects(n, b"hello world", 415)
    def test_rejects_empty(self): self.rejects("a.png", b"", 422, "empty")
    def test_rejects_corrupted_png(self): self.rejects("a.png", png()[:100] + b"garbage" * 50, 422, "corrupted")
    def test_rejects_truncated_jpeg(self): self.rejects("a.jpg", png(800, 800, "JPEG")[:900], 422, "corrupted")
    def test_rejects_fake_image_renamed(self): self.rejects("a.png", b"<html><script>alert(1)</script></html>", 415)
    def test_rejects_gif_renamed_png(self): self.rejects("a.png", png(fmt="GIF"), 415)
    def test_rejects_corrupted_pdf(self): self.rejects("a.pdf", b"%PDF-1.4\nthis is not a pdf at all", 422, "corrupted")
    def test_rejects_too_large(self): self.rejects("a.png", b"\x89PNG\r\n\x1a\n" + b"0" * (8 * 1024 * 1024), 413, "too large")
    def test_rejects_tiny_image(self): self.rejects("a.png", png(50, 50), 422, "too small")

    def test_global_413_for_huge_request(self):
        pid = self.mk()["id"]
        r = self.up(pid, "a.png", b"0" * (11 * 1024 * 1024))
        self.assertEqual(r.status_code, 413); self.assertIn("error", r.get_json())

    def test_sample_limit(self):
        from utils.profiles import MAX_SAMPLES
        pid = self.mk()["id"]; data = png(300, 300)
        for _ in range(MAX_SAMPLES): self.assertEqual(self.up(pid, "a.png", data).status_code, 201)
        r = self.up(pid, "a.png", data); self.assertEqual(r.status_code, 409)

    def test_filename_never_used_on_disk(self):
        pid = self.mk()["id"]; evil = "../../../etc/passwd.png"
        j = self.up(pid, evil, png()).get_json()
        self.assertNotIn("..", j["name"]); self.assertNotIn("/", j["name"])
        for p in Path(os.environ["DATA_DIR"]).rglob("*"):
            self.assertNotIn("passwd", p.name)

    def test_remove_sample(self):
        pid = self.mk()["id"]; sid = self.up(pid, "a.png", png()).get_json()["id"]
        self.assertEqual(self.c.delete(f"/api/hw/profiles/{pid}/samples/{sid}").status_code, 200)
        self.assertEqual(self.c.get(f"/api/hw/profiles/{pid}").get_json()["sample_count"], 0)
        self.assertEqual(self.c.delete(f"/api/hw/profiles/{pid}/samples/{sid}").status_code, 404)


class ProfileTests(Base):
    def test_save_requires_name_and_sample(self):
        pid = self.mk()["id"]
        self.assertEqual(self.c.post(f"/api/hw/profiles/{pid}/save", json={"name": "X"}).status_code, 422)   # no samples
        self.up(pid, "a.png", png())
        self.assertEqual(self.c.post(f"/api/hw/profiles/{pid}/save", json={"name": "   "}).status_code, 422)
        self.assertEqual(self.c.post(f"/api/hw/profiles/{pid}/save", json={"name": "x" * 41}).status_code, 422)
        r = self.c.post(f"/api/hw/profiles/{pid}/save", json={"name": "  College   Notes "})
        self.assertEqual(r.status_code, 200); self.assertEqual(r.get_json()["name"], "College Notes")

    def test_list_edit_delete_lifecycle(self):
        c = appmod.app.test_client()
        pid = self.mk(c)["id"]; self.up(pid, "a.png", png(), c=c); self.up(pid, "b.png", png(), c=c)
        self.assertEqual(c.get("/api/hw/profiles").get_json()["profiles"], [])          # drafts are not listed
        c.post(f"/api/hw/profiles/{pid}/save", json={"name": "My Handwriting"})
        lst = c.get("/api/hw/profiles").get_json()["profiles"]
        self.assertEqual(len(lst), 1); self.assertEqual(lst[0]["sample_count"], 2)
        self.assertTrue(lst[0]["created"] > 0); self.assertEqual(lst[0]["processing"]["state"], "not_started")
        c.post(f"/api/hw/profiles/{pid}/save", json={"name": "Renamed"})                  # edit = re-save
        self.assertEqual(c.get("/api/hw/profiles").get_json()["profiles"][0]["name"], "Renamed")
        self.assertEqual(c.delete(f"/api/hw/profiles/{pid}").status_code, 200)
        self.assertEqual(c.get("/api/hw/profiles").get_json()["profiles"], [])
        self.assertEqual(c.get(f"/api/hw/profiles/{pid}").status_code, 404)

    def test_duplicate_name_rejected(self):
        c = appmod.app.test_client(); a, b = self.mk(c)["id"], self.mk(c)["id"]
        for p in (a, b): self.up(p, "a.png", png(), c=c)
        self.assertEqual(c.post(f"/api/hw/profiles/{a}/save", json={"name": "Notes"}).status_code, 200)
        self.assertEqual(c.post(f"/api/hw/profiles/{b}/save", json={"name": "notes"}).status_code, 409)

    def test_other_visitors_cannot_access(self):
        owner, thief = appmod.app.test_client(), appmod.app.test_client()
        pid = self.mk(owner)["id"]; s = self.up(pid, "a.png", png(), c=owner).get_json()
        owner.post(f"/api/hw/profiles/{pid}/save", json={"name": "Private"})
        for method, url in (("get", f"/api/hw/profiles/{pid}"), ("get", s["thumb"]), ("get", s["url"]),
                            ("delete", f"/api/hw/profiles/{pid}"), ("delete", f"/api/hw/profiles/{pid}/samples/{s['id']}"),
                            ("post", f"/api/hw/profiles/{pid}/process")):
            self.assertEqual(getattr(thief, method)(url).status_code, 404, url)
        self.assertEqual(self.up(pid, "a.png", png(), c=thief).status_code, 404)
        self.assertEqual(thief.get("/api/hw/profiles").get_json()["profiles"], [])
        self.assertEqual(owner.get(s["url"]).status_code, 200)                       # owner still can

    def test_bad_ids_and_traversal(self):
        for u in ("/api/hw/profiles/..", "/api/hw/profiles/zzz", "/api/hw/profiles/" + "a" * 32 + "/samples/../x/file"):
            self.assertIn(self.c.get(u).status_code, (404, 405))

    def test_uploads_not_in_static_or_public_urls(self):
        pid = self.mk()["id"]; self.up(pid, "a.png", png())
        self.assertEqual(self.c.get(f"/static/../profiles/{pid}/profile.json").status_code, 404)
        self.assertEqual(self.c.get(f"/uploads/{pid}").status_code, 404)

    def test_process_stub(self):
        pid = self.mk()["id"]; r = self.c.post(f"/api/hw/profiles/{pid}/process")
        self.assertEqual(r.status_code, 501); self.assertEqual(r.get_json()["code"], "coming_soon")

    def test_draft_sweep_keeps_saved(self):
        from utils import profiles as P
        c = appmod.app.test_client(); keep, drop = self.mk(c)["id"], self.mk(c)["id"]
        self.up(keep, "a.png", png(), c=c); c.post(f"/api/hw/profiles/{keep}/save", json={"name": "Keeper"})
        store = appmod.app.blueprints["own_handwriting"].store
        old = P.DRAFT_TTL; P.DRAFT_TTL = -1
        try: store.sweep(force=True)
        finally: P.DRAFT_TTL = old
        self.assertEqual(c.get(f"/api/hw/profiles/{keep}").status_code, 200)
        self.assertEqual(c.get(f"/api/hw/profiles/{drop}").status_code, 404)


class ExistingFeatureTests(Base):
    def test_index_and_static(self):
        self.assertEqual(self.c.get("/").status_code, 200)
        for f in ("css/style.css", "js/script.js"): self.assertEqual(self.c.get("/static/" + f).status_code, 200)

    def test_docx_flow_still_works(self):
        sample = Path(__file__).resolve().parent.parent / "sample.docx"
        r = self.c.post("/api/upload", data={"file": (io.BytesIO(sample.read_bytes()), "sample.docx")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 200, r.get_json()); did = r.get_json()["id"]
        r = self.c.post("/api/render", json={"id": did, "options": {}, "final": True}); self.assertEqual(r.status_code, 200, r.get_json())
        d = self.c.get(r.get_json()["download"]); self.assertEqual(d.status_code, 200); self.assertTrue(d.data.startswith(b"%PDF"))
        self.assertEqual(self.c.get(f"/api/page/{did}/1").status_code, 200)

    def test_docx_rejects_non_docx_and_old_413(self):
        r = self.c.post("/api/upload", data={"file": (io.BytesIO(b"x"), "a.txt")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/upload", data={"file": (io.BytesIO(b"0" * (11 * 1024 * 1024)), "a.docx")}, content_type="multipart/form-data")
        self.assertEqual(r.status_code, 413); self.assertIn("10 MB", r.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
