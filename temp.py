from __future__ import annotations
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Literal

app = FastAPI(
    title="Student Assistant Chatbot API",
    version="1.0.0",
    description="Chatbot service for course registration"
)


courses = {
    "INT3405": {
        "course_id": "INT3405",
        "name": "Artificial Intelligence",
        "semester": "2026-1",
    },
    "INT3406": {
        "course_id": "INT3406",
        "name": "Machine Learning",
        "semester": "2026-1",
    },
}

class ChatRequest(BaseModel):
    user_id: str
    session_id: str
    role: Literal["student", "admin"]
    message: str = Field(min_length=1, max_length=500, description="Avoid too long messages.")

class ChatResponse(BaseModel):
    response: str

@app.get("/")
async def root():
    return {"message": "yea its working"}

@app.post("/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    # print(f"Received message: {request.message}")
    # return ChatResponse(response=f"You said: {request.message}")
    return {
        "response": f"You said: {request.message}",
        "session_id": request.session_id,
        "user_id": request.user_id,
    }

@app.get("/courses/{course_id}")
async def get_course(
    course_id: str = "CS101",
    semester: str = "2023-1",
    ):

    if course_id not in courses:
        raise HTTPException(
            status_code=404,
            detail="Course not found"
        )

    return courses[course_id]

# @app.delete("/chat")
# @app.put("/chat")


""" path parameters : dung khi can goi hay xac dinh 1 resource cu the trong API, vd: /courses/{course_id} """
# @app.get("/courses/{course_id}")
# async def get_course(course_id: str):
#     return {
#         "course_id": course_id
#     }

# @app.get("/users/me") // route khong dong, phai de truoc route dong
# async def get_me():
#     ...


# @app.get("/users/{user_id}") // route dong 
# async def get_user(user_id: str):
#     ...


# 1xx : Information
# 2xx : Success
# 3xx : Redirect
# 4xx : Client error
# 5xx : Server error    
