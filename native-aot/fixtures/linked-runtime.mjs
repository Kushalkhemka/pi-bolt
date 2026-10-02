// Regression for sidecar AOT: node:http initialization calls locally known functions
// before a StaticHeap would have installed their constants. Also exercises realms,
// escaping closures, constructors, regex code, exceptions and dynamic fallback.
import assert from 'node:assert/strict';
import http from 'node:http';
import { isIP } from 'node:net';
import { Readable } from 'node:stream';
import { runInNewContext } from 'node:vm';
import { Worker } from 'node:worker_threads';
function factory(prefix) {
    const label = `${prefix}:`;
    return value => label + value;
}
class Counter {
    constructor(value) { this.value = value; }
    add(delta) { this.value += delta; return this.value; }
}
// Ordinary call/construct semantics across the native entry adapters. Keep these
// bounded; this fixture verifies results rather than forcing a compilation tier.
function argumentShape(first, second = 'fallback', ...rest) {
    return { first, second, rest, argc: arguments.length };
}
function fixedArguments(first, second) {
    return [first, second, arguments.length, arguments[2]];
}
function ConstructorChoice(value, replacement) {
    this.value = value;
    this.argc = arguments.length;
    return replacement;
}
function ordinaryResult(value) {
    if (value < 0) throw new RangeError('ordinary negative value');
    return value === 0 ? undefined : { value };
}
function tailSum(remaining, total = 0) {
    if (remaining === 0) return total;
    return tailSum(remaining - 1, total + remaining);
}
assert.deepEqual(argumentShape(), { first: undefined, second: 'fallback', rest: [], argc: 0 });
assert.deepEqual(argumentShape(7, undefined), { first: 7, second: 'fallback', rest: [], argc: 2 });
assert.deepEqual(Reflect.apply(argumentShape, null, [7, 'present', true, { extra: 1 }]),
    { first: 7, second: 'present', rest: [true, { extra: 1 }], argc: 4 });
