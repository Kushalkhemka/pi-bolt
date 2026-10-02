#!/usr/bin/env python3
"""Read-only guards for a layout-only M5 sidecar pair; emit one-executable configs."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent
SOURCE = 'Source/JavaScriptCore/runtime/CachedTypes.cpp'


def fingerprint(path):
    with Path(path).open('rb') as stream:
        sha = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'sha256': sha, 'bytes': Path(path).stat().st_size}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def manifest(directory):
    data = json.loads((directory / 'manifest.json').read_text())
    for key in ('executable', 'image'):
        path = Path(data[key]).resolve()
        require(path.parent == directory, f'{key} is outside {directory}')
        require(fingerprint(path)['sha256'] == data[key + '_sha256'], f'{path}: artifact hash mismatch')
    return data


def embedded_sections(executable):
    # Mach-O 64-bit little-endian, LC_SEGMENT_64: fixed 72-byte segment and
    # 80-byte section records. Extract the declared graph length, not padding.
    data = Path(executable).read_bytes()
    require(len(data) >= 32 and struct.unpack_from('<I', data)[0] == 0xfeedfacf,
            f'{executable}: expected a thin little-endian Mach-O 64-bit executable')
    cpu = struct.unpack_from('<I', data, 4)[0]
    require(cpu == 0x0100000c, f'{executable}: expected ARM64')
    ncmds, command_size = struct.unpack_from('<II', data, 16)
    stop = 32 + command_size
    require(stop <= len(data), 'truncated Mach-O commands')
    cursor, sections = 32, {}
    for _ in range(ncmds):
        require(cursor + 8 <= stop, 'truncated Mach-O command header')
        command, size = struct.unpack_from('<II', data, cursor)
        require(size >= 8 and cursor + size <= stop, 'invalid Mach-O command size')
        if command == 0x19:
            require(size >= 72, 'truncated Mach-O segment')
            nsects = struct.unpack_from('<I', data, cursor + 64)[0]
            require(72 + nsects * 80 <= size, 'truncated Mach-O section table')
            for index in range(nsects):
                section = cursor + 72 + index * 80
                name = data[section:section + 16].split(b'\0', 1)[0].decode('ascii')
                segment = data[section + 16:section + 32].split(b'\0', 1)[0].decode('ascii')
                if (segment, name) not in (('__BUN', '__bun'), ('__TEXT', '__bun_builtins')):
                    continue
                length = struct.unpack_from('<Q', data, section + 40)[0]
                offset = struct.unpack_from('<I', data, section + 48)[0]
                require(offset + length <= len(data), 'embedded section extends beyond executable')
                payload = data[offset:offset + length]
                if segment == '__BUN':
                    require(len(payload) >= 8, 'graph has no length prefix')
                    graph_length = struct.unpack_from('<Q', payload)[0]
                    require(0 < graph_length <= len(payload) - 8, 'invalid embedded graph length')
                    payload = payload[8:8 + graph_length]
                key = segment + ',' + name
                require(key not in sections, 'duplicate relevant Mach-O section')
                sections[key] = {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)}
        cursor += size
    require(cursor == stop, 'Mach-O command count/size mismatch')
    require('__BUN,__bun' in sections, 'compiled graph section missing')
    require('__TEXT,__bun_builtins' in sections, 'runtime builtin-bytecode section missing')
    return sections


def image_header(path):
    # AOTImage.h's first fields are 5 uint64, linkTimeConstantsUsed[4],
    # followed by table/record offsets and numberOfFunctions (uint32).
    with Path(path).open('rb') as stream:
        header = stream.read(96)
    require(len(header) == 96, 'truncated AOT image header')
    magic, stamp, size, code_offset, code_size = struct.unpack_from('<5Q', header)
    functions = struct.unpack_from('<I', header, 88)[0]
    require(magic == 0x3130544f414e5542, 'unexpected AOT image magic/version')
    require(size == Path(path).stat().st_size, 'AOT header size/file size mismatch')
    require(code_offset + code_size <= size, 'AOT code extends beyond image')
    require(functions > 0, 'AOT image contains no functions')
    return {'magic': hex(magic), 'stamp': hex(stamp), 'functions': functions,
            'size': size, 'code_offset': code_offset, 'code_size': code_size}


def image_map(path):
    keys, physical_order = {}, []
    for line in Path(path).read_text().splitlines():
        fields = line.split('\t')
        require(len(fields) == 7 and fields[0] == 'F', f'{path}: unsupported image map record')
        index, offset, size, module, start, kind = map(int, fields[1:])
        key = (module, start, kind)
        require(index == len(physical_order) and size > 0 and offset >= 0 and key not in keys,
                f'{path}: invalid image map index/size/key')
        keys[key] = size
        physical_order.append(key)
    return keys, physical_order


def sections_of_patch(path):
    sections = {}
    current = None
    for line in Path(path).read_text().splitlines(keepends=True):
        if line.startswith('diff --git '):
            match = re.fullmatch(r'diff --git a/(\S+) b/\1\n?', line)
            require(match is not None, f'{path}: unsupported full patch file header')
            current = match.group(1)
            require(current not in sections, f'{path}: duplicate patch file')
            sections[current] = []
        require(current is not None, f'{path}: expected a full git diff patch')
        sections[current].append(line)
    return {key: ''.join(value) for key, value in sections.items()}


def verify_proposal(source, full_patch, patch):
    # Both exports use the same compiler source. Check that its archived source
    # contains the exact proposed implementation, not merely the option name.
    lines = Path(patch).read_text().splitlines(keepends=True)
    additions, groups, current, files = [], [], None, set()
    def close_group():
        if additions:
            groups.append((current, ''.join(additions)))
            additions.clear()
    for line in lines:
        if line.startswith('--- a/'):
            close_group()
            continue
        if line.startswith('+++ b/'):
            current = line[6:].strip()
            files.add(current)
            continue
        if line.startswith('@@ '):
            close_group()
        elif line.startswith(' '):
            close_group()
        elif line.startswith('-'):
            close_group()
            continue
        elif line.startswith('+'):
            additions.append(line[1:])
            if current != SOURCE:
                require(line in full_patch[current], 'compiler full patch does not contain exact proposed option')
        else:
            raise ValueError('unsupported proposal patch record')
    close_group()
    require(files == {SOURCE, 'Source/JavaScriptCore/runtime/OptionsList.h'}, 'unexpected proposal patch files')
    for file, new in groups:
        if file == SOURCE:
            require(new and new in source, 'archived compiler source does not contain exact proposed implementation')


def guard(control, candidate, expected_patch):
    control, candidate = control.resolve(), candidate.resolve()
    old, new = manifest(control), manifest(candidate)
    alignment = ('pi_version', 'target', 'sources', 'typescript_type_annotations_preserved',
                 'unicode_fast_path', 'unicode_optimizer_sha256', 'build_driver_sha256',
                 'aot_pipeline', 'inline_loop_fast_paths', 'static_heap', 'prelinked_graph_version',
                 'native_call_linking', 'internal_modules_aot', 'bytecode_order_sha256', 'bun_patch_sha256',
                 'runtime_sha256', 'backend_patch_sha256', 'skip_trained_hot', 'priority_final_render',
                 'priority_final_render_optimizer_sha256', 'input_sources')
    for key in alignment:
        require(old.get(key) == new.get(key), f'{key} differs across layout artifacts')
    require(old['target'] == 'darwin-arm64' and old['static_heap'] is False, 'expected generic M5 ARM64 sidecar')
    require(old.get('native_hot_layout') is False and new.get('native_hot_layout') is True,
            'control/candidate must explicitly record native_hot_layout=false/true')
    require(old['executable_sha256'] == new['executable_sha256'], 'standalone executable bytes differ across layout exports')
    old_env = {k: v for k, v in old['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
    new_env = {k: v for k, v in new['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
    require(old_env == new_env, 'common runtime settings differ')
    old_source = (control / 'CachedTypes.cpp').read_text()
    new_source = (candidate / 'CachedTypes.cpp').read_text()
    require(old_source == new_source, 'compiler CachedTypes.cpp source differs across exports')
    patches = []
    for directory, data in ((control, old), (candidate, new)):
        patch = directory / 'backend.patch'
        require(fingerprint(patch)['sha256'] == data['backend_patch_sha256'], 'archived backend patch hash differs from manifest')
        patches.append(sections_of_patch(patch))
    require(patches[0] == patches[1], 'compiler full backend patch differs across exports')
    verify_proposal(old_source, patches[0], expected_patch)
    sections = [embedded_sections(data['executable']) for data in (old, new)]
    require(sections[0] == sections[1], 'embedded graph or runtime builtin bytecode differs')
    headers = [image_header(data['image']) for data in (old, new)]
    for key in ('magic', 'stamp', 'functions'):
        require(headers[0][key] == headers[1][key], f'AOT {key} differs')
    maps = [image_map(directory / 'image.map') for directory in (control, candidate)]
    require(maps[0][0] == maps[1][0], 'native key-to-code-size maps differ')
    require(len(maps[0][0]) == headers[0]['functions'], 'image map/header function counts differ')
    require(maps[0][1] != maps[1][1], 'native physical order did not change')
    env = {**old['common_runtime_env'], 'PI_OFFLINE': '1', 'BUN_JSC_useJIT': 'false',
           'BUN_JSC_useAOT': 'true', 'BUN_JSC_useAOTMappedImages': 'true', 'BUN_JSC_numberOfGCMarkers': '2'}
    configs = {name: {'command': [old['executable']], 'env': {**env, 'BUN_JSC_aotImagePath': data['image']}}
               for name, data in (('layout_old', old), ('layout_hot', new))}
    provenance = {}
    for directory, data in ((control, old), (candidate, new)):
        paths = [directory / name for name in ('manifest.json', 'CachedTypes.cpp', 'backend.patch', 'image.map')]
        paths += [Path(data['executable']), Path(data['image'])]
        provenance[str(directory)] = {str(path): fingerprint(path) for path in paths}
    return configs, {'date': datetime.now(timezone.utc).isoformat(), 'guard_sha256': fingerprint(__file__)['sha256'],
        'expected_layout_patch': {str(expected_patch.resolve()): fingerprint(expected_patch)},
        'verified_exact_source_diff': '',
        'export_setting_diff': {'control': {'native_hot_layout': False}, 'candidate': {'native_hot_layout': True}},
        'same_compiler_and_executable_bytes': True,
        'embedded_sections': sections[0], 'image_headers': headers, 'artifacts': provenance,
        'physical_function_positions_changed': sum(a != b for a, b in zip(maps[0][1], maps[1][1])),
        'runtime_sha256': {'control': old['runtime_sha256'], 'candidate': new['runtime_sha256']},
        'required_next_checks': [
            'Static guards do not replace loader/correctness validation. Use these configs for verbose native selection and all CLI/worker functional checks before timing.',
            'Confirm both images load and execute the expected native key set; reject any fallback or selection-count difference.',
            'Run a randomized paired-round cohort with this one unchanged control executable and asset directory. Only sidecar path differs.'
        ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--expected-patch', type=Path, default=ROOT / 'research/native-hot-layout.patch')
    parser.add_argument('--output', type=Path, default=ROOT / 'research/native-hot-layout-pair-variants.json')
    args = parser.parse_args()
    configs, report = guard(args.control, args.candidate, args.expected_patch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(configs, indent=2) + '\n')
    report['configs'] = configs
    report['variants_sha256'] = fingerprint(args.output)['sha256']
    report_path = args.output.with_name(args.output.stem + '-guard.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS static layout pair guards; wrote {args.output} and {report_path}; functional loader/CLI/worker validation still required')


if __name__ == '__main__':
    main()
