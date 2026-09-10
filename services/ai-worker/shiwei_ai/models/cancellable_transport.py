"""Cancellable HTTP waits; only network I/O runs off the SQLite owner thread."""
import asyncio
import queue
import threading

import httpx


class CancellableTransport(httpx.BaseTransport):
    def __init__(self, check):
        self.check = check

    def handle_request(self, request):
        stream = _NetworkStream(request, self.check)
        try:
            status, headers, extensions = stream.receive()
            return httpx.Response(status, headers=headers, extensions=extensions, stream=stream)
        except BaseException:
            stream.close()
            raise


class _NetworkStream(httpx.SyncByteStream):
    def __init__(self, request, check):
        self.check = check
        self.items = queue.Queue(maxsize=8)
        self.stopped = threading.Event()
        self.loop = None
        self.task = None
        # Read request body on the caller thread, never share DB/generators.
        self.request = httpx.Request(request.method, request.url, headers=request.headers,
                                     content=request.read(), extensions=request.extensions)
        self.thread = threading.Thread(target=self.run, daemon=True, name="shiwei-http")
        self.thread.start()

    async def put(self, value):
        while not self.stopped.is_set():
            try:
                self.items.put_nowait(value)
                return
            except queue.Full:
                await asyncio.sleep(.01)

    async def transfer(self):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.send(self.request, stream=True)
                try:
                    await self.put(("value", (response.status_code, response.headers, {})))
                    async for data in response.aiter_raw():
                        await self.put(("value", data))
                finally:
                    await response.aclose()
            await self.put(("end", None))
        except asyncio.CancelledError:
            raise
        except Exception as error:
            await self.put(("error", error))

    def run(self):
        async def main():
            self.loop = asyncio.get_running_loop()
            self.task = asyncio.current_task()
            if not self.stopped.is_set():
                await self.transfer()
        try:
            asyncio.run(main())
        except asyncio.CancelledError:
            pass

    def receive(self):
        while True:
            self.check()
            try:
                kind, value = self.items.get(timeout=.05)
            except queue.Empty:
                continue
            self.check()
            if kind == "error":
                raise value
            if kind == "end":
                raise StopIteration
            return value

    def __iter__(self):
        try:
            while True:
                try:
                    yield self.receive()
                except StopIteration:
                    return
        finally:
            self.close()

    def close(self):
        self.stopped.set()
        if self.loop and self.task and not self.loop.is_closed():
            try:
                self.loop.call_soon_threadsafe(self.task.cancel)
            except RuntimeError:
                pass
        self.thread.join(timeout=2)
