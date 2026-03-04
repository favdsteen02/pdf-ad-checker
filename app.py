from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import JSONResponse, HTMLResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass
import tempfile
import base64
import math
import os
import asyncio
import threading
import uuid
import json
import re
import unicodedata

import fitz  # PyMuPDF

# Optional (preview bbox detection). If not installed, we fall back gracefully.
try:
    import numpy as np
    from PIL import Image
    HAS_IMG = True
except Exception:
    HAS_IMG = False

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # local dev + Framer
    # Wildcard origins cannot be used with credentialed browser CORS requests.
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Models / Config
# ----------------------------

@dataclass
class FormatSpec:
    id: str
    label: str
    kind: str  # "full" | "half" | "quarter"
    bleed_required: bool
    allow_landscape: bool = False
    size_mm: Optional[Tuple[float, float]] = None

@dataclass
class MagazineSpec:
    id: str
    name: str
    display_name: Optional[str]
    publisher: str
    base_trim_mm: Tuple[float, float]  # (w, h)
    bleed_mm: float
    min_effective_ppi: int
    preview_padding_pct: float
    support: Dict[str, Any]
    formats: List[FormatSpec]

def load_magazines_from_json(path: str) -> List[MagazineSpec]:
    def infer_kind(format_id: str) -> str:
        fid = (format_id or "").lower()
        if "spread" in fid:
            return "spread"
        if "quarter" in fid:
            return "quarter"
        if "half" in fid:
            return "half"
        if "eighth" in fid:
            return "eighth"
        return "full"

    def derive_formats(base_trim_mm: Tuple[float, float], bleed_mm: float) -> List[Dict[str, Any]]:
        tw, th = float(base_trim_mm[0]), float(base_trim_mm[1])
        has_bleed = bleed_mm > 0
        return [
            {
                "id": "full_bleed" if has_bleed else "full",
                "label": "1/1 page (bleed required)" if has_bleed else "1/1 page",
                "kind": "full",
                "size_mm": [tw, th],
                "bleed_required": has_bleed,
                "allow_landscape": False,
            },
            {
                "id": "half",
                "label": "1/2 page",
                "kind": "half",
                "size_mm": [tw, round(th / 2.0, 3)],
                "bleed_required": False,
                "allow_landscape": True,
            },
            {
                "id": "quarter",
                "label": "1/4 page",
                "kind": "quarter",
                "size_mm": [round(tw / 2.0, 3), round(th / 2.0, 3)],
                "bleed_required": False,
                "allow_landscape": True,
            },
            {
                "id": "spread_bleed" if has_bleed else "spread",
                "label": "2/1 spread (bleed required)" if has_bleed else "2/1 spread",
                "kind": "spread",
                "size_mm": [round(2.0 * tw, 3), th],
                "bleed_required": has_bleed,
                "allow_landscape": False,
            },
        ]

    with open(path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    mags = raw.get("magazines")
    if not isinstance(mags, list):
        pubs = raw.get("publishers")
        if isinstance(pubs, dict):
            mags = []
            for pub_name, pub in pubs.items():
                if isinstance(pub, dict) and isinstance(pub.get("magazines"), list):
                    for mg in pub["magazines"]:
                        if isinstance(mg, dict):
                            item = dict(mg)
                            if not item.get("publisher"):
                                item["publisher"] = str(pub_name)
                            mags.append(item)
    if not isinstance(mags, list):
        raise ValueError("magazines.json must contain a 'magazines' array (or publishers.*.magazines)")

    out: List[MagazineSpec] = []
    for m in mags:
        base_trim = m.get("base_trim_mm") or [0, 0]
        bleed_mm = float(m.get("bleed_mm", 0.0))
        formats_raw = m.get("formats") or derive_formats((float(base_trim[0]), float(base_trim[1])), bleed_mm)
        formats: List[FormatSpec] = []
        for fr in formats_raw:
            fmt_id = str(fr.get("id") or "")
            size_mm_raw = fr.get("size_mm")
            if not size_mm_raw:
                size_mm_raw = fr.get("trim_mm")
            size_mm: Optional[Tuple[float, float]] = None
            if isinstance(size_mm_raw, (list, tuple)) and len(size_mm_raw) == 2:
                try:
                    size_mm = (float(size_mm_raw[0]), float(size_mm_raw[1]))
                except Exception:
                    size_mm = None
            bleed_required = fr.get("bleed_required")
            if bleed_required is None:
                local_bleed = fr.get("bleed_mm")
                kind_hint = str(fr.get("kind") or infer_kind(fmt_id))
                # Only full/spread formats should infer bleed-required from bleed_mm.
                if local_bleed is not None and kind_hint in ("full", "spread"):
                    try:
                        bleed_required = float(local_bleed) > 0
                    except Exception:
                        bleed_required = None
            if bleed_required is None:
                bleed_required = ("full" in fmt_id.lower()) or ("spread" in fmt_id.lower())
            kind = str(fr.get("kind") or infer_kind(fmt_id))
            formats.append(
                FormatSpec(
                    id=fmt_id,
                    label=str(fr.get("label") or fr.get("name") or fmt_id),
                    kind=kind,
                    bleed_required=bool(bleed_required),
                    allow_landscape=bool(fr.get("allow_landscape", kind in ("half", "quarter", "eighth"))),
                    size_mm=size_mm,
                )
            )
        publisher_name = str(m.get("publisher") or "VirtuMedia")
        format_ids = [f.id for f in formats]
        kinds = sorted(set(f.kind for f in formats))
        derived_support = {
            "magazine_support": publisher_name,
            "publisher": publisher_name,
            "format_ids": format_ids,
            "kinds": kinds,
            "has_spread": any(f.kind == "spread" for f in formats),
            "has_half": any(f.kind == "half" for f in formats),
            "has_quarter": any(f.kind == "quarter" for f in formats),
            "has_bleed_format": any(bool(f.bleed_required) for f in formats),
        }
        support_raw = m.get("support")
        support: Dict[str, Any]
        if isinstance(support_raw, dict):
            support = dict(derived_support)
            support.update(support_raw)
        else:
            support = derived_support
        out.append(
            MagazineSpec(
                id=str(m["id"]),
                name=str(m["name"]),
                display_name=(str(m["display_name"]) if m.get("display_name") is not None else None),
                publisher=publisher_name,
                base_trim_mm=(float(base_trim[0]), float(base_trim[1])),
                bleed_mm=bleed_mm,
                min_effective_ppi=int(m["min_effective_ppi"]),
                preview_padding_pct=float(m.get("preview_padding_pct", 2.6)),
                support=support,
                formats=formats,
            )
        )
    return out


MAGAZINES_PATH = os.path.join(os.path.dirname(__file__), "magazines.json")
MAGAZINES: List[MagazineSpec] = load_magazines_from_json(MAGAZINES_PATH)

# ----------------------------
# Helpers
# ----------------------------

def pt_to_mm(pt: float) -> float:
    return pt * 25.4 / 72.0

def mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4

def approx_equal_mm(a: Tuple[float, float], b: Tuple[float, float], tol_mm: float = 0.5) -> bool:
    return abs(a[0] - b[0]) <= tol_mm and abs(a[1] - b[1]) <= tol_mm

def get_crop_size_mm(page: fitz.Page) -> Tuple[float, float]:
    r = page.rect
    return (round(pt_to_mm(r.width), 3), round(pt_to_mm(r.height), 3))

def expected_page_sizes_for_format(mag: MagazineSpec, fmt: FormatSpec) -> List[Tuple[float, float]]:
    if fmt.size_mm and len(fmt.size_mm) == 2:
        return [(round(float(fmt.size_mm[0]), 3), round(float(fmt.size_mm[1]), 3))]
    tw, th = mag.base_trim_mm
    if fmt.kind == "full":
        if fmt.bleed_required:
            return [(round(tw + 2 * mag.bleed_mm, 3), round(th + 2 * mag.bleed_mm, 3))]
        return [(round(tw, 3), round(th, 3))]
    if fmt.kind == "half":
        return [(round(tw, 3), round(th / 2.0, 3)), (round(tw / 2.0, 3), round(th, 3))]
    if fmt.kind == "quarter":
        return [(round(tw / 2.0, 3), round(th / 2.0, 3))]
    if fmt.kind == "spread":
        if fmt.bleed_required:
            return [(round((2.0 * tw) + 2.0 * mag.bleed_mm, 3), round(th + 2.0 * mag.bleed_mm, 3))]
        return [(round(2.0 * tw, 3), round(th, 3))]
    return [(round(tw, 3), round(th, 3))]

def effective_ppi(px_w: int, px_h: int, rect_pt: fitz.Rect) -> float:
    w_in = rect_pt.width / 72.0
    h_in = rect_pt.height / 72.0
    if w_in <= 0 or h_in <= 0:
        return 10**9
    return min(px_w / w_in if w_in else 10**9, px_h / h_in if h_in else 10**9)

def extract_images_with_ppi(page: fitz.Page, min_ppi: int) -> Tuple[List[Dict[str, Any]], Optional[int], int]:
    """
    CHANGE: effective_ppi rounded to whole numbers (int) everywhere.
    """
    out: List[Dict[str, Any]] = []
    lowest: Optional[float] = None
    below = 0

    for img in page.get_images(full=True):
        xref = img[0]
        try:
            info = page.parent.extract_image(xref)
            px_w = int(info.get("width", 0) or 0)
            px_h = int(info.get("height", 0) or 0)
        except Exception:
            px_w = px_h = 0

        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []

        for r in rects:
            ppi = effective_ppi(px_w, px_h, r)
            if lowest is None or ppi < lowest:
                lowest = ppi
            if ppi < min_ppi:
                below += 1

            ppi_int = int(round(ppi))
            out.append(
                {
                    "xref": xref,
                    "pixels": [px_w, px_h],
                    "rect_pt": [round(r.x0, 3), round(r.y0, 3), round(r.x1, 3), round(r.y1, 3)],
                    "effective_ppi": ppi_int,
                    "ppi_ok": bool(ppi_int >= min_ppi),
                }
            )

    lowest_int = int(round(lowest)) if lowest is not None else None
    return out, lowest_int, below

def render_clip_preview(page: fitz.Page, rect: fitz.Rect, dpi: int = 144, pad_pt: float = 8.0) -> Optional[str]:
    try:
        r = fitz.Rect(rect)
        r = fitz.Rect(r.x0 - pad_pt, r.y0 - pad_pt, r.x1 + pad_pt, r.y1 + pad_pt)
        r = r & page.rect
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), clip=r, alpha=False)
        b = pix.tobytes("png")
        return "data:image/png;base64," + base64.b64encode(b).decode("ascii")
    except Exception:
        return None

