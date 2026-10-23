"""Optimize embedded textures without changing GLB geometry or requiring decoders.
Run once against a staging copy or development source; requires Pillow.
"""
from pathlib import Path
from io import BytesIO
import argparse, hashlib, json, struct
from PIL import Image


def optimize_glb(path, max_dimension=1024):
    raw = path.read_bytes()
    magic, version, length = struct.unpack_from('<III', raw)
    assert magic == 0x46546C67 and version == 2 and length == len(raw)
    chunks, offset = [], 12
    while offset < length:
        size, kind = struct.unpack_from('<II', raw, offset)
        chunks.append((kind, raw[offset+8:offset+8+size]))
        offset += size + 8
    assert len(chunks) == 2 and chunks[0][0] == 0x4E4F534A and chunks[1][0] == 0x004E4942
    doc = json.loads(chunks[0][1]); binary = chunks[1][1]
    original_doc = json.loads(chunks[0][1])
    replacements = {}; changed = 0
    for image in doc.get('images', []):
        if 'bufferView' not in image:
            continue
        view = doc['bufferViews'][image['bufferView']]
        start = view.get('byteOffset', 0)
        data = binary[start:start+view['byteLength']]
        with Image.open(BytesIO(data)) as picture:
            picture.load()
            if max(picture.size) <= max_dimension:
                continue
            picture.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            output = BytesIO()
            # Preserve alpha and texture color-space interpretation; no JPEG or mesh changes.
            picture.save(output, format='PNG', optimize=True)
            candidate = output.getvalue()
        if len(candidate) < len(data):
            replacements[image['bufferView']] = candidate
            image['mimeType'] = 'image/png'
            changed += 1
    if not replacements:
        return {'file': path.name, 'before': len(raw), 'after': len(raw), 'textures': 0}
    packed = bytearray()
    for index, view in enumerate(doc['bufferViews']):
        start = view.get('byteOffset', 0)
        payload = replacements.get(index, binary[start:start+view['byteLength']])
        packed.extend(b'\0' * (-len(packed) % 4))
        view['byteOffset'] = len(packed); view['byteLength'] = len(payload)
        packed.extend(payload)
    doc['buffers'][0]['byteLength'] = len(packed)
    # Every non-image view (including interleaved vertex/index data) remains byte-identical.
    for index, old in enumerate(original_doc['bufferViews']):
        if index not in replacements:
            new = doc['bufferViews'][index]
            assert binary[old.get('byteOffset',0):old.get('byteOffset',0)+old['byteLength']] == packed[new['byteOffset']:new['byteOffset']+new['byteLength']]
    for field in ('accessors','meshes','nodes','scenes','materials','animations','skins','textures','samplers'):
        assert doc.get(field) == original_doc.get(field), field
    text = json.dumps(doc, ensure_ascii=False, separators=(',', ':')).encode()
    text += b' ' * (-len(text) % 4); packed.extend(b'\0' * (-len(packed) % 4))
    result = struct.pack('<III', magic, version, 28+len(text)+len(packed)) + struct.pack('<II',len(text),0x4E4F534A)+text+struct.pack('<II',len(packed),0x004E4942)+packed
    path.write_bytes(result)
    return {'file': path.name, 'before':len(raw), 'after':len(result), 'textures':changed, 'max_texture_dimension':max_dimension}


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('plugin',type=Path)
    args=parser.parse_args()
    report=[]
    for name in ('art/board/station_remake.glb','art/black_train/station_black_gold.glb'):
        report.append(optimize_glb(args.plugin/name))
    manifest_path=args.plugin/'art/black_train/manifest.json'
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    for name, metadata in manifest['files'].items():
        data=(manifest_path.parent/name).read_bytes()
        metadata.update(size=len(data), sha256=hashlib.sha256(data).hexdigest())
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))
if __name__=='__main__': main()
