"""Event emitter service for the agent."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from windows_use.agent.events.subscriber import BaseEventSubscriber
from windows_use.agent.events.views import AgentEvent
from windows_use.safety import redact

EventSubscriber = BaseEventSubscriber | Callable[[AgentEvent], None]


class Event:
    """Manages event subscribers and dispatches events to them.

    Accepts both `BaseEventSubscriber` subclass instances and plain callables.

    Usage:
        ```python
        event = Event()
        event.add_subscriber(ConsoleEventSubscriber())
        event.add_subscriber(lambda e: print(e))
        event.emit(AgentEvent(type=EventType.THOUGHT, data={"step": 0, "thought": "..."}))
        ```
    """

    def __init__(self, secrets=()) -> None:
        self._subscribers: list[EventSubscriber] = []
        self.secrets = tuple(secrets)

    def add_subscriber(self, subscriber: EventSubscriber) -> None:
        """Register an event subscriber."""
        self._subscribers.append(subscriber)

    def remove_subscriber(self, subscriber: EventSubscriber) -> None:
        """Unregister an event subscriber."""
        if subscriber in self._subscribers:
            self._subscribers.remove(subscriber)

    def emit(self, event: AgentEvent) -> None:
        """Dispatch an event to all registered subscribers."""
        if self.secrets:
            event = replace(event, data=redact(event.data, self.secrets))
        for subscriber in self._subscribers:
            try:
                if isinstance(subscriber, BaseEventSubscriber):
                    subscriber.invoke(event)
                else:
                    subscriber(event)
            except Exception:
                pass

    def close(self) -> None:
        """Close all subscribers that support it and clear the list."""
        for subscriber in self._subscribers:
            if isinstance(subscriber, BaseEventSubscriber):
                try:
                    subscriber.close()
                except Exception:
                    pass
        self._subscribers.clear()