def find_content_bbox_mm_and_rect(page: fitz.Page, dpi: int = 150, white_thresh: int = 248) -> Tuple[Optional[Tuple[float, float]], Optional[fitz.Rect]]:
    """
    Returns:
      - bbox_size_mm (w_mm, h_mm) of visible content (non-white) detected by rendering
      - bbox_rect_pt in page coordinates (for cropping preview)
    """
    if not HAS_IMG:
        return None, None

    try:
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        arr = np.asarray(img)

        mask = np.any(arr < white_thresh, axis=2)
        ys, xs = np.where(mask)
        if xs.size == 0 or ys.size == 0:
            return None, None

        x0, x1 = int(xs.min()), int(xs.max())
        y0, y1 = int(ys.min()), int(ys.max())

        pt_x0 = x0 / zoom
        pt_x1 = (x1 + 1) / zoom
        pt_y0 = y0 / zoom
        pt_y1 = (y1 + 1) / zoom

        rect = fitz.Rect(pt_x0, pt_y0, pt_x1, pt_y1)

        w_mm = round(pt_to_mm(rect.width), 3)
        h_mm = round(pt_to_mm(rect.height), 3)
        return (w_mm, h_mm), rect
    except Exception:
        return None, None

def bleed_content_reaches_edges(page_rect: fitz.Rect, content_rect: Optional[fitz.Rect], max_gap_mm: float = 1.0) -> bool:
    """
    Heuristic check for full-bleed ads: visible content should reach near all page edges.
    If no content bbox is detected, we fail closed.
    """
    if content_rect is None:
        return False
    max_gap_pt = mm_to_pt(max_gap_mm)
    left_gap = abs(content_rect.x0 - page_rect.x0)
    right_gap = abs(page_rect.x1 - content_rect.x1)
    top_gap = abs(content_rect.y0 - page_rect.y0)
    bottom_gap = abs(page_rect.y1 - content_rect.y1)
    return all(gap <= max_gap_pt for gap in (left_gap, right_gap, top_gap, bottom_gap))

def summarize_page_checks(pages: List[Dict[str, Any]]) -> Tuple[bool, bool, bool]:
    if not pages:
        return False, False, False
    size_ok = all(bool(p.get("pdf_size_ok", False)) for p in pages)
    bleed_ok = all(bool(p.get("bleed_ok", True)) and bool(p.get("bleed_content_ok", True)) for p in pages)
    ppi_ok = all(bool(p.get("ppi_ok", False)) for p in pages)
    return size_ok, bleed_ok, ppi_ok


def closest_expected_size_mm(
    actual_mm: Tuple[float, float], expected_list_mm: List[Tuple[float, float]]
) -> Tuple[float, float]:
    if not expected_list_mm:
        return actual_mm
    return min(
        expected_list_mm,
        key=lambda e: abs(actual_mm[0] - e[0]) + abs(actual_mm[1] - e[1]),
    )


def find_magazine_and_format(magazine_id: str, format_id: str) -> Tuple[Optional[MagazineSpec], Optional[FormatSpec]]:
    mag = next((m for m in MAGAZINES if m.id == magazine_id), None)
    if not mag:
        return None, None
    fmt = next((f for f in mag.formats if f.id == format_id), None)
    return mag, fmt


def detect_best_magazine_format(pdf_bytes: bytes, filename: Optional[str] = None) -> Dict[str, Any]:
    def normalize_text(s: str) -> str:
        if not s:
            return ""
        s = unicodedata.normalize("NFKD", s)
        s = s.encode("ascii", "ignore").decode("ascii")
        s = s.lower()
        s = re.sub(r"[^a-z0-9]+", " ", s)
        return re.sub(r"\s+", " ", s).strip()

    def distance_mm(a: Tuple[float, float], b: Tuple[float, float]) -> float:
        d1 = abs(a[0] - b[0]) + abs(a[1] - b[1])
        d2 = abs(a[0] - b[1]) + abs(a[1] - b[0])
        return min(d1, d2)

    def score_magazine_from_filename(name_norm: str, mag: MagazineSpec) -> int:
        if not name_norm:
            return 0
        hay = f" {name_norm} "
        terms = [
            mag.id,
            mag.id.replace("_", " ").replace("-", " "),
            mag.name,
            (mag.display_name or "").split("(")[0].strip(),
            mag.name.replace("Magazine", "").strip(),
        ]
        best = 0
        for t in terms:
            tn = normalize_text(t)
            if len(tn) < 3:
                continue
            if f" {tn} " in hay:
                best = max(best, len(tn))
        return best

    doc = None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        if doc.page_count <= 0:
            raise ValueError("PDF bevat geen pagina's.")

        first_page = doc.load_page(0)
        actual_mm = get_crop_size_mm(first_page)

        filename_norm = normalize_text(filename or "")
        mag_scores = [(score_magazine_from_filename(filename_norm, mag), mag) for mag in MAGAZINES]
        best_mag = max(mag_scores, key=lambda x: x[0])[1] if mag_scores else None
        best_mag_score = max((s for s, _ in mag_scores), default=0)

        candidate_mags = [best_mag] if best_mag and best_mag_score > 0 else MAGAZINES
        match_source = "filename+size" if candidate_mags and len(candidate_mags) == 1 and best_mag_score > 0 else "size_only"

        best: Optional[Tuple[float, MagazineSpec, FormatSpec, Tuple[float, float]]] = None
        for mag in candidate_mags:
            for fmt in mag.formats:
                expected_list = expected_page_sizes_for_format(mag, fmt)
                for ex in expected_list:
                    d = distance_mm(actual_mm, ex)
                    if best is None or d < best[0]:
                        best = (d, mag, fmt, ex)

        if best is None:
            raise ValueError("Geen magazine/formaat beschikbaar voor detectie.")

        dist, mag, fmt, matched_mm = best
        return {
            "page_count": int(doc.page_count),
            "actual_page_mm": [actual_mm[0], actual_mm[1]],
            "match": {
                "publisher": mag.publisher,
                "magazine_id": mag.id,
                "magazine": mag.display_name or mag.name,
                "format_id": fmt.id,
                "format": fmt.label,
                "matched_size_mm": [matched_mm[0], matched_mm[1]],
                "distance_mm": round(float(dist), 3),
                "source": match_source,
            },
        }
    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass


