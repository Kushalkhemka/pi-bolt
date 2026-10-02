// The shell's native-code check must be performed after each function is called.
function sum(values) {
    let total = 0;
    for (let i = 0; i < values.length; i++) total += values[i];
    return total;
}

function makeCounter(initial) {
    return function increment(amount) {
        initial += amount;
        return initial;
    };
}

function caughtMessage() {
    try { throw new Error("native-smoke"); }
    catch (error) { return error.message; }
}

function fibonacci(n) {
    return n < 2 ? n : fibonacci(n - 1) + fibonacci(n - 2);
}

const counter = makeCounter(4);
const result = [sum(new Float64Array([1, 2, 3, 4])), counter(3), counter(-1),
    caughtMessage(), fibonacci(10), JSON.parse('{"value":9}').value];
if (JSON.stringify(result) !== '[10,7,6,"native-smoke",55,9]') {
    throw new Error("Incorrect backend results: " + JSON.stringify(result));
}

const functions = [sum, makeCounter, counter, caughtMessage, fibonacci];
const native = functions.map(function(fn) { return isAOTCompiled(fn); });
if (native.some(function(value) { return value; }) &&
    !native.every(function(value) { return value; })) {
    throw new Error("Only some smoke functions used native AOT: " + JSON.stringify(native));
}
print(JSON.stringify({ result: result, native: native }));
