"""Install the pinned official Textractor CLI locally; no game is attached."""
import hashlib
from pathlib import Path
import shutil
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
VERSION = '5.2.0'
URL = f'https://github.com/Artikash/Textractor/releases/download/v{VERSION}/Textractor-{VERSION}-Zip-Version-English-Only.zip'
SHA256 = '3efdcf390261fcfb6a44220113c68b06eba22a3ef441ac618208238b52ee7974'
FILES = {'TextractorCLI.exe', 'texthook.dll', 'msvcp140.dll', 'msvcp140_1.dll',
         'msvcp140_2.dll', 'msvcp140_atomic_wait.dll', 'msvcp140_codecvt_ids.dll',
         'vcruntime140.dll', 'vcruntime140_1.dll'}


def install(archive=None):
    target = ROOT / 'tools' / 'textractor'
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        staging = Path(temporary)
        package = Path(archive) if archive else staging / 'textractor.zip'
        if archive is None:
            with urllib.request.urlopen(URL, timeout=60) as response, package.open('wb') as output:
                shutil.copyfileobj(response, output)
        if hashlib.sha256(package.read_bytes()).hexdigest() != SHA256:
            raise ValueError('取词工具校验未通过，未安装。')
        with zipfile.ZipFile(package) as source:
            for arch in ('x86', 'x64'):
                folder = target / arch
                folder.mkdir(parents=True, exist_ok=True)
                for name in FILES:
                    member = f'Textractor/{arch}/{name}'
                    if member not in source.namelist():
                        continue  # vcruntime140_1 is only present on x64.
                    destination = folder / name
                    part = staging / (arch + '-' + name)
                    part.write_bytes(source.read(member))
                    part.replace(destination)
        (target / 'SOURCE.txt').write_text(
            f'Textractor {VERSION}, GPL-3.0\nhttps://github.com/Artikash/Textractor\n'
            f'{URL}\nSHA256 {SHA256}\n', encoding='utf-8')
    return target


if __name__ == '__main__':
    print('取词内核已准备：' + str(install()))
