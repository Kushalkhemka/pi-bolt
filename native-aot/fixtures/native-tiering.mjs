import assert from 'node:assert/strict';
import * as jsc from 'bun:jsc';
function hotValue(value, extra = 3) { return value * 2 + extra; }
function hotArgs(first, ...rest) { return [first, rest.length, arguments.length]; }
function hotConstruct(value, replacement) { this.value = value; return replacement; }
function hotRecursive(depth) { return depth ? hotRecursive(depth - 1) + 1 : 0; }
function hotThrows(value) { if (value < 0) throw new RangeError('tier-error'); return value + 1; }
function suspendedNative(value) {
    const before = value.marker;
    for (let i = 0; i < 200; i++) assert.equal(hotRecursive(6), 6);
    Bun.gc(true);
    return [before, value.marker, hotValue(5)];
}
for (const fn of [hotValue, hotArgs, hotConstruct, hotRecursive, hotThrows]) jsc.noInline(fn);
assert.deepEqual(suspendedNative({marker: 'alive'}), ['alive', 'alive', 13]);
let checksum = 0;
for (let i = 0; i < 30000; i++) {
    checksum += hotValue(i & 255);
    assert.deepEqual(hotArgs(i, 'x', 'y'), [i, 2, 3]);
    assert.equal(new hotConstruct(i).value, i);
    assert.equal(hotRecursive(6), 6);
    assert.equal(hotThrows(i), i + 1);
}
assert.equal(checksum, 7730016);
assert.deepEqual(hotArgs(), [undefined, 0, 0]);
assert.equal(hotValue(2, undefined), 7);
assert.equal(Reflect.apply(hotValue, null, [2, 9, 'extra']), 13);
const replacement = { replacement: true };
assert.equal(Reflect.construct(hotConstruct, [9, replacement]), replacement);
assert.throws(() => hotThrows(-1), {name: 'RangeError', message: 'tier-error'});
Bun.gc(true);
assert.equal(hotValue(9), 21);
const compiles = Object.fromEntries([hotValue, hotArgs, hotConstruct, hotRecursive, hotThrows].map(fn => [fn.name, jsc.numberOfDFGCompiles(fn)]));
if (process.env.EXPECT_NATIVE_TIERING === '1') {
    assert.ok(compiles.hotValue > 0 && compiles.hotRecursive > 0, JSON.stringify(compiles));
}
console.log(JSON.stringify({ checksum, semantics: true, compiles }));
