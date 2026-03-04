import * as React from "react"
import { addPropertyControls, ControlType } from "framer"

type Magazine = {
    id: string
    name: string
    display_name?: string
    publisher?: string
    support?: {
        magazine_support?: string
        publisher?: string
        [key: string]: any
    }
    base_trim_mm: [number, number]
    bleed_mm: number
    min_effective_ppi: number
    preview_padding_pct?: number
    formats: {
        id: string
        label: string
        kind: string
        bleed_required: boolean
        size_mm?: [number, number] | null
    }[]
}

type WorstImage = {
    page: number
    xref: number
    effective_ppi: number
    pixels: [number, number]
    preview?: string | null
}

type PageReport = {
    page: number
    pdf_size_ok: boolean
    bleed_ok: boolean
    bleed_content_ok: boolean
    ppi_ok: boolean

    actual_page_mm: [number, number]
    expected_allowed_mm: [number, number][]
    magazine_trim_mm: [number, number]

    template_mm?: [number, number] | null
    ad_size_mm_bbox?: [number, number] | null
    ad_preview?: string | null

    lowest_effective_ppi: number | null
    images_below_min_ppi: number
    images_found: number
    content_classification?: string
    rules?: {
        size?: {
            status: "pass" | "fail" | "not_applicable"
            message?: string | null
        }
        bleed_size?: {
            status: "pass" | "fail" | "not_applicable"
            message?: string | null
        }
        bleed_content?: {
            status: "pass" | "fail" | "not_applicable"
            message?: string | null
            edges?: Record<string, { ok: boolean; ink_ratio: number }>
        }
        ppi?: {
            status: "pass" | "fail" | "not_applicable"
            message?: string | null
        }
    }
    issues: string[]
}

type Report = {
    magazine: string
    magazine_id: string
    format: string
    format_id: string
    bleed_required: boolean
    min_effective_ppi: number
    preview_padding_pct?: number
    summary: { ok: boolean }
    issues: string[]
    pages: PageReport[]
    worst_images: WorstImage[]
    recommendations?: string[]
    print_checks?: {
        fonts_embedded_ok: boolean
        non_embedded_fonts: string[]
        color_space_ok: boolean
        non_cmyk_images: { xref: number; colorspace: string; page: number }[]
    }
    [key: string]: any
}

type DetectResponse = {
    page_count: number
    actual_page_mm: [number, number]
    match?: {
        publisher?: string
        magazine_id: string
        format_id: string
        [key: string]: any
    }
}

function round2(n: number) {
    return Math.round(n * 100) / 100
}
function mm(n: number | undefined | null) {
    if (n === undefined || n === null || Number.isNaN(n)) return "—"
    return `${round2(n)}mm`
}
function mmPair(p?: [number, number] | null) {
    if (!p) return "—"
    return `${mm(p[0])} × ${mm(p[1])}`
}
function intOrDash(n: any) {
    if (n === undefined || n === null || Number.isNaN(Number(n))) return "—"
    return String(Math.round(Number(n)))
}

function statusNl(v?: string | null) {
    const s = (v || "").toLowerCase()
    if (s === "pass") return "geslaagd"
    if (s === "fail") return "fout"
    if (s === "not_applicable") return "niet van toepassing"
    if (!s) return "—"
    return s.replaceAll("_", " ")
}

function contentTypeNl(v?: string | null) {
    const s = (v || "").toLowerCase()
    if (s === "mixed") return "gemengd"
    if (s === "vector_or_text") return "vector/tekst"
    if (s === "images_only") return "alleen afbeeldingen"
    if (s === "empty") return "leeg"
    if (!s) return "—"
    return s.replaceAll("_", " ")
}

function pickClosestExpected(
    expected: [number, number][],
    actual: [number, number]
) {
    if (!expected?.length) return null
    let best = expected[0]
    let bestD = Math.abs(actual[0] - best[0]) + Math.abs(actual[1] - best[1])
    for (const e of expected.slice(1)) {
        const d = Math.abs(actual[0] - e[0]) + Math.abs(actual[1] - e[1])
        if (d < bestD) {
            best = e
            bestD = d
        }
    }
    return best
}

function formatFileSize(bytes: number) {
    if (!bytes || bytes <= 0) return "—"
    const mb = bytes / (1024 * 1024)
    if (mb >= 1) return `${mb.toFixed(1)} MB`
    const kb = bytes / 1024
    return `${Math.round(kb)} KB`
}

function normalizedDistance(a: [number, number], b: [number, number]) {
    const d1 = Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1])
    const d2 = Math.abs(a[0] - b[1]) + Math.abs(a[1] - b[0])
    return Math.min(d1, d2)
}

function area(p: [number, number]) {
    return Math.max(0, p[0]) * Math.max(0, p[1])
}

type PublisherKey = "virtumedia" | "alea"

function normalizePublisher(value?: string): PublisherKey {
    const v = (value || "").toLowerCase()
    if (v.includes("alea")) return "alea"
    return "virtumedia"
}

function magazinePublisherKey(m: Magazine): PublisherKey {
    return normalizePublisher(
        m.publisher || m.support?.magazine_support || m.support?.publisher
    )
}

function publisherLabel(key: PublisherKey) {
    return key === "alea" ? "Alea Publishers" : "VirtuMedia"
}

