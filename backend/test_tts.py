import os, requests
from dotenv import load_dotenv
load_dotenv()
h = {"xi-api-key": os.environ["ELEVENLABS_API_KEY"]}
vid = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")

r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{vid}", headers=h,
                  json={"text": "Hello, this is a test.", "model_id": "eleven_flash_v2_5"})
print("TTS", r.status_code, f"{len(r.content)} bytes OK" if r.ok else r.text[:300])

r = requests.get("https://api.elevenlabs.io/v1/voices", headers=h)
print("VOICES", r.status_code)
if r.ok:
    for v in r.json()["voices"]:
        print(v["voice_id"], v["name"], v.get("category"))
else:
    print(r.text[:200])

r = requests.get("https://api.elevenlabs.io/v1/user/subscription", headers=h)
print("SUB", r.status_code, r.text[:200])