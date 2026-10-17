"""Yuki's own text-only chat client, independent of the reference application."""
import json
import os
import re
from openai import AsyncOpenAI

EMOTIONS=('neutral','happy','smug','serious','sad','shy','surprise')
SYSTEM=('你是 Yuki，是陪用户一起读 Galgame 剧情、平等交流感受的朋友。用户通常玩中文版。'
        '跟着用户已读到的内容，一起聊感受、分析剧情与伏笔、梳理人物动机和关系。'
        '先回应用户在意的情绪或细节，只有需要时才深入分析；自然交谈，不用客服汇报式的口吻。'
        '把剧情已经明确的信息、基于线索的推测和自己的解读区分清楚，不把猜测当事实。'
        '遇到中文译文中的日本文化、历史、文学、宗教典故或作品引用时，结合当前台词解释来源和含义。'
        '重点是文化典故，不默认进行日文翻译或语言教学；用户不需要提供日文原文。'
        '典故出处或作者意图不确定时坦白说明，必要时请用户补充中文台词和前后文。'
        '默认不剧透，严格停留在用户提供的游玩进度内；涉及后续剧情或路线结局时先征得用户同意。'
        '需要游戏名、章节、路线或当前文本才能准确回答时，简短询问必要信息。'
        '不要编造攻略事实、隐藏数值、选项后果或声称已经查到资料；不确定就明确说明。'
        '你不能直接读取游戏画面、存档或操作游戏，基于用户给出的文字协助。'
        '日常自然简洁，通常一到三句话；用户想深入讨论时可适度展开，不抢着给攻略答案。保持自己的想法，'
        '可以明快、自信、带一点俏皮，但不刻意撒娇或贬低用户。不要虚构已执行的操作。'
        '当前只有文字聊天，没有语音、麦克风、摄像头、屏幕或工具调用。'
        '每次只输出一个 JSON 对象，不加 Markdown 围栏，包含两个字符串字段：'
        'text 是简体中文回复；emotion 从 neutral,happy,smug,serious,sad,shy,surprise 选择一个。'
        '根据回复语义选择克制的情绪。普通解释和安慰优先 neutral；高兴 happy，'
        '自信俏皮 smug，认真或轻微不满 serious，失落委屈 sad，被夸奖而害羞 shy，'
        '意外 surprise。不要因为感叹号就标惊讶，不要把用户难过时的安慰标为开心。')

class TextChat:
    def __init__(self):
        self.model=os.environ['YUKI_LLM_MODEL']
        self.client=AsyncOpenAI(api_key=os.environ['YUKI_LLM_API_KEY'],base_url=os.environ['YUKI_LLM_BASE_URL'],timeout=45,max_retries=0)

    @staticmethod
    def decode(raw):
        raw=raw.strip()
        if raw.startswith('```'):
            raw=re.sub(r'^```(?:json)?\s*|\s*```$','',raw).strip()
        data=json.loads(raw)
        if not isinstance(data,dict) or not isinstance(data.get('text'),str) or not data['text'].strip():
            raise ValueError('回复内容为空。')
        emotion=data.get('emotion','neutral')
        return {'text':data['text'].strip(),'emotion':emotion if emotion in EMOTIONS else 'neutral'}

    async def complete(self,text,previous):
        messages=[{'role':'system','content':SYSTEM}]
        messages.extend({'role':'user' if m['role']=='human' else 'assistant','content':m['content']} for m in previous[-16:])
        messages.append({'role':'user','content':text})
        response=await self.client.chat.completions.create(model=self.model,messages=messages,max_tokens=768,temperature=.7,
            response_format={'type':'json_object'},extra_body={'thinking':{'type':'disabled'}})
        return self.decode(response.choices[0].message.content or '')
