"""Bounded validator for the big-endian HSD archive container.

This validates container layout only; it does not relocate pointers or convert
meshes, animations, or fighter data.
"""
from __future__ import annotations
import struct
from dataclasses import dataclass

HEADER_SIZE=0x20
MAX_ENTRIES=1_000_000

class HsdArchiveError(ValueError): pass

@dataclass(frozen=True)
class HsdArchiveInfo:
    file_size:int
    data_size:int
    relocations:int
    publics:int
    externs:int
    version:bytes
    table_end:int
    symbol_bytes:int

    @property
    def data_offset(self):
        return HEADER_SIZE

    @property
    def string_table_offset(self):
        return self.table_end

def _u32(data, offset):
    return struct.unpack_from(">I", data, offset)[0]

def validate_hsd_archive(data:bytes|bytearray|memoryview, *, max_size:int=256*1024*1024)->HsdArchiveInfo:
    raw=memoryview(data)
    if len(raw)<HEADER_SIZE: raise HsdArchiveError("HSD archive is shorter than its header")
    if len(raw)>max_size: raise HsdArchiveError("HSD archive exceeds size limit")
    file_size,data_size,reloc_count,public_count,extern_count=struct.unpack_from(">5I",raw,0)
    version=bytes(raw[0x14:0x18])
    if file_size != len(raw): raise HsdArchiveError("header file_size does not match input length")
    if any(n>MAX_ENTRIES for n in (reloc_count,public_count,extern_count)): raise HsdArchiveError("HSD table count exceeds limit")
    table_end=HEADER_SIZE+data_size+4*reloc_count+8*public_count+8*extern_count
    if data_size > len(raw)-HEADER_SIZE or table_end>len(raw): raise HsdArchiveError("HSD data or table region exceeds file")
    symbol_bytes=len(raw)-table_end
    def check_data_offset(offset, width, label):
        if offset>data_size or width>data_size-offset: raise HsdArchiveError(f"{label} points outside HSD data")
    pos=HEADER_SIZE+data_size
    for i in range(reloc_count):
        check_data_offset(_u32(raw,pos),4,"relocation"); pos+=4
    for i in range(public_count):
        check_data_offset(_u32(raw,pos),1,"public"); symbol=_u32(raw,pos+4)
        if symbol>=symbol_bytes: raise HsdArchiveError("public symbol points outside symbol table")
        pos+=8
    for i in range(extern_count):
        check_data_offset(_u32(raw,pos),4,"external"); symbol=_u32(raw,pos+4)
        if symbol>=symbol_bytes: raise HsdArchiveError("external symbol points outside symbol table")
        pos+=8
    return HsdArchiveInfo(file_size,data_size,reloc_count,public_count,extern_count,version,table_end,symbol_bytes)


def build_hsd_archive(data:bytes|bytearray|memoryview, *, relocations=(), publics=(), externs=(), version=b"HSD1") -> bytes:
    """Build a deterministic HSD container from already-converted table data.

    ``publics`` and ``externs`` contain ``(data_offset, symbol_name)`` pairs.
    This function does not create or interpret HSD object graphs.
    """
    body=bytes(data); version=bytes(version)
    if len(version)!=4: raise HsdArchiveError("HSD version must contain four bytes")
    reloc=tuple(int(x) for x in relocations)
    pub=tuple(publics); ext=tuple(externs)
    if any(x<0 or x+4>len(body) for x in reloc): raise HsdArchiveError("relocation points outside HSD data")
    symbols=bytearray(); symbol_offsets={}
    def symbol_offset(value):
        if not isinstance(value,str) or not value or "\x00" in value: raise HsdArchiveError("symbol names must be non-empty strings without NUL")
        if value not in symbol_offsets:
            symbol_offsets[value]=len(symbols); symbols.extend(value.encode("ascii")); symbols.append(0)
        return symbol_offsets[value]
    def entries(items,label):
        out=[]
        for item in items:
            if not isinstance(item,(tuple,list)) or len(item)!=2: raise HsdArchiveError(f"{label} entries require (offset, name)")
            offset=int(item[0]);
            if offset<0 or offset+4>len(body): raise HsdArchiveError(f"{label} points outside HSD data")
            out.append((offset,symbol_offset(item[1])))
        return out
    pub_entries=entries(pub,"public"); ext_entries=entries(ext,"external")
    table=bytearray()
    table.extend(struct.pack(">"+"I"*len(reloc),*reloc) if reloc else b"")
    for offset,sym in pub_entries: table.extend(struct.pack(">II",offset,sym))
    for offset,sym in ext_entries: table.extend(struct.pack(">II",offset,sym))
    file_size=HEADER_SIZE+len(body)+len(table)+len(symbols)
    header=struct.pack(">5I",file_size,len(body),len(reloc),len(pub_entries),len(ext_entries))+version+b"\x00"*8
    result=header+body+bytes(table)+bytes(symbols); validate_hsd_archive(result); return result


