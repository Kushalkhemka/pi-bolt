import assert from 'node:assert/strict';
import vm from 'node:vm';
import {loadTypeScript, checkedTypeScript} from './frontend.mjs';

const ts = loadTypeScript();
function primitiveCheck(value, mask) {
    const tag = value === undefined ? 1 : value === null ? 2 :
        ({boolean: 4, number: 8, string: 16, symbol: 32, bigint: 64})[typeof value];
    if (!tag || !(tag & mask)) throw new TypeError(`Type check failed: ${mask}`);
    return value;
}
function compile(source) {
    const result = checkedTypeScript(ts, source, 'fixture.ts');
    const js = ts.transpileModule(result.contents, {compilerOptions: {
        target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS}}).outputText;
    const context = {exports: {}, $$t: primitiveCheck};
    vm.runInNewContext(js, context);
    return {...result, api: context.exports};
}
const basic = compile(`
export function add(x:number, y:number):number { return x+y; }
export const optional = (x:string|undefined):string|undefined => x;
export function symbol(x:symbol):symbol { return x; }
export function bigint(x:bigint):bigint { return x+1n; }
export function badReturn():number { return 'wrong' as any; }
export function fallthrough():number { if (false) return 1; }
export function voidResult():void { return; }
`);
assert.equal(basic.api.add(2,3),5);
assert.ok(Number.isNaN(basic.api.add(NaN,1)));
assert.equal(basic.api.add(Infinity,1),Infinity);
assert.throws(()=>basic.api.add('2',3),TypeError);
assert.equal(basic.api.optional(undefined),undefined);
assert.equal(basic.api.optional('x'),'x');
assert.throws(()=>basic.api.optional(3),TypeError);
const sym=Symbol('value'); assert.equal(basic.api.symbol(sym),sym);
assert.equal(basic.api.bigint(2n),3n);
assert.throws(()=>basic.api.bigint(2),TypeError);
assert.throws(()=>basic.api.badReturn(),TypeError);
assert.throws(()=>basic.api.fallthrough(),TypeError);
assert.equal(basic.api.voidResult(),undefined);

const writes = compile(`
export function captured() { let x:number=1; return {get:()=>x, set:(v:any)=>{x=v;}, shadow:()=>{const x:string='ok';return x;}}; }
export function compound() {let x:number=2;let calls=0; const rhs=()=>{calls++;return 3;};x+=rhs(); const before=x++; const after=++x;return [x,before,after,calls];}
export function badCompound() {let x:number=2; const attempt=()=>{x+='x' as any;};return {attempt,get:()=>x};}
export function receiver(this:any,x:number):number { return this.offset+x; }
export function arrowReceiver(this:any) {return (x:number):number=>this.offset+x;}
export class C {value:number=1; constructor(x:number){this.value=x;} method(x:number):number {return this.value+x;} get scalar():number{return this.value;}}
export function object(x:string) {return {x};}
export function evalShorthand() {let x:string='good'; eval("x='wrong-type' && 3"); return {x};}
`);
const captured=writes.api.captured(); assert.equal(captured.get(),1);captured.set(4);assert.equal(captured.get(),4);
assert.throws(()=>captured.set('bad'),TypeError);assert.equal(captured.get(),4);assert.equal(captured.shadow(),'ok');
assert.deepEqual(Array.from(writes.api.compound()),[7,5,7,1]);
const bad=writes.api.badCompound();assert.throws(()=>bad.attempt(),TypeError);assert.equal(bad.get(),2);
assert.equal(writes.api.receiver.call({offset:3},2),5);assert.equal(writes.api.arrowReceiver.call({offset:3})(2),5);
assert.throws(()=>new writes.api.C('bad'),TypeError);assert.equal(new writes.api.C(2).method(3),5);
assert.equal(new writes.api.C(2).scalar,2);assert.equal(writes.api.object('value').x,'value');
assert.throws(()=>writes.api.evalShorthand(),TypeError);
assert.match(writes.contents,/return \{ x: \$\$t\(x, 16\) \}/);

const unsupported=compile(`
export function generic(value:{x:number}) {return value;}
export function logical() {let x:number=0; x ||= 'generic' as any; return x;}
export function destructure() {let x:number=0; [x]=['generic' as any]; return x;}
export function arrayContract(value:number[]) {return value;}
export function wrappedUpdate() {let x:number=1; const old=(x)++; return [old,x];}
export function objectDestructure() {let x:string='initial'; ({x}={x:3 as any}); return x;}
export function objectDefault() {let x:string='initial'; ({x=3 as any}={}); return x;}
declare const UNDEFINED_AMBIENT:boolean;
declare namespace Ambient {const VALUE:number;}
export const ambientAbsent=typeof UNDEFINED_AMBIENT==='undefined';
`);
assert.equal(unsupported.api.generic('unchanged'),'unchanged');
assert.equal(unsupported.api.logical(),'generic');assert.equal(unsupported.api.destructure(),'generic');
assert.equal(unsupported.api.arrayContract('unchanged'),'unchanged');
assert.deepEqual(Array.from(unsupported.api.wrappedUpdate()),[1,2]);
assert.equal(unsupported.api.objectDestructure(),3);assert.equal(unsupported.api.objectDefault(),3);
assert.equal(unsupported.api.ambientAbsent,true);
assert.ok(unsupported.report.skipped.filter(x=>x.reason.includes('Ambient declaration')).length===2);
assert.ok(unsupported.report.skipped.some(x=>x.reason.includes('Logical assignment')));
assert.ok(unsupported.report.skipped.some(x=>x.reason.includes('Destructuring')));
assert.equal(unsupported.report.bindings,0);
assert.throws(()=>checkedTypeScript(ts,'const $$t = (v:any)=>v;', 'reserved.ts'), /reserved/);
assert.throws(()=>checkedTypeScript(ts,'function f($$t:number){}', 'reserved.ts'), /reserved/);
const directives=compile(`export function strict(x:number):number {'use strict'; return x;}`);
assert.match(directives.contents,/['"]use strict['"];\s*\$\$t\(x, 8\)/);
assert.equal(directives.report.return_checks,2);
console.log('PASS: primitive/union/default return contracts, single evaluation, captures/shadows, atomic assignment, numeric updates, receivers/methods/constructors, directives, explicit unsupported fallback and reserved intrinsic name');
