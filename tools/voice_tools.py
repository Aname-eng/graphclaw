import tempfile
from pathlib import Path
from typing import Optional, Callable

from tools.registry import registry, tool_result, tool_error
from tools.utils import redact_sensitive_text


# 延迟导入所有语音库，避免模块加载时耗时
EDGE_TTS_AVAILABLE = False
WHISPER_AVAILABLE = False
FASTER_WHISPER_AVAILABLE = False

# 使用延迟检查，只在需要时才导入
def _check_edge_tts():
    global EDGE_TTS_AVAILABLE
    if EDGE_TTS_AVAILABLE is False:
        try:
            import edge_tts
            EDGE_TTS_AVAILABLE = True
        except ImportError:
            EDGE_TTS_AVAILABLE = False
    return EDGE_TTS_AVAILABLE

def _check_whisper():
    global WHISPER_AVAILABLE
    if WHISPER_AVAILABLE is False:
        try:
            import importlib.util
            WHISPER_AVAILABLE = importlib.util.find_spec("whisper") is not None
        except Exception:
            WHISPER_AVAILABLE = False
    return WHISPER_AVAILABLE

def _check_faster_whisper():
    global FASTER_WHISPER_AVAILABLE
    if FASTER_WHISPER_AVAILABLE is False:
        try:
            import importlib.util
            FASTER_WHISPER_AVAILABLE = importlib.util.find_spec("faster_whisper") is not None
        except Exception:
            FASTER_WHISPER_AVAILABLE = False
    return FASTER_WHISPER_AVAILABLE


def text_to_speech(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    output_path: Optional[str] = None,
    task_id: str = "",
    on_log: Optional[Callable] = None
) -> dict:
    """
    将文本转换为语音。

    Args:
        text: 要转换的文本内容
        voice: 语音模型，默认为 "zh-CN-XiaoxiaoNeural"
        output_path: 输出文件路径，可选，不提供则使用临时文件
        task_id: 任务标识符
        on_log: 日志回调函数

    Returns:
        包含成功状态、数据或错误信息的字典
    """
    if not _check_edge_tts():
        return tool_error(
            code="DEPENDENCY_MISSING",
            message="edge-tts not installed. Please install with: pip install edge-tts"
        )

    try:
        import edge_tts
        import asyncio

        if on_log:
            on_log(f"🔊 正在将文本转换为语音: {redact_sensitive_text(text[:50])}...")

        output_file = Path(output_path) if output_path else Path(tempfile.mktemp(suffix=".mp3"))

        async def _generate():
            communicate = edge_tts.Communicate(text, voice)
            await communicate.save(str(output_file))

        asyncio.run(_generate())

        if on_log:
            on_log(f"✅ 语音生成完成: {output_file}")

        return tool_result(data={
            "audio_path": str(output_file),
            "text": redact_sensitive_text(text),
            "voice": voice
        })
    except Exception as e:
        return tool_error(message=f"Text-to-speech error: {str(e)}")


def speech_to_text(
    audio_path: str,
    model: str = "base",
    task_id: str = "",
    on_log: Optional[Callable] = None
) -> dict:
    """
    将语音转换为文本。

    Args:
        audio_path: 音频文件路径
        model: Whisper 模型大小，可选 "tiny", "base", "small", "medium", "large"
        task_id: 任务标识符
        on_log: 日志回调函数

    Returns:
        包含成功状态、数据或错误信息的字典
    """
    if not _check_whisper() and not _check_faster_whisper():
        return tool_error(
            code="DEPENDENCY_MISSING",
            message="whisper or faster-whisper not installed. Please install with: pip install openai-whisper or pip install faster-whisper"
        )

    try:
        audio_file = Path(audio_path)
        if not audio_file.exists():
            return tool_error(message=f"Audio file not found: {audio_path}")

        if on_log:
            on_log(f"🎧 正在从音频文件转录文本: {audio_file}")

        text_result = ""
        segments_result = []

        if _check_faster_whisper():
            # 延迟导入 faster_whisper
            from faster_whisper import WhisperModel
            model_instance = WhisperModel(model, device="cpu", compute_type="int8")
            segments, info = model_instance.transcribe(str(audio_file), beam_size=5)
            for segment in segments:
                segments_result.append({
                    "start": segment.start,
                    "end": segment.end,
                    "text": segment.text
                })
                text_result += segment.text
        else:
            # 延迟导入 whisper
            import whisper
            model_instance = whisper.load_model(model)
            result = model_instance.transcribe(str(audio_file))
            text_result = result["text"]
            for segment in result["segments"]:
                segments_result.append({
                    "start": segment["start"],
                    "end": segment["end"],
                    "text": segment["text"]
                })

        text_result = redact_sensitive_text(text_result)

        if on_log:
            on_log(f"✅ 语音转录完成: {len(text_result)} 字符")

        return tool_result(data={
            "text": text_result,
            "segments": segments_result,
            "audio_path": str(audio_file),
            "model": model
        })
    except Exception as e:
        return tool_error(message=f"Speech-to-text error: {str(e)}")


VOICE_TOOLS_SCHEMAS = [
    {
        "name": "text_to_speech",
        "description": "Convert text to speech using edge-tts. Generates an MP3 file.",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to convert to speech"},
                "voice": {"type": "string", "description": "Voice model to use", "default": "zh-CN-XiaoxiaoNeural"},
                "output_path": {"type": "string", "description": "Output file path (optional, uses temp file if not provided)", "default": None},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["text"]
        }
    },
    {
        "name": "speech_to_text",
        "description": "Convert speech to text using whisper or faster-whisper.",
        "parameters": {
            "type": "object",
            "properties": {
                "audio_path": {"type": "string", "description": "Path to the audio file"},
                "model": {"type": "string", "description": "Whisper model size (tiny, base, small, medium, large)", "default": "base"},
                "task_id": {"type": "string", "description": "Task identifier for tracking", "default": ""},
            },
            "required": ["audio_path"]
        }
    }
]


for schema in VOICE_TOOLS_SCHEMAS:
    handler = globals()[schema["name"]]
    registry.register(
        name=schema["name"],
        handler=handler,
        description=schema["description"],
        parameters=schema["parameters"],
        toolset="voice",
        emoji="🎤"
    )