def classify_page_content(page: fitz.Page, image_count: int) -> str:
    has_text = False
    has_drawings = False
    try:
        has_text = bool(page.get_text("text").strip())
    except Exception:
        has_text = False
    try:
        has_drawings = len(page.get_drawings()) > 0
    except Exception:
        has_drawings = False

    if image_count > 0 and (has_text or has_drawings):
        return "mixed"
    if image_count > 0:
        return "raster_only"
    if has_text or has_drawings:
        return "vector_or_text"
    return "empty"


def check_bleed_strip_coverage(
    page: fitz.Page,
    bleed_mm: float,
    dpi: int = 150,
    white_thresh: int = 252,
    min_ink_ratio: float = 0.0008,
) -> Dict[str, Any]:
    if not HAS_IMG:
        return {"ok": False, "status": "not_checked", "reason": "Image libs unavailable", "edges": {}}

    try:
        zoom = dpi / 72.0
        pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        arr = np.asarray(img)
        bleed_px = max(1, int(round(mm_to_pt(bleed_mm) * zoom)))

        strips = {
            "top": arr[:bleed_px, :, :],
            "bottom": arr[-bleed_px:, :, :],
            "left": arr[:, :bleed_px, :],
            "right": arr[:, -bleed_px:, :],
        }

        edges: Dict[str, Dict[str, Any]] = {}
        all_ok = True
        for edge_name, strip in strips.items():
            if strip.size == 0:
                ink_ratio = 0.0
            else:
                ink_mask = np.any(strip < white_thresh, axis=2)
                ink_ratio = float(np.mean(ink_mask))
            edge_ok = ink_ratio >= min_ink_ratio
            edges[edge_name] = {"ok": edge_ok, "ink_ratio": round(ink_ratio, 5)}
            if not edge_ok:
                all_ok = False

        return {"ok": all_ok, "status": "checked", "reason": None, "edges": edges}
    except Exception as exc:
        return {"ok": False, "status": "error", "reason": str(exc), "edges": {}}


def collect_print_checks(doc: fitz.Document) -> Dict[str, Any]:
    non_embedded_fonts: List[str] = []
    non_cmyk_images: List[Dict[str, Any]] = []
    seen_images = set()

    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)

        try:
            for font in page.get_fonts(full=True):
                xref = int(font[0]) if len(font) > 0 and isinstance(font[0], int) else 0
                font_name = str(font[3]) if len(font) > 3 else str(font)
                if xref == 0 and font_name not in non_embedded_fonts:
                    non_embedded_fonts.append(font_name)
        except Exception:
            pass

        try:
            for img in page.get_images(full=True):
                xref = int(img[0])
                if xref in seen_images:
                    continue
                seen_images.add(xref)
                info = doc.extract_image(xref)
                cs = info.get("cs-name")
                if not cs:
                    colorspace = info.get("colorspace")
                    if colorspace == 1:
                        cs = "DeviceGray"
                    elif colorspace == 3:
                        cs = "DeviceRGB"
                    elif colorspace == 4:
                        cs = "DeviceCMYK"
                    else:
                        cs = str(colorspace or "Unknown")
                cs_upper = str(cs).upper()
                if "CMYK" not in cs_upper:
                    non_cmyk_images.append({"xref": xref, "colorspace": cs, "page": page_index + 1})
        except Exception:
            pass

    return {
        "fonts_embedded_ok": len(non_embedded_fonts) == 0,
        "non_embedded_fonts": non_embedded_fonts,
        "color_space_ok": len(non_cmyk_images) == 0,
        "non_cmyk_images": non_cmyk_images,
    }


def recommendations_for_page(page_out: Dict[str, Any], min_ppi: int, bleed_mm: float) -> List[str]:
    recs: List[str] = []
    rules = page_out.get("rules", {})

    if rules.get("size", {}).get("status") == "fail":
        recs.append("Exporteer op het exacte advertentieformaat van het gekozen formaat.")
    if rules.get("bleed_size", {}).get("status") == "fail":
        recs.append(f"Gebruik de full-bleed template met {bleed_mm}mm afloop aan alle zijden.")
    if rules.get("bleed_content", {}).get("status") == "fail":
        recs.append("Laat achtergrond en rand-elementen volledig doorlopen in de afloop.")
    if rules.get("ppi", {}).get("status") == "fail":
        recs.append(f"Vervang lage-resolutie afbeeldingen zodat effectieve PPI minimaal {min_ppi} is.")
    if page_out.get("content_classification") == "empty":
        recs.append("Pagina lijkt leeg na renderen; controleer exportinstellingen en laag-zichtbaarheid.")

    return recs


# ----------------------------
# Routes
# ----------------------------

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/magazines")
def get_magazines() -> List[Dict[str, Any]]:
    out = []
    for m in sorted(MAGAZINES, key=lambda x: (str(x.display_name or x.name).lower(), x.id)):
        out.append(
            {
                "id": m.id,
                "name": m.name,
                "display_name": m.display_name,
                "publisher": m.publisher,
                "base_trim_mm": [m.base_trim_mm[0], m.base_trim_mm[1]],
                "bleed_mm": m.bleed_mm,
                "min_effective_ppi": m.min_effective_ppi,
                "preview_padding_pct": m.preview_padding_pct,
                "support": m.support,
                "formats": [
                    {
                        "id": f.id,
                        "label": f.label,
                        "kind": f.kind,
                        "bleed_required": f.bleed_required,
                        "allow_landscape": f.allow_landscape,
                        "size_mm": ([f.size_mm[0], f.size_mm[1]] if f.size_mm else None),
                    }
                    for f in m.formats
                ],
            }
        )
    return out


