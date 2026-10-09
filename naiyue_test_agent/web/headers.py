"""Response protections without wrapping streams in a second task lifetime."""


class LocalHeaders:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def guarded(message):
            if message['type'] == 'http.response.start':
                protected = {b'cache-control', b'x-content-type-options', b'referrer-policy',
                             b'content-security-policy', b'x-frame-options'}
                headers = [(key, value) for key, value in message['headers'] if key.lower() not in protected]
                headers.extend([
                    (b'cache-control', b'no-store'), (b'x-content-type-options', b'nosniff'),
                    (b'referrer-policy', b'no-referrer'), (b'x-frame-options', b'DENY'),
                    (b'content-security-policy', b"default-src 'self'; script-src 'self'; style-src 'self'; "
                     b"connect-src 'self'; img-src 'self'; font-src 'self'; object-src 'none'; "
                     b"base-uri 'none'; frame-ancestors 'none'"),
                ])
                message = {**message, 'headers': headers}
            await send(message)
        await self.app(scope, receive, guarded)
