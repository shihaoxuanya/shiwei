"""Cooperative cancellation without moving database work off its sole writer."""
from collections.abc import Callable

from shiwei_ai.models import ModelGateway
from shiwei_ai.models.gateway import OpenAICompatibleGateway, create_gateway
from shiwei_ai.models.cancellable_transport import CancellableTransport


class ChatCancelled(RuntimeError):
    pass


class CancellableGateway(ModelGateway):
    def __init__(self, gateway: ModelGateway, check: Callable[[], None]):
        self.gateway = gateway
        self.check = check

    def _request_gateway(self):
        # A request-scoped client: cancelling it cannot close a different chat,
        # provider test or the persistent gateway. Custom/test gateways retain
        # their original behavior.
        if isinstance(self.gateway, OpenAICompatibleGateway):
            return create_gateway(self.gateway.config, transport=CancellableTransport(self.check))
        return self.gateway

    def chat(self, messages, **options):
        self.check()
        gateway = self._request_gateway()
        try:
            answer = gateway.chat(messages, **options)
            self.check()
            return answer
        finally:
            if gateway is not self.gateway:
                gateway.close()

    def stream_chat(self, messages, **options):
        self.check()
        gateway = self._request_gateway()
        stream = gateway.stream_chat(messages, **options)
        try:
            for token in stream:
                self.check()
                yield token
            self.check()
        finally:
            close = getattr(stream, "close", None)
            if close:
                close()
            if gateway is not self.gateway:
                gateway.close()

    def embed(self, texts):
        self.check()
        result = self.gateway.embed(texts)
        self.check()
        return result
