"""Assemble an offline x64 ZhIME installation directory and ZIP."""
import argparse
import importlib.metadata
import importlib.util
from pathlib import Path
import shutil
import subprocess
import zipfile


DEPENDENCIES = {
    'torch': 'torch', 'sentencepiece': 'sentencepiece', 'filelock': 'filelock',
    'typing_extensions': 'typing_extensions', 'networkx': 'networkx', 'jinja2': 'jinja2',
    'markupsafe': 'markupsafe', 'fsspec': 'fsspec', 'sympy': 'sympy', 'mpmath': 'mpmath',
    'numpy': 'numpy',
}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-zip',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--runtime-root',type=Path)
    parser.add_argument('--build-dir',type=Path)
    parser.add_argument('--zip',action='store_true')
    args=parser.parse_args()
    root=Path(__file__).resolve().parent.parent
    runtime=(args.runtime_root or root.parent/'zhime-local').resolve()
    release=(args.build_dir or runtime/'build-dev').resolve()
    if args.build_dir is None and not (release/'native-dll.txt').exists():
        release=runtime/'native'/'build'
    out=args.output.resolve()
    out.mkdir(parents=True)
    python=out/'python'
    with zipfile.ZipFile(args.python_zip) as archive:
        archive.extractall(python)
    (python/'python310._pth').write_text('python310.zip\n.\n..\nsite-packages\n',encoding='ascii')
    packages=python/'site-packages'
    packages.mkdir()
    for module, package in DEPENDENCIES.items():
        spec=importlib.util.find_spec(module)
        location=Path(spec.origin)
        if spec.submodule_search_locations:
            shutil.copytree(location.parent,packages/module,ignore=shutil.ignore_patterns('__pycache__'))
            libs=location.parent.with_name(f'{module}.libs')
            if libs.is_dir():
                shutil.copytree(libs,packages/libs.name)
        else:
            shutil.copy2(location,packages/location.name)
        info=Path(importlib.metadata.distribution(package)._path)
        shutil.copytree(info,packages/info.name)
    for module in ('torchgen','functorch'):
        location=Path(importlib.util.find_spec(module).origin).parent
        shutil.copytree(location,packages/module,ignore=shutil.ignore_patterns('__pycache__'))
    (out/'zhime').mkdir()
    for name in ('__init__','autostart','independent_server','model','engine','joint_score','guided_search','rerank','suggest','compile_lexicon_snapshot'):
        shutil.copy2(root/'src'/'zhime'/f'{name}.py',out/'zhime')
    dll_name=(release/'native-dll.txt').read_text().strip()
    shutil.copy2(release/'Release'/dll_name,out/'ZhIME.dll')
    for name in ('Install-ZhIME.cmd','Uninstall-ZhIME.cmd'):
        shutil.copy2(root/'tools'/name,out)
    for name in ('install-native.ps1','LICENSE','THIRD_PARTY.md'):
        shutil.copy2(root/name,out)
    shutil.copy2(root/'tools'/'DISTRIBUTION.md',out/'README.md')
    for name in ('artifacts/deployment-local-gpu.json','artifacts/tokenizer.model',
                 'artifacts/lexicon/lexicon.sqlite','artifacts/lexicon/manifest.json','artifacts/200m/model-200m.pt'):
        dest=out/name
        dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(runtime/name,dest)
    subprocess.run([str(python/'python.exe'),'-m','zhime.compile_lexicon_snapshot'],cwd=out,check=True)
    shutil.copytree(root/'licenses',out/'licenses')
    (out/'runtime').mkdir()
    (out/'automatic-start.enabled').touch()
    if args.zip:
        destination=out.with_suffix('.zip')
        partial=destination.with_suffix('.partial')
        with zipfile.ZipFile(partial,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
            for file in sorted(out.rglob('*')):
                if file.is_file() and '__pycache__' not in file.parts:
                    archive.write(file,file.relative_to(out.parent))
            archive.writestr(out.name+'/runtime/','')
        with zipfile.ZipFile(partial) as archive:
            if archive.testzip() is not None:
                raise ValueError('Archive verification failed')
        partial.replace(destination)
    print(out,flush=True)


if __name__=='__main__':
    main()
