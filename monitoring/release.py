"""Prepare a native Supervisely release source folder without development files."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile


def prepare(image, instance_version, output):
    # Use a concrete deployment image, never an invented published registry name.
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._:/@-]+', image) or (':' not in image.rsplit('/', 1)[-1]):
        raise ValueError('Supply the built image with an explicit tag or sha256 digest.')
    if not re.fullmatch(r'\d+\.\d+\.\d+', instance_version):
        raise ValueError('Supply the Supervisely instance version validated by the live pilot (x.y.z).')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Output already exists. Choose a new release directory.')
    source = Path(__file__).resolve().parents[1]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='monitor-release-', dir=output.parent) as staging:
        root = Path(staging) / 'release'
        root.mkdir()
        shutil.copytree(source / 'monitoring', root / 'monitoring',
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        shutil.copytree(source / 'examples' / 'organiser-demo', root / 'examples' / 'organiser-demo')
        config = json.loads((source / 'config.json').read_text())
        config.update(docker_image=image, instance_version=instance_version,
                      min_instance_version=instance_version, entrypoint='python -m monitoring.run --production')
        (root / 'config.json').write_text(json.dumps(config, indent=2) + '\n', encoding='utf-8')
        shutil.copytree(source / 'docs', root / 'docs')
        shutil.copyfile(source / 'README.md', root / 'README.md')
        manifest = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in sorted(root.rglob('*')) if p.is_file()}
        (root / 'release-checksums.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        root.rename(output)
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Image built from Dockerfile and pushed to your registry, including tag/digest.')
    parser.add_argument('--instance-version', required=True, help='Actual Supervisely version verified in the pilot.')
    parser.add_argument('--output', default='dist/supervisely-release')
    args = parser.parse_args()
    try:
        result = prepare(args.image, args.instance_version, args.output)
    except ValueError as exc:
        parser.error(str(exc))
    print(f'Release sources prepared: {result}. Image publishing and live validation are separate steps.')


if __name__ == '__main__':
    main()
