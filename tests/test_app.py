import io
import json
import os
import tempfile
import unittest
from unittest import mock

import fitz
from starlette.datastructures import UploadFile

import app

TEST_MAG_ID = app.MAGAZINES[0].id if app.MAGAZINES else ""


class FakeDoc:
    def __init__(self, page_count: int = 0):
        self.page_count = page_count
        self.closed = False

    def load_page(self, index: int):
        raise AssertionError("load_page should not be called for zero-page docs")

    def close(self):
        self.closed = True


class FakeNamedTempFile:
    def __init__(self, path: str):
        self.name = path
        self._fh = None

    def __enter__(self):
        self._fh = open(self.name, "wb")
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._fh:
            self._fh.close()

    def write(self, data: bytes):
        return self._fh.write(data)


class AppTests(unittest.IsolatedAsyncioTestCase):
    async def test_detect_pdf_uses_filename_for_magazine(self):
        mag = app.MAGAZINES[0]
        fmt = next(
            (f for f in mag.formats if f.kind in ("quarter", "eighth", "spread")),
            mag.formats[0],
        )
        w, h = app.expected_page_sizes_for_format(mag, fmt)[0]

        doc = fitz.open()
        doc.new_page(width=app.mm_to_pt(w), height=app.mm_to_pt(h))
        pdf_data = doc.tobytes()
        doc.close()

        upload = UploadFile(
            filename=f"{mag.id}_advertentie.pdf",
            file=io.BytesIO(pdf_data),
        )
        response = await app.detect_pdf(pdf=upload)
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["match"]["magazine_id"], mag.id)
        self.assertEqual(payload["match"]["source"], "filename+size")

    async def test_detect_pdf_returns_match(self):
        # Build a simple one-page PDF; exact match target is less important than stable response shape.
        doc = fitz.open()
        doc.new_page(width=app.mm_to_pt(210.0), height=app.mm_to_pt(297.0))
        pdf_data = doc.tobytes()
        doc.close()

        upload = UploadFile(filename="detect.pdf", file=io.BytesIO(pdf_data))
        response = await app.detect_pdf(pdf=upload)
        payload = json.loads(response.body.decode("utf-8"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("match", payload)
        self.assertIn("magazine_id", payload["match"])
        self.assertIn("format_id", payload["match"])
        self.assertIn("actual_page_mm", payload)

    async def test_analyze_pdf_zero_pages_does_not_crash(self):
        upload = UploadFile(filename="empty.pdf", file=io.BytesIO(b"%PDF-1.4\n%%EOF"))
        fake_doc = FakeDoc(page_count=0)

        with mock.patch("app.fitz.open", return_value=fake_doc):
            response = await app.analyze_pdf(
                pdf=upload,
                magazine_id=TEST_MAG_ID,
                format_id="full_bleed",
            )

        payload = json.loads(response.body.decode("utf-8"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["pages"], [])
        self.assertEqual(payload["worst_images"], [])
        self.assertTrue(fake_doc.closed)

    async def test_analyze_pdf_always_removes_temp_file(self):
        fd, path = tempfile.mkstemp(suffix=".pdf")
        os.close(fd)
        os.unlink(path)
        upload = UploadFile(filename="input.pdf", file=io.BytesIO(b"%PDF-1.4\n%%EOF"))

        def fake_tmp(*args, **kwargs):
            return FakeNamedTempFile(path)

        with mock.patch("app.tempfile.NamedTemporaryFile", side_effect=fake_tmp), mock.patch(
            "app.fitz.open", return_value=FakeDoc(page_count=0)
        ):
            await app.analyze_pdf(pdf=upload, magazine_id=TEST_MAG_ID, format_id="full_bleed")

        self.assertFalse(os.path.exists(path), "temporary PDF should be removed in finally block")

    def test_summarize_page_checks_uses_all_pages(self):
        pages = [
            {"pdf_size_ok": True, "bleed_ok": True, "bleed_content_ok": True, "ppi_ok": True},
            {"pdf_size_ok": False, "bleed_ok": True, "bleed_content_ok": False, "ppi_ok": True},
        ]
        size_ok, bleed_ok, ppi_ok = app.summarize_page_checks(pages)
        self.assertFalse(size_ok)
        self.assertFalse(bleed_ok)
        self.assertTrue(ppi_ok)

    def test_render_html_report_uses_aggregated_status(self):
        report = {
            "magazine": "Default A4",
            "format": "Full page",
            "bleed_required": True,
            "min_effective_ppi": 300,
            "summary": {"ok": False},
            "pages": [
                {"pdf_size_ok": True, "bleed_ok": True, "bleed_content_ok": True, "ppi_ok": True},
                {"pdf_size_ok": False, "bleed_ok": True, "bleed_content_ok": True, "ppi_ok": False},
            ],
            "issues": ["Page 2: mismatch"],
            "worst_images": [],
        }
        html = app.render_html_report(report)
        self.assertIn("Formaat: fout", html)
        self.assertIn("PPI: fout", html)
        self.assertIn("Afloop: OK", html)

    def test_bleed_content_reaches_edges(self):
        page = app.fitz.Rect(0, 0, 100, 200)
        inside = app.fitz.Rect(3, 3, 97, 197)
        touching = app.fitz.Rect(0.5, 0.5, 99.5, 199.5)
        self.assertFalse(app.bleed_content_reaches_edges(page, inside, max_gap_mm=1.0))
        self.assertTrue(app.bleed_content_reaches_edges(page, touching, max_gap_mm=1.0))

    def test_issue_prefix_is_normalized_to_dutch(self):
        self.assertEqual(app._issue_nl("Page 1: mismatch"), "Pagina 1: mismatch")


if __name__ == "__main__":
    unittest.main()
