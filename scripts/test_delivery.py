"""Delivery acceptance: independent paths, clean assets, and private-data exclusion."""
import json
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from fastapi.testclient import TestClient
from scripts.build_alpha import audit_bundle, copy_ui_licenses
from scripts.yuki_config import ConfigStore
from scripts.yuki_paths import data_root, resource_root
from scripts.yuki_server import create_app
from scripts.test_capture import CaptureFixtureRuntime


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
    def tearDown(self):self.temp.cleanup()

    def test_source_keeps_existing_location_and_frozen_uses_user_directory(self):
        with patch.dict(os.environ,{},clear=True),patch.object(sys,'frozen',False,create=True):
            self.assertEqual(data_root(),resource_root())
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.root)},clear=True),patch.object(sys,'frozen',True,create=True):
            self.assertEqual(data_root(),self.root/'YukiAI');self.assertFalse(data_root().exists())

    def test_explicit_directory_overrides_source_and_frozen_defaults(self):
        for frozen in (False,True):
            with patch.dict(os.environ,{'YUKI_DATA_DIR':str(self.root/'personal')},clear=True),patch.object(sys,'frozen',frozen,create=True):
                self.assertEqual(data_root(),self.root/'personal');self.assertFalse(data_root().exists())

    def test_check_runs_without_writing_personal_records(self):
        result=subprocess.run([sys.executable,str(resource_root()/'start_yuki.py'),'--check','--headless','--data-dir',str(self.root/'personal')],cwd=self.root,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        self.assertTrue(json.loads(result.stdout)['ready']);self.assertFalse((self.root/'personal').exists())

    def test_default_stores_obey_isolated_directory_in_another_process(self):
        source="""
from scripts.yuki_config import ConfigStore
from scripts.yuki_history import HistoryStore
from scripts.yuki_story import StoryStore
from scripts.yuki_capture import ROOT as tools
from scripts.yuki_window import ROOT as window
import json
print(json.dumps([str(ConfigStore(env={}).path),str(HistoryStore().directory),str(StoryStore().path),str(tools),str(window)]))
"""
        env={key:value for key,value in os.environ.items() if not key.startswith('YUKI_')};env['YUKI_DATA_DIR']=str(self.root/'personal')
        result=subprocess.run([sys.executable,'-c',source],cwd=resource_root(),env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=True)
        for location in json.loads(result.stdout):self.assertTrue(Path(location).is_relative_to(self.root/'personal'))

    def test_clean_web_without_model_can_create_and_read_local_story(self):
        web=self.root/'web';web.mkdir();(web/'index.html').write_text('<p>fixture</p>',encoding='utf-8')
        with TestClient(create_app(history_directory=self.root/'history',config=ConfigStore(self.root/'settings.json',env={}),allow_cloud=False,web_directory=web,capture_runtime=CaptureFixtureRuntime())) as client:
            self.assertFalse(client.get('/api/status').json()['model_available'])
            self.assertEqual(client.get('/').status_code,200)
            state=client.post('/api/story/runs',json={'epoch':0,'title':'自编雨伞故事','name':'体验','mode':'galgame'}).json()
            state=client.post('/api/story/import',json={'epoch':state['epoch'],'run_id':state['active']['id'],'text':'美咲：我在等朋友。','kind':'auto','speaker':'','request_id':'delivery-fixture','confirmed_read':True}).json()
            self.assertEqual(state['active']['position'],1);self.assertEqual(state['active']['events'][0]['speaker'],'美咲')
            self.assertEqual(client.get('/model/yuki_cutout.model3.json').status_code,404)

    def test_bundle_audit_accepts_runtime_and_rejects_private_assets(self):
        folder=self.root/'bundle';folder.mkdir();(folder/'runtime.dll').write_bytes(b'fixture runtime')
        self.assertTrue(audit_bundle(folder,self.root)['passed'])
        for name in ('story.sqlite3','.yuki.local.json','private.moc3'):
            file=folder/name;file.write_bytes(b'fixture')
            with self.assertRaises(ValueError):audit_bundle(folder,self.root)
            file.unlink()

    def test_bundle_audit_checks_secrets_across_read_boundaries(self):
        secret='fixture-private-credential-for-bundle'
        (self.root/'.env.local').write_text('YUKI_LLM_API_KEY='+secret,encoding='utf-8')
        folder=self.root/'bundle';folder.mkdir();(folder/'runtime.dll').write_bytes(b'x'*(1024*1024-4)+secret.encode())
        with self.assertRaises(ValueError):audit_bundle(folder,self.root)

    def test_ui_font_license_is_copied_to_portable_bundle(self):
        copy_ui_licenses(self.root)
        source=resource_root()/'app/src/fonts/fusion-pixel-OFL.txt'
        self.assertEqual((self.root/'dependency-licenses/fusion-pixel/OFL.txt').read_bytes(),source.read_bytes())

    @unittest.skipUnless(os.name=='nt','Windows resource paths')
    def test_non_ascii_runtime_path_refers_to_same_directory_without_moving_data(self):
        from scripts.yuki_window import qt_runtime_path
        folder=self.root/'中文运行组件';folder.mkdir();marker=folder/'marker.txt';marker.write_text('unchanged',encoding='utf-8')
        try:alias=qt_runtime_path(folder)
        except RuntimeError as error:
            self.assertIn('英文目录',str(error))
        else:
            self.assertTrue(str(alias).isascii());self.assertTrue(alias.samefile(folder))
        self.assertEqual(marker.read_text(encoding='utf-8'),'unchanged')


class UploadAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        subprocess.run(['git','init','--quiet',str(self.root)],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        self.font_path='app/src/fonts/fusion-pixel-12-ui.woff2'
        self.license_path='app/src/fonts/fusion-pixel-OFL.txt'
        self.font=(resource_root()/self.font_path).read_bytes()
        self.license=(resource_root()/self.license_path).read_bytes()

    def tearDown(self):self.temp.cleanup()

    def stage(self,path,data):
        target=self.root/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(data)
        subprocess.run(['git','-C',str(self.root),'add','--',path],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    def audit(self):
        from scripts import audit_upload
        output=io.StringIO()
        with patch.object(audit_upload,'ROOT',self.root),redirect_stdout(output):
            try:audit_upload.main()
            except SystemExit:pass
        return json.loads(output.getvalue())

    def test_reviewed_font_with_exact_staged_license_is_allowed(self):
        self.stage(self.font_path,self.font);self.stage(self.license_path,self.license)
        self.assertTrue(self.audit()['passed'])

    def test_font_replacement_cannot_use_the_binary_exception(self):
        self.stage(self.font_path,self.font+b'changed');self.stage(self.license_path,self.license)
        self.assertFalse(self.audit()['passed'])

    def test_font_license_must_be_present_in_index_and_unchanged(self):
        self.stage(self.font_path,self.font)
        self.assertFalse(self.audit()['passed'])
        self.stage(self.license_path,b'Not the reviewed license')
        self.assertFalse(self.audit()['passed'])

    def test_renamed_fonts_and_other_binary_assets_remain_blocked(self):
        self.stage('app/src/fonts/other.woff2',self.font)
        self.stage('app/src/renamed-model.bin',b'\x00private model fixture')
        report=self.audit();self.assertFalse(report['passed'])
        self.assertEqual({issue['path'] for issue in report['issues']},{'app/src/fonts/other.woff2','app/src/renamed-model.bin'})

    def test_secret_scanning_still_runs_with_public_font_present(self):
        secret='fixture-credential-not-for-public-upload'
        (self.root/'.env.local').write_text('YUKI_LLM_API_KEY='+secret,encoding='utf-8')
        self.stage(self.font_path,self.font);self.stage(self.license_path,self.license)
        self.stage('app/src/example.ts',('const accidental='+repr(secret)).encode())
        report=self.audit();self.assertFalse(report['passed']);self.assertEqual(report['configuredSecretValuesChecked'],1)
        self.assertNotIn(secret,json.dumps(report))


if __name__=='__main__':unittest.main()
