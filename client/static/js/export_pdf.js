// ==============================================================================
// Dream Enterprise AI Hub - PDF Export Module (Universal Adaptive Engine)
// ==============================================================================

let _pdfFontP = null;

function ensurePdfFont() {
    if (_pdfFontP) return _pdfFontP;
    _pdfFontP = (async () => {
        for (const u of ['/static/fonts/Amiri-Regular.ttf', 'https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/amiri/Amiri-Regular.ttf']) {
            try {
                const r = await fetch(u);
                if (!r.ok) continue;
                const bytes = new Uint8Array(await r.arrayBuffer());
                let bin = '';
                for (let i = 0; i < bytes.length; i += 0x8000) {
                    bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
                }
                return btoa(bin);
            } catch (e) {}
        }
        return null;
    })();
    _pdfFontP.then(v => { if (!v) _pdfFontP = null; });
    return _pdfFontP;
}

function textImg(text, pt, bold) {
    const S = 4, px = pt * S, c = document.createElement('canvas'), x = c.getContext('2d');
    const font = `${bold ? 'bold ' : ''}${px}px "Noto Sans SC","Microsoft YaHei","PingFang SC","Hiragino Sans GB","Noto Sans CJK SC","Noto Sans Devanagari","Nirmala UI","Noto Sans Thai","Leelawadee UI","Malgun Gothic","Segoe UI Emoji","Segoe UI",Arial,sans-serif`;
    x.font = font;
    const w = Math.ceil(x.measureText(text).width) + 8, h = Math.ceil(px * 1.5);
    c.width = w; c.height = h;
    x.font = font; x.fillStyle = '#141414'; x.textBaseline = 'middle';
    x.fillText(text, 4, h / 2);
    const mm = 0.3528 / S;
    return { url: c.toDataURL('image/png'), w: w * mm, h: h * mm };
}

