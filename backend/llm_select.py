"""Pick the first working LLM from a fallback chain (all via OpenAI-compatible APIs).
Run `python llm_select.py` to test the chain, `python llm_select.py list gemini` to list model ids."""
import asyncio, os, sys, time, aiohttp
from dotenv import load_dotenv
from pipecat.services.openai.llm import OpenAILLMService

load_dotenv()
PROVIDERS = {
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GOOGLE_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "cerebras": ("https://api.cerebras.ai/v1", "CEREBRAS_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
}
# provider:model, tried in order; entries without an API key are skipped. Override with LLM_CHAIN in .env.
DEFAULT_CHAIN = ("groq:llama-3.3-70b-versatile,gemini:gemini-3.8-flash,gemini:gemini-2.5-flash,"
                 "gemini:gemini-2.5-flash-lite,openrouter:meta-llama/llama-3.3-70b-instruct:free")
_good = None

def _chain():
    out = []
    for item in os.getenv("LLM_CHAIN", DEFAULT_CHAIN).split(","):
        if not item.strip(): continue
        prov, model = item.strip().split(":", 1)
        base, env = PROVIDERS[prov]
        if os.getenv(env): out.append((prov, model, base, os.getenv(env)))
    return out

async def _probe(s, base, key, model):
    try:
        async with s.post(f"{base}/chat/completions", headers={"Authorization": f"Bearer {key}"},
                          json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
                          timeout=aiohttp.ClientTimeout(total=8)) as r:
            return None if r.status == 200 else f"HTTP {r.status}: {(await r.text())[:120]}"
    except Exception as e:
        return repr(e)

SKIP = ("embed","whisper","tts","image","imagen","veo","guard","vision","audio","live","tuning","aqa","robotics","compound","orpheus","moderation","transcribe","dall")

async def discover(s, tried):
    """Chain exhausted: ask each provider which models it has right now and probe the likely chat models."""
    for prov, (base, env) in PROVIDERS.items():
        key = os.getenv(env)
        if not key: continue
        try:
            async with s.get(f"{base}/models", headers={"Authorization": f"Bearer {key}"},
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                ids = [m["id"].removeprefix("models/") for m in (await r.json()).get("data", [])]
        except Exception:
            continue
        ids = [i for i in ids if not any(w in i.lower() for w in SKIP) and (prov, i) not in tried]
        ids.sort(key=lambda i: 0 if any(w in i.lower() for w in ("flash", "gpt-oss", "llama", "instruct")) else 1)
        for i in ids[:8]:
            if await _probe(s, base, key, i) is None: return (prov, i, base, key)
            tried.add((prov, i))
    return None

async def pick():
    global _good
    if _good and time.time() - _good[1] < 120: return _good[0]
    errs = []; tried = set()
    async with aiohttp.ClientSession() as s:
        for c in _chain():
            err = await _probe(s, c[2], c[3], c[1])
            if err is None:
                _good = (c, time.time()); print(f"[llm] using {c[0]}:{c[1]}"); return c
            errs.append(f"{c[0]}:{c[1]} -> {err}"); tried.add((c[0], c[1])); print("[llm] skipped", errs[-1])
        c = await discover(s, tried)
        if c:
            _good = (c, time.time()); print(f"[llm] auto-discovered {c[0]}:{c[1]}"); return c
    raise RuntimeError("No LLM available. Add a key or fix LLM_CHAIN:\n" + "\n".join(errs))

async def build_llm():
    prov, model, base, key = await pick()
    if prov == "gemini":  # native service: Gemini's OpenAI-compat layer mangles streamed tool calls
        from pipecat.services.google.llm import GoogleLLMService
        return GoogleLLMService(api_key=key, model=model)
    return OpenAILLMService(api_key=key, base_url=base, model=model)

if __name__ == "__main__":
    async def main():
        async with aiohttp.ClientSession() as s:
            if sys.argv[1:2] == ["list"]:
                base, env = PROVIDERS[sys.argv[2]]
                async with s.get(f"{base}/models", headers={"Authorization": f"Bearer {os.getenv(env)}"}) as r:
                    print([m["id"] for m in (await r.json()).get("data", [])]); return
            for p, m, b, k in _chain():
                e = await _probe(s, b, k, m); print(f"{p}:{m}", "OK" if e is None else e)
        try: await pick()
        except Exception as ex: print(ex)
    asyncio.run(main())