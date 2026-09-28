# HSD archive validation

The core library now validates the big-endian HSD archive container documented by the local Melee decompilation. It checks the 0x20-byte header, exact `file_size`, bounded data/table regions, relocation offsets, public/external data offsets, and symbol-table bounds. The core library also provides a deterministic container writer for caller-supplied data and table entries. Validation is size-bounded and does not mutate or relocate pointers.

This is a conversion prerequisite, not a converter: meshes, joints, animations, fighter `ftData`, `PlCo.dat`, and runtime callback references remain out of scope until their formats are implemented and tested with user-owned assets.


The parser extracts the NUL-terminated public symbol `ftLoadCommonData` and its validated data offset. The validator was also run against the user-provided `orig/GALE01/files/PlCo.dat` and accepted its header/table layout (149,101 bytes; 805 relocations; one public symbol; version `001B`, with public symbol `ftLoadCommonData`). The asset remains outside the Character Studio repository.


The parser also validates pre-relocation pointer words. Against the real `PlCo.dat`, all 805 relocation values were inside the 145,824-byte data region (minimum 0, maximum 137,632). No relocation was applied or written.


The CLI command `melee-character inspect-hsd PATH` exposes the same read-only metadata and public roots without writing or modifying the archive.

## Phase 2 container serializer reconciliation

The repository decoder uses `HsdReader` and `HsdModelReport` in `core/src/melee_character_studio/hsd_model.py`. `HsdReader.data` is a view over the flattened data block, `ptr()` interprets relocated words as offsets within that block, and `HsdModelReport` is a read-only projection of joints and offsets. The existing `hsd_writeback.py` remains a fixed-size stream patcher and explicitly rejects topology/archive edits; `hsd_animation.py` has a specialized append-and-rebuild path for one animation segment.

`serialize_hsd_archive()` is therefore intentionally a container-level, unmodified re-encode: it validates the archive, copies the flattened data block, and re-emits the declared relocation table plus public/external symbol tables in their original order. The computed `HsdArchiveInfo.data_offset` is `0x20`; `string_table_offset` is the start of the NUL-terminated symbol table after those tables. The round-trip test is byte-exact for the supported input because no graph allocation or pointer rewriting occurs.

Confirmed later slice order: 2b GX display-list codegen and encode/decode coverage; 2c planar/box UV generation; 2d TEV/material authoring presets; 2e end-to-end write-back and PascalPatch load validation. A future graph serializer must allocate the flattened block from the decoder's source offsets, rewrite every pointer recorded by the relocation table to data-relative offsets, and publish fighter loader names such as `ftData*` and `ft*NormalAJ`.

## Native-first load validation (retarget 2026-09-24)

Slice 2e's "PascalPatch load validation" now targets the native-first pipeline:
a composed archive is installed as a data file in a PascalPatch profile ISO and
booted under the pinned melee-unlocked native runtime, with loader markers
read from `melee_port.log`. Container-level checks in this document are
runtime-independent and unchanged. No load-validation claim is made yet on
the native runtime.
