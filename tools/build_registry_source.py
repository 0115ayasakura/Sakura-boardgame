"""Create a root-manifest distribution tree from the development repository.
The registry branch/tag must contain this tree, NOT the development repository root.
"""
from pathlib import Path
import argparse, hashlib, json, shutil
from build_release import PLUGIN, selected


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('output',type=Path)
    args=parser.parse_args(); target=args.output.resolve()
    # Never clear or overwrite an existing tree; publish tooling owns Git operations.
    assert not target.exists(), 'Use a new empty destination'
    files=sorted(p for p in PLUGIN.rglob('*') if p.is_file() and selected(p))
    total=sum(p.stat().st_size for p in files)
    assert total <= 32*1024*1024 and len(files)<=512, (total,len(files))
    assert all(p.stat().st_size <= 16*1024*1024 for p in files)
    assert sum(p.name=='plugin.yaml' for p in files)==1
    target.mkdir(parents=True)
    for path in files:
        relative=path.relative_to(PLUGIN); out=target/relative
        out.parent.mkdir(parents=True,exist_ok=True); shutil.copyfile(path,out)
        assert hashlib.sha256(out.read_bytes()).digest()==hashlib.sha256(path.read_bytes()).digest()
    assert (target/'plugin.yaml').is_file() and (target/'plugin.py').is_file()
    print(json.dumps({'root':str(target),'files':len(files),'uncompressed_bytes':total,'limit_bytes':32*1024*1024,'largest_file_bytes':max(p.stat().st_size for p in files)}))
if __name__=='__main__':main()
