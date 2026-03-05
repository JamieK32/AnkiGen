# AI Anki Card Generator (Desktop)

PySide6 桌面应用，用于可视化管理单词 JSON 和音频，并一键同步到 Anki。

## Features

- 查看与搜索词汇列表
- 新增单词（调用 OpenAI 生成词条 + Edge TTS 生成音频）
- 编辑词条并保存到 `data/words.json`
- 删除词条（同时删除 `audio/{word}.mp3` 和 `audio/{word}_sentence.mp3`）
- 播放单词与例句音频
- 重新生成音频
- 同步到 Anki（含去重：`findNotes`）
- 状态栏消息与错误弹窗

## Project Structure

```text
anki_tts/
├─ main.py
├─ gui/
│  ├─ main_window.py
│  └─ word_editor.py
├─ services/
│  ├─ gpt_generator.py
│  ├─ tts_generator.py
│  └─ anki_api.py
├─ utils/
│  └─ file_manager.py
├─ data/
│  └─ words.json
├─ audio/
├─ requirements.txt
└─ README.md
```

## Requirements

- Python 3.10+
- Anki Desktop
- AnkiConnect addon (`2055492159`)

## Install

```bash
cd C:\Users\kjmsd\Documents\GitHub\anki_tts
pip install -r requirements.txt
```

## Configure

在项目根目录创建或编辑 `.env`：

```env
YUNWU_API_KEY=your_real_api_key
OPENAI_BASE_URL=https://yunwu.ai/v1
OPENAI_MODEL=gpt-5-mini

ANKI_CONNECT_URL=http://localhost:8765
ANKI_DECK_NAME=AI Vocabulary
ANKI_MODEL_NAME=AI Vocabulary Note

TTS_VOICE=en-US-AriaNeural
```

## Run

```bash
python main.py
```

## Data Format

`data/words.json`:

```json
[
  {
    "word": "abandon",
    "phonetic": "/əˈbændən/",
    "part_of_speech": "verb",
    "translation": "放弃；遗弃",
    "example": "He decided to abandon the plan.",
    "analysis": "表示彻底停止或遗弃某事"
  }
]
```

音频文件：

- `audio/{word}.mp3`
- `audio/{word}_sentence.mp3`

## Anki Fields

- `Word`
- `Phonetic`
- `PartOfSpeech`
- `Translation`
- `Example`
- `Analysis`
- `AudioWord` -> `[sound:{word}.mp3]`
- `AudioSentence` -> `[sound:{word}_sentence.mp3]`
