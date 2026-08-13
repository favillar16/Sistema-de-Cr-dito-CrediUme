"""Shared test doubles for direct-servicer-call style tests.

Mirrors the FakeContext/AbortCalled pair originally defined in
test_auth_service.py so client/loan servicer tests don't need a real grpc
ServicerContext (see test_interceptor_integration.py for the real-server
alternative used for RBAC coverage).
"""


class AbortCalled(Exception):
    def __init__(self, code, message):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class FakeContext:
    def __init__(self, peer="ipv4:127.0.0.1:1234"):
        self._peer = peer

    def abort(self, code, message):
        raise AbortCalled(code, message)

    def peer(self):
        return self._peer
