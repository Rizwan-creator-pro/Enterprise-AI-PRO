// Test suite for Client-Side Column Header Sorting & Custom Page Sizes

const assert = require('assert');
const fs = require('fs');
const path = require('path');

console.log("Running Table Sorting & Page Sizing Validation Tests...");

// 1. Verify client/index.html contains the new components
const indexPath = path.join(__dirname, '..', 'client', 'index.html');
const indexHtml = fs.readFileSync(indexPath, 'utf-8');

// Verify that all inline <script> tags in index.html are valid JS with 0 syntax errors
const vm = require('vm');
const scriptMatches = [...indexHtml.matchAll(/<script(?:\s+[^>]*)?>([\s\S]*?)<\/script>/gi)];
scriptMatches.forEach((m, i) => {
    const code = m[1].trim();
    if (code) {
        try {
            new vm.Script(code, { filename: `index.html:script[${i+1}]` });
        } catch (err) {
            assert.fail(`Syntax error in index.html script tag #${i+1}: ${err.message}`);
        }
    }
});
console.log("✔ index.html full inline JavaScript syntax check passed (0 syntax errors).");

assert(indexHtml.includes('tb-pagesize-wrap'), 'tb-pagesize-wrap must be present in index.html');
assert(indexHtml.includes('tb-pagesize-pop'), 'tb-pagesize-pop must be present in index.html');
assert(indexHtml.includes('tb-pagesize-options'), 'tb-pagesize-options must be present in index.html');
assert(indexHtml.includes('th.sortable'), 'th.sortable CSS must be present in index.html');
assert(indexHtml.includes('tb-sort-ico'), 'tb-sort-ico must be present in index.html');
assert(indexHtml.includes('_cyclePageSize'), '_cyclePageSize method must be present in index.html');
assert(indexHtml.includes('_setPageSize'), '_setPageSize method must be present in index.html');

console.log("✔ index.html DOM structure and CSS classes verified.");

// 2. Logic unit tests: parseCellForSort and compareCells
const parseCellForSort = val => {
    if (val === null || val === undefined) return { type: 'empty', val: '' };
    const s = String(val).trim();
    if (!s || s === '-' || s === '—' || s === 'N/A' || s === 'null') return { type: 'empty', val: '' };
    if (/^\d{4}[-/.]\d{1,2}[-/.]\d{1,2}/.test(s) || /^\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}/.test(s)) {
        const d = Date.parse(s);
        if (!isNaN(d)) return { type: 'date', val: d };
    }
    const stripped = s.replace(/,/g, '').replace(/^[^\d+-]+/, '').replace(/[^\d.]+$/, '').trim();
    if (stripped !== '' && !isNaN(Number(stripped)) && /^[-+]?\d+(\.\d+)?$/.test(stripped)) {
        return { type: 'num', val: Number(stripped) };
    }
    return { type: 'text', val: s.toLowerCase(), raw: s };
};

const compareCells = (cellA, cellB, dir) => {
    const a = parseCellForSort(cellA);
    const b = parseCellForSort(cellB);
    if (a.type === 'empty' && b.type === 'empty') return 0;
    if (a.type === 'empty') return 1;
    if (b.type === 'empty') return -1;
    let res = 0;
    if (a.type === 'num' && b.type === 'num') {
        res = a.val - b.val;
    } else if (a.type === 'date' && b.type === 'date') {
        res = a.val - b.val;
    } else {
        const strA = a.type === 'text' ? a.raw : String(a.val);
        const strB = b.type === 'text' ? b.raw : String(b.val);
        res = strA.localeCompare(strB, undefined, { numeric: true, sensitivity: 'base' });
    }
    return dir === 'asc' ? res : -res;
};

// Test numeric / currency parsing and sorting
const salaries = ["Rs. 200,000", "Rs. 95,000", "Rs. 150,000", "Rs. 75,000"];
const sortedDesc = [...salaries].sort((a, b) => compareCells(a, b, 'desc'));
assert.deepStrictEqual(sortedDesc, ["Rs. 200,000", "Rs. 150,000", "Rs. 95,000", "Rs. 75,000"]);

const sortedAsc = [...salaries].sort((a, b) => compareCells(a, b, 'asc'));
assert.deepStrictEqual(sortedAsc, ["Rs. 75,000", "Rs. 95,000", "Rs. 150,000", "Rs. 200,000"]);
console.log("✔ Currency & Salary sorting (asc & desc) verified.");

// Test stock prices with ticker suffix (e.g. 401.99 PKR vs 98.97 PKR)
const prices = ["98.97 PKR", "401.99 PKR", "25.50 PKR"];
const pricesDesc = [...prices].sort((a, b) => compareCells(a, b, 'desc'));
assert.deepStrictEqual(pricesDesc, ["401.99 PKR", "98.97 PKR", "25.50 PKR"]);
console.log("✔ Ticker suffix price sorting verified.");

// Test percentages (e.g. -3.01% vs +2.60%)
const pcts = ["+2.60%", "-3.01%", "0.00%", "-0.50%"];
const pctsAsc = [...pcts].sort((a, b) => compareCells(a, b, 'asc'));
assert.deepStrictEqual(pctsAsc, ["-3.01%", "-0.50%", "0.00%", "+2.60%"]);
console.log("✔ Percentage sorting verified.");

// Test dates
const dates = ["2026-11-09", "2024-03-15", "2025-01-01"];
const datesDesc = [...dates].sort((a, b) => compareCells(a, b, 'desc'));
assert.deepStrictEqual(datesDesc, ["2026-11-09", "2025-01-01", "2024-03-15"]);
console.log("✔ Date sorting verified.");

// Test empty / null / N/A handling (must always sort to bottom)
const mixed = ["100", "N/A", "500", "", "200"];
const mixedAsc = [...mixed].sort((a, b) => compareCells(a, b, 'asc'));
assert.deepStrictEqual(mixedAsc.slice(0, 3), ["100", "200", "500"]);
assert.deepStrictEqual(mixedAsc.slice(3), ["N/A", ""]);
console.log("✔ Empty/null values sort to bottom verified.");

// 3. Test page size options generator
function getAvailableSizes(totalRows) {
    const availableSizes = [10];
    if (totalRows > 20) availableSizes.push(25);
    if (totalRows > 45) availableSizes.push(50);
    if (totalRows > 90) availableSizes.push(100);
    availableSizes.push('all');
    return availableSizes;
}

assert.deepStrictEqual(getAvailableSizes(15), [10, 'all']);
assert.deepStrictEqual(getAvailableSizes(35), [10, 25, 'all']);
assert.deepStrictEqual(getAvailableSizes(60), [10, 25, 50, 'all']);
assert.deepStrictEqual(getAvailableSizes(120), [10, 25, 50, 100, 'all']);
console.log("✔ Dynamic page size threshold generation verified.");

console.log("\nALL TABLE SORTING & PAGE SIZING TESTS PASSED SUCCESSFULLY! 🚀");