def serialize_hsd_archive(data:bytes|bytearray|memoryview) -> bytes:
    """Re-encode a validated archive without changing its object graph.

    The decoder represents fighter data as views into the flattened data block;
    therefore an unmodified round-trip retains that block and re-emits the
    declared relocation and public/external symbol tables in their original
    order. This is the safe foundation for later graph allocation.
    """
    raw=bytes(data)
    info=validate_hsd_archive(raw)
    body=raw[HEADER_SIZE:HEADER_SIZE+info.data_size]
    relocations,publics,externs=extract_hsd_tables(raw)
    return build_hsd_archive(body, relocations=relocations, publics=publics,
                             externs=externs, version=info.version)


def reencode_hsd_file(source, output):
    """Write an unmodified, validated HSD archive to ``output``."""
    from pathlib import Path
    result=serialize_hsd_archive(Path(source).read_bytes())
    destination=Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(result)
    return destination


def extract_hsd_tables(data:bytes|bytearray|memoryview):
    """Return relocation, public, and external entries from one HSD archive."""
    raw=bytes(data); info=validate_hsd_archive(raw); body_base=HEADER_SIZE; table=body_base+info.data_size; relocations=tuple(struct.unpack_from(">I",raw,table+4*i)[0] for i in range(info.relocations)); entries_base=table+4*info.relocations; symbol_base=entries_base+8*(info.publics+info.externs)
    def name(offset):
        end=raw.find(b"\0",symbol_base+offset); 
        if end<0: raise HsdArchiveError("unterminated HSD symbol")
        return raw[symbol_base+offset:end].decode("ascii")
    publics=[]; externs=[]
    for i in range(info.publics):
        off,sym=struct.unpack_from(">II",raw,entries_base+8*i); publics.append((off,name(sym)))
    start=info.publics
    for i in range(info.externs):
        off,sym=struct.unpack_from(">II",raw,entries_base+8*(start+i)); externs.append((off,name(sym)))
    return relocations,tuple(publics),tuple(externs)

def extract_hsd_symbols(data:bytes|bytearray|memoryview) -> tuple[str, ...]:
    """Return the NUL-terminated ASCII symbols in a validated archive."""
    info=validate_hsd_archive(data); raw=memoryview(data)[info.table_end:]
    if not raw: return ()
    if raw[-1] != 0: raise HsdArchiveError("HSD symbol table is not NUL terminated")
    names=[]; start=0
    for index,value in enumerate(raw):
        if value == 0:
            chunk=bytes(raw[start:index])
            try: name=chunk.decode("ascii")
            except UnicodeDecodeError as exc: raise HsdArchiveError("HSD symbol table is not ASCII") from exc
            if not name: raise HsdArchiveError("HSD symbol table contains an empty symbol")
            names.append(name); start=index+1
    return tuple(names)


def extract_hsd_publics(data:bytes|bytearray|memoryview) -> dict[str, int]:
    """Return public symbol names mapped to offsets within the archive data region."""
    info=validate_hsd_archive(data); raw=memoryview(data); symbols=raw[info.table_end:]
    pos=HEADER_SIZE+info.data_size+4*info.relocations
    result={}
    for _ in range(info.publics):
        offset=_u32(raw,pos); symbol_offset=_u32(raw,pos+4); pos+=8
        if symbol_offset>=len(symbols): raise HsdArchiveError("public symbol points outside symbol table")
        end=symbols[symbol_offset:].tobytes().find(b"\x00")
        if end < 1: raise HsdArchiveError("public symbol is not a non-empty NUL-terminated name")
        try: name=symbols[symbol_offset:symbol_offset+end].tobytes().decode("ascii")
        except UnicodeDecodeError as exc: raise HsdArchiveError("public symbol is not ASCII") from exc
        if name in result: raise HsdArchiveError("duplicate public symbol")
        result[name]=offset
    return result


def validate_hsd_relocations(data:bytes|bytearray|memoryview) -> tuple[int, ...]:
    """Validate and return pre-relocation data offsets for every relocation entry."""
    info=validate_hsd_archive(data); raw=memoryview(data); pos=HEADER_SIZE+info.data_size
    offsets=[]
    for index in range(info.relocations):
        target=_u32(raw,pos+index*4); value=_u32(raw,HEADER_SIZE+target)
        if value >= info.data_size: raise HsdArchiveError("HSD relocation value points outside data")
        offsets.append(value)
    return tuple(offsets)
