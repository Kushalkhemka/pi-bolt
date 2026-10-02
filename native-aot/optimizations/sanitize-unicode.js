// Pi removes invalid UTF-16 units. Well-formed primitive strings need no changes.
// Capture the intrinsic once so later extension changes do not affect the check.
const checkWellFormed = typeof String.prototype.isWellFormed === "function"
    ? Function.prototype.call.bind(String.prototype.isWellFormed) : null;
export function sanitizeSurrogates(text) {
    if (typeof text === "string" && checkWellFormed && checkWellFormed(text)) return text;
    return text.replace(/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/g, "");
}
