"""Create a clean Windows transfer ZIP without local data or development tools."""
from pathlib import Path
import sys
import zipfile


def main():
    root = Path(__file__).resolve().parents[1]
    destination = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv) > 1 else root / 'dist' / 'QatYar-Windows.zip'
    destination.parent.mkdir(parents=True, exist_ok=True)
    files = [root / name for name in ('README.md', 'VERSION', 'requirements.txt', 'requirements.lock', 'run.py')]
    for directory in ('store', 'windows'):
        files.extend(path for path in (root / directory).rglob('*') if path.is_file()
                     and '__pycache__' not in path.parts)
    files.extend((root / 'docs' / name for name in ('architecture.md', 'accounting-transfer.md')))
    files = sorted(files)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in files:
            archive.write(path, 'QatYar/' + str(path.relative_to(root)).replace('\\', '/'))
    print(f'{destination} ({len(files)} files, {destination.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
