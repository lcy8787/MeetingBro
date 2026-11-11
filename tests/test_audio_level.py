import asyncio
import math
import numpy as np
from meetingbro.audio.level import audio_level


def test_meter_measures_silence_soft_and_loud_audio_without_nan():
    assert audio_level(np.zeros(0)) == {"level": 0., "db": -80.}
    assert audio_level(np.zeros(1600))["level"] == 0
    soft = audio_level(np.full(1600, .01))
    loud = audio_level(np.full(1600, .1))
    assert 0 < soft["level"] < loud["level"] < 1
    assert math.isclose(loud["db"] - soft["db"], 20.)
    assert math.isfinite(audio_level(np.array([np.nan, np.inf]))["level"])


def test_input_preview_disconnect_closes_source(monkeypatch):
    from meetingbro import main
    from meetingbro.audio.capture import AudioChunk
    class Source:
        closed = False
        async def stream(self):
            yield AudioChunk(np.full(1600, .1), 16000, 0.)
            await asyncio.Event().wait()
        async def aclose(self): self.closed = True
    class Socket:
        query_params = {"source":"mic", "mic_device":"2"}
        def __init__(self): self.sent = []; self.disconnect = asyncio.Event()
        async def accept(self): pass
        async def close(self):
            from fastapi import WebSocketDisconnect
            raise WebSocketDisconnect()
        async def receive(self):
            await self.disconnect.wait()
            return {"type":"websocket.disconnect"}
        async def send_json(self, value):
            self.sent.append(value); self.disconnect.set()
    async def run():
        source, socket = Source(), Socket()
        monkeypatch.setattr(main, '_build_audio_source', lambda *args, **kwargs: source)
        await asyncio.wait_for(main.audio_level_preview(socket), 1)
        assert source.closed and socket.sent[0]['level'] > 0
    asyncio.run(run())
