"""Audit the exact Git index without printing secret values."""
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]
ROOT_FILES={'.gitignore','.env.example','README.md','requirements.txt','start_yuki.py','start_yuki.cmd','rebuild_frontend.cmd'}
APP_FILES={'app/'+x for x in ['package.json','package-lock.json','tsconfig.json','vite.config.ts','index.html']}
SCRIPT_FILES={'scripts/'+x for x in ['settings.py','yuki_chat.py','yuki_history.py','yuki_server.py','yuki_window.py','yuki_config.py','yuki_speech.py','yuki_story.py','yuki_capture.py','install_textractor.py','test_capture.py','verify_capture_native.py','verify_capture.cjs','test_story_memory.py','verify_story.cjs','test_window_backend.py','test_window_settings.py','verify_window.cjs','audit_upload.py']}
CORE_FILES={'app/vendor/live2d/Core/'+x for x in ['live2dcubismcore.d.ts','LICENSE.md','RedistributableFiles.txt']}
SCRIPT_FILES.update({'scripts/yuki_memory.py','scripts/test_memory_management.py','scripts/verify_memory.cjs'})
SCRIPT_FILES.update({'scripts/test_continuity.py','scripts/verify_continuity.cjs'})
SCRIPT_FILES.update({'scripts/yuki_characters.py','scripts/test_characters.py','scripts/verify_characters.cjs'})
OTHER_FILES={'docs/PUBLISHING.md','docs/SYSTEM.md','docs/WORKLOG.md','third_party/README.md','third_party/Open-LLM-VTuber-MIT.txt'}
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
        if b'\x00' in data:issues.append((path,'binary content'))
        if any(value in data for value in secrets):issues.append((path,'matches a configured secret'))
        if any(re.search(pattern,data) for pattern in PATTERNS):issues.append((path,'credential-like content'))
        if path=='.env.example':
            for line in data.decode('utf-8').splitlines():
                if re.match(r'^[A-Z_]*(KEY|TOKEN|SECRET|PASSWORD)[A-Z_]*=.+',line):issues.append((path,'nonempty example credential'))
    report={'passed':not issues,'sourceFiles':len(entries),'bytes':total,'configuredSecretValuesChecked':len(secrets),'issues':[{'path':p,'reason':r} for p,r in issues]}
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if issues:raise SystemExit(1)

if __name__=='__main__':main()
