import fs from 'node:fs';

const versions = JSON.parse(fs.readFileSync(new URL('./versions.json', import.meta.url), 'utf8'));
export function piVersionPin(version) {
    if (!Object.hasOwn(versions, version)) throw new Error(`Unsupported checked-source Pi version: ${version}`);
    return versions[version];
}
