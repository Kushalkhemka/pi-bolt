import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { transformStandaloneWorkerSpecifier } from '../../optimizations/standalone-workers.js';
const packageDir=process.argv[2];
if(!packageDir)throw new Error('Pass the pinned Pi1.0 package directory');
const filename=path.resolve(packageDir,'dist/utils/image-resize.js');
const original=fs.readFileSync(filename,'utf8');
const transformed=transformStandaloneWorkerSpecifier(original,filename,packageDir,'1.0.0');
assert.throws(()=>transformStandaloneWorkerSpecifier(original+'\n',filename,packageDir,'1.0.0'),/changed/);
assert.match(transformed,/await worker\.terminate\(\)/);
const fixture=transformed.slice(0,transformed.indexOf('/**')).replace(/^import .*;$/gm,'');
for(const failure of [false,true]) {
    let releaseTermination,terminationRequested=false,settled=false,observedExit=false;
    class Worker {
        handlers={};once(event,callback){this.handlers[event]=callback;}
        postMessage(){queueMicrotask(()=>this.handlers.message(failure?{error:'expected failure'}:{result:{ok:true}}));}
        terminate(){terminationRequested=true;return new Promise(resolve=>{releaseTermination=()=>{observedExit=true;this.handlers.exit(0);resolve(0);};});}
    }
    const context=vm.createContext({Worker,Uint8Array,Error,Promise});vm.runInContext(fixture,context);
    const result=vm.runInContext('resizeImageInWorker("./image-resize-worker.js", new Uint8Array([1]), "image/png", {})',context);
    const observation=result.then(value=>{settled=true;return value;},error=>{settled=true;return error;});
    await new Promise(resolve=>setImmediate(resolve));
    assert.equal(terminationRequested,true);assert.equal(settled,false,'tool completion must await termination');
    releaseTermination();const value=await observation;
    assert.equal(observedExit,true);assert.equal(settled,true);
    if(failure)assert.equal(value.message,'expected failure');else assert.equal(value.ok,true);
}
console.log('PASS: successful and failed image responses wait for observed worker termination');
