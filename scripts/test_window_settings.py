"""Offline configuration, credential boundaries and both speech request formats."""
import asyncio
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import httpx
from fastapi.testclient import TestClient

from scripts.yuki_chat import TextChat
from scripts.yuki_config import ConfigStore, Configuration
from scripts.yuki_server import create_app
from scripts.yuki_speech import SpeechClient


def wav_fixture():
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setparams((1,2,24000,0,'NONE','not compressed'))
        wav.writeframes(b'\0\0'*4800)
    return output.getvalue()


def editable(store):
    data=store.public()
    for endpoint in (data['llm'],data['voice']['fish'],data['voice']['openai']):endpoint.pop('has_key')
    return data


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'private.json'
        self.store=ConfigStore(self.path,env={})
        self.client=TestClient(create_app(history_directory=Path(self.temp.name)/'history',config=self.store,allow_cloud=False))

    def tearDown(self):self.client.close();self.temp.cleanup()

    def test_persist_redact_and_keep_independent_keys(self):
        data=editable(self.store)
        data['llm'].update(base_url='https://chat.example.invalid/v1',model='fixture-chat',api_key='fixture-chat-secret')
        data['voice']['fish']['api_key']='fixture-fish-secret'
        data['voice']['openai'].update(base_url='https://speech.example.invalid/v1',model='fixture-tts',api_key='fixture-speech-secret')
        response=self.client.post('/api/settings',json=data)
        self.assertEqual(response.status_code,200)
        self.assertNotIn('fixture-chat-secret',response.text)
        self.assertNotIn('api_key',response.text)
        self.assertTrue(response.json()['llm']['has_key'])
        data=editable(self.store);data['avatar_scale']=.72;data['voice']['provider']='openai'
        self.assertEqual(self.client.post('/api/settings',json=data).status_code,200)
        reopened=ConfigStore(self.path,env={})
        self.assertEqual(reopened.data['avatar_scale'],.72)
        self.assertEqual(reopened.data['llm']['api_key'],'fixture-chat-secret')
        self.assertEqual(reopened.data['voice']['fish']['api_key'],'fixture-fish-secret')
        self.assertEqual(reopened.data['voice']['openai']['api_key'],'fixture-speech-secret')
        data['llm']['clear_key']=True
        self.client.post('/api/settings',json=data)
        self.assertFalse(self.client.get('/api/settings').json()['llm']['has_key'])
        self.assertEqual(self.store.data['voice']['fish']['api_key'],'fixture-fish-secret')

    def test_preview_never_calls_configured_services(self):
        data=editable(self.store);data['llm'].update(base_url='https://service.invalid/v1',model='test',api_key='fixture-key')
        data['voice']['enabled']=True;data['voice']['fish']['api_key']='fixture-key'
        with patch.object(TextChat,'complete',side_effect=AssertionError('network forbidden')),patch.object(SpeechClient,'synthesize',side_effect=AssertionError('network forbidden')):
            self.assertEqual(self.client.post('/api/settings',json=data).status_code,200)
            self.assertEqual(self.client.post('/api/settings/test-llm',json={}).status_code,403)
            self.assertEqual(self.client.post('/api/settings/test-voice',json={}).status_code,403)
            self.assertEqual(self.client.post('/api/speech',json={'text':'你好'}).status_code,403)
        self.assertFalse(self.client.get('/api/status').json()['chat_enabled'])

    def test_invalid_config_and_provider_errors_never_echo_secrets(self):
        data=editable(self.store);data['llm'].update(base_url='https://example.invalid/?key=fixture-hidden',api_key='fixture-hidden')
        response=self.client.post('/api/settings',json=data)
        self.assertEqual(response.status_code,422);self.assertNotIn('fixture-hidden',response.text)
        data=editable(self.store);data['voice']['fish']['model']='unknown-would-fallback-to-paid'
        self.assertEqual(self.client.post('/api/settings',json=data).status_code,422)
        data=editable(self.store);data['voice']['enabled']=True
        self.assertEqual(self.client.post('/api/settings',json=data).status_code,400)
        class Failure:
            async def complete(self,*args):raise ValueError('fixture-hidden-provider-body')
        with TestClient(create_app(Failure(),Path(self.temp.name)/'history',config=self.store)) as client:
            response=client.post('/api/settings/test-llm',json={})
            self.assertEqual(response.status_code,502);self.assertNotIn('fixture-hidden',response.text)
            self.assertEqual(client.post('/api/settings',json=data,headers={'Origin':'https://evil.invalid'}).status_code,403)

    def test_voice_off_requires_explicit_test_and_returns_audio(self):
        class Speech:
            calls=[]
            async def synthesize(self,text):self.calls.append(text);return wav_fixture()
        fixture=Speech()
        with TestClient(create_app(history_directory=Path(self.temp.name)/'history',config=self.store,speech=fixture)) as client:
            self.assertFalse(client.get('/api/status').json()['voice_enabled'])
            self.assertEqual(client.post('/api/speech',json={'text':'不能自动朗读'}).status_code,403)
            self.assertEqual(fixture.calls,[])
            response=client.post('/api/settings/test-voice',json={})
            self.assertEqual(response.status_code,200);self.assertEqual(response.headers['content-type'],'audio/wav')
            self.assertTrue(response.content.startswith(b'RIFF'));self.assertEqual(len(fixture.calls),1)


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_generic_chat_keeps_compatible_body_and_no_retries(self):
        requests=[]
        def handle(request):
            requests.append(request)
            return httpx.Response(200,json={'id':'fixture','object':'chat.completion','created':0,'model':'generic',
                'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':'{"text":"一起看吧。","emotion":"neutral"}'}}]})
        chat=TextChat({'base_url':'https://fixture.invalid/v1','api_key':'fixture-secret','model':'generic','json_mode':False},transport=httpx.MockTransport(handle))
        try:result=await chat.complete('你好',[])
        finally:await chat.client.close()
        self.assertEqual(result['text'],'一起看吧。');self.assertEqual(len(requests),1)
        self.assertEqual(requests[0].url.path,'/v1/chat/completions')
        body=json.loads(requests[0].content)
        self.assertNotIn('thinking',body);self.assertNotIn('response_format',body)

    async def test_both_speech_protocols_and_redirect_rejection(self):
        for provider in ('fish','openai'):
            requests=[]
            def handle(request):requests.append(request);return httpx.Response(200,content=wav_fixture())
            config={'base_url':'https://fixture.invalid/v1/tts' if provider=='fish' else 'https://fixture.invalid/v1',
                    'api_key':'fixture-'+provider,'model':'s2.1-pro-free' if provider=='fish' else 'fixture-tts',
                    'voice':'fixture-voice','speed':1.1}
            audio=await SpeechClient(provider,config,httpx.MockTransport(handle)).synthesize('你好')
            self.assertTrue(audio.startswith(b'RIFF'));self.assertEqual(len(requests),1)
            body=json.loads(requests[0].content)
            self.assertEqual(requests[0].headers['authorization'],'Bearer fixture-'+provider)
            if provider=='fish':
                self.assertEqual(requests[0].headers['model'],'s2.1-pro-free')
                self.assertEqual(body['reference_id'],'fixture-voice');self.assertEqual(body['text'],'你好')
            else:
                self.assertEqual(requests[0].url.path,'/v1/audio/speech');self.assertEqual(body['voice'],'fixture-voice')
                self.assertEqual(body['input'],'你好');self.assertEqual(body['response_format'],'wav')
            redirects=[]
            def redirect(request):redirects.append(request);return httpx.Response(307,headers={'Location':'https://other.invalid/'})
            with self.assertRaises(httpx.HTTPStatusError):
                await SpeechClient(provider,config,httpx.MockTransport(redirect)).synthesize('你好')
            self.assertEqual(len(redirects),1)

if __name__=='__main__':unittest.main()
