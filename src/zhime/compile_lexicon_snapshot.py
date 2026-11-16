"""Compile the bundled lexicon into the embedded Python runtime's snapshot format."""
import json
from pathlib import Path

from .engine import Engine


def main():
    config=Path('artifacts/deployment-local-gpu.json')
    profile=json.loads(config.read_text(encoding='utf-8'))
    database=Path('artifacts/lexicon/lexicon.sqlite')
    engine=Engine(str(database),beam=profile['beam'],fuzzy_penalty=profile['fuzzy_penalty'])
    profile['lexicon_snapshot_sha256']=engine.write_snapshot(database.with_suffix('.marshal'))
    temporary=config.with_suffix('.tmp')
    temporary.write_text(json.dumps(profile,indent=2)+'\n',encoding='utf-8')
    temporary.replace(config)


if __name__=='__main__':
    main()