function Pill({ ok, label }: { ok: boolean; label: string }) {
    return (
        <span
            style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 8,
                padding: "6px 10px",
                borderRadius: 999,
                border: "1px solid rgba(0,0,0,0.10)",
                background: ok
                    ? "rgba(34,197,94,0.12)"
                    : "rgba(236,72,153,0.12)",
                color: ok ? "rgba(21,128,61,1)" : "rgba(190,24,93,1)",
                fontSize: 12,
                fontWeight: 700,
                lineHeight: "12px",
                whiteSpace: "nowrap",
            }}
        >
            <span
                style={{
                    width: 9,
                    height: 9,
                    borderRadius: 99,
                    background: ok ? "rgba(34,197,94,1)" : "rgba(236,72,153,1)",
                }}
            />
            {label}
        </span>
    )
}

function Card({
    title,
    right,
    children,
}: {
    title: string
    right?: React.ReactNode
    children: React.ReactNode
}) {
    return (
        <div
            style={{
                borderRadius: 16,
                background: "rgba(255,255,255,0.92)",
                border: "1px solid rgba(0,0,0,0.08)",
                padding: 16,
                boxShadow: "0 12px 28px rgba(0,0,0,0.08)",
            }}
        >
            <div
                style={{
                    display: "flex",
                    justifyContent: "space-between",
                    gap: 12,
                    alignItems: "center",
                    flexWrap: "wrap",
                }}
            >
                <div
                    style={{
                        fontSize: 13,
                        fontWeight: 800,
                        color: "rgba(0,0,0,0.88)",
                    }}
                >
                    {title}
                </div>
                {right}
            </div>
            <div style={{ height: 12 }} />
            {children}
        </div>
    )
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
    return (
        <div
            style={{
                display: "flex",
                justifyContent: "space-between",
                gap: 12,
                alignItems: "baseline",
                minWidth: 0,
            }}
        >
            <div
                style={{
                    fontSize: 12,
                    color: "rgba(0,0,0,0.55)",
                    fontWeight: 700,
                    minWidth: 0,
                    flexShrink: 0,
                }}
            >
                {k}
            </div>
            <div
                style={{
                    fontSize: 12,
                    color: "rgba(0,0,0,0.90)",
                    fontWeight: 800,
                    textAlign: "right",
                    minWidth: 0,
                    flex: "1 1 auto",
                    overflowWrap: "anywhere",
                    wordBreak: "break-word",
                }}
            >
                {v}
            </div>
        </div>
    )
}

function useResizeObserverSize(ref: React.RefObject<HTMLElement>) {
    const [size, setSize] = React.useState({ w: 0, h: 0 })

    React.useEffect(() => {
        const el = ref.current
        if (!el) return

        const ro = new ResizeObserver((entries) => {
            for (const e of entries) {
                const cr = e.contentRect
                setSize({ w: cr.width, h: cr.height })
            }
        })
        ro.observe(el)
        const r = el.getBoundingClientRect()
        setSize({ w: r.width, h: r.height })

        return () => ro.disconnect()
    }, [ref])

    return size
}

