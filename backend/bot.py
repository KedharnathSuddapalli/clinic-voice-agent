"""Pipecat pipeline: WebRTC audio -> ElevenLabs STT -> Gemini (tools) -> ElevenLabs TTS -> WebRTC audio."""
import os, datetime as dt, aiohttp
from dotenv import load_dotenv
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import LLMRunFrame, TranscriptionFrame, TTSTextFrame, TTSStoppedFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frame_processor import FrameProcessor
from pipecat.services.elevenlabs.stt import ElevenLabsSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
import llm_select
from pipecat.services.llm_service import FunctionCallParams
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
import store

load_dotenv()

SYSTEM = f"""You are Aria, a friendly phone receptionist for a clinic. Today is {dt.date.today().isoformat()}.
You can: check doctor availability, book, and cancel appointments. Speak in short, natural sentences. No markdown, no lists.
Rules:
- Identify the caller ONLY by the phone number they say aloud. Ask for it.
- Read the number back digit by digit and ask "is that correct?". Only after a clear yes, pass phone_confirmed=true.
- Never book or cancel before the number is confirmed. Never invent slots; use check_doctor_availability.
- Offer at most 3 slots at a time. Say dates naturally, but pass ISO dates and 24h times to tools.
- Only pass a date to check_doctor_availability if the caller names one. Slots exist for the next 5 days, starting tomorrow.
- Before booking, confirm doctor, date and time with the caller. Ask for the patient's name.
- Before cancelling, confirm they want to cancel."""

S = {"type": "string"}
def _fs(name, desc, props, req):
    return FunctionSchema(name=name, description=desc, properties=props, required=req)

TOOLS = ToolsSchema(standard_tools=[
    _fs("check_doctor_availability", "List free slots. Filter by doctor name, speciality and/or date.",
        {"doctor_name": S, "speciality": S, "date": {"type": "string", "description": "YYYY-MM-DD"}}, []),
    _fs("book_appointment", "Book a slot for the caller.",
        {"phone_number": S, "phone_confirmed": {"type": "boolean", "description": "true only after caller confirmed the read-back"},
         "doctor_name": S, "date": {"type": "string", "description": "YYYY-MM-DD"},
         "time": {"type": "string", "description": "HH:MM 24h"}, "patient_name": S},
        ["phone_number", "phone_confirmed", "doctor_name", "date", "time", "patient_name"]),
    _fs("cancel_appointment", "Cancel the caller's appointment, found by phone number.",
        {"phone_number": S, "phone_confirmed": {"type": "boolean"}}, ["phone_number", "phone_confirmed"]),
])

NEED_CONFIRM = {"ok": False, "error": "Read the number back and get a yes first."}

async def h_check(p: FunctionCallParams):
    a = p.arguments
    docs = store.available(a.get("doctor_name"), a.get("speciality"), a.get("date"))
    out = {"doctors": docs}
    if not docs and a.get("date"):
        out = {"doctors": store.available(a.get("doctor_name"), a.get("speciality")),
               "note": f"No free slots on {a['date']}; these are the next available."}
    await p.result_callback(out)

async def h_book(p: FunctionCallParams):
    a = p.arguments
    if not a.get("phone_confirmed"): return await p.result_callback(NEED_CONFIRM)
    await p.result_callback(store.book(a["phone_number"], a["doctor_name"], a["date"], a["time"], a["patient_name"]))

async def h_cancel(p: FunctionCallParams):
    a = p.arguments
    if not a.get("phone_confirmed"): return await p.result_callback(NEED_CONFIRM)
    await p.result_callback(store.cancel(a["phone_number"]))

class DebugTap(FrameProcessor):
    """Prints speech/turn/transcript frames so you can see where the audio path stops."""
    def __init__(self, name):
        super().__init__(); self.label = name
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        n = type(frame).__name__
        if os.getenv("DEBUG_FRAMES") and "Interim" not in n and any(k in n for k in ("Speaking", "Transcription", "VAD", "Turn")):
            print(f"[{self.label}] {n} {getattr(frame, 'text', '')}")
        await self.push_frame(frame, direction)

class TranscriptTap(FrameProcessor):
    """Forwards user/bot text to the dashboard."""
    def __init__(self):
        super().__init__(); self.buf = []
    async def process_frame(self, frame, direction):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text.strip():
            store.publish({"type": "transcript", "role": "user", "text": frame.text.strip()})
        elif isinstance(frame, TTSTextFrame):
            self.buf.append(frame.text)
        elif isinstance(frame, TTSStoppedFrame) and self.buf:
            store.publish({"type": "transcript", "role": "agent", "text": " ".join(self.buf).strip()}); self.buf = []
        await self.push_frame(frame, direction)

async def run_bot(conn):
    async with aiohttp.ClientSession() as session:
        vad = SileroVADAnalyzer(); user_params = None
        try:  # newer Pipecat runs VAD inside the user aggregator
            from pipecat.processors.aggregators.llm_response_universal import LLMUserAggregatorParams
            user_params = LLMUserAggregatorParams(vad_analyzer=vad)
        except Exception as e:
            print("[vad] falling back to transport-level VAD:", e)
        transport = SmallWebRTCTransport(webrtc_connection=conn, params=TransportParams(
            audio_in_enabled=True, audio_out_enabled=True, vad_analyzer=None if user_params else vad))
        stt = ElevenLabsSTTService(api_key=os.environ["ELEVENLABS_API_KEY"], aiohttp_session=session)
        tts = ElevenLabsTTSService(api_key=os.environ["ELEVENLABS_API_KEY"],
                                   voice_id=os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM"))
        llm = await llm_select.build_llm()
        llm.register_function("check_doctor_availability", h_check)
        llm.register_function("book_appointment", h_book)
        llm.register_function("cancel_appointment", h_cancel)
        ctx = LLMContext([{"role": "system", "content": SYSTEM}], TOOLS)
        agg = LLMContextAggregatorPair(ctx, user_params=user_params) if user_params else LLMContextAggregatorPair(ctx)
        pipeline = Pipeline([transport.input(), DebugTap('in'), stt, DebugTap('stt'), TranscriptTap(), agg.user(), llm, tts,
                             TranscriptTap(), transport.output(), agg.assistant()])
        task = PipelineTask(pipeline)

        @transport.event_handler("on_client_connected")
        async def _c(_t, _c):
            ctx.add_message({"role": "user", "content": "A caller just connected. Greet them briefly and ask how you can help."})
            await task.queue_frames([LLMRunFrame()])
        @transport.event_handler("on_client_disconnected")
        async def _d(_t, _c):
            await task.cancel()
        await PipelineRunner(handle_sigint=False).run(task)