def analyze_pdf_bytes(pdf_bytes: bytes, magazine_id: str, format_id: str) -> Dict[str, Any]:
    mag, fmt = find_magazine_and_format(magazine_id, format_id)
    if not mag:
        raise ValueError(f"Onbekende magazine_id: {magazine_id}")
    if not fmt:
        raise ValueError(f"Onbekende format_id: {format_id}")

    min_ppi = mag.min_effective_ppi
    allowed_pages = expected_page_sizes_for_format(mag, fmt)
    expected_str = " OR ".join([f"{a[0]}×{a[1]}mm" for a in allowed_pages])

    with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
        tmp.write(pdf_bytes)
        tmp_path = tmp.name

    issues_all: List[str] = []
    pages_out: List[Dict[str, Any]] = []
    worst_images: List[Dict[str, Any]] = []
    worst_images_sorted: List[Dict[str, Any]] = []
    recommendations: List[str] = []

    doc = None
    try:
        doc = fitz.open(tmp_path)

        for i in range(doc.page_count):
            page = doc.load_page(i)
            page_no = i + 1
            actual_mm = get_crop_size_mm(page)
            size_ok = any(approx_equal_mm(actual_mm, a, tol_mm=0.6) for a in allowed_pages)

            page_issues: List[str] = []
            page_rules: Dict[str, Dict[str, Any]] = {}

            page_rules["size"] = {
                "status": "pass" if size_ok else "fail",
                "measured_mm": [actual_mm[0], actual_mm[1]],
                "expected_mm": [[a[0], a[1]] for a in allowed_pages],
                "tolerance_mm": 0.6,
                "message": None if size_ok else f"Formaat klopt niet: ontvangen {actual_mm[0]}×{actual_mm[1]}mm, verwacht {expected_str}.",
            }
            if not size_ok:
                page_issues.append(page_rules["size"]["message"])

            bleed_ok = True
            bleed_expected = None
            if fmt.bleed_required:
                bleed_expected = allowed_pages[0]
                bleed_ok = approx_equal_mm(actual_mm, bleed_expected, tol_mm=0.6)
                page_rules["bleed_size"] = {
                    "status": "pass" if bleed_ok else "fail",
                    "measured_mm": [actual_mm[0], actual_mm[1]],
                    "expected_mm": [bleed_expected[0], bleed_expected[1]],
                    "tolerance_mm": 0.6,
                    "message": None if bleed_ok else f"Afloopformaat klopt niet: verwacht {bleed_expected[0]}×{bleed_expected[1]}mm bij {mag.bleed_mm}mm afloop.",
                }
                if not bleed_ok:
                    page_issues.append(page_rules["bleed_size"]["message"])
            else:
                page_rules["bleed_size"] = {
                    "status": "not_applicable",
                    "measured_mm": [actual_mm[0], actual_mm[1]],
                    "expected_mm": None,
                    "tolerance_mm": None,
                    "message": "Afloop niet vereist voor dit formaat.",
                }

            imgs, lowest_ppi, below = extract_images_with_ppi(page, min_ppi)
            content_classification = classify_page_content(page, len(imgs))

            if len(imgs) == 0:
                ppi_ok = True
                page_rules["ppi"] = {
                    "status": "not_applicable",
                    "measured_lowest_ppi": None,
                    "min_required_ppi": min_ppi,
                    "images_below": 0,
                    "message": "Geen rasterafbeeldingen gevonden; PPI-controle niet van toepassing.",
                }
            else:
                ppi_ok = (below == 0)
                page_rules["ppi"] = {
                    "status": "pass" if ppi_ok else "fail",
                    "measured_lowest_ppi": lowest_ppi,
                    "min_required_ppi": min_ppi,
                    "images_below": int(below),
                    "message": None if ppi_ok else f"{below} afbeeldingsplaatsing(en) onder {min_ppi} effectieve PPI.",
                }
                if not ppi_ok:
                    page_issues.append(page_rules["ppi"]["message"])

            for im in imgs:
                if im.get("ppi_ok") is False:
                    rect = fitz.Rect(im["rect_pt"])
                    prev = render_clip_preview(page, rect, dpi=144, pad_pt=10.0)
                    worst_images.append(
                        {
                            "page": page_no,
                            "xref": im.get("xref"),
                            "effective_ppi": im.get("effective_ppi"),
                            "pixels": im.get("pixels"),
                            "preview": prev,
                        }
                    )
            worst_images_sorted = sorted(worst_images, key=lambda x: (x.get("effective_ppi") or 10**9))[:9]

            ad_size_mm_bbox, ad_rect_pt = find_content_bbox_mm_and_rect(page, dpi=150, white_thresh=248)
            ad_preview = render_clip_preview(page, ad_rect_pt, dpi=144, pad_pt=8.0) if ad_rect_pt else None

            if fmt.bleed_required:
                bleed_strip = check_bleed_strip_coverage(page, bleed_mm=mag.bleed_mm, dpi=150, white_thresh=252, min_ink_ratio=0.0008)
                if bleed_strip.get("status") in ("not_checked", "error"):
                    bleed_content_ok = True
                    page_rules["bleed_content"] = {
                        "status": "not_applicable",
                        "edges": bleed_strip.get("edges", {}),
                        "message": "Afloop-inhoud controle niet beschikbaar voor dit bestand.",
                    }
                else:
                    bleed_content_ok = bool(bleed_strip.get("ok"))
                    failed_edges = [name for name, meta in bleed_strip.get("edges", {}).items() if not meta.get("ok")]
                    page_rules["bleed_content"] = {
                        "status": "pass" if bleed_content_ok else "fail",
                        "edges": bleed_strip.get("edges"),
                        "message": None if bleed_content_ok else f"Afloop-inhoud ontbreekt nabij rand(en): {', '.join(failed_edges) or 'onbekend'}.",
                    }
                    if not bleed_content_ok:
                        page_issues.append(page_rules["bleed_content"]["message"])
            else:
                bleed_content_ok = True
                page_rules["bleed_content"] = {
                    "status": "not_applicable",
                    "edges": {},
                    "message": "Afloop-inhoud controle niet vereist voor dit formaat.",
                }

            page_out = {
                "page": page_no,
                "pdf_size_ok": bool(size_ok),
                "bleed_ok": bool(bleed_ok),
                "bleed_content_ok": bool(bleed_content_ok),
                "ppi_ok": bool(ppi_ok),
                "actual_page_mm": [actual_mm[0], actual_mm[1]],
                "expected_allowed_mm": [[a[0], a[1]] for a in allowed_pages],
                "magazine_trim_mm": [mag.base_trim_mm[0], mag.base_trim_mm[1]],
                # Placement preview is drawn on the magazine page canvas.
                "template_mm": [mag.base_trim_mm[0], mag.base_trim_mm[1]],
                "ad_size_mm_bbox": ([ad_size_mm_bbox[0], ad_size_mm_bbox[1]] if ad_size_mm_bbox else None),
                "ad_preview": ad_preview,
                "lowest_effective_ppi": lowest_ppi,
                "images_below_min_ppi": int(below),
                "images_found": int(len(imgs)),
                "content_classification": content_classification,
                "rules": page_rules,
                "issues": page_issues,
            }
            pages_out.append(page_out)

            for iss in page_issues:
                issues_all.append(f"Pagina {page_no}: {iss}")

            recommendations.extend(recommendations_for_page(page_out, min_ppi=min_ppi, bleed_mm=mag.bleed_mm))

        print_checks = collect_print_checks(doc)
        if not print_checks["fonts_embedded_ok"]:
            issues_all.append(f"Document: {len(print_checks['non_embedded_fonts'])} niet-ingesloten font(s) gevonden.")
            recommendations.append("Sluit alle fonts in bij export van de PDF.")
        if not print_checks["color_space_ok"]:
            issues_all.append(f"Document: {len(print_checks['non_cmyk_images'])} niet-CMYK afbeelding(en) gevonden.")
            recommendations.append("Converteer rasterafbeeldingen naar CMYK voor drukwerk.")

        deduped_recommendations = list(dict.fromkeys(recommendations))
        ok = (len(issues_all) == 0)

        return {
            "schema_version": "2.0",
            "magazine": f"{mag.name}",
            "magazine_id": mag.id,
            "format": fmt.label,
            "format_id": fmt.id,
            "bleed_required": bool(fmt.bleed_required),
            "min_effective_ppi": int(min_ppi),
            "preview_padding_pct": float(mag.preview_padding_pct),
            "summary": {"ok": bool(ok), "page_count": int(doc.page_count)},
            "issues": issues_all,
            "pages": pages_out,
            "worst_images": worst_images_sorted,
            "print_checks": print_checks,
            "recommendations": deduped_recommendations,
        }
    finally:
        try:
            if doc:
                doc.close()
        except Exception:
            pass
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