function PlacementPreview({
    page,
    formatId,
    previewPaddingPct,
}: {
    page: PageReport
    formatId?: string
    previewPaddingPct?: number
}) {
    const expected = page.expected_allowed_mm ?? []
    const template =
        page.magazine_trim_mm ??
        page.template_mm ??
        pickClosestExpected(expected, page.actual_page_mm)
    const adSize = page.ad_size_mm_bbox ?? null
    const adImg = page.ad_preview ?? null

    if (!template) return null

    const [tw, th] = template
    const aspect = tw > 0 && th > 0 ? tw / th : 1

    const baseW = 800
    const baseH = baseW / aspect

    let adW = 0
    let adH = 0
    if (adSize) {
        adW = (adSize[0] / tw) * baseW
        adH = (adSize[1] / th) * baseH

        const maxW = baseW * 0.98
        const maxH = baseH * 0.98
        const s = Math.min(maxW / Math.max(adW, 1), maxH / Math.max(adH, 1), 1)
        adW *= s
        adH *= s
    }

    const wrapperRef = React.useRef<HTMLDivElement>(null)
    const { w: pw, h: ph } = useResizeObserverSize(wrapperRef)
    const scale = pw > 0 && ph > 0 ? Math.min(pw / baseW, ph / baseH) : 1

    const layoutKind = (formatId || "").toLowerCase()
    const isHalf = layoutKind.includes("half")
    const isQuarter = layoutKind.includes("quarter")
    const padPct = Number.isFinite(previewPaddingPct as number)
        ? Number(previewPaddingPct)
        : 2.6
    const pad = Math.max(8, Math.round((padPct / 100) * baseW))

    const rawLeft = isQuarter
        ? Math.max(baseW - adW - pad, pad)
        : Math.max((baseW - adW) / 2, pad)
    const rawTop = isQuarter
        ? Math.max(baseH - adH - pad, pad)
        : isHalf
          ? Math.max(baseH / 2 + pad, pad)
          : Math.max((baseH - adH) / 2, pad)
    const adLeft = Math.min(rawLeft, Math.max(pad, baseW - adW - pad))
    const adTop = Math.min(rawTop, Math.max(pad, baseH - adH - pad))

    return (
        <div style={{ display: "grid", gap: 12 }}>
            <div style={{ display: "grid", gap: 8 }}>
                <KV
                    k="Template pagina"
                    v={
                        <span style={{ fontWeight: 900 }}>
                            {mmPair(template)}
                        </span>
                    }
                />
                <KV
                    k="Verwacht advertentieformaat"
                    v={
                        <span style={{ fontWeight: 900 }}>
                            {page.expected_allowed_mm?.length
                                ? page.expected_allowed_mm
                                      .map(
                                          (p) =>
                                              `${round2(p[0])}×${round2(p[1])}mm`
                                      )
                                      .join(" OR ")
                                : "—"}
                        </span>
                    }
                />
                <KV
                    k="Gemeten advertentie-inhoud"
                    v={
                        <span style={{ fontWeight: 900 }}>
                            {adSize ? mmPair(adSize) : "—"}
                        </span>
                    }
                />
            </div>

            <div
                style={{
                    width: "100%",
                    maxWidth: 1100,
                    margin: "0 auto",
                    borderRadius: 16,
                    background: "rgba(0,0,0,0.03)",
                    border: "1px solid rgba(0,0,0,0.08)",
                    padding: 12,
                }}
            >
                <div
                    ref={wrapperRef}
                    style={{
                        width: "100%",
                        aspectRatio: `${tw} / ${th}`,
                        borderRadius: 12,
                        background: "#ffffff",
                        border: "1px solid rgba(0,0,0,0.08)",
                        overflow: "hidden",
                        position: "relative",
                    }}
                >
                    <div
                        style={{
                            width: baseW,
                            height: baseH,
                            position: "absolute",
                            left: "50%",
                            top: "50%",
                            transform: `translate(-50%, -50%) scale(${scale})`,
                            transformOrigin: "center",
                        }}
                    >
                        {adImg && adSize ? (
                            <img
                                src={adImg}
                                alt="Advertentie preview"
                                style={{
                                    position: "absolute",
                                    left: adLeft,
                                    top: adTop,
                                    width: adW,
                                    height: adH,
                                    objectFit: "contain",
                                    background: "#fff",
                                    borderRadius: 0,
                                    border: "1px solid rgba(0,0,0,0.12)",
                                    boxShadow: "none",
                                }}
                            />
                        ) : (
                            <div
                                style={{
                                    position: "absolute",
                                    inset: 0,
                                    display: "grid",
                                    placeItems: "center",
                                    color: "rgba(0,0,0,0.5)",
                                    fontSize: 12,
                                    fontWeight: 800,
                                }}
                            >
                                Geen advertentie-preview beschikbaar
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    )
}

export default function PdfAdChecker(props: { apiBaseUrl: string }) {
    const apiBaseUrl = (props.apiBaseUrl || "http://127.0.0.1:8000").replace(
        /\/$/,
        ""
    )

    const [magazines, setMagazines] = React.useState<Magazine[]>([])
    const [publisherKey, setPublisherKey] =
        React.useState<PublisherKey>("virtumedia")
    const [magazineId, setMagazineId] = React.useState<string>("")
    const [formatId, setFormatId] = React.useState<string>("")

    const [file, setFile] = React.useState<File | null>(null)
    const [dragOver, setDragOver] = React.useState(false)

    const [loading, setLoading] = React.useState(false)
    const [detecting, setDetecting] = React.useState(false)
    const [openingReport, setOpeningReport] = React.useState(false)
    const [downloadingReport, setDownloadingReport] = React.useState(false)

    const [report, setReport] = React.useState<Report | null>(null)
    const [error, setError] = React.useState<string>("")

    const [activePageIdx, setActivePageIdx] = React.useState(0)

    React.useEffect(() => {
        ;(async () => {
            try {
                setError("")
                const res = await fetch(`${apiBaseUrl}/magazines`)
                const data = (await res.json()) as Magazine[]
                setMagazines(data)
                const initialPublisher: PublisherKey = "virtumedia"
                setPublisherKey(initialPublisher)
                const firstForPublisher = data.find(
                    (m) => magazinePublisherKey(m) === initialPublisher
                )
                const first = firstForPublisher ?? data[0]
                if (first) {
                    setMagazineId(first.id)
                    setFormatId(first.formats?.[0]?.id ?? "")
                }
            } catch {
                setError(
                    "Kon magazines niet laden vanuit de API. Draait de server?"
                )
            }
        })()
    }, [apiBaseUrl])

    const magazinesForPublisher = React.useMemo(
        () =>
            magazines.filter(
                (m) => magazinePublisherKey(m) === publisherKey
            ),
        [magazines, publisherKey]
    )

    React.useEffect(() => {
        if (!magazinesForPublisher.length) {
            setMagazineId("")
            setFormatId("")
            return
        }
        if (!magazinesForPublisher.some((m) => m.id === magazineId)) {
            const first = magazinesForPublisher[0]
            setMagazineId(first.id)
            setFormatId(first.formats?.[0]?.id ?? "")
        }
    }, [magazinesForPublisher, magazineId])

    React.useEffect(() => {
        const mag = magazinesForPublisher.find((m) => m.id === magazineId)
        if (!mag) return
        if (!mag.formats.find((f) => f.id === formatId)) {
            setFormatId(mag.formats?.[0]?.id ?? "")
        }
    }, [magazinesForPublisher, magazineId, formatId])

    const selectedMag =
        magazinesForPublisher.find((m) => m.id === magazineId) || null

    function onPickFile(f: File | null) {
        setReport(null)
        setError("")
        setFile(f)
        setActivePageIdx(0)
    }

    React.useEffect(() => {
        if (!file || magazines.length === 0) return
        let cancelled = false
        ;(async () => {
            try {
                setDetecting(true)
                const fd = new FormData()
                fd.append("pdf", file)
                const res = await fetch(`${apiBaseUrl}/detect`, {
                    method: "POST",
                    body: fd,
                })
                const text = await res.text()
                if (!res.ok) return
                const data = JSON.parse(text) as DetectResponse
                const match = data?.match
                if (!match?.magazine_id || !match?.format_id) return
                if (cancelled) return
                const matchedMag = magazines.find(
                    (m) => m.id === match.magazine_id
                )
                if (!matchedMag) return
                setPublisherKey(magazinePublisherKey(matchedMag))
                setMagazineId(match.magazine_id)
                setFormatId(match.format_id)
            } catch {
                // Detectie is ondersteunend; niet blokkeren bij fouten.
            } finally {
                if (!cancelled) setDetecting(false)
            }
        })()
        return () => {
            cancelled = true
        }
    }, [file, magazines, apiBaseUrl])

    function onDrop(ev: React.DragEvent) {
        ev.preventDefault()
        setDragOver(false)
        const f = ev.dataTransfer.files?.[0]
        const name = (f?.name || "").toLowerCase()
        const looksLikePdf = Boolean(
            f &&
                (f.type === "application/pdf" ||
                    f.type === "application/x-pdf" ||
                    name.endsWith(".pdf"))
        )
        if (f && looksLikePdf) onPickFile(f)
        else setError("Plaats een geldig PDF-bestand.")
    }

    async function analyze() {
        if (!file) return setError("Selecteer eerst een PDF.")
        if (!magazineId || !formatId)
            return setError("Selecteer eerst magazine + formaat.")
        setLoading(true)
        setError("")
        setReport(null)
        try {
            const fd = new FormData()
            fd.append("pdf", file)
            fd.append("magazine_id", magazineId)
            fd.append("format_id", formatId)

            const res = await fetch(`${apiBaseUrl}/analyze`, {
                method: "POST",
                body: fd,
            })
            const text = await res.text()
            if (!res.ok) throw new Error(text || `API error: ${res.status}`)
            const data = JSON.parse(text) as any
            if (data?.error) throw new Error(data.error)
            setReport(data as Report)
            setActivePageIdx(0)
        } catch (e: any) {
            setError(e?.message || "Aanvraag mislukt.")
        } finally {
            setLoading(false)
        }
    }

    function resetAll() {
        setReport(null)
        setError("")
        setFile(null)
        setActivePageIdx(0)
    }

    async function openHtmlReport() {
        if (!file) return setError("Selecteer eerst een PDF.")
        if (!magazineId || !formatId)
            return setError("Selecteer eerst magazine + formaat.")

        const reportWindow = window.open("", "_blank")
        if (!reportWindow) {
            return setError("Popup geblokkeerd. Sta popups toe voor rapport.")
        }

        reportWindow.document.open()
        reportWindow.document.write(
            "<!doctype html><html><head><meta charset='utf-8'><title>Rapport openen…</title></head><body style='font-family:system-ui;padding:20px'>Rapport wordt geopend…</body></html>"
        )
        reportWindow.document.close()

        setOpeningReport(true)
        setError("")
        try {
            const fd = new FormData()
            fd.append("pdf", file)
            fd.append("magazine_id", magazineId)
            fd.append("format_id", formatId)
            const res = await fetch(`${apiBaseUrl}/report`, {
                method: "POST",
                body: fd,
            })
            const html = await res.text()
            if (!res.ok) throw new Error(html || `API error: ${res.status}`)

            reportWindow.document.open()
            reportWindow.document.write(html)
            reportWindow.document.close()
        } catch (e: any) {
            try {
                reportWindow.close()
            } catch {
                // ignore
            }
            setError(e?.message || "Kon HTML-rapport niet openen.")
        } finally {
            setOpeningReport(false)
        }
    }

    async function downloadPdfReport() {
        if (!file) return setError("Selecteer eerst een PDF.")
        if (!magazineId || !formatId)
            return setError("Selecteer eerst magazine + formaat.")

        setDownloadingReport(true)
        setError("")
        try {
            const fd = new FormData()
            fd.append("pdf", file)
            fd.append("magazine_id", magazineId)
            fd.append("format_id", formatId)
            const res = await fetch(`${apiBaseUrl}/report.pdf`, {
                method: "POST",
                body: fd,
            })
            if (!res.ok)
                throw new Error(`Kon PDF-rapport niet ophalen (${res.status}).`)
            const blob = await res.blob()
            const url = URL.createObjectURL(blob)
            const a = document.createElement("a")
            a.href = url
            a.download = `pdf-check-report-${Date.now()}.pdf`
            document.body.appendChild(a)
            a.click()
            a.remove()
            URL.revokeObjectURL(url)
        } catch (e: any) {
            setError(e?.message || "Kon PDF-rapport niet downloaden.")
        } finally {
            setDownloadingReport(false)
        }
    }

    const container: React.CSSProperties = {
        width: "100%",
        maxWidth: 980,
        margin: "0 auto",
        height: "100%",
        padding: 16,
        boxSizing: "border-box",
        fontFamily:
            "Inter, system-ui, -apple-system, Segoe UI, Roboto, Arial, sans-serif",
        color: "rgba(0,0,0,0.90)",
        background: "transparent",
        overflow: "auto",
    }

    const selectStyle: React.CSSProperties = {
        width: "100%",
        padding: "10px 12px",
        borderRadius: 12,
        background: "rgba(255,255,255,0.96)",
        border: "1px solid rgba(0,0,0,0.10)",
        color: "rgba(0,0,0,0.88)",
        fontWeight: 800,
        outline: "none",
    }

    const buttonStyle: React.CSSProperties = {
        borderRadius: 14,
        padding: "12px 14px",
        background: loading || detecting ? "rgba(0,0,0,0.06)" : "rgba(0,0,0,0.08)",
        border: "1px solid rgba(0,0,0,0.10)",
        color: "rgba(0,0,0,0.88)",
        fontWeight: 900,
        cursor: loading || detecting ? "not-allowed" : "pointer",
    }

    const smallButton: React.CSSProperties = {
        borderRadius: 12,
        padding: "10px 12px",
        background: "rgba(0,0,0,0.06)",
        border: "1px solid rgba(0,0,0,0.10)",
        color: "rgba(0,0,0,0.86)",
        fontWeight: 900,
        cursor: "pointer",
        fontSize: 12,
        whiteSpace: "nowrap",
    }

    const pages = report?.pages ?? []
    const safePageIdx = Math.min(activePageIdx, Math.max(0, pages.length - 1))
    const activePage = pages[safePageIdx] ?? null

    const formatSuggestion = React.useMemo(() => {
        if (!report || !report.pages?.length) return null
        if (report.pages.every((p) => p.pdf_size_ok)) return null

        const actual = report.pages[0]?.actual_page_mm
        if (!actual || actual[0] <= 0 || actual[1] <= 0) return null

        const reportMag = magazines.find((m) => m.id === report.magazine_id)
        if (!reportMag) return null

        const formats = (reportMag.formats || []).filter(
            (f) => Array.isArray(f.size_mm) && f.size_mm.length === 2
        )
        if (!formats.length) return null

        const current = formats.find((f) => f.id === report.format_id)
        if (!current?.size_mm) return null

        const currentSize: [number, number] = [
            Number(current.size_mm[0]),
            Number(current.size_mm[1]),
        ]
        const currentArea = area(currentSize)
        if (currentArea <= 0) return null

        const actualArea = area(actual)
        const areaRatio = actualArea / currentArea
        if (areaRatio >= 0.8) return null

        const currentDist = normalizedDistance(actual, currentSize)
        const best = formats
            .map((f) => {
                const sz = f.size_mm as [number, number]
                return { f, dist: normalizedDistance(actual, sz) }
            })
            .sort((a, b) => a.dist - b.dist)[0]

        if (!best || best.f.id === current.id) return null
        if (best.dist >= currentDist - 1.0) return null

        const bestSize: [number, number] = [
            Number((best.f.size_mm as [number, number])[0]),
            Number((best.f.size_mm as [number, number])[1]),
        ]
        return `Geselecteerd formaat lijkt te groot voor deze PDF (${round2(
            actual[0]
        )}×${round2(actual[1])}mm). Waarschijnlijk past ${best.f.label} beter (${round2(
            bestSize[0]
        )}×${round2(bestSize[1])}mm).`
    }, [report, magazines])

    return (
        <div style={container}>
            <div style={{ display: "grid", gap: 14 }}>
                <div
                    style={{
                        display: "flex",
                        justifyContent: "space-between",
                        gap: 12,
                        flexWrap: "wrap",
                    }}
                >
                    <div>
                        <div style={{ fontSize: 16, fontWeight: 900 }}>
                            PDF Advertentie Checker
                        </div>
                        <div
                            style={{
                                marginTop: 4,
                                fontSize: 12,
                                color: "rgba(0,0,0,0.55)",
                                fontWeight: 700,
                            }}
                        >
                            Controle op formaat, afloop en effectieve PPI.
                        </div>
                    </div>

                    <div
                        style={{
                            display: "flex",
                            gap: 10,
                            alignItems: "center",
                            flexWrap: "wrap",
                        }}
                    >
                        {report ? (
                            <Pill
                                ok={report.summary.ok}
                                label={report.summary.ok ? "Geslaagd" : "Fout"}
                            />
                        ) : null}
                        <button
                            type="button"
                            onClick={resetAll}
                            style={{ ...smallButton, padding: "10px 12px" }}
                        >
                            Opnieuw
                        </button>
                    </div>
                </div>

                <Card title="Invoer">
                    <div style={{ display: "grid", gap: 12 }}>
                        <div style={{ display: "grid", gap: 8 }}>
                            <div
                                style={{
                                    fontSize: 12,
                                    color: "rgba(0,0,0,0.55)",
                                    fontWeight: 800,
                                }}
                            >
                                PDF
                            </div>

                            <div
                                onDragOver={(e) => {
                                    e.preventDefault()
                                    setDragOver(true)
                                }}
                                onDragLeave={() => setDragOver(false)}
                                onDrop={onDrop}
                                style={{
                                    borderRadius: 14,
                                    padding: 14,
                                    border: "1px dashed rgba(0,0,0,0.20)",
                                    background: dragOver
                                        ? "rgba(0,0,0,0.04)"
                                        : "rgba(0,0,0,0.02)",
                                    display: "grid",
                                    gap: 10,
                                }}
                            >
                                <div
                                    style={{
                                        display: "flex",
                                        justifyContent: "space-between",
                                        gap: 10,
                                        flexWrap: "wrap",
                                    }}
                                >
                                    <div
                                        style={{
                                            fontSize: 12,
                                            color: "rgba(0,0,0,0.65)",
                                            fontWeight: 700,
                                        }}
                                    >
                                        {file ? (
                                            <>
                                                <span
                                                    style={{
                                                        color: "rgba(0,0,0,0.90)",
                                                        fontWeight: 900,
                                                    }}
                                                >
                                                    {file.name}
                                                </span>{" "}
                                                <span style={{ opacity: 0.75 }}>
                                                    ({formatFileSize(file.size)}
                                                    )
                                                </span>
                                            </>
                                        ) : (
                                            "Sleep een PDF hierheen of kies een bestand."
                                        )}
                                    </div>

                                    <label
                                        style={{
                                            display: "inline-flex",
                                            alignItems: "center",
                                            gap: 8,
                                            padding: "10px 12px",
                                            borderRadius: 12,
                                            background: "rgba(0,0,0,0.06)",
                                            border: "1px solid rgba(0,0,0,0.10)",
                                            cursor: "pointer",
                                            fontWeight: 900,
                                            fontSize: 12,
                                            color: "rgba(0,0,0,0.86)",
                                        }}
                                    >
                                        Kies PDF
                                        <input
                                            type="file"
                                            accept="application/pdf"
                                            onChange={(e) =>
                                                onPickFile(
                                                    e.target.files?.[0] ?? null
                                                )
                                            }
                                            style={{ display: "none" }}
                                        />
                                    </label>
                                </div>
                            </div>
                        </div>

                        <div style={{ display: "grid", gap: 10 }}>
                            <div style={{ display: "grid", gap: 8 }}>
                                <div
                                    style={{
                                        fontSize: 12,
                                        color: "rgba(0,0,0,0.55)",
                                        fontWeight: 800,
                                    }}
                                >
                                    Uitgever
                                </div>
                                <select
                                    value={publisherKey}
                                    onChange={(e) =>
                                        setPublisherKey(
                                            e.target.value as PublisherKey
                                        )
                                    }
                                    style={selectStyle}
                                    disabled={magazines.length === 0}
                                >
                                    <option value="virtumedia">
                                        {publisherLabel("virtumedia")}
                                    </option>
                                    <option value="alea">
                                        {publisherLabel("alea")}
                                    </option>
                                </select>
                            </div>

                            <div style={{ display: "grid", gap: 8 }}>
                                <div
                                    style={{
                                        fontSize: 12,
                                        color: "rgba(0,0,0,0.55)",
                                        fontWeight: 800,
                                    }}
                                >
                                    Magazine
                                </div>
                                <select
                                    value={magazineId}
                                    onChange={(e) =>
                                        setMagazineId(e.target.value)
                                    }
                                    style={selectStyle}
                                    disabled={magazinesForPublisher.length === 0}
                                >
                                    {magazinesForPublisher.length === 0 ? (
                                    <option value="">
                                            Geen magazines voor deze uitgever...
                                        </option>
                                    ) : (
                                        magazinesForPublisher.map((m) => (
                                            <option key={m.id} value={m.id}>
                                                {m.display_name ??
                                                    `${m.name} (${m.base_trim_mm[0]}×${m.base_trim_mm[1]}mm)`}
                                            </option>
                                        ))
                                    )}
                                </select>
                            </div>

                            <div style={{ display: "grid", gap: 8 }}>
                                <div
                                    style={{
                                        fontSize: 12,
                                        color: "rgba(0,0,0,0.55)",
                                        fontWeight: 800,
                                    }}
                                >
                                    Formaat
                                </div>
                                <select
                                    value={formatId}
                                    onChange={(e) =>
                                        setFormatId(e.target.value)
                                    }
                                    style={selectStyle}
                                    disabled={!selectedMag}
                                >
                                    {(selectedMag?.formats ?? []).length ? (
                                        (selectedMag?.formats ?? []).map(
                                            (f) => (
                                                <option key={f.id} value={f.id}>
                                                    {f.label}
                                                </option>
                                            )
                                        )
                                    ) : (
                                        <option value="">
                                            Selecteer eerst een magazine…
                                        </option>
                                    )}
                                </select>
                            </div>
                        </div>

                        <button
                            type="button"
                            onClick={analyze}
                            disabled={loading || detecting}
                            style={buttonStyle}
                        >
                            {loading
                                ? "Controleren..."
                                : detecting
                                  ? "Formaat detecteren..."
                                  : "Controleer PDF"}
                        </button>

                        {error ? (
                            <div
                                style={{
                                    borderRadius: 14,
                                    padding: 12,
                                    background: "rgba(236,72,153,0.10)",
                                    border: "1px solid rgba(236,72,153,0.20)",
                                    color: "rgba(0,0,0,0.85)",
                                    fontSize: 12,
                                    fontWeight: 800,
                                }}
                            >
                                {error}
                            </div>
                        ) : null}
                    </div>
                </Card>

                {report ? (
                    <>
                        {formatSuggestion ? (
                            <div
                                style={{
                                    borderRadius: 14,
                                    padding: 12,
                                    background: "rgba(236,72,153,0.10)",
                                    border: "1px solid rgba(236,72,153,0.20)",
                                    color: "rgba(0,0,0,0.85)",
                                    fontSize: 12,
                                    fontWeight: 800,
                                }}
                            >
                                {formatSuggestion}
                            </div>
                        ) : null}

                        <Card
                            title="Samenvatting"
                            right={
                                <div
                                    style={{
                                        display: "flex",
                                        gap: 10,
                                        alignItems: "center",
                                        flexWrap: "wrap",
                                        justifyContent: "flex-end",
                                    }}
                                >
                                    <Pill
                                        ok={report.summary.ok}
                                        label={report.summary.ok ? "Alles ok" : "Problemen"}
                                    />
                                    <button
                                        type="button"
                                        onClick={openHtmlReport}
                                        disabled={openingReport}
                                        style={{
                                            ...smallButton,
                                            opacity: openingReport ? 0.7 : 1,
                                            cursor: openingReport
                                                ? "not-allowed"
                                                : "pointer",
                                        }}
                                    >
                                        {openingReport
                                            ? "Openen…"
                                            : "Open rapport"}
                                    </button>
                                    <button
                                        type="button"
                                        onClick={downloadPdfReport}
                                        disabled={downloadingReport}
                                        style={{
                                            ...smallButton,
                                            opacity: downloadingReport ? 0.7 : 1,
                                            cursor: downloadingReport
                                                ? "not-allowed"
                                                : "pointer",
                                        }}
                                    >
                                        {downloadingReport
                                            ? "Downloaden…"
                                            : "Download rapport (PDF)"}
                                    </button>
                                </div>
                            }
                        >
                            <div style={{ display: "grid", gap: 10 }}>
                                <KV k="Magazine" v={report.magazine} />
                                <KV k="Formaat" v={report.format} />
                                <KV
                                    k="Minimale effectieve PPI"
                                    v={intOrDash(report.min_effective_ppi)}
                                />
                                <KV
                                    k="Pagina's geanalyseerd"
                                    v={report.pages.length}
                                />
                            </div>

                            {report.issues?.length ? (
                                <>
                                    <div style={{ height: 12 }} />
                                    <div
                                        style={{
                                            fontSize: 12,
                                            fontWeight: 900,
                                            color: "rgba(0,0,0,0.85)",
                                        }}
                                    >
                                        Problemen
                                    </div>
                                    <div style={{ height: 8 }} />
                                    <div style={{ display: "grid", gap: 8 }}>
                                        {report.issues
                                            .slice(0, 12)
                                            .map((iss, idx) => (
                                                <div
                                                    key={idx}
                                                    style={{
                                                        borderRadius: 12,
                                                        padding: "10px 12px",
                                                        background:
                                                            "rgba(0,0,0,0.03)",
                                                        border: "1px solid rgba(0,0,0,0.08)",
                                                        fontSize: 12,
                                                        color: "rgba(0,0,0,0.80)",
                                                        fontWeight: 700,
                                                    }}
                                                >
                                                    {iss}
                                                </div>
                                            ))}
                                    </div>
                                </>
                            ) : null}
                        </Card>

                        <Card
                            title={`Pagina ${activePage?.page ?? 1}`}
                            right={
                                <div
                                    style={{
                                        display: "flex",
                                        gap: 8,
                                        flexWrap: "wrap",
                                        justifyContent: "flex-end",
                                    }}
                                >
                                        {pages.length > 0 ? (
                                            <select
                                                value={safePageIdx}
                                                onChange={(e) =>
                                                    setActivePageIdx(
                                                        Number(e.target.value)
                                                    )
                                                }
                                                style={{
                                                    ...selectStyle,
                                                    width: 145,
                                                    padding: "7px 10px",
                                                    fontSize: 12,
                                                    fontWeight: 800,
                                                }}
                                            >
                                                {pages.map((p, idx) => (
                                                    <option
                                                        key={p.page}
                                                        value={idx}
                                                    >
                                                    Pagina {p.page}
                                                    </option>
                                                ))}
                                            </select>
                                        ) : null}
                                        <Pill
                                            ok={
                                                activePage?.pdf_size_ok ?? false
                                            }
                                            label="Formaat"
                                        />
                                        <Pill
                                            ok={
                                                (activePage?.bleed_ok ?? true) &&
                                                (activePage?.bleed_content_ok ??
                                                    true)
                                            }
                                            label="Afloop"
                                        />
                                        <Pill
                                            ok={activePage?.ppi_ok ?? false}
                                            label="PPI"
                                        />
                                    </div>
                                }
                        >
                            {activePage ? (
                                <div style={{ display: "grid", gap: 16 }}>
                                    <PlacementPreview
                                        page={activePage}
                                        formatId={report.format_id}
                                        previewPaddingPct={
                                            report.preview_padding_pct ??
                                            selectedMag?.preview_padding_pct ??
                                            2.6
                                        }
                                    />

                                    <div style={{ display: "grid", gap: 8 }}>
                                        <KV
                                            k="Werkelijk PDF-formaat"
                                            v={mmPair(activePage.actual_page_mm)}
                                        />
                                        <KV
                                            k="Verwachte formaat(en)"
                                            v={activePage.expected_allowed_mm
                                                .map(
                                                    (p) =>
                                                        `${round2(p[0])}×${round2(p[1])}mm`
                                                )
                                                .join(" OF ")}
                                        />
                                        <KV
                                            k="Afbeeldingen gevonden"
                                            v={activePage.images_found}
                                        />
                                        <KV
                                            k="Afbeeldingen onder min PPI"
                                            v={activePage.images_below_min_ppi}
                                        />
                                        <KV
                                            k="Laagste effectieve PPI"
                                            v={intOrDash(activePage.lowest_effective_ppi)}
                                        />
                                        <KV
                                            k="Inhoud type"
                                            v={contentTypeNl(activePage.content_classification)}
                                        />
                                        <KV
                                            k="Regel: Formaat"
                                            v={statusNl(activePage.rules?.size?.status)}
                                        />
                                        <KV
                                            k="Regel: Afloop formaat"
                                            v={statusNl(activePage.rules?.bleed_size?.status)}
                                        />
                                        <KV
                                            k="Regel: Afloop inhoud"
                                            v={statusNl(activePage.rules?.bleed_content?.status)}
                                        />
                                        <KV
                                            k="Regel: PPI"
                                            v={statusNl(activePage.rules?.ppi?.status)}
                                        />
                                    </div>

                                    {activePage.issues?.length ? (
                                        <div
                                            style={{ display: "grid", gap: 8 }}
                                        >
                                            {activePage.issues.map(
                                                (iss, idx) => (
                                                    <div
                                                        key={idx}
                                                        style={{
                                                            borderRadius: 12,
                                                            padding:
                                                                "10px 12px",
                                                            background:
                                                                "rgba(236,72,153,0.08)",
                                                            border: "1px solid rgba(236,72,153,0.18)",
                                                            fontSize: 12,
                                                            color: "rgba(0,0,0,0.82)",
                                                            fontWeight: 800,
                                                        }}
                                                    >
                                                        {iss}
                                                    </div>
                                                )
                                            )}
                                        </div>
                                    ) : null}
                                </div>
                            ) : (
                                <div
                                    style={{
                                        fontSize: 12,
                                        color: "rgba(0,0,0,0.60)",
                                        fontWeight: 700,
                                    }}
                                >
                                    Geen pagina's gevonden in dit PDF-bestand.
                                </div>
                            )}
                        </Card>

                        {report.recommendations?.length ? (
                            <Card title="Aanbevelingen">
                                <div style={{ display: "grid", gap: 8 }}>
                                    {report.recommendations.map((rec, idx) => (
                                        <div
                                            key={idx}
                                            style={{
                                                borderRadius: 12,
                                                padding: "10px 12px",
                                                background: "rgba(0,0,0,0.03)",
                                                border: "1px solid rgba(0,0,0,0.08)",
                                                fontSize: 12,
                                                color: "rgba(0,0,0,0.80)",
                                                fontWeight: 700,
                                            }}
                                        >
                                            {rec}
                                        </div>
                                    ))}
                                </div>
                            </Card>
                        ) : null}

                        {report.print_checks ? (
                            <Card title="Drukwerk checks">
                                <div style={{ display: "grid", gap: 8 }}>
                                    <KV
                                        k="Fonts ingesloten"
                                        v={
                                            <Pill
                                                ok={
                                                    report.print_checks
                                                        .fonts_embedded_ok
                                                }
                                                label={
                                                    report.print_checks
                                                        .fonts_embedded_ok
                                                        ? "Geslaagd"
                                                        : "Fout"
                                                }
                                            />
                                        }
                                    />
                                    <KV
                                        k="Kleurprofiel (CMYK)"
                                        v={
                                            <Pill
                                                ok={
                                                    report.print_checks
                                                        .color_space_ok
                                                }
                                                label={
                                                    report.print_checks
                                                        .color_space_ok
                                                        ? "Geslaagd"
                                                        : "Fout"
                                                }
                                            />
                                        }
                                    />
                                    {!report.print_checks.fonts_embedded_ok && (
                                        <KV
                                            k="Niet-ingesloten fonts"
                                            v={report.print_checks.non_embedded_fonts.join(", ")}
                                        />
                                    )}
                                    {!report.print_checks.color_space_ok && (
                                        <KV
                                            k="Niet-CMYK afbeeldingen"
                                            v={report.print_checks.non_cmyk_images.length}
                                        />
                                    )}
                                </div>
                            </Card>
                        ) : null}

                        {report.worst_images?.length ? (
                            <Card title="Laagste effectieve PPI afbeeldingen">
                                <div
                                    style={{
                                        display: "grid",
                                        gridTemplateColumns:
                                            "repeat(auto-fit, minmax(170px, 1fr))",
                                        gap: 12,
                                    }}
                                >
                                    {report.worst_images.map((w, idx) => (
                                        <div
                                            key={idx}
                                            style={{
                                                borderRadius: 14,
                                                padding: 12,
                                                background: "rgba(0,0,0,0.02)",
                                                border: "1px solid rgba(0,0,0,0.08)",
                                                display: "grid",
                                                gap: 10,
                                            }}
                                        >
                                            <div
                                                style={{
                                                    display: "grid",
                                                    gap: 4,
                                                }}
                                            >
                                                <div
                                                    style={{
                                                        fontSize: 12,
                                                        fontWeight: 900,
                                                        color: "rgba(0,0,0,0.88)",
                                                    }}
                                                >
                                                    {intOrDash(w.effective_ppi)}{" "}
                                                    PPI
                                                </div>
                                                <div
                                                    style={{
                                                        fontSize: 12,
                                                        color: "rgba(0,0,0,0.55)",
                                                        fontWeight: 800,
                                                    }}
                                                >
                                                    Pagina {w.page} •{" "}
                                                    {w.pixels?.[0]}×
                                                    {w.pixels?.[1]} px
                                                </div>
                                            </div>

                                            {w.preview ? (
                                                <img
                                                    src={w.preview}
                                                    alt={`Worst image ${idx}`}
                                                    style={{
                                                        width: "100%",
                                                        borderRadius: 12,
                                                        border: "1px solid rgba(0,0,0,0.10)",
                                                        background: "#fff",
                                                        display: "block",
                                                    }}
                                                />
                                            ) : (
                                                <div
                                                    style={{
                                                        fontSize: 12,
                                                        color: "rgba(0,0,0,0.55)",
                                                        fontWeight: 700,
                                                    }}
                                                >
                                                    Geen preview
                                                </div>
                                            )}
                                        </div>
                                    ))}
                                </div>
                                <div style={{ height: 1 }} />
                            </Card>
                        ) : null}
                    </>
                ) : null}
            </div>
        </div>
    )
}

addPropertyControls(PdfAdChecker, {
    apiBaseUrl: {
        type: ControlType.String,
        title: "API Base URL",
        defaultValue: "http://127.0.0.1:8000",
    },
})
