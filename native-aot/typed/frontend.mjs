import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const defaultCompiler = new URL('../sources/bun/node_modules/typescript/lib/typescript.js', import.meta.url);
export function typeScriptCompilerPath(filename = defaultCompiler.pathname) {
    return require.resolve(path.resolve(filename));
}
export function loadTypeScript(filename) {
    const ts = require(typeScriptCompilerPath(filename));
    if (ts.version !== '6.0.2') throw new Error(`Typed subset requires TypeScript 6.0.2, found ${ts.version}`);
    return ts;
}

export const compilerOptions = ts => ({target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext,
    useDefineForClassFields: true, experimentalDecorators: true, emitDecoratorMetadata: true,
    rewriteRelativeImportExtensions: true, removeComments: false});

function maskFor(ts, type) {
    if (!type) return null;
    if (ts.isParenthesizedTypeNode(type)) return maskFor(ts, type.type);
    if (ts.isUnionTypeNode(type)) {
        const masks = type.types.map(t => maskFor(ts, t));
        return masks.every(m => m !== null) ? masks.reduce((a, b) => a | b, 0) : null;
    }
    if (ts.isLiteralTypeNode(type) && type.literal.kind === ts.SyntaxKind.NullKeyword) return 2;
    return new Map([[ts.SyntaxKind.UndefinedKeyword, 1], [ts.SyntaxKind.VoidKeyword, 1],
        [ts.SyntaxKind.BooleanKeyword, 4], [ts.SyntaxKind.NumberKeyword, 8],
        [ts.SyntaxKind.StringKeyword, 16], [ts.SyntaxKind.SymbolKeyword, 32],
        [ts.SyntaxKind.BigIntKeyword, 64]]).get(type.kind) ?? null;
}

export function normalizedJavaScript(ts, source, filename = 'input.js') {
    const file = ts.createSourceFile(filename, source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.JS);
    if (file.parseDiagnostics.length) throw new Error(`${filename}: invalid JavaScript`);
    const result = ts.transform(file, [context => {
        const visit = node => ts.isStringLiteral(node)
            ? context.factory.createStringLiteral(node.text)
            : ts.visitEachChild(node, visit, context);
        return root => ts.visitNode(root, visit);
    }]);
    try { return ts.createPrinter({removeComments: true}).printFile(result.transformed[0]); }
    finally { result.dispose(); }
}

export function eraseTypeScript(ts, source, filename) {
    const result = ts.transpileModule(source, {fileName: filename, compilerOptions: compilerOptions(ts), reportDiagnostics: true});
    const errors = result.diagnostics?.filter(d => d.category === ts.DiagnosticCategory.Error) ?? [];
    if (errors.length) throw new Error(ts.formatDiagnosticsWithColorAndContext(errors, {
        getCurrentDirectory: () => process.cwd(), getCanonicalFileName: p => p, getNewLine: () => '\n'}));
    return result.outputText;
}

