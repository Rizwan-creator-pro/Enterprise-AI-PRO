// ==============================================================================
// Dream Enterprise AI Hub - Excel Export Module (ExcelJS)
// ==============================================================================

async function buildExcel({ head, body, extra_tables }, title, summary = '', charts = []) {
    if (!window.ExcelJS) throw new Error('Excel library failed to load.');
    const heading = niceTitle(title);
    const wb = new ExcelJS.Workbook();
    wb.creator = FILE_COMPANY;
    wb.created = new Date();
    const ws = wb.addWorksheet(sheetSafe(heading), {
        pageSetup: { orientation: 'landscape', paperSize: 9, fitToPage: true, fitToWidth: 1, fitToHeight: 0 }
    });
    ws.headerFooter.oddFooter = `&L&8${PDF_CREDIT}&R&8Page &P of &N`;
    const edge = { style: 'thin' };
    const border = { top: edge, left: edge, bottom: edge, right: edge };
    const ncol = Math.max(head.length, 2);
    let r = 1;

    const banner = (text, font) => {
        const c = ws.getCell(r, 1);
        c.value = text;
        c.font = font;
        c.alignment = { horizontal: 'center' };
        ws.mergeCells(r, 1, r, ncol);
        r++;
    };

    banner(FILE_COMPANY, { bold: true, size: 14 });
    banner(heading, { bold: true, size: 12 });
    banner('Generated: ' + stamp(), { italic: true, size: 10, color: { argb: 'FF555555' } });

    summaryLines(summary).forEach(l => {
        const c = ws.getCell(r++, 1);
        c.value = l;
        c.font = { bold: true, italic: true, size: 10, color: { argb: 'FF555555' } };
        c.alignment = { horizontal: 'left' };
    });
    r++;

    const hr = r;
    head.forEach((h, i) => {
        const c = ws.getCell(r, i + 1);
        c.value = h;
        c.border = border;
        c.font = { bold: true, size: 10 };
        c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFDFFFFF' } };
        c.alignment = { horizontal: 'center', vertical: 'middle', wrapText: true };
    });
    r++;

    body.forEach(row => {
        head.forEach((_, i) => {
            const t = typeCell(row[i]);
            const c = ws.getCell(r, i + 1);
            c.value = t.v;
            if (t.fmt) c.numFmt = t.fmt;
            c.font = { size: 9, ...xlFont(String(row[i] ?? '')) };
            c.border = border;
            c.alignment = { horizontal: t.right ? 'right' : t.center ? 'center' : 'left' };
            if (rtlT(String(row[i] ?? ''))) c.alignment = { horizontal: 'right', readingOrder: 'rtl' };
        });
        r++;
    });

    head.forEach((h, i) => {
        let m = String(h).length;
        body.forEach(row => { m = Math.max(m, String(row[i] ?? '').length); });
        ws.getColumn(i + 1).width = Math.max(12, Math.min(m + 3, 50));
    });

    if (hr <= 8) ws.views = [{ state: 'frozen', ySplit: hr }];
    ws.autoFilter = { from: { row: hr, column: 1 }, to: { row: hr, column: head.length } };
    ws.pageSetup.printTitlesRow = `${hr}:${hr}`;

    if (extra_tables && extra_tables.length) {
        extra_tables.forEach((tbl, idx) => {
            const subTitle = tbl.title || `Table ${idx + 2}`;
            const subWs = wb.addWorksheet(sheetSafe(subTitle), {
                pageSetup: { orientation: 'landscape', paperSize: 9, fitToPage: true, fitToWidth: 1, fitToHeight: 0 }
            });
            subWs.headerFooter.oddFooter = `&L&8${PDF_CREDIT}&R&8Page &P of &N`;
            const tHead = tbl.head || [];
            const tBody = tbl.body || [];
            const subNcol = Math.max(tHead.length, 2);
            let sr = 1;
            const subBanner = (text, font) => {
                const c = subWs.getCell(sr, 1);
                c.value = text;
                c.font = font;
                c.alignment = { horizontal: 'center' };
                subWs.mergeCells(sr, 1, sr, subNcol);
                sr++;
            };
            subBanner(FILE_COMPANY, { bold: true, size: 14 });
            subBanner(subTitle, { bold: true, size: 12 });
            subBanner('Generated: ' + stamp(), { italic: true, size: 10, color: { argb: 'FF555555' } });
            sr++;
            const subHr = sr;
            tHead.forEach((h, i) => {
                const c = subWs.getCell(sr, i + 1);
                c.value = h;
                c.border = border;
                c.font = { bold: true, size: 10 };
                c.fill = { type: 'pattern', pattern: 'solid', fgColor: { argb: 'FFDFFFFF' } };
                c.alignment = { horizontal: 'center', vertical: 'middle', wrapText: true };
            });
            sr++;
            tBody.forEach(row => {
                tHead.forEach((_, i) => {
                    const t = typeCell(row[i]);
                    const c = subWs.getCell(sr, i + 1);
                    c.value = t.v;
                    if (t.fmt) c.numFmt = t.fmt;
                    c.font = { size: 9, ...xlFont(String(row[i] ?? '')) };
                    c.border = border;
                    c.alignment = { horizontal: t.right ? 'right' : t.center ? 'center' : 'left' };
                    if (rtlT(String(row[i] ?? ''))) c.alignment = { horizontal: 'right', readingOrder: 'rtl' };
                });
                sr++;
            });
            tHead.forEach((h, i) => {
                let m = String(h).length;
                tBody.forEach(row => { m = Math.max(m, String(row[i] ?? '').length); });
                subWs.getColumn(i + 1).width = Math.max(12, Math.min(m + 3, 50));
            });
            if (subHr <= 8) subWs.views = [{ state: 'frozen', ySplit: subHr }];
            subWs.autoFilter = { from: { row: subHr, column: 1 }, to: { row: subHr, column: tHead.length } };
            subWs.pageSetup.printTitlesRow = `${subHr}:${subHr}`;
        });
    }

    if (charts.length) {
        const cs = wb.addWorksheet('Charts', { views: [{ showGridLines: false }] });
        cs.getCell(1, 1).value = heading + ' — Charts';
        cs.getCell(1, 1).font = { bold: true, size: 14 };
        let row = 3;
        charts.forEach(c => {
            cs.getCell(row, 1).value = c.title;
            cs.getCell(row, 1).font = { bold: true, size: 11 };
            let wpx = 520, hpx = Math.round(wpx * c.h / c.w);
            if (hpx > 320) { hpx = 320; wpx = Math.round(hpx * c.w / c.h); }
            const id = wb.addImage({ base64: c.img, extension: 'png' });
            cs.addImage(id, { tl: { col: 0, row }, ext: { width: wpx, height: hpx } });
            row += Math.ceil(hpx / 20) + 3;
        });
    }

    const buf = await wb.xlsx.writeBuffer();
    return new Blob([buf], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
}
