"""Build and validate the installable plugin ZIP; no host data is accessed."""
from pathlib import Path
import hashlib, json, tempfile, zipfile

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'sakura_boardgame'
OUT = ROOT / 'release-assets'

def selected(p):
    rel = p.relative_to(PLUGIN)
    if '__pycache__' in rel.parts or 'tests' in rel.parts or rel.as_posix() == 'art/board/board_scene.glb':
        return False
    if len(rel.parts) == 1:
        return p.suffix == '.py' or p.name in {'plugin.yaml','board.html','board_layout.json','README.md','AGENT_CONTROL.md','THIRD_PARTY_NOTICES.md','LICENSE'}
    if rel.parts[0] == 'themes':
        return p.suffix == '.json'
    return rel.parts[0] == 'art' and p.suffix.lower() in {'.js','.json','.png','.jpg','.svg','.glb'}

def main():
    files = sorted(p for p in PLUGIN.rglob('*') if p.is_file() and selected(p))
    assert files
    manifest = (PLUGIN/'plugin.yaml').read_text(encoding='utf-8')
    assert 'version: 1.0.3-pre' in manifest
    assert 'min_app_version: 1.3.6' in manifest
    required = {'plugin.py','agent_turns.py','agent_snapshot.py','board_server.py','board.html','board_layout.json','LICENSE','THIRD_PARTY_NOTICES.md'}
    assert required <= {p.name for p in files}
    for p in files:
        if p.suffix == '.py': compile(p.read_bytes(), str(p.relative_to(ROOT)), 'exec')
        if p.suffix == '.json': json.loads(p.read_text(encoding='utf-8'))
    black = PLUGIN/'art/black_train'
    for name, meta in json.loads((black/'manifest.json').read_text())['files'].items():
        data = (black/name).read_bytes()
        assert len(data) == meta['size'] and hashlib.sha256(data).hexdigest() == meta['sha256'], name
    OUT.mkdir(exist_ok=True)
    archive = OUT/'sakura-boardgame-plugin.zip'
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in files:
            info=zipfile.ZipInfo('sakura_boardgame/'+p.relative_to(PLUGIN).as_posix(), (2026,10,23,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            info.external_attr=0o100644 << 16
            z.writestr(info,p.read_bytes())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
        assert len(z.namelist()) == len(files)
        assert all(not any(t in n.split('/') for t in ('__pycache__','tests','config.json','state.json','.env')) for n in z.namelist())
        with tempfile.TemporaryDirectory() as tmp:
            z.extractall(tmp)
            for p in files:
                rel=Path('sakura_boardgame')/p.relative_to(PLUGIN)
                assert (Path(tmp)/rel).read_bytes() == p.read_bytes()
    digest=hashlib.sha256(archive.read_bytes()).hexdigest()
    (OUT/'SHA256SUMS.txt').write_text(digest+'  '+archive.name+'\n',encoding='ascii')
    print(json.dumps({'archive':archive.name,'files':len(files),'bytes':archive.stat().st_size,'sha256':digest,'syntax_json_assets_zip':'passed'},ensure_ascii=False))
if __name__ == '__main__': main()
