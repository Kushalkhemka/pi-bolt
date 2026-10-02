import assert from 'node:assert/strict';
import * as jsc from 'bun:jsc';

function scalarAdd(x: number, y: number): number { return x + y; }
function unionIdentity(value: string | undefined): string | undefined { return value; }
function capturedScalar() {
    let value: number = 1;
    return {get: () => value, set: (next: unknown) => { value = next as number; }};
}
function compoundScalar() {
    let value: number = 2;
    let calls = 0;
    const rhs = () => { calls++; return 3; };
    value += rhs();
    const old = value++;
    const current = ++value;
    return [value, old, current, calls];
}
function uncheckedValue(): any { return 'wrong'; }
function badScalarReturn(): number { return uncheckedValue(); }
function typedShadow() { const value: number = 2; return () => { const value: string = 'shadow'; return value; }; }
function scalarDefault(value: number = 7): number { return value; }
function nullableScalar(value: number | null): number | null { return value; }
class ScalarBox {
    constructor(readonlyIgnored: unknown, value: number) { this.value = value; }
    value = 0;
    add(value: number): number { return this.value + value; }
}
jsc.noInline(scalarAdd);
let checksum = 0;
for (let i = 0; i < 30000; i++) checksum += scalarAdd(i & 255, 3);
assert.equal(checksum, 3910008);
const dfgCompiles = jsc.numberOfDFGCompiles(scalarAdd);
if (process.env.EXPECT_TYPED_NATIVE_TIERING === '1') assert.ok(dfgCompiles > 0, `typed scalar DFG compiles: ${dfgCompiles}`);
let failures = 0;
// Reflect bypasses any checked assertion/call-site conversion: these failures
// must come from the checked callee entry/return, including after DFG promotion.
for (const attempt of [() => Reflect.apply(scalarAdd, null, ['wrong', 1]), () => badScalarReturn(),
    () => Reflect.apply(unionIdentity, null, [1]), () => Reflect.construct(ScalarBox, [null, 'wrong'])]) {
    assert.throws(attempt, {name: 'TypeError'}); failures++;
}
assert.equal(scalarAdd(10, 11), 21);
assert.ok(Number.isNaN(scalarAdd(NaN, 1)));
assert.equal(scalarAdd(Infinity, 1), Infinity);
assert.equal(unionIdentity(undefined), undefined);
assert.equal(unionIdentity('good'), 'good');
assert.equal(nullableScalar(null), null);
assert.equal(nullableScalar(2), 2);
assert.equal(scalarDefault(), 7);
const captured = capturedScalar(); captured.set(6); assert.equal(captured.get(), 6);
assert.throws(() => captured.set('wrong'), {name: 'TypeError'}); assert.equal(captured.get(), 6);
assert.deepEqual(compoundScalar(), [7, 5, 7, 1]);
assert.equal(typedShadow()(), 'shadow');
assert.equal(new ScalarBox(null, 2).add(3), 5);
console.log(JSON.stringify({primitive_contract_validation: true, scalar_result: 21, captured: captured.get(), failures, checksum, dfg_compiles: dfgCompiles}));
