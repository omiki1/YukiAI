"""Optional speech APIs; requests and credentials stay on the loopback server."""
import io
import wave

import httpx


class SpeechClient:
    def __init__(self, provider, config, transport=None):
        self.provider, self.config, self.transport = provider, dict(config), transport

    async def synthesize(self, text):
        config = self.config
        headers = {'Content-Type': 'application/json'}
        if config['api_key']:
            headers['Authorization'] = 'Bearer ' + config['api_key']
        if self.provider == 'fish':
            from .yuki_config import FISH_MODELS
            if config['model'] not in FISH_MODELS:
                raise ValueError('请选择有效的 Fish 模型。')
            url = config['base_url']
            headers['model'] = config['model']
            body = {'text': text, 'format': 'wav', 'sample_rate': 44100,
                    'prosody': {'speed': config['speed']}}
            if config['voice']:
                body['reference_id'] = config['voice']
        else:
            url = config['base_url'].rstrip('/') + '/audio/speech'
            body = {'model': config['model'], 'input': text, 'voice': config['voice'] or 'alloy',
                    'response_format': 'wav', 'speed': config['speed']}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15), follow_redirects=False,
                                     transport=self.transport) as client:
            async with client.stream('POST', url, headers=headers, json=body) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    raise ValueError('语音服务未返回音频。')
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 16 * 1024 * 1024:
                        raise ValueError('语音文件过大。')
                    chunks.append(chunk)
        audio = b''.join(chunks)
        if not audio.startswith(b'RIFF') or audio[8:12] != b'WAVE':
            raise ValueError('语音服务没有返回 WAV 音频。')
        # Fish streaming WAV may have placeholder lengths; make a finite header.
        with wave.open(io.BytesIO(audio), 'rb') as wav:
            params = wav.getparams()
            pcm = wav.readframes(len(audio))
            duration = len(pcm) / (params.framerate * params.nchannels * params.sampwidth)
        if not .05 <= duration <= 180:
            raise ValueError('音频时长不在预期范围。')
        result = io.BytesIO()
        with wave.open(result, 'wb') as output:
            output.setparams(params)
            output.writeframes(pcm)
        return result.getvalue()
