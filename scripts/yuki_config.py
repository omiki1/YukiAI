"""Local configuration. Public views never contain credentials."""
import copy
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

ROOT = Path(__file__).resolve().parents[1]
FISH_MODELS = ('s2.1-pro-free', 's2.1-pro', 's2-pro', 's1', 'drama-3')


def local_url(value):
    return urlsplit(value).hostname in ('localhost', '127.0.0.1', '::1')


class Endpoint(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    base_url: str = Field(default='', max_length=500)
    api_key: str = Field(default='', max_length=2000)
    clear_key: bool = False
    model: str = Field(default='', max_length=200)

    @field_validator('base_url')
    @classmethod
    def validate_url(cls, value):
        if not value:
            return value
        try:
            url = urlsplit(value)
            valid = (url.hostname and not url.username and not url.password and
                     not url.query and not url.fragment and
                     (url.scheme == 'https' or (url.scheme == 'http' and local_url(value))))
            _ = url.port
        except ValueError:
            valid = False
        if not valid:
            raise ValueError('接口地址需使用 HTTPS；本机服务可使用 HTTP。地址不能包含密钥。')
        return value.rstrip('/')


class LLMSettings(Endpoint):
    json_mode: bool = True


class SpeechEndpoint(Endpoint):
    voice: str = Field(default='', max_length=200)
    speed: float = Field(default=1, ge=.5, le=2)


class FishSettings(SpeechEndpoint):
    base_url: str = 'https://api.fish.audio/v1/tts'
    model: Literal['s2.1-pro-free', 's2.1-pro', 's2-pro', 's1', 'drama-3'] = 's2.1-pro-free'


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    provider: Literal['fish', 'openai'] = 'fish'
    fish: FishSettings = Field(default_factory=FishSettings)
    openai: SpeechEndpoint = Field(default_factory=SpeechEndpoint)


class Configuration(BaseModel):
    model_config = ConfigDict(extra='forbid')
    avatar_scale: float = Field(default=.85, ge=.6, le=1.1)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    voice: VoiceSettings = Field(default_factory=VoiceSettings)


def ready(endpoint):
    return bool(endpoint['base_url'] and endpoint['model'] and
                (endpoint['api_key'] or local_url(endpoint['base_url'])))


class ConfigStore:
    def __init__(self, path=None, env=None):
        self.path = Path(path) if path else ROOT / '.yuki.local.json'
        env = os.environ if env is None else env
        initial = Configuration().model_dump()
        initial['llm'].update(base_url=env.get('YUKI_LLM_BASE_URL', ''),
                              model=env.get('YUKI_LLM_MODEL', ''), api_key=env.get('YUKI_LLM_API_KEY', ''))
        initial['voice']['fish'].update(api_key=env.get('YUKI_FISH_API_KEY', ''),
                                       voice=env.get('YUKI_FISH_VOICE_ID', ''))
        # Old voice flags are deliberately not imported: reading aloud starts off.
        if self.path.exists():
            try:
                initial = json.loads(self.path.read_text(encoding='utf-8'))
            except (ValueError, OSError):
                raise ValueError('本机设置文件无法读取，请检查 .yuki.local.json。') from None
        try:
            self.data = Configuration.model_validate(initial).model_dump()
        except ValueError:
            raise ValueError('本机接口配置格式不正确，请检查本地设置。') from None

    def public(self):
        data = copy.deepcopy(self.data)
        for endpoint in (data['llm'], data['voice']['fish'], data['voice']['openai']):
            endpoint['has_key'] = bool(endpoint.pop('api_key'))
            endpoint.pop('clear_key', None)
        return data

    def save(self, update: Configuration):
        data = update.model_dump()
        for current, previous in ((data['llm'], self.data['llm']),
                                  (data['voice']['fish'], self.data['voice']['fish']),
                                  (data['voice']['openai'], self.data['voice']['openai'])):
            if current.pop('clear_key'):
                current['api_key'] = ''
            elif not current['api_key']:
                current['api_key'] = previous['api_key']
        if data['voice']['enabled'] and not ready(data['voice'][data['voice']['provider']]):
            raise ValueError('开启自动朗读前，请先填写所选语音服务的地址、模型和密钥。')
        temporary = self.path.with_suffix('.json.tmp')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(self.path)
        except OSError:
            temporary.unlink(missing_ok=True)
            raise ValueError('本机设置未能保存，原来的配置仍然有效。') from None
        self.data = data
        return self.public()
