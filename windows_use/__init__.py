"""Public API; database/settings readers need not initialize Windows UI automation."""


def __getattr__(name):
    if name in __all__:
        from windows_use import agent

        value = getattr(agent, name)
        globals()[name] = value
        return value
    raise AttributeError(name)


__all__ = [
    "Agent",
    "Browser",
    "AgentEvent",
    "EventType",
    "Event",
    "BaseEventSubscriber",
    "ConsoleEventSubscriber",
    "FileEventSubscriber",
]
