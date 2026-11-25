import json, uvicorn
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
import store, bot

app = FastAPI()
conns = {}

@app.get("/api/doctors")
def doctors(): return store.doctors_with_slots()

@app.get("/api/appointments")
def appts(): return store.appointments()

@app.get("/api/events")
async def events():
    q = store.subscribe()
    async def gen():
        try:
            while True: yield f"data: {json.dumps(await q.get())}\n\n"
        finally: store.unsubscribe(q)
    return StreamingResponse(gen(), media_type="text/event-stream")

@app.post("/api/offer")
async def offer(req: dict, bg: BackgroundTasks):
    pc_id = req.get("pc_id")
    if pc_id in conns:
        conn = conns[pc_id]
        await conn.renegotiate(sdp=req["sdp"], type=req["type"])
    else:
        conn = SmallWebRTCConnection([])
        await conn.initialize(sdp=req["sdp"], type=req["type"])
        @conn.event_handler("closed")
        async def _(c): conns.pop(c.pc_id, None)
        bg.add_task(bot.run_bot, conn)
    ans = conn.get_answer(); conns[ans["pc_id"]] = conn
    return ans

app.mount("/", StaticFiles(directory=Path(__file__).parent.parent / "frontend", html=True))
if __name__ == "__main__": uvicorn.run(app, host="0.0.0.0", port=7860)
