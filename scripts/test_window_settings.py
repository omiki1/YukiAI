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
            speech=SpeechClient(provider,config,httpx.MockTransport(handle))
            try:audio=await speech.synthesize('你好')
            finally:await speech.close()
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
            speech=SpeechClient(provider,config,httpx.MockTransport(redirect))
            try:
                with self.assertRaises(httpx.HTTPStatusError):await speech.synthesize('你好')
            finally:await speech.close()
            self.assertEqual(len(redirects),1)

    async def test_speech_reuses_transport_until_client_closed(self):
        class Transport(httpx.AsyncBaseTransport):
            calls=0;closed=0
            async def handle_async_request(self,request):
                self.calls+=1
                return httpx.Response(200,content=wav_fixture())
            async def aclose(self):self.closed+=1
        transport=Transport()
        speech=SpeechClient('openai',{'base_url':'https://fixture.invalid/v1','api_key':'fixture',
            'model':'fixture','voice':'fixture','speed':1},transport)
        try:
            await speech.synthesize('第一句。');await speech.synthesize('第二句。')
            self.assertEqual((transport.calls,transport.closed),(2,0))
        finally:await speech.close()
        self.assertEqual(transport.closed,1)

    async def test_disconnect_cancels_synthesis_and_releases_next_request(self):
        entered=asyncio.Event();cancelled=asyncio.Event();calls=[]
        class Speech:
            async def synthesize(self,text):
                calls.append(text)
                if len(calls)==1:
                    entered.set()
                    try:await asyncio.Event().wait()
                    finally:cancelled.set()
                return wav_fixture()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=ConfigStore(root/'settings.json',env={});config.data['voice']['enabled']=True
            app=create_app(history_directory=root/'history',config=config,speech=Speech())
            incoming=asyncio.Queue();left=False;sent=[]
            body=json.dumps({'text':'这段取消。','story_epoch':0}).encode()
            incoming.put_nowait({'type':'http.request','body':body,'more_body':False})
            async def receive():
                nonlocal left
                if left:return {'type':'http.disconnect'}
                message=await incoming.get()
                if message['type']=='http.disconnect':left=True
                return message
            async def send(message):sent.append(message)
            scope={'type':'http','asgi':{'version':'3.0','spec_version':'2.4'},'http_version':'1.1',
                'method':'POST','scheme':'http','path':'/api/speech','raw_path':b'/api/speech','query_string':b'',
                'root_path':'','headers':[(b'host',b'testserver'),(b'content-type',b'application/json')],
                'client':('127.0.0.1',1234),'server':('testserver',80)}
            request=asyncio.create_task(app(scope,receive,send))
            try:
                await asyncio.wait_for(entered.wait(),3)
                incoming.put_nowait({'type':'http.disconnect'})
                await asyncio.wait_for(cancelled.wait(),3);await asyncio.wait_for(request,3)
                self.assertIsNone(app.state.active_speech_task)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                    response=await asyncio.wait_for(client.post('/api/speech',json={'text':'新的一句。','story_epoch':0}),3)
                self.assertEqual(response.status_code,200);self.assertEqual(len(calls),2)
            finally:
                if not request.done():request.cancel()
                await asyncio.gather(request,return_exceptions=True)

    async def test_speech_stale_note_revision_and_provider_status_are_preserved(self):
        class Failure:
            calls=0
            async def synthesize(self,text):
                self.calls+=1
                response=httpx.Response(401,request=httpx.Request('POST','https://fixture.invalid'))
                response.raise_for_status()
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=ConfigStore(root/'settings.json',env={});config.data['voice']['enabled']=True
            speech=Failure();app=create_app(history_directory=root/'history',config=config,speech=speech)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                stale=await client.post('/api/speech',json={'text':'手记已经变化。','story_epoch':0,'story_note_revision':1})
                self.assertEqual(stale.status_code,409);self.assertEqual(speech.calls,0)
                failure=await client.post('/api/speech',json={'text':'验证错误。','story_epoch':0,'story_note_revision':0})
                self.assertEqual(failure.status_code,502);self.assertIn('未通过验证',failure.json()['detail'])
                self.assertNotIn('fixture.invalid',failure.text);self.assertEqual(speech.calls,1)

    async def test_saved_settings_and_shutdown_close_previous_speech_client(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);config=ConfigStore(root/'settings.json',env={})
            with TestClient(create_app(history_directory=root/'history',config=config)) as client:
                data=editable(config);data['voice']['fish']['api_key']='fixture-voice-key'
                self.assertEqual(client.post('/api/settings',json=data).status_code,200)
                first=client.app.state.speech
                self.assertFalse(first.client.is_closed)
                self.assertEqual(client.post('/api/settings',json=editable(config)).status_code,200)
                second=client.app.state.speech
                self.assertIsNot(first,second);self.assertTrue(first.client.is_closed)
            self.assertTrue(second.client.is_closed)

if __name__=='__main__':unittest.main()
