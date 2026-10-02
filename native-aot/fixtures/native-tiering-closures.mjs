import assert from 'node:assert/strict';
import * as jsc from 'bun:jsc';

function makeScoped(seed, stride) {
    let state = seed;
    function hotScoped(delta = 1) { state += delta * stride; return state; }
    function snapshot() { return state; }
    return { hotScoped, snapshot };
}
function makeDual(seed) {
    let calls = 0;
    let constructs = 0;
    function hotDual(value, replacement) {
        if (new.target) {
            constructs++;
            this.value = seed + value;
            this.ordinal = constructs;
            return replacement;
        }
        calls++;
        return seed + value + calls;
    }
    function snapshot() { return [calls, constructs]; }
    return { hotDual, snapshot };
}
const left = makeScoped(10, 2);
const right = makeScoped(100, -3);
const dualLeft = makeDual(7);
const dualRight = makeDual(70);
for (const fn of [left.hotScoped, right.hotScoped, dualLeft.hotDual, dualRight.hotDual]) jsc.noInline(fn);
assert.notEqual(left.hotScoped, right.hotScoped);
assert.notEqual(dualLeft.hotDual, dualRight.hotDual);
assert.equal(left.hotScoped(2), 14);
assert.equal(right.hotScoped(2), 94);
assert.equal(Reflect.apply(dualLeft.hotDual, { label: 'left' }, [1]), 9);
assert.equal(Reflect.apply(dualRight.hotDual, { label: 'right' }, [1]), 72);
assert.deepEqual([new dualLeft.hotDual(5).value, new dualRight.hotDual(5).value], [12, 75]);
let expectedLeft = 14;
let expectedRight = 94;
let checksum = 0;
for (let i = 0; i < 30000; i++) {
    const delta = (i % 3) + 1;
    expectedLeft += delta * 2;
    expectedRight -= delta * 3;
    assert.equal(left.hotScoped(delta), expectedLeft);
    assert.equal(right.hotScoped(delta), expectedRight);
    const calledLeft = Reflect.apply(dualLeft.hotDual, null, [i]);
    const calledRight = Reflect.apply(dualRight.hotDual, undefined, [-i]);
    assert.equal(calledLeft, 7 + i + i + 2);
    assert.equal(calledRight, 72);
    const madeLeft = new dualLeft.hotDual(i);
    const madeRight = Reflect.construct(dualRight.hotDual, [-i]);
    assert.deepEqual([madeLeft.value, madeLeft.ordinal], [7 + i, i + 2]);
    assert.deepEqual([madeRight.value, madeRight.ordinal], [70 - i, i + 2]);
    checksum += calledLeft + calledRight;
    if (i === 4096) Bun.gc(true);
}
assert.deepEqual([left.snapshot(), right.snapshot()], [expectedLeft, expectedRight]);
assert.deepEqual(dualLeft.snapshot(), [30001, 30001]);
assert.deepEqual(dualRight.snapshot(), [30001, 30001]);
Bun.gc(true);
const third = makeScoped(500, 5);
const dualThird = makeDual(700);
assert.equal(third.hotScoped(2), 510);
assert.equal(left.hotScoped(), expectedLeft + 2);
assert.equal(right.hotScoped(), expectedRight - 3);
assert.deepEqual([third.snapshot(), left.snapshot(), right.snapshot()], [510, expectedLeft + 2, expectedRight - 3]);
assert.equal(Reflect.apply(dualThird.hotDual, null, [3]), 704);
assert.deepEqual([new dualThird.hotDual(3).value, dualThird.snapshot()], [703, [1, 1]]);
const replacement = { replacement: true };
assert.equal(Reflect.construct(dualLeft.hotDual, [9, replacement]), replacement);
assert.equal(new dualLeft.hotDual(9, 0).value, 16);
function Alternate() {}
const alternate = Reflect.construct(dualRight.hotDual, [9], Alternate);
assert.equal(alternate.value, 79);
assert.equal(Object.getPrototypeOf(alternate), Alternate.prototype);
Bun.gc(true);
assert.deepEqual([dualLeft.snapshot(), dualRight.snapshot(), dualThird.snapshot()], [[30001, 30003], [30001, 30002], [1, 1]]);
const compiles = {
    scopedLeft: jsc.numberOfDFGCompiles(left.hotScoped),
    scopedRight: jsc.numberOfDFGCompiles(right.hotScoped),
    scopedThird: jsc.numberOfDFGCompiles(third.hotScoped),
    dualLeft: jsc.numberOfDFGCompiles(dualLeft.hotDual),
    dualRight: jsc.numberOfDFGCompiles(dualRight.hotDual),
    dualThird: jsc.numberOfDFGCompiles(dualThird.hotDual),
};
if (process.env.EXPECT_NATIVE_TIERING === '1') {
    assert.ok(compiles.scopedLeft > 0 && compiles.dualLeft > 0, JSON.stringify(compiles));
    assert.equal(compiles.scopedLeft, compiles.scopedRight);
    assert.equal(compiles.scopedLeft, compiles.scopedThird);
    assert.equal(compiles.dualLeft, compiles.dualRight);
    assert.equal(compiles.dualLeft, compiles.dualThird);
}
console.log(JSON.stringify({ semantics: true, checksum, scopeStates: [third.snapshot(), left.snapshot(), right.snapshot()], constructorCounts: [dualLeft.snapshot(), dualRight.snapshot(), dualThird.snapshot()], compiles }));
