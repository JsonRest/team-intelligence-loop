# Lazy-load root_agent so the frontend container can import til_agent.database
# without requiring google-adk to be installed.
try:
    from til_agent.agent import root_agent
    __all__ = ["root_agent"]
except ImportError:
    pass  # ADK not installed — database tools still importable
