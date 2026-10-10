"""对话工具智能体入口。"""

__all__ = ["run_tool_agent"]


def __getattr__(name: str):
    if name == "run_tool_agent":
        from boundary_agent.agent.react_agent import run_tool_agent

        return run_tool_agent
    raise AttributeError(name)