ANALYZE_JOBS: Dict[str, Dict[str, Any]] = {}
ANALYZE_JOBS_LOCK = threading.Lock()


async def _run_analyze_job(job_id: str, pdf_bytes: bytes, magazine_id: str, format_id: str) -> None:
    with ANALYZE_JOBS_LOCK:
        ANALYZE_JOBS[job_id]["status"] = "running"
        ANALYZE_JOBS[job_id]["progress"] = 15

    try:
        result = await asyncio.to_thread(analyze_pdf_bytes, pdf_bytes, magazine_id, format_id)
        with ANALYZE_JOBS_LOCK:
            ANALYZE_JOBS[job_id]["status"] = "completed"
            ANALYZE_JOBS[job_id]["progress"] = 100
            ANALYZE_JOBS[job_id]["result"] = result
    except Exception as exc:
        with ANALYZE_JOBS_LOCK:
            ANALYZE_JOBS[job_id]["status"] = "failed"
            ANALYZE_JOBS[job_id]["progress"] = 100
            ANALYZE_JOBS[job_id]["error"] = str(exc)


@app.post("/analyze")
async def analyze_pdf(
    pdf: UploadFile = File(...),
    magazine_id: str = Form(...),
    format_id: str = Form(...),
):
    try:
        report = analyze_pdf_bytes(await pdf.read(), magazine_id=magazine_id, format_id=format_id)
        return JSONResponse(report)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"Analyse mislukt: {exc}"}, status_code=500)


@app.post("/check")
async def check_pdf(
    pdf: UploadFile = File(...),
    magazine_id: str = Form(...),
    format_id: str = Form(...),
):
    return await analyze_pdf(pdf=pdf, magazine_id=magazine_id, format_id=format_id)


@app.post("/detect")
async def detect_pdf(
    pdf: UploadFile = File(...),
):
    try:
        result = detect_best_magazine_format(await pdf.read(), filename=pdf.filename)
        return JSONResponse(result)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except Exception as exc:
        return JSONResponse({"error": f"Detectie mislukt: {exc}"}, status_code=500)


@app.post("/analyze.async")
async def analyze_pdf_async(
    pdf: UploadFile = File(...),
    magazine_id: str = Form(...),
    format_id: str = Form(...),
):
    job_id = str(uuid.uuid4())
    pdf_bytes = await pdf.read()
    with ANALYZE_JOBS_LOCK:
        ANALYZE_JOBS[job_id] = {"job_id": job_id, "status": "queued", "progress": 0}
    asyncio.create_task(_run_analyze_job(job_id, pdf_bytes, magazine_id, format_id))
    return JSONResponse({"job_id": job_id, "status": "queued", "progress": 0})


@app.get("/jobs/{job_id}")
def get_analyze_job(job_id: str):
    with ANALYZE_JOBS_LOCK:
        job = ANALYZE_JOBS.get(job_id)
    if not job:
        return JSONResponse({"error": f"Onbekende job_id: {job_id}"}, status_code=404)
    return JSONResponse(job)


# ===============================
# PDF REPORT (unchanged)
# ===============================

