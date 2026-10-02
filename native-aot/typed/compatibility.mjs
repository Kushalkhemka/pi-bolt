import {createHash} from 'node:crypto';
import {piVersionPin} from './versions.mjs';

// Audited Node host boundary: isTTY is boolean on a TTY and undefined on a
// pipe. Pi's own implementation already handles both through truthiness.
export function piContractOverrides(ts, source, packageName, relative, version = '0.85.1') {
    if (packageName !== '@earendil-works/pi-coding-agent' || relative !== 'main.js') return [];
    const mainSHA256 = piVersionPin(version).tty_main_sha256;
    if (createHash('sha256').update(source).digest('hex') !== mainSHA256)
        throw new Error('Audited Pi main.ts host contract input changed');
    const file = ts.createSourceFile('main.ts', source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
    const functions = file.statements.filter(n => ts.isFunctionDeclaration(n) && n.name?.text === 'resolveAppMode');
    if (functions.length !== 1) throw new Error('Expected exactly one resolveAppMode boundary');
    const fn = functions[0];
    if (!fn.body || fn.parameters.length !== 3 || fn.parameters[1].name.getText(file) !== 'stdinIsTTY' ||
        fn.parameters[2].name.getText(file) !== 'stdoutIsTTY' ||
        fn.parameters.slice(1).some(p => p.type?.kind !== ts.SyntaxKind.BooleanKeyword))
        throw new Error('Audited TTY boundary signature changed');
    const expected = `{
        if (parsed.mode === "rpc") { return "rpc"; }
        if (parsed.mode === "json") { return "json"; }
        if (parsed.print || !stdinIsTTY || !stdoutIsTTY) { return "print"; }
        return "interactive";
    }`;
    const printer = ts.createPrinter({removeComments: true});
    const expectedFile = ts.createSourceFile('shape.ts', `function shape() ${expected}`, ts.ScriptTarget.ES2022, true);
    if (printer.printNode(ts.EmitHint.Unspecified, fn.body, file) !==
        printer.printNode(ts.EmitHint.Unspecified, expectedFile.statements[0].body, expectedFile))
        throw new Error('Audited TTY truthiness/early-return implementation changed');
    return ['stdinIsTTY', 'stdoutIsTTY'].map(parameter => ({function_name: 'resolveAppMode', parameter,
        original_mask: 4, accepted_mask: 5, original_contract: 'boolean', accepted_contract: 'boolean | undefined',
        source_sha256: mainSHA256, reason: 'Node process.stdin/stdout.isTTY is undefined on non-TTY pipes; original Pi explicitly handles truthiness'}));
}
