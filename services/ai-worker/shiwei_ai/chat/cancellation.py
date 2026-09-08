"""Cooperative cancellation without moving database work off its sole writer."""
from collections.abc import Callable

from shiwei_ai.models import ModelGateway


class ChatCancelled(RuntimeError):
    pass


class CancellableGateway(ModelGateway):
    def __init__(self, gateway: ModelGateway, check: Callable[[], None]):
        self.gateway = gateway
        self.check = check

    def chat(self, messages, **options):
        self.check()
        answer = self.gateway.chat(messages, **options)
        self.check()
        return answer

    def stream_chat(self, messages, **options):
        self.check()
        stream = self.gateway.stream_chat(messages, **options)
        try:
            for token in stream:
                self.check()
                yield token
            self.check()
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()

    def embed(self, texts):
        self.check()
        result = self.gateway.embed(texts)
        self.check()
        return result
