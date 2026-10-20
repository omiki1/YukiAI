"""Build a local, model-free Windows test bundle from explicit inputs only."""
import argparse
import hashlib
from importlib import metadata
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
from uuid import uuid4
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def audit_bundle(folder, private_root=ROOT):
    """Do not copy personal settings, stories, Core runtime, or character assets."""
    secrets=[]
    from scripts.yuki_config import ConfigStore
    config=ConfigStore(private_root/'.yuki.local.json',env={}).data
    for item in (config['llm'],config['voice']['fish'],config['voice']['openai']):
        if len(item['api_key'])>=8:secrets.append(item['api_key'].encode())
    env=private_root/'.env.local'
    if env.is_file():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):continue
            key,value=line.split('=',1);value=value.strip().strip('"\'')
            if re.search(r'KEY|TOKEN|SECRET|PASSWORD',key,re.I) and len(value)>=8:secrets.append(value.encode())
    files=0
    for path in folder.rglob('*'):
        if path.is_symlink():raise ValueError('Bundle contains a symbolic link.')
        if not path.is_file():continue
        name=path.relative_to(folder).as_posix().lower();files+=1
        if (any(part in name.split('/') for part in ('model','chat_history','memory_backups','.cache','logs','live2d-core'))
                or path.name in ('.env.local','.yuki.local.json','story.sqlite3')
                or path.suffix.lower() in ('.sqlite3','.moc3','.kra','.psd','.wav','.mp3')
                or path.name.endswith(('.model3.json','.exp3.json','.motion3.json'))):
            raise ValueError('Bundle contains excluded private/model content: '+name)
        with path.open('rb') as stream:
            previous=b''
            while data:=stream.read(1024*1024):
                block=previous+data
                if any(value in block for value in secrets):raise ValueError('Bundle matches a configured secret; no archive created.')
                previous=block[-4096:]
    return {'passed':True,'files':files,'configured_secret_values_checked':len(secrets)}


def finalize_bundle(bundle):
    (bundle/'qt.conf').write_text('[Paths]\nPrefix = _internal/PyQt5/Qt5\n',encoding='utf-8')
    report=audit_bundle(bundle);output=bundle.parent
    archive=output/'YukiAI-Windows-Alpha.zip'
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
        for file in bundle.rglob('*'):
            if file.is_file():package.write(file,file.relative_to(output))
    with archive.open('rb') as content:checksum=hashlib.file_digest(content,'sha256').hexdigest()
    report.update(archive=str(archive),sha256=checksum)
    (output/'build-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--refresh-web',type=Path,help='Only when Python is unchanged: refresh an existing local test bundle UI.')
    args=parser.parse_args()
    if sys.platform!='win32':raise SystemExit('Build this Windows bundle on Windows.')
    if metadata.version('pyinstaller')!='6.22.3':raise SystemExit('Install requirements-build.txt in an isolated environment.')
    subprocess.run(['npm.cmd','run','check'],cwd=ROOT/'app',check=True)
    subprocess.run(['npm.cmd','exec','--','vite','build','--mode','alpha'],cwd=ROOT/'app',check=True)
    web=ROOT/'app/dist-alpha'
    if any(path.is_dir() for path in web.iterdir() if path.name!='assets'):raise ValueError('Unexpected Alpha web resources.')
    if args.refresh_web:
        bundle=args.refresh_web.resolve();target=(bundle/'_internal/app/dist').resolve()
        if (not target.is_relative_to((ROOT/'artifacts').resolve()) or not (bundle/'YukiAI.exe').is_file()
                or not (target/'index.html').is_file()):raise ValueError('Select an existing bundle inside this workspace artifacts directory.')
        previous=[file for file in target.rglob('*') if file.is_file()]
        if any(file.suffix not in ('.html','.js','.css') for file in previous):raise ValueError('Unexpected existing UI files; use a full build.')
        for file in previous:file.unlink()
        shutil.copytree(web,target,dirs_exist_ok=True)
        finalize_bundle(bundle)
        return
    token=uuid4().hex[:10];work=ROOT/'.cache'/('alpha-build-'+token);work.mkdir(parents=True)
    output=ROOT/'artifacts'/('alpha-'+token);output.mkdir(parents=True)
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--clean','--onedir','--windowed',
                    '--name','YukiAI','--distpath',str(output),'--workpath',str(work/'work'),'--specpath',str(work),
                    '--add-data',str(web)+':app/dist','--collect-submodules','uvicorn',str(ROOT/'start_yuki.py')],cwd=ROOT,check=True)
    bundle=output/'YukiAI'
    (bundle/'START_HERE.txt').write_text(
        'YukiAI Windows 本机 Alpha 测试包\n\n解压整个文件夹，双击 YukiAI.exe，无需安装 Python、Node.js 或角色素材。\n'
        '点 Yuki 按钮打开小窗；首次使用引导中可配置自己的聊天接口，或先体验三句自编故事。\n'
        '本包使用纸签界面，不含 Live2D、私人模型、取词工具、密钥或阅读资料。取词尚未准备时请用粘贴。\n'
        '记录和设置位于 %LOCALAPPDATA%\\YukiAI；删除程序目录不会清空这些记录。记忆管理可以备份和清理。\n'
        '聊天仅在你发送/测试时调用所配置服务，可能产生费用。选中的已读原文、问题及相关讨论会发送给该服务。\n'
        '语音默认关闭。本包不是已完成真实玩家验收的正式发行版。\n'
        '项目：https://github.com/omiki1/YukiAI\n',encoding='utf-8')
    notices=bundle/'dependency-licenses';notices.mkdir()
    versions=[]
    for distribution in sorted(metadata.distributions(),key=lambda item:item.metadata['Name'].lower()):
        name=distribution.metadata['Name'];versions.append(name+'=='+distribution.version)
        for item in distribution.files or ():
            if ('license' in item.name.lower() or 'copying' in item.name.lower()) and '.dist-info' in str(item):
                source=Path(distribution.locate_file(item))
                if source.is_file():
                    target=notices/name/str(item).split('.dist-info/',1)[-1];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    (bundle/'BUILD_DEPENDENCIES.txt').write_text('\n'.join(versions)+'\n',encoding='utf-8')
    finalize_bundle(bundle)


if __name__=='__main__':main()
