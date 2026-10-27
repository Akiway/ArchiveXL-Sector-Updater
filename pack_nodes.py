# Copyright © 2026 Aurélien "Akiway" Klein.
"""Pack a node export folder into nodes-<version>.zip, the file published on the GitHub release.

Usage:
    python pack_nodes.py <export folder> --version 2.5 [--exe-version 3.0.5813403] [--unreadable unreadable.txt]

Every <sector path>.jsonl of the folder is stored LZMA-compressed under its sector path, so axl_sector_updater.py
reads one sector at a time without unpacking. manifest.json records the game version, the record schema and the
counts; docs/NODE_RECORDS.md is added so the zip documents itself.
"""
import argparse, datetime, hashlib, json, os, sys, zipfile

from axl_sector_updater import NODES_MANIFEST, NODES_SCHEMA, SCRIPT_DIR, VERSION_RX, VERSIONS_DIR, nodes_zip_name


def main():
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('export', help='folder holding base/.../<sector>.streamingsector.jsonl')
    ap.add_argument('--version', required=True, help='game version, as typed after --from / --to (e.g. 2.31)')
    ap.add_argument('--exe-version', help='FileVersion of Cyberpunk2077.exe the sectors come from')
    ap.add_argument('--unreadable', help='text file listing the sectors that could not be read (one path per line)')
    ap.add_argument('--out', help='zip path (default: versions/nodes-<version>.zip)')
    args = ap.parse_args()
    if not VERSION_RX.match(args.version):
        raise SystemExit(f'{args.version}: expected a version such as 2.31')

    files = sorted(os.path.join(folder, name) for folder, _, names in os.walk(args.export)
                   for name in names if name.endswith('.streamingsector.jsonl'))
    if not files:
        raise SystemExit(f'no .streamingsector.jsonl in {args.export}')
    out = args.out or os.path.join(VERSIONS_DIR, nodes_zip_name(args.version))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    records = 0
    with zipfile.ZipFile(out + '.part', 'w', zipfile.ZIP_LZMA) as z:
        for n, path in enumerate(files, 1):
            data = open(path, 'rb').read()
            records += data.count(b'\n')
            z.writestr(os.path.relpath(path, args.export).replace(os.sep, '/'), data)
            if n % 500 == 0 or n == len(files):
                print(f'\r{n} / {len(files)} sectors', end='', file=sys.stderr)
        print(file=sys.stderr)
        unreadable = [l.strip() for l in open(args.unreadable, encoding='utf-8') if l.strip()] if args.unreadable else []
        manifest = {
            'game_version': args.version,
            'exe_version': args.exe_version,
            'schema': NODES_SCHEMA,
            'sectors': len(files),
            'records': records,
            'unreadable_sectors': unreadable,
            'created': datetime.date.today().isoformat(),
            'copyright': 'Copyright © 2026 Aurélien "Akiway" Klein. Derived from Cyberpunk 2077 game data '
                         '(CD PROJEKT S.A.).',
        }
        z.writestr(NODES_MANIFEST, json.dumps(manifest, indent=2))
        schema_doc = os.path.join(SCRIPT_DIR, 'docs', 'NODE_RECORDS.md')
        if os.path.exists(schema_doc):
            z.write(schema_doc, 'NODE_RECORDS.md')
    os.replace(out + '.part', out)

    sha = hashlib.sha256()
    with open(out, 'rb') as f:
        while chunk := f.read(1 << 20):
            sha.update(chunk)
    print(f'{out}: {len(files)} sectors, {records} records, {os.path.getsize(out) / 2**20:.0f} MiB, '
          f'sha256 {sha.hexdigest()}')
    print(f'publish: gh release upload node-exports "{out}" --clobber')


if __name__ == '__main__':
    main()
