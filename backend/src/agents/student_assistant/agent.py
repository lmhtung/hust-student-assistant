"""LangGraph thực hiện model → tools → model với ngân sách hữu hạn."""

from langgraph.graph import END, START, StateGraph

from src.agents.base_assistant.agent import AgentResult, BaseAssistant, StatusEmitter
from src.agents.student_assistant.context import AgentContext
from src.agents.student_assistant.node import SYSTEM_PROMPT, StudentNodes
from src.agents.student_assistant.state import StudentState
from src.guardrails.policy import SAFE_CLARIFICATION, SAFE_REFUSAL


class StudentAssistant(BaseAssistant):
    def __init__(self, llm, tools_factory, guardrails, settings):
        self.llm = llm
        self.tools_factory = tools_factory
        self.guardrails = guardrails
        self.settings = settings

    @staticmethod
    def _build_graph(nodes: StudentNodes):
        """Luồng cố định; model chỉ quyết định gọi tool hay trả lời trong luồng này."""
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

    async def run(
        self, message: str, history: list[dict], context: AgentContext, emit: StatusEmitter
    ) -> AgentResult:
        await emit({"stage": "checking_input"})
        assessment = await self.guardrails.assess(message, "input")
        if assessment.decision != "allow":
            if assessment.decision == "clarify":
                return AgentResult(SAFE_CLARIFICATION, "clarification_required")
            return AgentResult(SAFE_REFUSAL, "blocked")

        async with self.tools_factory(context.current_user).connect() as tools:
            # Mỗi lượt có nodes/state riêng: không chia sẻ lịch sử hay danh tính.
            nodes = StudentNodes(self.llm, tools, self.guardrails, self.settings, emit)
            graph = self._build_graph(nodes)
            result = await graph.ainvoke(
                {
                    "messages": [{"role": "system", "content": SYSTEM_PROMPT}]
                    + history
                    + [{"role": "user", "content": message}],
                    "draft": "",
                    "response": "",
                    "status": "answered",
                    "tool_rounds": 0,
                    "tool_calls": 0,
                },
                {"recursion_limit": 2 * self.settings.max_tool_rounds + 5},
            )
        return AgentResult(result["response"], result["status"])