@app.post("/report.pdf")
async def report_pdf(
    pdf: UploadFile = File(...),
    magazine_id: str = Form(...),
    format_id: str = Form(...),
):
    """
    Pretty PDF report (inline -> opens in new tab):
    - Header + summary pills
    - Issues box
    - Placement preview (template page + centered ad preview)  [smaller]
    - Worst offenders table (with previews)
    """
    import io, base64
    from typing import Optional, List
    from starlette.responses import Response

    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib import colors
    from reportlab.lib.units import mm as _mm

    try:
        from PIL import Image as PILImage, ImageOps
    except Exception:
        PILImage = None

    rep = await analyze_pdf(pdf=pdf, magazine_id=magazine_id, format_id=format_id)

    # If analyze_pdf returned JSONResponse, decode body
    if hasattr(rep, "body"):
        import json
        rep = json.loads(rep.body.decode("utf-8"))

    if isinstance(rep, dict) and rep.get("error"):
        return Response(rep["error"], status_code=400, media_type="text/plain")

    def esc(s: str) -> str:
        return (s or "").replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

    def pill(label: str, kind: str):
        if kind == "ok":
            bg, fg = colors.Color(0.93, 0.98, 0.95), colors.Color(0.13, 0.50, 0.24)
        else:
            bg, fg = colors.Color(0.99, 0.93, 0.96), colors.Color(0.67, 0.08, 0.33)

        t = Table([[f"●  {label}"]], colWidths=[None])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0), bg),
            ("TEXTCOLOR",(0,0),(0,0), fg),
            ("FONTNAME",(0,0),(0,0), "Helvetica-Bold"),
            ("FONTSIZE",(0,0),(0,0), 10),
            ("LEFTPADDING",(0,0),(0,0), 10),
            ("RIGHTPADDING",(0,0),(0,0), 10),
            ("TOPPADDING",(0,0),(0,0), 6),
            ("BOTTOMPADDING",(0,0),(0,0), 6),
            ("BOX",(0,0),(0,0), 0.5, colors.Color(0,0,0,0.12)),
            ("VALIGN",(0,0),(0,0), "MIDDLE"),
        ]))
        return t

    def data_url_to_bytes(data_url: str) -> Optional[bytes]:
        if not isinstance(data_url, str):
            return None
        if "base64," not in data_url:
            return None
        try:
            b64 = data_url.split("base64,", 1)[1]
            return base64.b64decode(b64)
        except Exception:
            return None

    def rounded_png_bytes(raw: bytes, radius_px: int = 14) -> bytes:
        if PILImage is None:
            return raw
        try:
            from PIL import ImageDraw
            im = PILImage.open(io.BytesIO(raw)).convert("RGBA")
            w, h = im.size
            r = max(2, min(radius_px, int(min(w, h) * 0.2)))
            mask = PILImage.new("L", (w, h), 0)
            draw = ImageDraw.Draw(mask)
            draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=r, fill=255)
            out = PILImage.new("RGBA", (w, h), (255, 255, 255, 0))
            out.paste(im, (0, 0), mask)
            b = io.BytesIO()
            out.save(b, format="PNG")
            return b.getvalue()
        except Exception:
            return raw

    def make_rl_image_from_data_url(data_url: str, max_w_pt: float, max_h_pt: float) -> Optional[RLImage]:
        b = data_url_to_bytes(data_url)
        if not b:
            return None
        bio = io.BytesIO(rounded_png_bytes(b, radius_px=12))
        img = RLImage(bio)
        iw, ih = img.imageWidth, img.imageHeight
        if iw <= 0 or ih <= 0:
            return img
        s = min(max_w_pt / iw, max_h_pt / ih, 1.0)
        img.drawWidth = iw * s
        img.drawHeight = ih * s
        return img

    def build_placement_preview_png(
        page: dict,
        format_id: str,
        preview_padding_pct: float,
        out_w_px: int = 900,
    ) -> Optional[bytes]:
        if PILImage is None:
            return None

        template = page.get("magazine_trim_mm") or page.get("template_mm")
        ad_size = page.get("ad_size_mm_bbox")
        ad_preview = page.get("ad_preview")
        if not (template and ad_size and ad_preview):
            return None

        try:
            tw, th = float(template[0]), float(template[1])
            aw, ah = float(ad_size[0]), float(ad_size[1])
            if tw <= 0 or th <= 0 or aw <= 0 or ah <= 0:
                return None
        except Exception:
            return None

        ad_bytes = data_url_to_bytes(ad_preview)
        if not ad_bytes:
            return None

        try:
            ad_img = PILImage.open(io.BytesIO(ad_bytes)).convert("RGB")
        except Exception:
            return None

        out_h_px = max(1, int(round(out_w_px * (th / tw))))
        canvas = PILImage.new("RGB", (out_w_px, out_h_px), (255,255,255))

        border_col = (220,220,220)
        for x in range(out_w_px):
            canvas.putpixel((x,0), border_col)
            canvas.putpixel((x,out_h_px-1), border_col)
        for y in range(out_h_px):
            canvas.putpixel((0,y), border_col)
            canvas.putpixel((out_w_px-1,y), border_col)

        ad_w_px = int(round((aw / tw) * out_w_px))
        ad_h_px = int(round((ah / th) * out_h_px))

        ad_w_px = min(ad_w_px, int(out_w_px * 0.98))
        ad_h_px = min(ad_h_px, int(out_h_px * 0.98))

        ad_img = ImageOps.contain(ad_img, (ad_w_px, ad_h_px))

        fmt = (format_id or "").lower()
        is_half = "half" in fmt
        is_quarter = "quarter" in fmt
        pad = max(2, int(round(min(out_w_px, out_h_px) * (preview_padding_pct / 100.0))))
        if is_quarter:
            x0 = max(pad, out_w_px - ad_img.size[0] - pad)
            y0 = max(pad, out_h_px - ad_img.size[1] - pad)
        elif is_half:
            x0 = max(pad, (out_w_px - ad_img.size[0]) // 2)
            y0 = max(pad, out_h_px // 2 + pad)
        else:
            x0 = (out_w_px - ad_img.size[0]) // 2
            y0 = (out_h_px - ad_img.size[1]) // 2
        x0 = max(pad, min(x0, out_w_px - ad_img.size[0] - pad))
        y0 = max(pad, min(y0, out_h_px - ad_img.size[1] - pad))
        canvas.paste(ad_img, (x0, y0))

        out = io.BytesIO()
        canvas.save(out, format="PNG")
        return out.getvalue()

    mag_name = rep.get("magazine", "")
    fmt_label = rep.get("format", "")
    bleed_required = bool(rep.get("bleed_required", False))
    min_ppi = rep.get("min_effective_ppi", 300)
    preview_padding_pct = float(rep.get("preview_padding_pct", 2.6) or 2.6)

    pages = rep.get("pages") or []
    p1 = pages[0] if pages else {}
    ok = bool(rep.get("summary", {}).get("ok", False))

    size_ok, bleed_ok, ppi_ok = summarize_page_checks(pages)

    issues_all = [_issue_nl(i) for i in (rep.get("issues") or [])]
    page_count = len(pages)

    worst = rep.get("worst_images") or []

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18*_mm,
        rightMargin=18*_mm,
        topMargin=16*_mm,
        bottomMargin=16*_mm,
        title="PDF Controle Rapport",
        author="PDF Checker",
    )

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=18, leading=22, spaceAfter=6)
    SUB = ParagraphStyle("SUB", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5, leading=14, textColor=colors.Color(0,0,0,0.65))
    H2 = ParagraphStyle("H2", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=10, spaceAfter=8)
    BODY = ParagraphStyle("BODY", parent=styles["Normal"], fontName="Helvetica", fontSize=10.5, leading=14, alignment=TA_LEFT)

    story: List[object] = []
    story.append(Paragraph("PDF Controle Rapport", H1))
    story.append(Paragraph(
        f"{esc(mag_name)} — {esc(fmt_label)} • Afloop verplicht: {'Ja' if bleed_required else 'Nee'} • Min PPI: {min_ppi}",
        SUB
    ))
    story.append(Spacer(1, 10))

    pills = [
        pill("OK" if ok else "FOUT", "ok" if ok else "bad"),
        pill("Formaat: OK" if size_ok else "Formaat: fout", "ok" if size_ok else "bad"),
        pill("Afloop: OK" if bleed_ok else "Afloop: fout", "ok" if bleed_ok else "bad"),
        pill("PPI: OK" if ppi_ok else "PPI: fout", "ok" if ppi_ok else "bad"),
    ]
    pills_tbl = Table([pills], colWidths=[None]*4, hAlign="LEFT")
    pills_tbl.setStyle(TableStyle([("LEFTPADDING",(0,0),(-1,-1),0), ("RIGHTPADDING",(0,0),(-1,-1),6)]))
    story.append(pills_tbl)
    story.append(Spacer(1, 10))

    placement_png = build_placement_preview_png(
        p1,
        rep.get("format_id", ""),
        preview_padding_pct=preview_padding_pct,
        out_w_px=820,
    )
    if placement_png:
        story.append(Paragraph("Plaatsing op pagina", H2))
        img = RLImage(io.BytesIO(rounded_png_bytes(placement_png, radius_px=16)))
        # Hard cap keeps preview on page 1 by shrinking when needed.
        max_w = doc.width * 0.64
        max_h = 75 * _mm
        s = min(max_w / img.imageWidth, max_h / img.imageHeight, 1.0)
        img.drawWidth = img.imageWidth * s
        img.drawHeight = img.imageHeight * s

        wrapper = Table([[img]], colWidths=[doc.width])
        wrapper.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0), colors.Color(0,0,0,0.03)),
            ("BOX",(0,0),(0,0), 0.6, colors.Color(0,0,0,0.12)),
            ("LEFTPADDING",(0,0),(0,0), 10),
            ("RIGHTPADDING",(0,0),(0,0), 10),
            ("TOPPADDING",(0,0),(0,0), 10),
            ("BOTTOMPADDING",(0,0),(0,0), 10),
        ]))
        story.append(wrapper)
        story.append(Spacer(1, 10))

    story.append(Paragraph("Samenvatting", H2))
    summary_rows = [
        ["Magazine", esc(mag_name)],
        ["Formaat", esc(fmt_label)],
        ["Afloop verplicht", "Ja" if bleed_required else "Nee"],
        ["Min PPI", str(min_ppi)],
        ["Pagina's geanalyseerd", str(page_count)],
        ["Aantal problemen", str(len(issues_all))],
    ]
    summary_tbl = Table(summary_rows, colWidths=[45*_mm, doc.width - 45*_mm], hAlign="LEFT")
    summary_tbl.setStyle(TableStyle([
        ("BACKGROUND",(0,0),(-1,-1), colors.white),
        ("BOX",(0,0),(-1,-1), 0.6, colors.Color(0,0,0,0.12)),
        ("GRID",(0,0),(-1,-1), 0.4, colors.Color(0,0,0,0.08)),
        ("FONTNAME",(0,0),(0,-1),"Helvetica-Bold"),
        ("LEFTPADDING",(0,0),(-1,-1), 8),
        ("RIGHTPADDING",(0,0),(-1,-1), 8),
        ("TOPPADDING",(0,0),(-1,-1), 7),
        ("BOTTOMPADDING",(0,0),(-1,-1), 7),
    ]))
    story.append(summary_tbl)
    story.append(Spacer(1, 10))

    if issues_all:
        story.append(Paragraph("Problemen", H2))
        for iss in issues_all:
            issue_box = Table([[Paragraph(esc(iss), BODY)]], colWidths=[doc.width])
            issue_box.setStyle(TableStyle([
                ("BACKGROUND",(0,0),(0,0), colors.Color(1.0, 0.95, 0.97)),
                ("BOX",(0,0),(0,0), 0.6, colors.Color(0.92, 0.45, 0.65)),
                ("LEFTPADDING",(0,0),(0,0), 10),
                ("RIGHTPADDING",(0,0),(0,0), 10),
                ("TOPPADDING",(0,0),(0,0), 8),
                ("BOTTOMPADDING",(0,0),(0,0), 8),
            ]))
            story.append(issue_box)
            story.append(Spacer(1, 6))
    else:
        no_issue_box = Table([[Paragraph("Geen problemen.", BODY)]], colWidths=[doc.width])
        no_issue_box.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(0,0), colors.white),
            ("BOX",(0,0),(0,0), 0.6, colors.Color(0,0,0,0.12)),
            ("LEFTPADDING",(0,0),(0,0), 10),
            ("RIGHTPADDING",(0,0),(0,0), 10),
            ("TOPPADDING",(0,0),(0,0), 8),
            ("BOTTOMPADDING",(0,0),(0,0), 8),
        ]))
        story.append(no_issue_box)
    story.append(Spacer(1, 12))

    story.append(Paragraph("Laagste effectieve PPI", H2))

    header = ["Rang", "Pagina", "Effectieve PPI", "Pixels", "Voorbeeld"]
    rows = [header]

    max_preview_w = doc.width * 0.45
    max_preview_h = 70

    for idx, w in enumerate(worst[:9], start=1):
        prev = None
        if w.get("preview"):
            prev = make_rl_image_from_data_url(w["preview"], max_preview_w, max_preview_h)
        rows.append([
            f"#{idx}",
            str(w.get("page","—")),
            str(w.get("effective_ppi","—")),
            f'{(w.get("pixels") or ["—","—"])[0]}×{(w.get("pixels") or ["—","—"])[1]}',
            prev if prev else Paragraph("—", BODY),
        ])

    tbl = Table(
        rows,
        colWidths=[22*_mm, 20*_mm, 40*_mm, 30*_mm, doc.width - (22*_mm+20*_mm+40*_mm+30*_mm)],
        repeatRows=1
    )
    tbl.setStyle(TableStyle([
        ("FONTNAME",(0,0),(-1,0),"Helvetica-Bold"),
        ("FONTSIZE",(0,0),(-1,0),10),
        ("BACKGROUND",(0,0),(-1,0),colors.Color(0,0,0,0.02)),
        ("TEXTCOLOR",(0,0),(-1,0),colors.black),
        ("GRID",(0,0),(-1,-1),0.6,colors.Color(0,0,0,0.12)),
        ("VALIGN",(0,0),(-1,-1),"TOP"),
        ("LEFTPADDING",(0,0),(-1,-1),8),
        ("RIGHTPADDING",(0,0),(-1,-1),8),
        ("TOPPADDING",(0,0),(-1,-1),8),
        ("BOTTOMPADDING",(0,0),(-1,-1),8),
    ]))
    story.append(tbl)

    doc.build(story)

    pdf_bytes = buf.getvalue()
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="pdf-check-report.pdf"'},
    )


