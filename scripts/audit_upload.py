"""Audit the exact Git index without printing secret values."""
import json
import hashlib
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.gitignore','.env.example','README.md','requirements.txt','start_yuki.py','start_yuki.cmd','rebuild_frontend.cmd'}
ROOT_FILES.update({'requirements-build.txt','setup_yuki.cmd'})
APP_FILES={'app/'+x for x in ['package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html']}
SCRIPT_FILES={'scripts/'+x for x in ['settings.py','yuki_chat.py','yuki_history.py','yuki_server.py','yuki_window.py','yuki_config.py','yuki_speech.py','yuki_story.py','yuki_capture.py','install_textractor.py','test_capture.py','verify_capture_native.py','verify_capture.cjs','test_story_memory.py','verify_story.cjs','test_window_backend.py','test_window_settings.py','verify_window.cjs','audit_upload.py']}
CORE_FILES={'app/vendor/live2d/Core/'+x for x in ['live2dcubismcore.d.ts','LICENSE.md','RedistributableFiles.txt']}
SCRIPT_FILES.update({'scripts/yuki_memory.py','scripts/test_memory_management.py','scripts/verify_memory.cjs'})
SCRIPT_FILES.update({'scripts/test_continuity.py','scripts/verify_continuity.cjs'})
SCRIPT_FILES.update({'scripts/test_timeline.py','scripts/verify_timeline.cjs'})
SCRIPT_FILES.add('scripts/verify_themes.cjs')
SCRIPT_FILES.add('scripts/verify_speech_queue.cjs')
SCRIPT_FILES.add('scripts/verify_speech_latency.cjs')
SCRIPT_FILES.update({'scripts/yuki_screen.py','scripts/yuki_game_reader.py','scripts/yuki_locator.py',
                     'scripts/test_game_reading.py','scripts/verify_game_reading.cjs','scripts/verify_local_ocr.py'})
SCRIPT_FILES.add('scripts/verify_capture_setup.cjs')
SCRIPT_FILES.update({'scripts/yuki_characters.py','scripts/test_characters.py','scripts/verify_characters.cjs'})
SCRIPT_FILES.update({'scripts/yuki_paths.py','scripts/build_alpha.py','scripts/test_delivery.py','scripts/verify_delivery.cjs'})
OTHER_FILES={'docs/PUBLISHING.md','docs/SYSTEM.md','docs/WORKLOG.md','third_party/README.md','third_party/Open-LLM-VTuber-MIT.txt'}
OTHER_FILES.add('docs/PRODUCT_ROADMAP.md')
PUBLIC_UI_FONTS={'app/src/fonts/fusion-pixel-12-ui.woff2':{
    'sha256':'ca8e020adbf7641a47c71e35ee1cfa0551f5efc48177c64b474dba741dfc6c30',
    'license':'app/src/fonts/fusion-pixel-OFL.txt',
    'license_sha256':'bc518cf64b8032c07690f33cc270c35c179255a6ac8efa7c165ebae7e8f76a63'}}
EXTENSIONS={'.pem','.key','.p12','.pfx','.moc3','.png','.jpg','.jpeg','.webp','.kra','.psd','.ora','.zip','.wav','.mp3','.mp4','.onnx','.safetensors','.pt','.pth','.ckpt','.gguf'}
PATTERNS=[rb'\b(?:gh[pousr]_[A-Za-z0-9_]{30,}|github_pat_[A-Za-z0-9_]{40,})\b',
          rb'\bsk-(?:fish-|proj-)?[A-Za-z0-9_-]{20,}\b',
          rb'\bapikey_[A-Za-z0-9_]{25,}\b',rb'\buser_[A-Za-z0-9]{50,}\b',
          rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----']

def git(*args,input=None):
    return subprocess.run(['git',*args],cwd=ROOT,input=input,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True).stdout

def allowed(path):
    return (path in ROOT_FILES|APP_FILES|SCRIPT_FILES|CORE_FILES|OTHER_FILES or
            path.startswith('app/src/') or path.startswith('app/vendor/live2d/Framework/'))

def main():
    secrets=[]
    env=ROOT/'.env.local'
    if env.exists():
        for line in env.read_text(encoding='utf-8-sig').splitlines():
            if '=' not in line or line.lstrip().startswith('#'):continue
            key,value=line.split('=',1)
            if re.search(r'KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL',key,re.I):
                value=value.strip().strip('"\'')
                if len(value)>=8:secrets.append(value.encode())
    local_config=ROOT/'.yuki.local.json'
    if local_config.exists():
        def collect(value):
            if isinstance(value,dict):
                for key,item in value.items():
                    if key=='api_key' and isinstance(item,str) and len(item)>=8:secrets.append(item.encode())
                    else:collect(item)
            elif isinstance(value,list):
                for item in value:collect(item)
        collect(json.loads(local_config.read_text(encoding='utf-8')))
    entries=[]
    for record in git('ls-files','--stage','-z').split(b'\0'):
        if not record:continue
        meta,filename=record.split(b'\t',1)
        mode,sha,stage=meta.split()
        entries.append((filename.decode('utf-8'),sha.decode(),mode))
    if not entries:raise SystemExit('No staged source files to audit.')
    staged_paths={path for path,_,_ in entries}
    font_licenses={path:git('show',':'+path) for path in {font['license'] for font in PUBLIC_UI_FONTS.values()} if path in staged_paths}
    request=''.join(sha+'\n' for _,sha,_ in entries).encode()
    batch=git('cat-file','--batch',input=request)
    cursor=0;issues=[];total=0
    for path,sha,mode in entries:
        end=batch.index(b'\n',cursor)
        header=batch[cursor:end].split();size=int(header[-1]);cursor=end+1
        data=batch[cursor:cursor+size];cursor+=size+1;total+=size
        if not allowed(path):issues.append((path,'outside source allowlist'))
        if mode not in (b'100644',b'100755'):issues.append((path,'not a regular source file'))
        if Path(path).suffix.lower() in EXTENSIONS or path.endswith(('.model3.json','.exp3.json','.motion3.json')):
            issues.append((path,'model/media/private artifact'))
        font=PUBLIC_UI_FONTS.get(path)
        if font:
            if len(data)>65536 or not data.startswith(b'wOF2') or hashlib.sha256(data).hexdigest()!=font['sha256']:
                issues.append((path,'UI font differs from the reviewed subset'))
            license_data=font_licenses.get(font['license'])
            if license_data is None or hashlib.sha256(license_data).hexdigest()!=font['license_sha256']:
                issues.append((path,'missing or changed staged font license'))
        elif b'\x00' in data:issues.append((path,'binary content'))
        if Path(path).suffix.lower() in ('.woff','.woff2','.ttf','.otf') and not font:
            issues.append((path,'font outside the exact UI resource allowlist'))
        if any(value in data for value in secrets):issues.append((path,'matches a configured secret'))
        if any(re.search(pattern,data) for pattern in PATTERNS):issues.append((path,'credential-like content'))
        if path=='.env.example':
            for line in data.decode('utf-8').splitlines():
                if re.match(r'^[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=.+',line):issues.append((path,'nonempty example credential'))
    report={'passed':not issues,'sourceFiles':len(entries),'bytes':total,'configuredSecretValuesChecked':len(secrets),'issues':[{'path':p,'reason':r} for p,r in issues]}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if issues:raise SystemExit(1)

if __name__=='__main__':main()
