import io
import unittest
import asyncio
import json

import fitz
from starlette.datastructures import UploadFile

import app

TEST_MAG_ID = app.MAGAZINES[0].id if app.MAGAZINES else ""


def _pdf_bytes_with_page(width_mm: float, height_mm: float, draw_mode: str = "full_bleed_vector") -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=app.mm_to_pt(width_mm), height=app.mm_to_pt(height_mm))

    if draw_mode == "full_bleed_vector":
        shape = page.new_shape()
        shape.draw_rect(page.rect)
        shape.finish(fill=(0, 0, 0), color=(0, 0, 0))
        shape.commit()
    elif draw_mode == "inset_vector":
        inset = app.mm_to_pt(8)
        rect = fitz.Rect(inset, inset, page.rect.width - inset, page.rect.height - inset)
        shape = page.new_shape()
        shape.draw_rect(rect)
        shape.finish(fill=(0, 0, 0), color=(0, 0, 0))
        shape.commit()
    elif draw_mode == "rgb_image":
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 40, 40), 0)
        page.insert_image(fitz.Rect(20, 20, 140, 140), pixmap=pix)

    out = doc.tobytes()
    doc.close()
    return out


class GoldenFixtureTests(unittest.TestCase):
    def test_spread_format_size_passes(self):
        mag = next(m for m in app.MAGAZINES if m.id == TEST_MAG_ID)
        spread_fmt = next((f for f in mag.formats if f.kind == "spread"), None)
        self.assertIsNotNone(spread_fmt)
        if spread_fmt.size_mm:
            w, h = spread_fmt.size_mm
        else:
            w = (2 * mag.base_trim_mm[0]) + (2 * mag.bleed_mm)
            h = mag.base_trim_mm[1] + (2 * mag.bleed_mm)
        pdf_data = _pdf_bytes_with_page(w, h, draw_mode="full_bleed_vector")

        report = app.analyze_pdf_bytes(pdf_data, TEST_MAG_ID, spread_fmt.id)
        self.assertEqual(report["summary"]["ok"], True)
        self.assertEqual(report["pages"][0]["rules"]["size"]["status"], "pass")
        self.assertEqual(report["pages"][0]["template_mm"], [mag.base_trim_mm[0], mag.base_trim_mm[1]])

    def test_vector_only_page_marks_ppi_not_applicable(self):
        mag = next(m for m in app.MAGAZINES if m.id == TEST_MAG_ID)
        w = mag.base_trim_mm[0] + (2 * mag.bleed_mm)
        h = mag.base_trim_mm[1] + (2 * mag.bleed_mm)
        pdf_data = _pdf_bytes_with_page(w, h, draw_mode="full_bleed_vector")

        report = app.analyze_pdf_bytes(pdf_data, TEST_MAG_ID, "full_bleed")
        page = report["pages"][0]
        self.assertEqual(page["content_classification"], "vector_or_text")
        self.assertEqual(page["rules"]["ppi"]["status"], "not_applicable")
        self.assertEqual(page["template_mm"], [mag.base_trim_mm[0], mag.base_trim_mm[1]])

    def test_bleed_strip_check_fails_when_content_is_inset(self):
        mag = next(m for m in app.MAGAZINES if m.id == TEST_MAG_ID)
        w = mag.base_trim_mm[0] + (2 * mag.bleed_mm)
        h = mag.base_trim_mm[1] + (2 * mag.bleed_mm)
        pdf_data = _pdf_bytes_with_page(w, h, draw_mode="inset_vector")

        report = app.analyze_pdf_bytes(pdf_data, TEST_MAG_ID, "full_bleed")
        page = report["pages"][0]
        self.assertEqual(page["rules"]["bleed_content"]["status"], "fail")
        self.assertTrue(any("Afloop-inhoud ontbreekt" in i for i in page["issues"]))
        self.assertTrue(any("doorlopen in de afloop" in r for r in report["recommendations"]))

    def test_print_checks_flag_non_cmyk_images(self):
        mag = next(m for m in app.MAGAZINES if m.id == TEST_MAG_ID)
        w = mag.base_trim_mm[0] + (2 * mag.bleed_mm)
        h = mag.base_trim_mm[1] + (2 * mag.bleed_mm)
        pdf_data = _pdf_bytes_with_page(w, h, draw_mode="rgb_image")

        report = app.analyze_pdf_bytes(pdf_data, TEST_MAG_ID, "full_bleed")
        self.assertFalse(report["print_checks"]["color_space_ok"])
        self.assertGreaterEqual(len(report["print_checks"]["non_cmyk_images"]), 1)
        self.assertTrue(any("CMYK" in r for r in report["recommendations"]))

class AsyncJobTests(unittest.IsolatedAsyncioTestCase):
    async def test_async_analyze_job_lifecycle(self):
        mag = next(m for m in app.MAGAZINES if m.id == TEST_MAG_ID)
        w = mag.base_trim_mm[0] + (2 * mag.bleed_mm)
        h = mag.base_trim_mm[1] + (2 * mag.bleed_mm)
        pdf_data = _pdf_bytes_with_page(w, h, draw_mode="full_bleed_vector")

        upload = UploadFile(filename="fixture.pdf", file=io.BytesIO(pdf_data))
        queued = await app.analyze_pdf_async(
            pdf=upload,
            magazine_id=TEST_MAG_ID,
            format_id="full_bleed",
        )
        queued_payload = json.loads(queued.body.decode("utf-8"))
        job_id = queued_payload["job_id"]
        self.assertEqual(queued_payload["status"], "queued")

        final_payload = None
        for _ in range(40):
            poll = app.get_analyze_job(job_id)
            payload = json.loads(poll.body.decode("utf-8"))
            if payload["status"] in ("completed", "failed"):
                final_payload = payload
                break
            await asyncio.sleep(0.05)

        self.assertIsNotNone(final_payload)
        self.assertEqual(final_payload["status"], "completed")
        self.assertIn("result", final_payload)


if __name__ == "__main__":
    unittest.main()
