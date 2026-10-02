#!/usr/bin/env python3
"""Read-only semantic/provenance checks for full and selectively omitted sidecars."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import struct

ROOT = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('layout_guard', ROOT / 'guard-image-layout-pair.py')
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)
require = layout.require


def semantic_image(path, map_path):
    """Validate the map against BUNAOT01's actual hash table and records.

    Offsets below follow the current ARM64 ImageHeader/ImageFunction/ImageKey
    schema. The magic check intentionally rejects a changed image version.
    """
    header = layout.image_header(path)
    data = Path(path).read_bytes()
    table, capacity, records, records_size, count = struct.unpack_from('<5I', data, 72)
    require(capacity > 0 and capacity & (capacity - 1) == 0, 'expected generic image hash table')
    require(table + capacity * 16 <= len(data) and records + records_size <= len(data),
            'image table/records exceed file')
    starts_at = struct.unpack_from('<I', data, 248)[0]
    require(starts_at + (count + 1) * 4 <= len(data), 'truncated function starts')
    starts = struct.unpack_from(f'<{count + 1}I', data, starts_at)
    # Address lookup uses UINT32_MAX as its final sentinel, not code end.
    require(starts[-1] == 0xffffffff, 'unexpected address-lookup sentinel')
    end_of_functions = struct.unpack_from('<I', data, 264)[0]
    keys, by_index = {}, {}
    for bucket in range(capacity):
        module, start, kind, record = struct.unpack_from('<4I', data, table + bucket * 16)
        if not record:
            continue
        require(record - 1 + 24 <= records_size, 'invalid function record')
        at = records + record - 1
        index = struct.unpack_from('<I', data, at)[0]
        known_callees = struct.unpack_from('<I', data, at + 16)[0] & 0x1ffff
        flags = data[at + 23]
        require(not known_callees and not flags & ((1 << 1) | (1 << 3)),
                'generic image contains direct callees, inline frames or static imports')
        key = (module, start, kind)
        require(key not in keys and index not in by_index and index < count, 'duplicate/invalid serialized key')
        keys[key] = index
        by_index[index] = key
    require(len(keys) == count, 'image hash table/header count mismatch')
    sizes, order = layout.image_map(map_path)
    require(set(sizes) == set(keys) and len(order) == count, 'image map does not describe serialized keys')
    for line in Path(map_path).read_text().splitlines():
        _, index, offset, size, module, start, kind = line.split('\t')
        index, offset, size = map(int, (index, offset, size))
        key = tuple(map(int, (module, start, kind)))
        require(by_index[index] == key and starts[index] == offset,
                'image map key/order/offset disagrees with serialized image')
        stop = starts[index + 1] if index + 1 < count else end_of_functions
        require(0 <= offset < stop <= header['code_size']
                and offset + size <= stop, 'map code size exceeds serialized function range')

    regexp_at, regexp_count, text_at = struct.unpack_from('<3I', data, 232)
    require(regexp_at + regexp_count * 24 <= len(data), 'truncated regex records')
    regexps = set()
    for index in range(regexp_count):
        _, text, packed_length, flags, code8, code16 = struct.unpack_from('<6I', data, regexp_at + index * 24)
        is8bit, length = packed_length >> 31, packed_length & 0x7fffffff
        byte_count = length * (1 if is8bit else 2)
        require(text_at + text + byte_count <= len(data), 'regex text exceeds file')
        raw = data[text_at + text:text_at + text + byte_count]
        pattern = raw.decode('latin1' if is8bit else 'utf-16-le', errors='surrogatepass')
        key = (pattern, flags, bool(code8), bool(code16))
        require(key not in regexps, 'duplicate regex identity')
        require(all(not offset or offset < header['code_size'] for offset in (code8, code16)),
                'regex code offset exceeds image')
        regexps.add(key)
    return header, sizes, order, regexps


def guard(control, candidate, expected_patch):
    control, candidate = control.resolve(), candidate.resolve()
    old, new = layout.manifest(control), layout.manifest(candidate)
    alignment = ('pi_version', 'target', 'sources', 'typescript_type_annotations_preserved',
                 'unicode_fast_path', 'unicode_optimizer_sha256', 'build_driver_sha256',
                 'aot_pipeline', 'inline_loop_fast_paths', 'static_heap', 'prelinked_graph_version',
                 'native_call_linking', 'internal_modules_aot', 'bytecode_order_sha256', 'bun_patch_sha256',
                 'runtime_sha256', 'backend_patch_sha256', 'native_hot_layout', 'priority_final_render',
                 'priority_final_render_optimizer_sha256')
    for field in alignment:
        require(old.get(field) == new.get(field), f'{field} differs across selective artifacts')
    require(old['target'] == 'darwin-arm64' and old['static_heap'] is False, 'expected generic M5 sidecars')
    require(old.get('skip_trained_hot') is False and new.get('skip_trained_hot') is True,
            'control/candidate must explicitly record skip_trained_hot=false/true')
    require(old['executable_sha256'] == new['executable_sha256'], 'standalone executable bytes differ')
    old_env = {k: v for k, v in old['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
    new_env = {k: v for k, v in new['common_runtime_env'].items() if k != 'PI_PACKAGE_DIR'}
    require(old_env == new_env, 'common runtime settings differ')
    source = (control / 'CachedTypes.cpp').read_text()
    require(source == (candidate / 'CachedTypes.cpp').read_text(), 'compiler source snapshots differ')
    patches = []
    for directory, manifest in ((control, old), (candidate, new)):
        require(layout.fingerprint(directory / 'backend.patch')['sha256'] == manifest['backend_patch_sha256'],
                'archived backend patch hash differs from manifest')
        patches.append(layout.sections_of_patch(directory / 'backend.patch'))
    require(patches[0] == patches[1], 'archived backend patches differ')
    layout.verify_proposal(source, patches[0], expected_patch)
    require(layout.embedded_sections(old['executable']) == layout.embedded_sections(new['executable']),
            'embedded graph/runtime builtin sections differ')

    log = (candidate / 'build.log').read_text()
    omitted = [tuple(map(int, key)) for key in re.findall(
        r'^AOT: selective hybrid omitted trained hot @(\d+):(\d+):(\d+)$', log, re.MULTILINE)]
    totals = re.findall(r'^AOT: selective hybrid skipped (\d+) trained hot function jobs$', log, re.MULTILINE)
    require(len(totals) == 1 and int(totals[0]) == len(omitted) == len(set(omitted)) > 0,
            'omitted key diagnostics/count invalid')
    omitted = set(omitted)
    require(new['native_skipped_hot_jobs'] == len(omitted), 'manifest omission count differs from log')
    require('AOT: selective hybrid ' not in (control / 'build.log').read_text(), 'control unexpectedly selective')
    full = semantic_image(old['image'], control / 'image.map')
    selective = semantic_image(new['image'], candidate / 'image.map')
    require(full[0]['stamp'] == selective[0]['stamp'], 'engine compatibility stamps differ')
    expected = set(full[1]) - omitted
    require(set(selective[1]) == expected, 'selective keys differ from full keys minus logged omissions')
    require(not omitted & set(selective[1]), 'an omitted key remains in the selective image')
    require(all(full[1][key] == selective[1][key] for key in expected), 'retained native key-to-code-size maps differ')
    require(full[3] == selective[3], 'regex pattern/flags/native-width coverage changed')
    require(all(key in selective[1] for key in full[1] if key[1] == 0xffffffff
                or key[0] in (0xeb17ffff, 0xb017ffff)), 'entry or engine/embedder builtin key omitted')
    for data, image in ((old, full), (new, selective)):
        require(data['aot_compilation']['compiled'] == len(image[1]), 'manifest compiled count differs from image')
    require(old['aot_compilation']['considered'] == new['aot_compilation']['considered'], 'considered jobs differ')
    require([key for key in full[2] if key in expected] == selective[2], 'retained physical order unexpectedly changed')

    env = {**old['common_runtime_env'], 'PI_OFFLINE': '1', 'BUN_JSC_useJIT': 'true',
           'BUN_JSC_useAOT': 'true', 'BUN_JSC_useAOTMappedImages': 'true', 'BUN_JSC_numberOfGCMarkers': '2'}
    configs = {name: {'command': [old['executable']], 'env': {**env, 'BUN_JSC_aotImagePath': data['image']}}
               for name, data in (('full_hybrid', old), ('selective_hybrid', new))}
    return configs, {'date': datetime.now(timezone.utc).isoformat(),
        'guard_sha256': layout.fingerprint(__file__)['sha256'], 'layout_helper_sha256': layout.fingerprint(layout.__file__)['sha256'],
        'expected_patch': layout.fingerprint(expected_patch), 'same_compiler_and_executable_bytes': True,
        'control_functions': len(full[1]), 'selective_functions': len(selective[1]),
        'logged_omitted_jobs': len(omitted), 'actual_removed_functions': len(set(full[1]) & omitted),
        'omitted_keys': sorted(omitted), 'retained_native_sizes_identical': True,
        'regex_patterns_flags_native_widths_identical': True, 'regex_count': len(full[3]),
        'image_headers': [full[0], selective[0]],
        'artifacts': {str(directory): {name: layout.fingerprint(directory / name) for name in
                     ('manifest.json', 'CachedTypes.cpp', 'backend.patch', 'build.log', 'image.map', 'pi-native', 'pi-native.aot')}
                     for directory in (control, candidate)},
        'required_next_checks': ['Static guards do not establish runtime stability or performance.',
            'Validate native selection/fallback, mapped and copying loaders, real CLI and worker/GC behavior before timing.',
            'Show an omitted hot function actually tiers to JIT; absence from the image alone does not prove tiering.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--control', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--expected-patch', type=Path, default=ROOT / 'research/native-selective-hybrid.patch')
    parser.add_argument('--output', type=Path, default=ROOT / 'research/native-selective-pair-variants.json')
    args = parser.parse_args()
    configs, report = guard(args.control, args.candidate, args.expected_patch)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(configs, indent=2) + '\n')
    report['configs'] = configs
    report['variants_sha256'] = layout.fingerprint(args.output)['sha256']
    report_path = args.output.with_name(args.output.stem + '-guard.json')
    report_path.write_text(json.dumps(report, indent=2) + '\n')
    print(f'PASS selective semantic/provenance guards; wrote {report_path}; runtime validation still required')


if __name__ == '__main__':
    main()
