# PDF Ad Checker

PDF Ad Checker validates advertisement PDFs against magazine format rules:

- Page size / format match
- Bleed (size + content coverage when required)
- Effective image PPI
- Basic print checks (fonts + CMYK)
- HTML and PDF report output

It also supports auto-detection on upload:

- Magazine from PDF filename
- Format from first-page PDF size
- Publisher from matched magazine

## Project Files

- `app.py` FastAPI backend
- `magazines.json` magazine + format definitions
- `PDFChecker.tsx` frontend component (Framer/React)
- `tests/` unit tests

## Requirements

Dependencies are listed in `requirements.txt`.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run API

```bash
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## API Endpoints

- `GET /health`
- `GET /magazines`
- `POST /detect` (auto-detect publisher/magazine/format)
- `POST /analyze`
- `POST /check` (alias of `/analyze`, handy for Framer)
- `POST /analyze.async`
- `GET /jobs/{job_id}`
- `POST /report` (HTML report)
- `POST /report.pdf` (PDF report)

### Analyze Example

```bash
curl -X POST http://127.0.0.1:8000/analyze \
  -F "pdf=@/path/to/ad.pdf" \
  -F "magazine_id=support" \
  -F "format_id=quarter"
```

### Detect Example

```bash
curl -X POST http://127.0.0.1:8000/detect \
  -F "pdf=@/path/to/support_quarter.pdf"
```

## Frontend Component

`PDFChecker.tsx` expects `apiBaseUrl` (default `http://127.0.0.1:8000`).

Upload flow:

1. Upload PDF
2. App auto-detects and pre-fills publisher/magazine/format
3. Click **Controleer PDF**

## Tests

Run all tests:

```bash
python -m unittest discover -s tests -v
```

## Notes

- Magazine/formats are loaded from `magazines.json`.
- Format names include size in mm.
- Magazines are returned alphabetically by API.
### Framer Fetch Example

```ts
fetch("https://YOUR-RENDER-URL/check", {
  method: "POST",
  body: formData,
})
```