# ===============================
# HTML REPORT (match screenshot)
# ===============================

import html

def _esc(s):
    return html.escape(str(s)) if s is not None else ""

def _issue_nl(s: str) -> str:
    t = str(s or "")
    if t.startswith("Page "):
        t = "Pagina " + t[5:]
    if t.startswith("Document:"):
        t = "Document:" + t[len("Document:"):]
    return t

def _pill(label: str, tone: str):
    colors_map = {
        "ok":   ("rgba(34,197,94,0.12)",  "rgba(21,128,61,1)",  "rgba(34,197,94,1)"),
        "bad":  ("rgba(236,72,153,0.12)","rgba(190,24,93,1)",  "rgba(236,72,153,1)"),
        "neutral": ("rgba(0,0,0,0.06)",  "rgba(0,0,0,0.75)",   "rgba(0,0,0,0.55)"),
    }
    bg, fg, dot = colors_map.get(tone, colors_map["neutral"])
    return f"""
    <span class="pill" style="background:{bg};color:{fg};border:1px solid rgba(0,0,0,0.10)">
        <span class="dot" style="background:{dot}"></span>
        {_esc(label)}
    </span>
    """

def render_html_report(report: dict) -> str:
    mag = report.get("magazine", "—")
    fmt = report.get("format", "—")
    bleed_required = "Ja" if report.get("bleed_required") else "Nee"
    min_ppi = report.get("min_effective_ppi", "—")
    format_id = str(report.get("format_id") or "").lower()
    preview_padding_pct = float(report.get("preview_padding_pct", 2.6) or 2.6)

    ok = bool((report.get("summary") or {}).get("ok"))
    pages = report.get("pages") or []
    p0 = pages[0] if pages else {}
    size_ok, bleed_ok, ppi_ok = summarize_page_checks(pages)

    # Keep labels exactly like screenshot
    pills_row = (
        _pill("OK" if ok else "FOUT", "ok" if ok else "bad")
        + _pill("Formaat: OK" if size_ok else "Formaat: fout", "ok" if size_ok else "bad")
        + _pill("Afloop: OK" if bleed_ok else "Afloop: fout", "ok" if bleed_ok else "bad")
        + _pill("PPI: OK" if ppi_ok else "PPI: fout", "ok" if ppi_ok else "bad")
    )

    issues_all = [_issue_nl(i) for i in (report.get("issues") or [])]
    issues_line = " • ".join(issues_all[:16]) if issues_all else "Geen problemen."
    pages_count = len(pages)
    ad_preview = p0.get("ad_preview")
    template_mm = p0.get("magazine_trim_mm") or p0.get("template_mm") or p0.get("actual_page_mm")
    ad_size_mm = p0.get("ad_size_mm_bbox")

    worst = report.get("worst_images") or []

    placement_markup = "<div class='no-preview'>Geen preview beschikbaar</div>"
    if (
        ad_preview
        and isinstance(template_mm, list)
        and len(template_mm) == 2
        and isinstance(ad_size_mm, list)
        and len(ad_size_mm) == 2
    ):
        try:
            tw, th = float(template_mm[0]), float(template_mm[1])
            aw, ah = float(ad_size_mm[0]), float(ad_size_mm[1])
            if tw > 0 and th > 0 and aw > 0 and ah > 0:
                width_pct = max(1.0, min(98.0, (aw / tw) * 100.0))
                height_pct = max(1.0, min(98.0, (ah / th) * 100.0))
                pad_pct = preview_padding_pct
                is_half = "half" in format_id
                is_quarter = "quarter" in format_id
                if is_quarter:
                    left = f"calc(100% - {width_pct:.2f}% - {pad_pct:.2f}%)"
                    top = f"calc(100% - {height_pct:.2f}% - {pad_pct:.2f}%)"
                elif is_half:
                    left = f"calc(50% - {width_pct / 2:.2f}%)"
                    top = f"calc(50% + {pad_pct:.2f}%)"
                else:
                    left = f"calc(50% - {width_pct / 2:.2f}%)"
                    top = f"calc(50% - {height_pct / 2:.2f}%)"

                guide_h = "<div class='guide-h'></div>" if (is_half or is_quarter) else ""
                guide_v = "<div class='guide-v'></div>" if is_quarter else ""
                placement_markup = f"""
                <div class="page-sheet" style="aspect-ratio:{tw:.3f}/{th:.3f}">
                    {guide_h}
                    {guide_v}
                    <img class="ad-placement" src="{_esc(ad_preview)}" alt="Plaatsingspreview" style="left:{left};top:{top};width:{width_pct:.2f}%;height:{height_pct:.2f}%"/>
                </div>
                """
        except Exception:
            pass

    rows = ""
    for i, w in enumerate(worst[:10], start=1):
        prev = w.get("preview")
        # effective_ppi is already int now
        rows += f"""
        <tr>
            <td>#{i}</td>
            <td>{_esc(w.get("page","—"))}</td>
            <td>{_esc(w.get("effective_ppi","—"))}</td>
            <td>{_esc((w.get("pixels") or ["—","—"])[0])}×{_esc((w.get("pixels") or ["—","—"])[1])}</td>
            <td>{'<img class="thumb" src="'+prev+'" />' if prev else '—'}</td>
        </tr>
        """

    summary_issues_markup = ""
    if issues_all:
        summary_issues_markup = "".join(
            [
                f"<div class='issue-item'>{_esc(iss)}</div>"
                for iss in issues_all
            ]
        )
    else:
        summary_issues_markup = "<div class='issue-item issue-ok'>Geen problemen.</div>"

    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8"/>
