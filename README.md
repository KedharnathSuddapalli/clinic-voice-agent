# Clinic Desk — Voice AI Doctor Appointment Assistant

A caller talks to "Aria", a voice agent that checks doctor availability, books and cancels appointments.
The caller is identified only by the phone number they say aloud. A dashboard shows the live call, doctors, slots and appointments.

**LLM:** Google Gemini (free tier), through Pipecat's native `GoogleLLMService`. The model is picked at call start from a
fallback chain (`backend/llm_select.py`): it tries each model in `LLM_CHAIN`, skips any that error (503/404/429), and finally
auto-discovers a working chat model. Groq/OpenRouter keys can be added as backups. **STT/TTS:** ElevenLabs. **VAD:** Silero.

## Run
```bash
cd backend
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp ../.env.example .env                                # add your keys
python server.py                                       # open http://localhost:7860 and click "Try call"
```
Useful checks: `python llm_select.py` (which LLMs work right now), `DEBUG_FRAMES=1` in `.env` (prints speech/transcript frames).

**ElevenLabs note:** free accounts cannot use *library* voices over the API. Create your own voice (Voices → Voice Design),
and put its ID in `ELEVENLABS_VOICE_ID`.

## Pipeline
Browser mic → WebRTC (Pipecat SmallWebRTC) → Silero VAD + smart turn detection → ElevenLabs STT → Gemini (function calling)
→ ElevenLabs TTS → WebRTC → browser speaker.

Tools (registered in the pipeline): `check_doctor_availability`, `book_appointment`, `cancel_appointment`, over in-memory mock data
(`backend/store.py`). The tools reject any booking/cancellation unless `phone_confirmed=true`, which the prompt allows only after
the agent reads the number back digit by digit and the caller says yes.
Transcripts and booking/cancel events are pushed to the dashboard over Server-Sent Events (`/api/events`).

## Dashboard (`frontend/index.html`, plain HTML/JS)
Phone-style call screen (ringing → connected with timer → ended), mute/end, live waveform, live transcript, booking/cancellation
card in the phone, doctor slots, appointments table with row highlight, ring/end tones, mobile layout.

## Known limitations
Mock data resets on restart; one active appointment per phone number; speech-to-text sometimes mishears digits (the read-back
step exists to catch this); no auth beyond the spoken number, as specified.