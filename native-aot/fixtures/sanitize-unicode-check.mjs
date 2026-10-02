import assert from "node:assert/strict";
import {sanitizeSurrogates} from "../optimizations/sanitize-unicode.js";
const original = text => text.replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, "");
const units = [0, 0x41, 0xd7ff, 0xd800, 0xd83d, 0xdbff, 0xdc00, 0xde00, 0xdfff, 0xe000, 0xffff];
let checked = 0;
for (const a of units) for (const b of units) for (const c of units) {
    const text = String.fromCharCode(a, b, c);
    assert.equal(sanitizeSurrogates(text), original(text)); checked++;
}
let seed = 0x12345678;
for (let i = 0; i < 5000; i++) {
    const codes = [];
    for (let j = 0; j < 64; j++) {
        seed ^= seed << 13; seed ^= seed >>> 17; seed ^= seed << 5;
        codes.push(seed & 0xffff);
    }
    const text = String.fromCharCode(...codes);
    assert.equal(sanitizeSurrogates(text), original(text)); checked++;
}
for (const text of ["", "ASCII".repeat(10000), "🙈🌏😀".repeat(1000), new String("boxed"),
    {replace() {return "custom";}}]) {
    assert.equal(sanitizeSurrogates(text), original(text)); checked++;
}
for (const text of [null, undefined, 123]) assert.throws(() => sanitizeSurrogates(text), TypeError);
console.log(JSON.stringify({checked, passed: true}));
