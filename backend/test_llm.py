import os
from dotenv import load_dotenv
from google import genai

load_dotenv()
c = genai.Client(api_key=os.environ["GOOGLE_API_KEY"])
model = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
print(model, "->", c.models.generate_content(model=model, contents="say hi").text)