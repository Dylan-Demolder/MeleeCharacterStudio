from __future__ import annotations
import struct, zlib


def _expand(v,bits): return (int(v)*255)//((1<<bits)-1)

def _rgb565(v): return (_expand((v>>11)&31,5),_expand((v>>5)&63,6),_expand(v&31,5),255)
def _rgb5a3(v):
    if v&0x8000:return (_expand((v>>10)&31,5),_expand((v>>5)&31,5),_expand(v&31,5),255)
    return (_expand((v>>8)&7,3),_expand((v>>4)&15,4),_expand(v&15,4),_expand((v>>12)&7,3))

def _rgba8(block,i): return (block[32+2*i+1],block[32+2*i+0],block[2*i+1],block[2*i+0])

def _cmpr(block):
    a,b=struct.unpack_from('>HH',block,0); c0=_rgb565(a)[:3]; c1=_rgb565(b)[:3]; colors=[c0,c1]
    if a>b: colors += [tuple((2*c0[k]+c1[k])//3 for k in range(3)),tuple((c0[k]+2*c1[k])//3 for k in range(3))]
    else: colors += [tuple((c0[k]+c1[k])//2 for k in range(3)),(0,0,0)]
    bits=struct.unpack_from('>I',block,4)[0]; return [(*colors[(bits>>(30-2*i))&3],0 if (a<=b and ((bits>>(30-2*i))&3)==3) else 255) for i in range(16)]

def decode_hsd_texture(reader,image_desc):
    """Decode a local HSD ImageDesc into RGBA8 pixels; no game data is packaged."""
    image=reader.ptr(image_desc); 
    if image is None or image+16>reader.info.data_size:return None
    data_ptr=reader.ptr(image); width=struct.unpack_from('>H',reader.data,image+4)[0]; height=struct.unpack_from('>H',reader.data,image+6)[0]; fmt=reader.u32(image+8)
    if not data_ptr or not width or not height:return None
    out=bytearray(width*height*4)
    def put(x,y,color):
        if 0<=x<width and 0<=y<height: out[(y*width+x)*4:(y*width+x+1)*4]=bytes(color)
    def read(off,n):
        if off<0 or off+n>reader.info.data_size:return bytes(n)
        return bytes(reader.data[off:off+n])
    if fmt==0:
        for by in range(0,height,8):
            for bx in range(0,width,8):
                block=read(data_ptr+((by//8)*( (width+7)//8)+(bx//8))*32,32)
                for i in range(64):
                    q=block[i//2]; val=(q>>4 if i%2==0 else q&15)*17; put(bx+i%8,by+i//8,(val,val,val,255))
    elif fmt==1:
        for by in range(0,height,4):
            for bx in range(0,width,8):
                block=read(data_ptr+((by//4)*((width+7)//8)+(bx//8))*32,32)
                for i,val in enumerate(block): put(bx+i%8,by+i//8,(val,val,val,255))
    elif fmt==2:
        for by in range(0,height,4):
            for bx in range(0,width,8):
                block=read(data_ptr+((by//4)*((width+7)//8)+(bx//8))*32,32)
                for i,val in enumerate(block): put(bx+i%8,by+i//8,((val>>4)*17,)*3+((val&15)*17,))
    elif fmt==3:
        for by in range(0,height,4):
            for bx in range(0,width,4):
                block=read(data_ptr+((by//4)*((width+3)//4)+(bx//4))*32,32)
                for i in range(16): put(bx+i%4,by+i//4,(block[2*i],)*3+(block[2*i+1],))
    elif fmt in (4,5):
        for by in range(0,height,4):
            for bx in range(0,width,4):
                block=read(data_ptr+((by//4)*((width+3)//4)+(bx//4))*32,32)
                for i in range(16):
                    v=struct.unpack_from('>H',block,i*2)[0]; put(bx+i%4,by+i//4,_rgb565(v) if fmt==4 else _rgb5a3(v))
    elif fmt==6:
        for by in range(0,height,4):
            for bx in range(0,width,4):
                block=read(data_ptr+((by//4)*((width+3)//4)+(bx//4))*64,64)
                for i in range(16):put(bx+i%4,by+i//4,_rgba8(block,i))
    elif fmt==14:
        for by in range(0,height,8):
            for bx in range(0,width,8):
                block=read(data_ptr+((by//8)*((width+7)//8)+(bx//8))*32,32)
                for sub in range(4):
                    sx=(sub&1)*4; sy=(sub>>1)*4; colors=_cmpr(block[sub*8:sub*8+8])
                    for i,color in enumerate(colors):put(bx+sx+i%4,by+sy+i//4,color)
    else:return None
    return {'width':width,'height':height,'format':fmt,'rgba':bytes(out)}

def rgba_png(texture):
    """Create a dependency-free PNG data URI payload for a decoded texture."""
    w,h=texture['width'],texture['height']; raw=b''.join(b'\0'+texture['rgba'][y*w*4:(y+1)*w*4] for y in range(h))
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',w,h,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw,9))+chunk(b'IEND',b'')
    return png



def decode_png_rgba(raw):
    """Decode common 8-bit PNG images into ``(width, height, rgba)``.

    This intentionally stays dependency-free for author-owned glTF imports.
    It supports grayscale, grayscale-alpha, RGB, RGBA, and indexed PNGs with
    optional transparency. Interlaced or non-8-bit PNGs are rejected so the
    viewer never presents a partially decoded texture as correct.
    """
    if not isinstance(raw,(bytes,bytearray)) or bytes(raw[:8])!=b"\x89PNG\r\n\x1a\n": raise ValueError("not a PNG image")
    pos=8; width=height=bit_depth=color_type=None; palette=None; transparency=None; compressed=bytearray()
    while pos+8<=len(raw):
        length=struct.unpack_from(">I",raw,pos)[0]; kind=bytes(raw[pos+4:pos+8]); start=pos+8; end=start+length
        if end+4>len(raw): raise ValueError("truncated PNG chunk")
        data=bytes(raw[start:end]); pos=end+4
        if kind==b"IHDR":
            if length!=13: raise ValueError("invalid PNG IHDR")
            width,height,bit_depth,color_type,compression,filter_method,interlace=struct.unpack(">IIBBBBB",data)
            if bit_depth!=8 or compression!=0 or filter_method!=0 or interlace!=0: raise ValueError("unsupported PNG layout")
        elif kind==b"PLTE": palette=[tuple(data[i:i+3]) for i in range(0,len(data)-2,3)]
        elif kind==b"tRNS": transparency=data
        elif kind==b"IDAT": compressed.extend(data)
        elif kind==b"IEND": break
    if not width or not height or color_type not in (0,2,3,4,6): raise ValueError("unsupported PNG color type")
    channels={0:1,2:3,3:1,4:2,6:4}[color_type]; stride=width*channels; decoded=zlib.decompress(bytes(compressed)); expected=height*(stride+1)
    if len(decoded)<expected: raise ValueError("truncated PNG pixel data")
    rows=[]; cursor=0; previous=bytearray(stride)
    def paeth(a,b,c):
        estimate=a+b-c; pa=abs(estimate-a); pb=abs(estimate-b); pc=abs(estimate-c); return a if pa<=pb and pa<=pc else (b if pb<=pc else c)
    for _ in range(height):
        mode=decoded[cursor]; cursor+=1; source=decoded[cursor:cursor+stride]; cursor+=stride; row=bytearray(stride)
        for i,value in enumerate(source):
            left=row[i-channels] if i>=channels else 0; up=previous[i]; up_left=previous[i-channels] if i>=channels else 0
            if mode==0: row[i]=value
            elif mode==1: row[i]=(value+left)&255
            elif mode==2: row[i]=(value+up)&255
            elif mode==3: row[i]=(value+((left+up)//2))&255
            elif mode==4: row[i]=(value+paeth(left,up,up_left))&255
            else: raise ValueError("unsupported PNG filter")
        rows.append(row); previous=row
    out=bytearray(width*height*4)
    for y,row in enumerate(rows):
        for x in range(width):
            values=row[x*channels:(x+1)*channels]
            if color_type==6: color=tuple(values)
            elif color_type==2: color=(*values,255)
            elif color_type==4: color=(values[0],values[0],values[0],values[1])
            elif color_type==0: color=(values[0],values[0],values[0],transparency[1] if transparency and len(transparency)>=2 and values[0]==transparency[1] else 255)
            else:
                index=values[0]; rgb=palette[index] if palette and index<len(palette) else (255,0,255); alpha=transparency[index] if transparency and index<len(transparency) else 255; color=(*rgb,alpha)
            out[(y*width+x)*4:(y*width+x+1)*4]=bytes(color)
    return {"width":width,"height":height,"rgba":bytes(out),"mapped":True,"format":"PNG"}
