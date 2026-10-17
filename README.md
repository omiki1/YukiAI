# Yuki · 一起看剧情的朋友

一个正在开发的 Galgame 共读伙伴。用户主要玩中文版：一起交流感受、分析剧情和伏笔、梳理人物动机与关系；遇到日本文化、历史、文学典故时结合上下文讨论。默认跟随已读进度，不剧透。

## 当前进度

- 独立的透明桌面小窗，默认460×640，Galgame风格对话框。
- 中文文字聊天、逐字显示、点击展开、当前对话回想和本地记录。
- 接入本地提供的 Live2D 模型，保留表情、眨眼和固定头身。
- 当前产品不包含语音、麦克风、摄像头、读屏或游戏操作功能。
- **剧情原文导入、人物事件记忆、分支与读档管理仍处于设计讨论阶段，尚未实现。** 现有聊天记录不能代替这些能力。

## 此仓库的范围

这是源码备份，**不含模型、原画、纹理、API密钥、本地配置、聊天记录、日志、截图、缓存、打包产物或旧参考项目**。Cubism Core 的运行库文件也留在本机，因此直接克隆本仓库不会得到完整可运行成品。

必要的 Cubism Framework 源码、Core 类型声明及许可说明保留在 `app/vendor/live2d/`。应用代码不依赖旧参考项目。第三方来源见 `third_party/README.md`。

## 目录

| 路径 | 内容 |
| --- | --- |
| `app/src/` | 界面、对话和模型渲染 |
| `scripts/yuki_server.py` | 本机文字接口与静态服务 |
| `scripts/yuki_chat.py` | 文字客户端与角色设定 |
| `scripts/yuki_history.py` | 本地聊天记录 |
| `scripts/yuki_window.py` | 透明桌面窗口 |
| `scripts/audit_upload.py` | 提交前的源码范围及敏感信息检查 |

## 本地准备

1. 准备 Python 3.12 环境及 `requirements.txt` 中的依赖。当前启动脚本保留开发机的 myenv 路径，换机时需调整 `start_yuki.py`、`start_yuki.cmd`。
2. 将自行持有的模型运行文件放到 `app/public/model/`，入口为 `yuki_cutout.model3.json`。将合法取得的 Cubism Core 运行库放到 `app/public/vendor/live2d-core/live2dcubismcore.js`。这些目录不会进入 Git。
3. 在 `app/` 执行 `npm ci --ignore-scripts`、`npm run check`、`npm run build`。
4. 复制 `.env.example` 为 `.env.local`，仅在本机填写文字服务参数。
5. 使用已配置的 Python 运行 `start_yuki.py`；`--mode preview` 不调用云 API，`--headless` 只启动本机预览服务。默认监听 `127.0.0.1:12393`。

只有提交文字时才调用配置好的文字服务，不自动发送模型或桌面素材。请勿将 `.env.local` 或真实凭据加入提交。

## 检查

```text
python -m unittest scripts.test_window_backend
python scripts/audit_upload.py
```

`scripts/verify_window.cjs` 需要本机另行准备模型、构建界面并启动预览服务，默认地址为 `http://127.0.0.1:12395/`，也可通过 `YUKI_VERIFY_URL` 指定。测试回复使用本地替身，不发送真实聊天请求。

上传规则采用源码白名单；提交前仍需运行检查。模型和本地资料的缺失是有意安排。