function sanitizeForPdf(str) {
    if (str === null || str === undefined) return '';
    let s = String(str)
        .replace(/₹\s*/g, 'Rs. ')
        .replace(/[\u2018\u2019]/g, "'")
        .replace(/[\u201C\u201D]/g, '"')
        .replace(/[\u2013\u2014]/g, '-')
        .replace(/\u2026/g, '...')
        .replace(/\u00A0/g, ' ')
        .replace(/[▍▎▏▌]/g, '')
        .replace(/\*\*/g, '')
        .replace(/`/g, '');

    // For extremely long unbroken URLs over 35 chars, allow natural break after delimiters
    if (/https?:\/\//i.test(s) && s.length > 35) {
        s = s.replace(/([\/?.&=])/g, '$1 ');
    }
    return s;
}

async function buildPDF(data, title, summary = '', charts = []) {
    if (!window.jspdf || !window.jspdf.jsPDF) {
        showError('error', 'PDF', 'PDF library failed to load.');
        return null;
    }
    const head = data.head || [];
    const body = data.body || [];
    const extraTables = data.extra_tables || [];
    const heading = niceTitle(title);
    const cleanHead = head.map(h => sanitizeForPdf(h));
    const cleanBody = body.map(row => row.map(cell => sanitizeForPdf(cell)));
    const cleanSummary = sanitizeForPdf(summary).trim();

    const totalCols = cleanHead.length;

    // Auto-select orientation: Landscape for multi-column tables (>5 cols) to maximize horizontal space
    const orientation = totalCols > 5 ? 'landscape' : 'portrait';
    const doc = new window.jspdf.jsPDF({ orientation, unit: 'mm', format: 'a4' });
    doc.setProperties({ title: heading });

    const everything = [heading, cleanSummary, PDF_COMPANY, ...charts.map(c => c.title), ...cleanHead, ...cleanBody.flat()].join(' ');
    let AR = 'helvetica';
    if (hasArabic(everything)) {
        const b64 = await ensurePdfFont();
        if (b64) {
            doc.addFileToVFS('Amiri-Regular.ttf', b64);
            doc.addFont('Amiri-Regular.ttf', 'Amiri', 'normal');
            doc.addFont('Amiri-Regular.ttf', 'Amiri', 'bold');
            AR = 'Amiri';
        }
    }
    const fontFor = t => (AR !== 'helvetica' && hasArabic(t)) ? AR : 'helvetica';
    const put = (t, x, y, o = {}) => {
        t = String(t ?? '');
        if (hasComplex(t)) {
            const im = textImg(t, doc.getFontSize(), o.bold), w = Math.min(im.w, o.maxW || 1e9), h = im.h * w / im.w;
            doc.addImage(im.url, 'PNG', x + (o.align === 'center' ? -w / 2 : o.align === 'right' ? -w : 0), y - h * 0.72, w, h);
            return;
        }
        const f = fontFor(t);
        doc.setFont(f, o.bold ? 'bold' : (o.italic && f === 'helvetica') ? 'italic' : 'normal');
        doc.text(t, x, y, o.align ? { align: o.align } : undefined);
    };

    const pageW = doc.internal.pageSize.getWidth();
    const marginSide = totalCols >= 12 ? 8 : 10;
    const availableWidth = pageW - (marginSide * 2);

    // Number column detection
    const num = i => cleanBody.length && cleanBody.every(r => (r[i] || '') === '' || !isNaN((r[i] || '').replace(/[,Rs.\s]/g, '')));

    // Dynamic proportional column widths based on content density that strictly fit availableWidth
    const colWeights = cleanHead.map((h, i) => {
        let maxLen = String(h).length;
        let sumLen = String(h).length;
        const sample = cleanBody.slice(0, 40);
        sample.forEach(r => {
            const cell = String(r[i] ?? '');
            const eff = Math.min(cell.length, 28);
            if (eff > maxLen) maxLen = eff;
            sumLen += eff;
        });
        const avgLen = sumLen / (sample.length + 1);
        const hLow = String(h).toLowerCase();
        const isId = /id$/i.test(hLow) || hLow === 'id';
        const isCode = /status|city|phone|gender|state/i.test(hLow);
        let w = avgLen * 0.5 + maxLen * 0.5;
        if (isId) w = Math.min(w, 8);
        else if (isCode) w = Math.min(w, 13);
        else w = Math.max(w, 10);
        return Math.max(w, 7);
    });

    const sumWeights = colWeights.reduce((a, b) => a + b, 0) || 1;
    const columnStyles = {};

    cleanHead.forEach((h, i) => {
        const isNumeric = num(i);
        // Proportional width that mathematically sums to exactly availableWidth - zero right-side clipping!
        const propW = (colWeights[i] / sumWeights) * availableWidth;

        columnStyles[i] = {
            cellWidth: propW,
            halign: isNumeric ? 'right' : 'left',
            overflow: 'linebreak'
        };
    });

    // Adaptive typography & padding scaling based on total column count
    let tableFontSize = 7.0;
    let pad = 0.8;
    if (totalCols >= 18) {
        tableFontSize = 4.8;
        pad = 0.35;
    } else if (totalCols >= 13) {
        tableFontSize = 5.2;
        pad = 0.45;
    } else if (totalCols >= 9) {
        tableFontSize = 6.0;
        pad = 0.6;
    } else if (totalCols <= 5) {
        tableFontSize = 8.0;
        pad = 1.0;
    }

    let startY = 15;
    if (cleanSummary) {
        let y = 19;
        doc.setFontSize(9).setTextColor(0, 139, 139);
        put('Summary', marginSide, y, { bold: true });
        doc.setTextColor(40);
        y += 4.5;
        const maxTextW = pageW - (marginSide * 2 + 6);
        doc.setFontSize(8);

        cleanSummary.split('\n').forEach(l => {
            const trimmed = l.trim();
            if (!trimmed) {
                y += 1.8;
                return;
            }
            const isHeading = /^[A-Z][A-Za-z0-9\s&,–-]{2,40}:?$/.test(trimmed) && !trimmed.startsWith('•') && !trimmed.startsWith('-');
            let parts = [trimmed];
            if (!hasComplex(trimmed)) {
                doc.setFont(fontFor(trimmed), isHeading ? 'bold' : 'normal');
                parts = doc.splitTextToSize(trimmed, maxTextW);
            }
            parts.forEach(sl => {
                const rtl = hasArabic(sl);
                put(sl, rtl ? pageW - marginSide : marginSide, y, { align: rtl ? 'right' : undefined, bold: isHeading, maxW: maxTextW });
                y += 3.8;
            });
        });
        startY = y + 3.5;
    }

    const TOTAL = '{totalPages}';
    const frame = n => {
        const h = doc.internal.pageSize.getHeight(), cx = pageW / 2, r = pageW - marginSide;
        doc.setFontSize(9).setTextColor(0);
        put(PDF_COMPANY, marginSide, 10, { bold: true });
        doc.setFontSize(12);
        put(heading, cx, 10, { bold: true, align: 'center', maxW: pageW - 130 });
        doc.setFontSize(8).setFont('helvetica', 'normal').setTextColor(80);
        doc.text(new Date().toLocaleDateString(), r - 28, 10, { align: 'right' });
        doc.text(`Page ${n}/${TOTAL}`, r, 10, { align: 'right' });

        if (cleanSummary && n === 1) {
            doc.setDrawColor(0, 128, 191).setLineWidth(0.25).line(marginSide, 13, r, 13);
        }

        doc.setFontSize(7).setFont('helvetica', 'italic').setTextColor(150).text(PDF_CREDIT, cx, h - 5, { align: 'center' });
    };

    let page = 0;
    doc.autoTable({
        head: [cleanHead],
        body: cleanBody,
        startY,
        tableWidth: availableWidth,
        columnStyles,
        margin: { top: 15, left: marginSide, right: marginSide, bottom: 10 },
        styles: {
            fontSize: tableFontSize,
            cellPadding: pad,
            lineWidth: 0.2,
            lineColor: [0, 128, 191],
            textColor: 20,
            overflow: 'linebreak',
            valign: 'middle'
        },
        headStyles: {
            fillColor: [223, 255, 255],
            textColor: 0,
            fontStyle: 'bold',
            halign: 'center',
            fontSize: tableFontSize + 0.5
        },
        didParseCell(d) {
            const raw = d.cell.raw && d.cell.raw.content !== undefined ? d.cell.raw.content : d.cell.raw;
            const t = String(raw ?? '');
            if (hasComplex(t)) {
                const im = textImg(t, d.cell.styles.fontSize, d.section === 'head');
                const w = Math.min(im.w, 60), h = im.h * w / im.w;
                d.cell._img = { im, w, h };
                d.cell.text = [''];
                d.cell.styles.minCellHeight = h + 2 * pad;
                d.cell.styles.minCellWidth = w + 2 * pad;
                return;
            }
            if (AR !== 'helvetica' && hasArabic(t)) {
                d.cell.styles.font = AR;
                if (!d.cell.styles.halign || d.cell.styles.halign === 'left') d.cell.styles.halign = 'right';
                doc.setFont(AR, 'normal');
                doc.setFontSize(d.cell.styles.fontSize);
                const tw = Math.max(...t.split('\n').map(l => doc.getTextWidth(l.replace(/\s+/g, ' ').trim())));
                const need = tw * 1.03 + 2 * pad + 0.3;
                if (need <= 55) {
                    d.cell.styles.overflow = 'visible';
                    d.cell.styles.minCellWidth = Math.max(d.cell.styles.minCellWidth || 0, need);
                }
            }
        },
        didDrawCell(d) {
            const g = d.cell._img;
            if (g) doc.addImage(g.im.url, 'PNG', d.cell.x + pad, d.cell.y + (d.cell.height - g.h) / 2, g.w, g.h);
        },
        didDrawPage() {
            page++;
            frame(page);
        }
    });

    if (extraTables && extraTables.length > 0) {
        for (const tbl of extraTables) {
            let nextY = (doc.lastAutoTable && doc.lastAutoTable.finalY) ? doc.lastAutoTable.finalY + 8 : startY + 8;
            if (nextY + 30 > doc.internal.pageSize.getHeight() - 15) {
                doc.addPage();
                page++;
                frame(page);
                nextY = 18;
            }
            if (tbl.title) {
                doc.setFontSize(10).setFont('helvetica', 'bold').setTextColor(0, 139, 139);
                put(sanitizeForPdf(tbl.title), marginSide, nextY, { bold: true });
                nextY += 5;
            }
            const tHead = (tbl.head || []).map(h => sanitizeForPdf(h));
            const tBody = (tbl.body || []).map(row => row.map(cell => sanitizeForPdf(cell)));
            doc.autoTable({
                head: [tHead],
                body: tBody,
                startY: nextY,
                margin: { top: 15, left: marginSide, right: marginSide, bottom: 10 },
                styles: {
                    fontSize: tableFontSize,
                    cellPadding: pad,
                    lineWidth: 0.2,
                    lineColor: [0, 128, 191],
                    textColor: 20,
                    overflow: 'linebreak',
                    valign: 'middle'
                },
                headStyles: {
                    fillColor: [223, 255, 255],
                    textColor: 0,
                    fontStyle: 'bold',
                    halign: 'center',
                    fontSize: tableFontSize + 0.5
                },
                didDrawPage() {
                    page++;
                    frame(page);
                }
            });
        }
    }

    const pageH = doc.internal.pageSize.getHeight();
    const bottomLimit = pageH - 14;
    const finalY = (doc.lastAutoTable && doc.lastAutoTable.finalY) ? doc.lastAutoTable.finalY : startY;
    let curY = finalY + 6;

    charts.forEach((c) => {
        let w = 210, h = w * (c.h || 1) / (c.w || 1);
        if (h > 72) { h = 72; w = h * (c.w || 1) / (c.h || 1); }
        if (h < 45) { h = 45; }
        if (w > pageW - (marginSide * 2)) { w = pageW - (marginSide * 2); h = w * (c.h || 1) / (c.w || 1); }
        const blockH = h + 10;

        if (curY + blockH > bottomLimit) {
            doc.addPage();
            frame(doc.getNumberOfPages());
            curY = 18;
        }

        doc.setFontSize(10).setTextColor(0);
        put(c.title, marginSide, curY, { bold: true, maxW: pageW - (marginSide * 2) });
        const left = (pageW - w) / 2;
        doc.addImage(c.img, 'PNG', left, curY + 3.5, w, h);
        curY += blockH + 4;
    });

    if (doc.putTotalPages) doc.putTotalPages(TOTAL);
    return doc;
}