assert.deepEqual(fixedArguments(), [undefined, undefined, 0, undefined]);
assert.deepEqual(fixedArguments(3), [3, undefined, 1, undefined]);
assert.deepEqual(fixedArguments(3, 4, 5, 6), [3, 4, 4, 5]);
const missingConstructor = new ConstructorChoice();
assert.equal(missingConstructor.value, undefined);
assert.equal(missingConstructor.argc, 0);
assert.equal(Object.getPrototypeOf(missingConstructor), ConstructorChoice.prototype);
const primitiveConstructor = new ConstructorChoice(9, 5, 'extra');
assert.equal(primitiveConstructor.value, 9);
assert.equal(primitiveConstructor.argc, 3);
assert.equal(Object.getPrototypeOf(primitiveConstructor), ConstructorChoice.prototype);
const replacementConstructor = { replacement: true };
assert.equal(Reflect.construct(ConstructorChoice, [9, replacementConstructor]), replacementConstructor);
assert.equal(ordinaryResult(0), undefined);
assert.deepEqual(ordinaryResult(4), { value: 4 });
assert.throws(() => ordinaryResult(-1), { name: 'RangeError', message: 'ordinary negative value' });
assert.equal(tailSum(0), 0);
assert.equal(tailSum(128), 8256);
function recurse(n) { return n < 2 ? n : recurse(n - 1) + recurse(n - 2); }
const server = http.createServer((req, res) => {
    res.setHeader('Connection', 'close');
    res.end(factory('reply')(req.url));
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const response = await fetch(`http://127.0.0.1:${server.address().port}/ok`);
assert.equal(await response.text(), 'reply:/ok');
await new Promise(resolve => server.close(resolve));
assert.equal(isIP('127.0.0.1'), 4);
assert.equal(isIP('::1'), 6);
assert.equal(isIP('not an ip'), 0);
assert.equal(factory('outer')(9), 'outer:9');
assert.equal(new Counter(4).add(7), 11);
assert.equal(recurse(10), 55);
assert.deepEqual([...('a12 b34').matchAll(/([a-z])(\d+)/g)].map(x => x[2]), ['12', '34']);
assert.equal(new RegExp('^a+$').test('aaa'), true);
// Builtin sidecar selection must preserve sparse arrays, callbacks, species, mutation and GC.
const sparse = [1, , 3];
const callbackReceiver = { delta: 4 };
const mapped = sparse.map(function(value, index, array) {
    assert.equal(array, sparse);
    if (index === 0) { array[1] = 2; Bun.gc(true); }
    return value + this.delta;
}, callbackReceiver);
assert.deepEqual(mapped, [5, 6, 7]);
assert.deepEqual([1, , 3].map(x => x * 2), [2, , 6]);
assert.deepEqual([1, 2, 3, 4].filter(x => x % 2 === 0), [2, 4]);
assert.equal([1, 2, 3].reduce((sum, x) => sum + x, 0), 6);
assert.equal([1, 2, 3].every(x => x > 0), true);
assert.equal([1, 2, 3].find(x => x > 1), 2);
// Argument-count-sensitive paths and errors are different from an explicit seed.
assert.equal([, , 2, 3].reduce((sum, x) => sum + x), 5);
assert.equal([7].reduce((sum, x) => `${sum}:${x}`, undefined), 'undefined:7');
assert.throws(() => [].reduce((sum, x) => sum + x), TypeError);
assert.throws(() => Array(3).reduce((sum, x) => sum + x), TypeError);
assert.throws(() => Array.prototype.map.call(null, x => x), TypeError);
assert.throws(() => [1].map(null), TypeError);
assert.throws(() => [1].reduce(null, 0), TypeError);
const everyIndices = [];
assert.equal([1, 2, 3].every((x, index) => { everyIndices.push(index); return x < 2; }), false);
assert.deepEqual(everyIndices, [0, 1]);
const findVisits = [];
assert.equal(Array(2).find((value, index) => { findVisits.push([index, value]); return index === 1; }), undefined);
assert.deepEqual(findVisits, [[0, undefined], [1, undefined]]);
// Generic receivers must retain HasProperty/Get order, including skipped holes.
const proxyOperations = [];
const proxyArrayLike = new Proxy({ 0: 4, 2: 6, length: 3 }, {
    has(target, key) { proxyOperations.push(`has:${String(key)}`); return Reflect.has(target, key); },
    get(target, key, receiver) { proxyOperations.push(`get:${String(key)}`); return Reflect.get(target, key, receiver); },
});
assert.deepEqual(Array.prototype.map.call(proxyArrayLike, (value, _index, receiver) => {
    assert.equal(receiver, proxyArrayLike);
    return value * 2;
}), [8, , 12]);
assert.deepEqual(proxyOperations, ['get:length', 'has:0', 'get:0', 'has:1', 'has:2', 'get:2']);
assert.deepEqual([1, 2].map(value => [value, value + 1].map(x => {
    Bun.gc(true);
    return x * 2;
}).reduce((sum, x) => sum + x, 0)), [6, 10]);
assert.throws(() => [1].map(() => { throw new Error('builtin-callback'); }), /builtin-callback/);
let speciesReads = 0;
class DerivedArray extends Array { static get [Symbol.species]() { ++speciesReads; Bun.gc(true); return Array; } }
assert.equal(new DerivedArray(1, 2).map(x => x).constructor, Array);
assert.equal(speciesReads, 1);
assert.deepEqual(Array.from(new Set([1, 2]), x => x + 1), [2, 3]);
const iterated = [1, 2];
const iterator = iterated.values();
assert.equal(iterator.next().value, 1);
iterated.push(3); Bun.gc(true);
assert.deepEqual([iterator.next().value, iterator.next().value, iterator.next().done], [2, 3, true]);
assert.equal(await Promise.resolve(7).then(x => Promise.resolve(x + 1)), 8);
// Bun's next-tick initializer and nested helpers have a different source family
// from JSC's Array/Promise builtins. Enter from an event-loop callback so the
// tick-before-microtask ordering does not depend on module evaluation context.
assert.throws(() => process.nextTick(null), TypeError);
const tickOrder = [];
const tickArgument = { retained: true };
await new Promise(resolve => setImmediate(() => {
    process.nextTick((a, b, c, d, e) => {
        assert.deepEqual([a, b, c, d, e], ['argument', 2, tickArgument, null, undefined]);
        assert.equal(c, tickArgument);
        tickOrder.push('tick');
        Bun.gc(true);
        process.nextTick(() => tickOrder.push('nested-tick'));
        queueMicrotask(() => tickOrder.push('microtask-from-tick'));
    }, 'argument', 2, tickArgument, null, undefined);
    queueMicrotask(() => {
        tickOrder.push('microtask');
        process.nextTick(() => tickOrder.push('tick-from-microtask'));
    });
    setImmediate(resolve);
}));
assert.deepEqual(tickOrder, ['tick', 'nested-tick', 'microtask', 'microtask-from-tick', 'tick-from-microtask']);
assert.equal(runInNewContext('[1, 2, 3].map(x => x + 1).reduce((a, b) => a + b, 0)'), 9);
// Shared native text must use each realm's constants and callback receiver.
const realmResult = runInNewContext(`(() => {
    const receiver = { delta: 5 };
    const input = [1, 2];
    let correctReceiver = true;
    let correctInput = true;
    const output = input.map(function(value, _index, array) {
        'use strict';
        correctReceiver = correctReceiver && this === receiver;
        correctInput = correctInput && array === input;
        return value + this.delta;
    }, receiver);
    return { ownArray: Object.getPrototypeOf(output) === Array.prototype,
        correctReceiver, correctInput, total: output.reduce((a, b) => a + b, 0) };
})()`);
assert.equal(realmResult.ownArray, true);
assert.equal(realmResult.correctReceiver, true);
assert.equal(realmResult.correctInput, true);
assert.equal(realmResult.total, 13);
const foreignArray = runInNewContext('[3, 4]');
const localMappedForeign = Array.prototype.map.call(foreignArray, x => x * 2);
assert.equal(Object.getPrototypeOf(localMappedForeign), Array.prototype);
assert.deepEqual(localMappedForeign, [6, 8]);
// Two concurrent VMs share sidecar text but must keep their builtin constants,
// next-tick queues and GC state separate. The data-URL application is dynamic
// fallback code; its builtin calls can still select the compiled source family.
const workerSource = `import { parentPort, workerData, threadId } from 'node:worker_threads';
const seed = workerData;
const mapped = [seed, seed + 1, seed + 2, seed + 3].map(value => {
    Bun.gc(true);
    return value * 2;
});
const filtered = mapped.filter(value => value % 4 === 0);
const total = filtered.reduce((sum, value) => sum + value, 0);
const last = mapped.findLast(value => value % 4 === 0);
const tickOrder = [];
await new Promise(resolve => setImmediate(() => {
    process.nextTick(value => {
        tickOrder.push('tick:' + value);
        Bun.gc(true);
        process.nextTick(() => tickOrder.push('nested-tick'));
    }, seed);
    queueMicrotask(() => tickOrder.push('microtask'));
    setImmediate(resolve);
}));
Bun.gc(true);
parentPort.postMessage({ seed, filtered, total, last, tickOrder, gc: true, threadId });
parentPort.close();`;
const workerURL = new URL('data:text/javascript,' + encodeURIComponent(workerSource));
const workers = [];
let workerResults;
try {
    workerResults = await Promise.all([3, 7].map(seed => new Promise((resolve, reject) => {
        const worker = new Worker(workerURL, { workerData: seed });
        workers.push(worker);
        const messages = [];
        const timeout = setTimeout(() => {
            void worker.terminate();
            reject(new Error('Builtin worker regression timed out'));
        }, 10000);
        worker.on('message', message => messages.push(message));
        worker.once('error', error => { clearTimeout(timeout); reject(error); });
        worker.once('exit', code => {
            clearTimeout(timeout);
            try {
                assert.equal(code, 0);
                assert.equal(messages.length, 1);
                const message = messages[0];
                assert.equal(Number.isInteger(message.threadId) && message.threadId > 0, true);
                assert.deepEqual(message, { seed, filtered: seed === 3 ? [8, 12] : [16, 20],
                    total: seed === 3 ? 20 : 36, last: seed === 3 ? 12 : 20,
                    tickOrder: ['tick:' + seed, 'nested-tick', 'microtask'], gc: true,
                    threadId: message.threadId });
                resolve(message);
            } catch (error) { reject(error); }
        });
    })));
} finally {
    await Promise.all(workers.map(worker => worker.terminate()));
}
assert.notEqual(workerResults[0].threadId, workerResults[1].threadId);
assert.equal(Array.prototype.map.name, 'map');
assert.equal(Array.prototype.map.length, 1);
assert.match(Function.prototype.toString.call(Array.prototype.map), /\[native code\]/);
assert.throws(() => { throw new TypeError('linked-error'); }, /linked-error/);
const original = { map: new Map([['a', [1, 2]]]), bytes: new Uint8Array([3, 4]) };
const copy = structuredClone(original);
copy.map.get('a').push(5);
assert.equal(original.map.get('a').length, 2);
assert.deepEqual([...copy.bytes], [3, 4]);
let text = '';
for await (const chunk of Readable.from(['a', 'b'])) text += chunk;
assert.equal(text, 'ab');
assert.equal(new Function('x', 'return x + 3')(4), 7);
const dynamic = await import('data:text/javascript,export default x => x * 2');
assert.equal(dynamic.default(8), 16);
for (let i = 0; i < 3; i++) { Bun.gc(true); assert.equal(factory('gc')(i), `gc:${i}`); }
// GC finalization must format weak native frames without creating a CodeBlock.
function makeUnformattedError() {
    const nativeErrorFrame = function nativeErrorFrame() { return new Error('native-finalizer-error'); };
    return { error: nativeErrorFrame(), weakCallee: new WeakRef(nativeErrorFrame) };
}
const unformattedErrors = Array.from({ length: 8 }, makeUnformattedError);
async function nativeErrorChildFrame() {
    await Promise.resolve();
    return new Error('native-async-finalizer-error');
}
async function nativeErrorParentFrame() {
    return await nativeErrorChildFrame();
}
const unformattedAsyncError = await nativeErrorParentFrame();
let calleesReclaimed = false;
for (let attempt = 0; attempt < 8; attempt++) {
    await new Promise(resolve => setImmediate(resolve));
    Bun.gc(true);
    calleesReclaimed = unformattedErrors.every(record => record.weakCallee.deref() === undefined);
    if (calleesReclaimed) break;
}
assert.equal(calleesReclaimed, true);
assert.match(unformattedAsyncError.stack, /^Error: native-async-finalizer-error\n/);
assert.match(unformattedAsyncError.stack, /nativeErrorParentFrame/);
assert.match(unformattedAsyncError.stack, /nativeErrorParentFrame.*linked-runtime/);
for (const { error } of unformattedErrors) {
    assert.match(error.stack, /^Error: native-finalizer-error\n/);
    assert.match(error.stack, /nativeErrorFrame/);
    assert.match(error.stack, /linked-runtime/);
}
console.log(JSON.stringify({ http: true, closures: true, constructors: true, regex: true,
    exceptions: true, clone: true, stream: true, dynamic: true, gc: true, error_finalization: true,
    builtin_validation: true, bun_builtin_validation: true, worker_builtin_validation: true,
    call_semantics_validation: true }));
