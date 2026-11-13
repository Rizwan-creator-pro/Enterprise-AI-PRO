const fs = require('fs');
const path = require('path');

const jsPath = path.resolve('client/static/js/export_pdf.js');
const content = fs.readFileSync(jsPath, 'utf8');
const match = content.match(/function sanitizeForPdf\([\s\S]*?\n\}/);
if (!match) {
    console.error('Could not find sanitizeForPdf');
    process.exit(1);
}

eval(match[0]);

const sample1 = '• Tesla equity \u202F ≈ \u202F 45 \u202F % • SpaceX equity \u202F ≈ \u202F 30 \u202F %';
const sample2 = 'Bernard\u202fArnault';
const sample3 = 'US\u202f$233\u202fB';
const sample4 = '• Bill\u202fGates (USA)';

const out1 = sanitizeForPdf(sample1);
const out2 = sanitizeForPdf(sample2);
const out3 = sanitizeForPdf(sample3);
const out4 = sanitizeForPdf(sample4);

console.log('Out 1:', out1);
console.log('Out 2:', out2);
console.log('Out 3:', out3);
console.log('Out 4:', out4);

if (out1.includes('/"H') || out2.includes('/') || out3.includes('/') || out4.includes('/')) {
    console.error('FAIL: Corrupt characters still present!');
    process.exit(1);
} else {
    console.log('SUCCESS: All samples cleaned perfectly without slashes or corrupted artifacts!');
}
