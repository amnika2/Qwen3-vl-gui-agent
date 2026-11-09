"""Prepare pinned app dependencies in project-owned directories."""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--case',choices=['sweeper-107','sweeper-142'],action='append')
    args=parser.parse_args();root=Path(__file__).resolve().parents[1]
    selected=args.case or ['sweeper-107','sweeper-142']
    evidence={}
    for case in selected:
        subprocess.run([sys.executable,str(root/'scripts/fetch_sources.py'),'--case',case],cwd=root,check=True)
        source=json.loads((root/'cases'/case/'private.json').read_text());base=root/'vendor/apps'/source['slug']/'base'
        if case=='sweeper-107':
            fixed=base.parent/'fixed'
            if (base/'composer.lock').read_bytes()!=(fixed/'composer.lock').read_bytes():
                raise ValueError('MantisBT dependency locks differ between builds')
            subprocess.run([str(root/'vendor/runtime/php'),str(root/'vendor/runtime/composer.phar'),
                'install','--no-dev','--no-interaction','--no-progress','--prefer-dist','--no-scripts'],cwd=base,check=True)
            evidence[case]={'lock_sha256':hashlib.sha256((base/'composer.lock').read_bytes()).hexdigest()}
        else:
            subprocess.run(['git','submodule','update','--init','--recursive'],cwd=base,check=True)
            runtime=root/'vendor/phpipam-runtime';runtime.mkdir(exist_ok=True)
            for package in ('php8.3-gmp','php-pear'):
                packages=list(runtime.glob(package+'_*.deb'))
                if not packages:
                    subprocess.run(['apt-get','download',package],cwd=runtime,check=True)
                    packages=list(runtime.glob(package+'_*.deb'))
                for path in packages:
                    subprocess.run(['dpkg-deb','-x',str(path),str(runtime/'root')],check=True)
            evidence[case]={'submodules':subprocess.check_output(['git','submodule','status','--recursive'],cwd=base,text=True).splitlines(),
                'runtime_packages':{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in runtime.glob('*.deb')}}
        evidence[case].update(base_commit=source['base_commit'],reference_commit=source['reference_commit'])
    (root/'docs/mantis-phpipam-dependencies.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