// This is an explicit coarse runtime contract, not a TypeScript semantic checker.
export function checkedTypeScript(ts, source, filename, {contractOverrides = []} = {}) {
    const file = ts.createSourceFile(filename, source, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TS);
    const host = {getSourceFile: p => p === filename ? file : undefined,
        getDefaultLibFileName: () => '', writeFile() {}, getCurrentDirectory: () => process.cwd(),
        getDirectories: () => [], fileExists: p => p === filename,
        readFile: p => p === filename ? source : undefined, getCanonicalFileName: p => p,
        useCaseSensitiveFileNames: () => true, getNewLine: () => '\n'};
    const program = ts.createProgram([filename], {...compilerOptions(ts), noLib: true, noResolve: true}, host);
    const checker = program.getTypeChecker();
    const bindings = new Map();
    const report = {schema: 'primitive-checked-subset-v1', bindings: 0, entry_checks: 0,
        read_checks: 0, write_checks: 0, return_checks: 0, assertion_checks: 0, skipped: [], contract_overrides: []};
    const location = node => {
        const p = file.getLineAndCharacterOfPosition(node.getStart(file));
        return `${filename}:${p.line + 1}:${p.character + 1}`;
    };
    const skip = (node, reason) => report.skipped.push({location: location(node), reason, type: node.type?.getText(file) ?? null});
    const scan = node => {
        if (ts.isIdentifier(node) && node.text === '$$t') throw new Error(`${location(node)}: $$t is reserved for the checked intrinsic`);
        if (ts.isPropertyDeclaration(node) && node.type) skip(node, 'Field contract is unsupported; remains generic');
        if ((ts.isParameter(node) || ts.isVariableDeclaration(node)) && node.type) {
            let ambient = file.isDeclarationFile;
            for (let parent = node; parent && !ambient; parent = parent.parent)
                ambient = parent.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.DeclareKeyword) ?? false;
            if (ambient) {
                skip(node, 'Ambient declaration is not an executable binding; remains generic');
                ts.forEachChild(node, scan);
                return;
            }
            if (ts.isParameter(node) && !node.parent.body) {
                skip(node, 'Declaration-only parameter is not an executable contract');
                ts.forEachChild(node, scan);
                return;
            }
            let mask = maskFor(ts, node.type);
            const override = ts.isParameter(node) && ts.isIdentifier(node.name) && ts.isFunctionDeclaration(node.parent)
                ? contractOverrides.find(item => item.function_name === node.parent.name?.text && item.parameter === node.name.text)
                : undefined;
            if (override) {
                if (mask !== override.original_mask || !Number.isInteger(override.accepted_mask) || override.accepted_mask < 1 || override.accepted_mask > 127)
                    throw new Error(`${location(node)}: invalid audited primitive contract override`);
                mask = override.accepted_mask;
                report.contract_overrides.push({...override, location: location(node)});
            }
            if (ts.isParameter(node) && node.questionToken && mask !== null) mask |= 1;
            if (mask === null || !ts.isIdentifier(node.name) || node.dotDotDotToken) skip(node, 'Unsupported binding annotation; remains generic');
            else {
                const symbol = checker.getSymbolAtLocation(node.name);
                if (!symbol) throw new Error(`${location(node)}: cannot resolve local binding`);
                bindings.set(symbol, {mask, declaration: node});
            }
        }
        ts.forEachChild(node, scan);
    };
    scan(file);
    if (report.contract_overrides.length !== contractOverrides.length) throw new Error(`${filename}: audited contract override did not match exactly`);
    const compound = new Map([
        [ts.SyntaxKind.PlusEqualsToken, ts.SyntaxKind.PlusToken], [ts.SyntaxKind.MinusEqualsToken, ts.SyntaxKind.MinusToken],
        [ts.SyntaxKind.AsteriskEqualsToken, ts.SyntaxKind.AsteriskToken], [ts.SyntaxKind.SlashEqualsToken, ts.SyntaxKind.SlashToken],
        [ts.SyntaxKind.PercentEqualsToken, ts.SyntaxKind.PercentToken], [ts.SyntaxKind.AsteriskAsteriskEqualsToken, ts.SyntaxKind.AsteriskAsteriskToken],
        [ts.SyntaxKind.AmpersandEqualsToken, ts.SyntaxKind.AmpersandToken], [ts.SyntaxKind.BarEqualsToken, ts.SyntaxKind.BarToken],
        [ts.SyntaxKind.CaretEqualsToken, ts.SyntaxKind.CaretToken], [ts.SyntaxKind.LessThanLessThanEqualsToken, ts.SyntaxKind.LessThanLessThanToken],
        [ts.SyntaxKind.GreaterThanGreaterThanEqualsToken, ts.SyntaxKind.GreaterThanGreaterThanToken],
        [ts.SyntaxKind.GreaterThanGreaterThanGreaterThanEqualsToken, ts.SyntaxKind.GreaterThanGreaterThanGreaterThanToken]]);
    const assignment = kind => kind >= ts.SyntaxKind.FirstAssignment && kind <= ts.SyntaxKind.LastAssignment;
    const bindingSymbol = node => {
        if (!ts.isIdentifier(node)) return undefined;
        // A shorthand property identifier has a property symbol distinct from
        // the captured/local value symbol. Guard the value being read.
        return ts.isShorthandPropertyAssignment(node.parent) && node.parent.name === node
            ? checker.getShorthandAssignmentValueSymbol(node.parent)
            : checker.getSymbolAtLocation(node);
    };
    const binding = node => bindings.get(bindingSymbol(node));
    const disableTargets = (node, reason) => {
        if (ts.isIdentifier(node)) {
            const symbol = bindingSymbol(node);
            const item = bindings.get(symbol);
            if (item) { skip(item.declaration, reason); bindings.delete(symbol); }
        } else ts.forEachChild(node, child => disableTargets(child, reason));
    };
    const preflight = node => {
        if (ts.isBinaryExpression(node) && assignment(node.operatorToken.kind)) {
            if (!ts.isIdentifier(node.left) && !ts.isPropertyAccessExpression(node.left) && !ts.isElementAccessExpression(node.left))
                disableTargets(node.left, 'Destructuring assignment is unsupported; binding remains generic');
            else if (binding(node.left) && node.operatorToken.kind !== ts.SyntaxKind.EqualsToken && !compound.has(node.operatorToken.kind))
                disableTargets(node.left, 'Logical assignment is unsupported; binding remains generic');
        }
        if (ts.isForOfStatement(node) || ts.isForInStatement(node))
            disableTargets(node.initializer, 'Loop assignment is unsupported; binding remains generic');
        if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) &&
            [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)) {
            // An update target must remain an lvalue. Do not insert a read guard
            // inside a parenthesized target or a destructured/other target shape.
            if (!ts.isIdentifier(node.operand) && !ts.isPropertyAccessExpression(node.operand) && !ts.isElementAccessExpression(node.operand))
                disableTargets(node.operand, 'Wrapped update target is unsupported; binding remains generic');
            const item = binding(node.operand);
            if (item && (item.mask & ~(8 | 64))) disableTargets(node.operand, 'Update requires number/bigint-only contract; binding remains generic');
        }
        ts.forEachChild(node, preflight);
    };
    preflight(file);
    report.bindings = bindings.size;
    const transformer = context => {
        const f = context.factory;
        let returnMask = null;
        const check = (expr, mask, counter) => {
            report[counter]++;
            return f.createCallExpression(f.createIdentifier('$$t'), undefined, [expr, f.createNumericLiteral(mask)]);
        };
        const isRead = node => {
            const p = node.parent;
            if (!p) return false;
            if ((ts.isPropertyAccessExpression(p) && p.name === node) ||
                (ts.isPropertyAssignment(p) && p.name === node) ||
                (ts.isBindingElement(p) && (p.name === node || p.propertyName === node)) ||
                (ts.isVariableDeclaration(p) && p.name === node) || (ts.isParameter(p) && p.name === node) ||
                (ts.isFunctionLike(p) && p.name === node) || (ts.isClassDeclaration(p) && p.name === node) ||
                (ts.isPropertyDeclaration(p) && p.name === node) || ts.isImportClause(p) || ts.isImportSpecifier(p) ||
                ts.isExportSpecifier(p) || ts.isNamespaceImport(p) || ts.isLabeledStatement(p) ||
                ts.isBreakStatement(p) || ts.isContinueStatement(p)) return false;
            return true;
        };
        const visit = node => {
            if (ts.isTypeNode(node)) return node;
            if (ts.isFunctionLike(node) && node.body) {
                const previous = returnMask;
                const async = node.modifiers?.some(m => m.kind === ts.SyntaxKind.AsyncKeyword);
                returnMask = async || node.asteriskToken ? null : maskFor(ts, node.type);
                if (node.type && returnMask === null) skip(node, 'Unsupported/async/generator return contract; remains generic');
                let result = ts.visitEachChild(node, visit, context);
                const entries = [];
                for (const p of node.parameters) {
                    const item = binding(p.name);
                    if (item) entries.push(f.createExpressionStatement(check(f.createIdentifier(p.name.text), item.mask, 'entry_checks')));
                }
                const body = result.body;
                if (ts.isBlock(body)) {
                    let index = 0;
                    while (index < body.statements.length && ts.isExpressionStatement(body.statements[index]) && ts.isStringLiteral(body.statements[index].expression)) index++;
                    const statements = [...body.statements.slice(0, index), ...entries, ...body.statements.slice(index)];
                    if (returnMask !== null) statements.push(f.createReturnStatement(check(f.createVoidZero(), returnMask, 'return_checks')));
                    const updated = f.updateBlock(body, statements);
                    if (ts.isFunctionDeclaration(result)) result = f.updateFunctionDeclaration(result, result.modifiers, result.asteriskToken, result.name, result.typeParameters, result.parameters, result.type, updated);
                    else if (ts.isFunctionExpression(result)) result = f.updateFunctionExpression(result, result.modifiers, result.asteriskToken, result.name, result.typeParameters, result.parameters, result.type, updated);
                    else if (ts.isMethodDeclaration(result)) result = f.updateMethodDeclaration(result, result.modifiers, result.asteriskToken, result.name, result.questionToken, result.typeParameters, result.parameters, result.type, updated);
                    else if (ts.isGetAccessorDeclaration(result)) result = f.updateGetAccessorDeclaration(result, result.modifiers, result.name, result.parameters, result.type, updated);
                    else if (ts.isSetAccessorDeclaration(result)) result = f.updateSetAccessorDeclaration(result, result.modifiers, result.name, result.parameters, updated);
                    else if (ts.isConstructorDeclaration(result)) result = f.updateConstructorDeclaration(result, result.modifiers, result.parameters, updated);
                    else if (ts.isArrowFunction(result)) result = f.updateArrowFunction(result, result.modifiers, result.typeParameters, result.parameters, result.type, result.equalsGreaterThanToken, updated);
                    else throw new Error(`${location(node)}: unsupported function body`);
                } else if (ts.isArrowFunction(result)) {
                    const expr = returnMask === null ? body : check(body, returnMask, 'return_checks');
                    result = f.updateArrowFunction(result, result.modifiers, result.typeParameters, result.parameters, result.type, result.equalsGreaterThanToken,
                        f.createBlock([...entries, f.createReturnStatement(expr)], true));
                }
                returnMask = previous;
                return result;
            }
            if (ts.isReturnStatement(node) && returnMask !== null)
                return f.updateReturnStatement(node, check(node.expression ? ts.visitNode(node.expression, visit) : f.createVoidZero(), returnMask, 'return_checks'));
            if (ts.isAsExpression(node) || ts.isTypeAssertionExpression(node)) {
                const mask = maskFor(ts, node.type);
                const expr = ts.visitNode(node.expression, visit);
                if (mask !== null) return check(expr, mask, 'assertion_checks');
                skip(node, 'Unsupported asserted type; remains generic');
                return f.updateAsExpression && ts.isAsExpression(node) ? f.updateAsExpression(node, expr, node.type) : f.updateTypeAssertion(node, node.type, expr);
            }
            if (ts.isVariableDeclaration(node)) {
                const item = binding(node.name);
                const init = node.initializer ? ts.visitNode(node.initializer, visit) : undefined;
                return f.updateVariableDeclaration(node, node.name, node.exclamationToken, node.type,
                    item && init ? check(init, item.mask, 'write_checks') : init);
            }
            if (ts.isBinaryExpression(node) && binding(node.left) && assignment(node.operatorToken.kind)) {
                const item = binding(node.left);
                const rhs = ts.visitNode(node.right, visit);
                const value = node.operatorToken.kind === ts.SyntaxKind.EqualsToken ? rhs
                    : f.createBinaryExpression(check(node.left, item.mask, 'read_checks'), compound.get(node.operatorToken.kind), rhs);
                return f.createBinaryExpression(node.left, ts.SyntaxKind.EqualsToken, check(value, item.mask, 'write_checks'));
            }
            if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) && binding(node.operand) &&
                [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)) {
                const item = binding(node.operand);
                return f.createParenthesizedExpression(f.createCommaListExpression([check(node.operand, item.mask, 'read_checks'), node]));
            }
            if (ts.isShorthandPropertyAssignment(node) && binding(node.name)) {
                const item = binding(node.name);
                return f.createPropertyAssignment(node.name, check(node.name, item.mask, 'read_checks'));
            }
            if (ts.isIdentifier(node) && isRead(node)) {
                const item = binding(node);
                if (item) return check(node, item.mask, 'read_checks');
            }
            return ts.visitEachChild(node, visit, context);
        };
        return root => ts.visitNode(root, visit);
    };
    // Preserve the exact tree associated with the binder; transpileModule would reparse it.
    const transformed = ts.transform(file, [transformer]);
    let intermediate;
    try { intermediate = ts.createPrinter().printFile(transformed.transformed[0]); }
    finally { transformed.dispose(); }
    return {contents: eraseTypeScript(ts, intermediate, filename), report};
}
