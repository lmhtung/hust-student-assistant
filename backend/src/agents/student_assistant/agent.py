"""Student chỉ định nghĩa prompt, state và graph; vòng đời do base quản lý."""

from langchain_core.messages import AnyMessage
from langgraph.graph import END, START, StateGraph

from src.agents.base_assistant.agent import BaseAssistant
from src.agents.base_assistant.events import StatusEmitter
from src.agents.student_assistant.context import AgentContext
from src.agents.student_assistant.node import SYSTEM_PROMPT, StudentNodes
from src.agents.student_assistant.state import StudentState


class StudentAssistant(BaseAssistant):
    # Kế thừa __init__, run() và astream(); chỉ bổ sung nghiệp vụ sinh viên.
    @property
    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def _initial_state(self, messages: list[AnyMessage]) -> StudentState:
        return {
            **super()._initial_state(messages),
            "draft": "",
            "tool_rounds": 0,
            "tool_calls": 0,
        }

    def _build_graph(self, tools, context: AgentContext, emit: StatusEmitter):
        """Luồng cố định; model chỉ quyết định gọi tool hay trả lời trong luồng này."""
        nodes = StudentNodes(self._generate, tools, self._settings, emit)
        graph = StateGraph(StudentState)
        graph.add_node("model", nodes.model)
        graph.add_node("tools", nodes.execute_tools)
        graph.add_node("output", nodes.output)
        graph.add_node("budget", nodes.budget)
        graph.add_edge(START, "model")
        graph.add_conditional_edges(
            "model",
            nodes.after_model,
            {"tools": "tools", "output": "output", "budget": "budget"},
        )
        graph.add_edge("tools", "model")
        graph.add_edge("output", END)
        graph.add_edge("budget", END)
        return graph.compile()
