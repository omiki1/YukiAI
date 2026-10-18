"""Offline checks for the new text-only boundary and history compatibility."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from scripts.yuki_server import create_app
from scripts.yuki_chat import TextChat
from scripts.yuki_config import ConfigStore

class Fixture:
    def __init__(self):self.calls=[]
    async def complete(self,text,previous):
        self.calls.append((text,previous))
        return {'text':'今天也见到你了，有点开心。','emotion':'happy'}

class WindowBackendTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.fixture=Fixture()
        self.config=ConfigStore(Path(self.temp.name)/'config.json',env={})
        self.app=create_app(self.fixture,self.temp.name,config=self.config);self.client=TestClient(self.app)
    def tearDown(self):self.client.close();self.temp.cleanup()
    def test_text_exchange_and_restart_history(self):
        uid=self.client.post('/api/sessions',json={}).json()['session_id']
        result=self.client.post('/api/chat',json={'text':'你好','session_id':uid})
        self.assertEqual(result.json()['emotion'],'happy')
        self.assertEqual(len(self.fixture.calls),1)
        self.assertFalse(any(k in result.json() for k in ['audio','speech_text']))
        messages=self.client.get('/api/sessions/'+uid).json()['messages']
        self.assertEqual([m['role'] for m in messages],['human','ai'])
        restored=TestClient(create_app(history_directory=self.temp.name,config=self.config))
        self.assertEqual(restored.get('/api/sessions/'+uid).json()['messages'],messages)
        restored.close()
        self.client.post('/api/chat',json={'text':'接着聊','session_id':uid})
        self.assertEqual(len(self.fixture.calls[-1][1]),2)
    def test_unrelated_sites_cannot_submit(self):
        response=self.client.post('/api/sessions',json={},headers={'Origin':'https://unrelated.invalid'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(self.client.post('/api/sessions',content='{}').status_code,415)
    def test_no_audio_routes_or_exposed_configuration(self):
        status=self.client.get('/api/status').json()
        self.assertFalse(status['voice_enabled']);self.assertTrue(status['independent'])
        self.assertNotIn('api_key',str(status));self.assertEqual(self.client.post('/v1/tts',json={}).status_code,405)
    def test_path_and_input_validation(self):
        self.assertEqual(self.client.get('/api/sessions/not-valid%2Ejson').status_code,404)
        self.assertEqual(self.client.post('/api/chat',json={'text':'x'*4001,'session_id':'x'}).status_code,422)
        self.assertEqual(self.client.post('/api/chat',json={'text':'x','session_id':'..'}).status_code,404)
    def test_parser_validates_reply_and_falls_back_to_neutral(self):
        self.assertEqual(TextChat.decode('{"text":"你好","emotion":"unlisted"}')['emotion'],'neutral')
        with self.assertRaises(ValueError):TextChat.decode('{"text":""}')

if __name__=='__main__':unittest.main()
