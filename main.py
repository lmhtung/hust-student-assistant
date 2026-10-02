from __future__ import annotations
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    role: str
    message: str

@app.get("/")
async def root():
    return {"message": "yea its working"}

@app.post("/chat")
async def chat(request: ChatRequest):
    print(f"Received message: {request.message}")
    return {"Response": f"You said: {request.message}"}

# @app.delete("/chat")
# @app.put("/chat")