<title>PDF Controle Rapport</title>
<style>
:root {{
    --bg: #f4f4f5;
    --card: #ffffff;
    --border: rgba(0,0,0,0.08);
    --text: rgba(0,0,0,0.90);
    --muted: rgba(0,0,0,0.55);
}}

body {{
    font-family: Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif;
    background: var(--bg);
    margin: 0;
    color: var(--text);
}}

.shell {{
    max-width: 1100px;
    margin: 40px auto;
    padding: 0 20px 60px;
}}

h1 {{
    margin: 0;
    font-size: 28px;
    font-weight: 900;
}}

.sub {{
    color: var(--muted);
    font-weight: 700;
    font-size: 13px;
    margin-top: 6px;
}}

.pills {{
    margin-top: 14px;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
}}

.pill {{
    display:inline-flex;
    align-items:center;
    gap:8px;
    padding:6px 10px;
    border-radius:999px;
    font-weight:800;
    font-size:12px;
    white-space: nowrap;
}}

.dot {{
    width:9px;
    height:9px;
    border-radius:99px;
}}

.issues {{
    margin-top: 14px;
    background: rgba(255,255,255,0.92);
    border: 1px solid rgba(0,0,0,0.10);
    border-radius: 16px;
    padding: 14px 16px;
}}

.issue-list {{
    display: grid;
    gap: 8px;
    margin-top: 10px;
}}

.issue-item {{
    border-radius: 12px;
    padding: 10px 12px;
    background: rgba(236,72,153,0.08);
    border: 1px solid rgba(236,72,153,0.18);
    font-size: 12px;
    color: rgba(0,0,0,0.82);
    font-weight: 800;
}}

.issue-ok {{
    background: rgba(0,0,0,0.03);
    border-color: rgba(0,0,0,0.08);
    color: rgba(0,0,0,0.75);
}}

.card {{
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 16px;
    padding: 16px;
    margin-top: 18px;
    box-shadow: 0 12px 28px rgba(0,0,0,0.08);
}}

.h2 {{
    margin: 0 0 10px;
    font-size: 20px;
    font-weight: 900;
}}

.preview-wrap {{
    margin-top: 12px;
    border-radius: 16px;
    padding: 12px;
    background: rgba(0,0,0,0.03);
    border: 1px solid rgba(0,0,0,0.08);
}}

.preview-stage {{
    background: #fff;
    border: 1px solid rgba(0,0,0,0.10);
    border-radius: 12px;
    padding: 14px;
    display:flex;
    justify-content:center;
}}

.page-sheet {{
    width: min(900px, 100%);
    background: #fff;
    border: 1px solid rgba(0,0,0,0.16);
    border-radius: 10px;
    position: relative;
    overflow: hidden;
}}

.guide-h {{
    position:absolute;
    left:0;
    right:0;
    top:50%;
    border-top: 1px dashed rgba(0,0,0,0.26);
}}

.guide-v {{
    position:absolute;
    top:0;
    bottom:0;
    left:50%;
    border-left: 1px dashed rgba(0,0,0,0.26);
}}

.ad-placement {{
    position:absolute;
    object-fit: contain;
    border: 1px solid rgba(0,0,0,0.18);
    background:#fff;
}}

.no-preview {{
    font-size:12px;
    color: rgba(0,0,0,0.55);
    font-weight:700;
}}

.table-wrap {{
    margin-top: 10px;
    border-radius: 16px;
    overflow: hidden;
    border: 1px solid rgba(0,0,0,0.10);
    background: #fff;
}}

table {{
    width: 100%;
    border-collapse: collapse;
}}

thead th {{
    background: rgba(0,0,0,0.02);
    font-weight: 900;
    font-size: 13px;
}}

th, td {{
    padding: 14px 12px;
    text-align: left;
    font-size: 13px;
    vertical-align: top;
}}

tbody tr {{
    border-top: 1px solid rgba(0,0,0,0.08);
}}

/* CHANGE: column lines left/right + between columns */
th, td {{
    border-right: 1px solid rgba(0,0,0,0.08);
}}
th:first-child, td:first-child {{
    border-left: 1px solid rgba(0,0,0,0.08);
}}
th:last-child, td:last-child {{
    border-right: 1px solid rgba(0,0,0,0.08);
}}

.thumb {{
    width: 320px;
    max-width: 100%;
    border-radius: 12px;
    border: 1px solid rgba(0,0,0,0.10);
    background: #fff;
    display:block;
}}

@media print {{
    body {{ background:#fff; }}
    .shell {{ margin: 0; }}
}}
</style>
</head>

<body>
<div class="shell">
    <h1>PDF Controle Rapport</h1>
    <div class="sub">{_esc(mag)} — {_esc(fmt)} • Afloop verplicht: {_esc(bleed_required)} • Min PPI: {_esc(min_ppi)}</div>

    <div class="pills">{pills_row}</div>

    <div class="issues">{_esc(issues_line)}</div>

    <div class="card">
        <div class="h2">Samenvatting</div>
        <div class="table-wrap">
            <table>
                <tbody>
                    <tr><td style="width:220px;font-weight:800">Magazine</td><td>{_esc(mag)}</td></tr>
                    <tr><td style="font-weight:800">Formaat</td><td>{_esc(fmt)}</td></tr>
                    <tr><td style="font-weight:800">Afloop verplicht</td><td>{_esc(bleed_required)}</td></tr>
                    <tr><td style="font-weight:800">Min PPI</td><td>{_esc(min_ppi)}</td></tr>
                    <tr><td style="font-weight:800">Pagina's geanalyseerd</td><td>{_esc(pages_count)}</td></tr>
                    <tr><td style="font-weight:800">Aantal problemen</td><td>{_esc(len(issues_all))}</td></tr>
                </tbody>
            </table>
        </div>
        <div class="issue-list">{summary_issues_markup}</div>
    </div>

    <div class="card">
        <div class="h2">Plaatsing op pagina</div>
        <div class="preview-wrap">
            <div class="preview-stage">
                {placement_markup}
            </div>
        </div>
    </div>

    <div class="card">
        <div class="h2">Laagste effectieve PPI</div>
        <div class="table-wrap">
            <table>
                <thead>
                    <tr>
                        <th style="width:90px">Rang</th>
                        <th style="width:90px">Pagina</th>
                        <th style="width:160px">Effectieve PPI</th>
                        <th style="width:160px">Pixels</th>
                        <th>Voorbeeld</th>
                    </tr>
                </thead>
                <tbody>
                    {rows if rows else "<tr><td colspan='5'>Geen afwijkingen</td></tr>"}
                </tbody>
            </table>
        </div>
    </div>
</div>
</body>
</html>
"""

@app.post("/report", response_class=HTMLResponse)
async def report_html(
    pdf: UploadFile = File(...),
    magazine_id: str = Form(...),
    format_id: str = Form(...),
):
    result = await analyze_pdf(pdf=pdf, magazine_id=magazine_id, format_id=format_id)

    # analyze_pdf returns JSONResponse
    if hasattr(result, "body"):
        import json
        result = json.loads(result.body.decode("utf-8"))

    if isinstance(result, dict) and result.get("error"):
        return HTMLResponse("<h1>"+_esc(result["error"])+"</h1>", status_code=400)

    return HTMLResponse(render_html_report(result))
