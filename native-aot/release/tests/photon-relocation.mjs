import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { createRequire } from 'node:module';
import { transformStandalonePhoton } from '../../optimizations/standalone-workers.js';
const packageDir=process.argv[2];
if(!packageDir)throw new Error('Pass the pinned Pi1.0 npm package directory');
const filename=path.resolve(packageDir,'node_modules/@silvia-odwyer/photon-node/photon_rs.js');
const original=fs.readFileSync(filename,'utf8');
const source=transformStandalonePhoton(original,filename,packageDir,'1.0.0');
assert.throws(()=>transformStandalonePhoton(original+'\n',filename,packageDir,'1.0.0'),/changed/);
assert.equal(transformStandalonePhoton(original,'/another/photon_rs.js',packageDir,'1.0.0'),original);
assert.equal(transformStandalonePhoton(original,filename,packageDir,'0.85.1'),original);
const require=createRequire(import.meta.url);
let reads=[];
const context={module:{exports:{}},process:{execPath:'/relocated package/pi-native'},__dirname:'/denied original build',
    TextDecoder,TextEncoder,console,Buffer,require(name){
        if(name==='fs')return {readFileSync(file){reads.push(file);if(file.includes('denied'))throw Object.assign(new Error('denied'),{code:'EPERM'});return new Uint8Array();}};
        return require(name);
    },WebAssembly:{Module:class{},Instance:class{constructor(){this.exports={__wbindgen_start(){}};}}}};
vm.runInNewContext(source,context,{timeout:1000});
assert.deepEqual(reads,['/relocated package/photon_rs_bg.wasm']);
console.log('PASS: exact Photon source guard and adjacent Wasm selection with inaccessible original build path');
