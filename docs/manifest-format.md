# The `.svm` manifest format

Version 1. All integers are little-endian.

A `.svm` file describes the official files of **one game build**. It is the
only thing `steamverify` trusts, so the format is small, explicit, and
validated on load.

## Goals

| Goal | Consequence |
|---|---|
| Ship inside a Git repository | Binary, interned tables, no pretty-printing |
| Reproducible scans, offline | Self-contained; no network, no Steam APIs |
| Detect stale manifests | Header records game, app id and build id |
| Survive format evolution | Explicit version gate; unknown trailing data ignored |
| Be game agnostic | Nothing title-specific in the layout |

A manifest for a 70 GB game with 73,000 chunks is roughly 2.7 MiB.

## Layout

```
offset  size   field
------  -----  ---------------------------------------------------------
0       8      magic  b"SVHASH\x00\x01"
8       4      u32    format version (1)
12      4      u32    header JSON byte length (H)
16      H      bytes  header JSON (UTF-8)
16+H    4      u32    path count (P)
...            paths  each: u16 length + UTF-8 bytes
        4      u32    hash count (N)
...            hashes each: 20 raw bytes (SHA-1)
        4      u32    file record count (F)
...            records each: 20 bytes, see below
        4      u32    chunk record count (C)
...            chunks each: 16 bytes, see below
```

### File record (20 bytes)

| Type | Field | Meaning |
|---|---|---|
| `u32` | path index | into the path table |
| `u64` | size | exact on-disk size in bytes |
| `u32` | sha1 index | into the hash table; whole-file digest |
| `u32` | first chunk | index of this file's first chunk record |
| `u32` | chunk count | `0` for a plain file |

### Chunk record (16 bytes)

| Type | Field | Meaning |
|---|---|---|
| `u64` | offset | byte offset of the chunk within the file |
| `u32` | length | chunk length in bytes, must be `> 0` |
| `u32` | sha1 index | into the hash table |

## Semantics

* `chunk count == 0` — the file is stored verbatim. Its bytes must hash to the
  record's `sha1`, and its length must equal `size`.
* `chunk count > 0` — the file is stored as SteamPipe chunks. The bytes at
  `[offset, offset + length)` must hash to the chunk's `sha1`. Chunk lengths
  must sum to `size`, and offsets must be unique. Together these guarantee
  every byte of the file is examined exactly once.

The explicit `size` means a truncated or padded file is rejected before any
hashing happens, which keeps a scan fast on a clearly-broken install.

## Header JSON

Required keys — a manifest without them is rejected:

| Key | Type | Meaning |
|---|---|---|
| `game` | string | display name |
| `platform` | string | `windows`, `linux`, `macos` |
| `file_count` | int | must equal the record count |
| `chunk_count` | int | must equal the chunk record count |
| `total_bytes` | int | sum of all `size` fields |
| `source` | string | how the manifest was produced |

Written by the current generator in addition:

| Key | Meaning |
|---|---|
| `slug` | short id used by `--game` |
| `app_id` | Steam app id |
| `build_id` | Steam build id — **the staleness check** |
| `vendor` | publisher, for attribution |
| `depots` | depot ids that contributed files |
| `language` | Steam language the install used |
| `generated` | UTC timestamp |
| `generator` | producing script |
| `replay` | exact command to regenerate |
| `notes` | free text |
| `chunk_size` | nominal chunk size when built from a directory |

## Validation performed on load

1. Magic matches.
2. Format version is understood.
3. Header parses as JSON and contains every required key.
4. Every path/hash/chunk index is in range.
5. `file_count` and `chunk_count` agree with the actual record counts.

`steamverify doctor` additionally runs `validate_coverage` over every file:
chunk lengths must sum to `size`, offsets must be unique and non-negative, and
plain files must carry a 40-character digest. `tools/build_manifest.py` runs
the same check before writing, so a broken manifest cannot be committed by
accident.

## Why hashes are interned

Repeated digests are common: a file built from identical 1 MiB blocks of
zero padding has thousands of identical chunk hashes, and several depots
re-declare shared files. Storing each digest once and referencing it by a
`u32` index costs 4 bytes instead of 20. On a real manifest this is the
difference between a few megabytes and tens of megabytes.

## Reading and writing

```python
from steamverify import manifest

mf = manifest.load("manifests/witcher3/witcher3-25575366.svm",
                   verify_digest="cb2ddd35...")   # optional SHA-256 pin
for entry in mf.files:
    print(entry.path, entry.size, entry.chunk_count)

manifest.write("out.svm", header_dict, entries)    # generator side
manifest.write_json("dump.json", mf)               # human-readable debug dump
```

## Compatibility rules for a future version 2

* Bump `FORMAT_VERSION`; never reinterpret existing bytes.
* Old readers must fail loudly on a newer version — they do (`unsupported
  manifest format version`).
* New optional data goes in the header JSON or after the last section, which
  readers already ignore.

## Relationship to Steam's own manifests

`manifests/<slug>/*.svm` is derived from Steam's binary depot manifests in
`<steam>/depotcache/`, which use protobuf and are documented in
`src/steamverify/depot.py`. `.svm` exists because the depot format is
per-depot, protobuf-encoded, and several megabytes per depot; `.svm` merges
every depot of one build into a single file that any language can read with a
few `struct.unpack` calls.
