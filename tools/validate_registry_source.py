"""Validate a staged root source with the official registry module on PYTHONPATH.
No plugin execution, host settings, or network access.
"""
from pathlib import Path
import argparse, hashlib, io, json, struct, zipfile
from registry.core import package_source, MAX_PACKAGE_BYTES, MAX_FILE_BYTES


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('source',type=Path)
    args=parser.parse_args(); root=args.source.resolve()
    commit='0'*40; repository='https://github.com/0115ayasakura/Sakura-boardgame'
    buf=io.BytesIO()
    files=sorted(p for p in root.rglob('*') if p.is_file() and '.git' not in p.relative_to(root).parts)
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as archive:
        for path in files: archive.writestr('Sakura-boardgame-'+commit+'/'+path.relative_to(root).as_posix(),path.read_bytes())
    package, manifest=package_source(buf.getvalue(),repository,commit)
    assert manifest['id']=='local.sakura.boardgame' and manifest['version']=='1.0.3-pre'
    assert (root/'plugin.py').is_file()
    black=root/'art/black_train'
    for name, metadata in json.loads((black/'manifest.json').read_text(encoding='utf-8'))['files'].items():
        data=(black/name).read_bytes()
        assert len(data)==metadata['size'] and hashlib.sha256(data).hexdigest()==metadata['sha256']
    for relative in ('art/black_train/station_black_gold.glb','art/board/station_remake.glb'):
        raw=(root/relative).read_bytes(); magic,version,length=struct.unpack_from('<III',raw)
        assert magic==0x46546c67 and version==2 and length==len(raw)
        size,kind=struct.unpack_from('<II',raw,12); assert kind==0x4e4f534a
        doc=json.loads(raw[20:20+size]); binary=raw[28+size:]
        assert len(binary)>=doc['buffers'][0]['byteLength']
        for view in doc['bufferViews']:
            assert view.get('buffer',0)==0 and view.get('byteOffset',0)%4==0
            assert view.get('byteOffset',0)+view['byteLength']<=doc['buffers'][0]['byteLength']
        from PIL import Image
        for image in doc.get('images',[]):
            if 'bufferView' in image:
                view=doc['bufferViews'][image['bufferView']]; start=view.get('byteOffset',0)
                with Image.open(io.BytesIO(binary[start:start+view['byteLength']])) as pic: pic.verify()
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert archive.testzip() is None
        assert all(n.startswith(manifest['id']+'/') for n in archive.namelist())
        assert len(archive.namelist())==len(files)
    output=root.parent/'sakura-boardgame-plugin.zip'
    output.write_bytes(package)
    (root.parent/'SHA256SUMS.txt').write_text(hashlib.sha256(package).hexdigest()+'  '+output.name+'\n',encoding='ascii')
    print(json.dumps({'official_registry_validation':'passed','id':manifest['id'],'version':manifest['version'],'files':len(files),'uncompressed_bytes':sum(p.stat().st_size for p in files),'limit':MAX_PACKAGE_BYTES,'largest_file_bytes':max(p.stat().st_size for p in files),'file_limit':MAX_FILE_BYTES,'registry_zip_bytes':len(package),'glb_images_and_buffers':'passed'}))
if __name__=='__main__':main()
