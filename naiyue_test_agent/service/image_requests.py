"""Nonwaiting image request reservations, owned until the producer settles."""
from ..image_input_settings import ImageInputSettings
from ..web.errors import WebError


class ImageRequests:
    def __init__(self, settings=None):
        self.settings = settings or ImageInputSettings()
        self.active = 0

    def reserve(self, scope):
        header = dict(scope['headers']).get(b'x-image-input')
        if header is None:
            return None
        parts = scope['path'].split('/')
        valid = (scope['method'] == 'POST' and len(parts) == 8 and parts[3] == 'scopes'
                 and ((parts[5] == 'sessions' and parts[7] == 'runs')
                      or (parts[5] == 'runs' and parts[7] == 'controls')))
        if header != b'1' or not valid:
            raise WebError('invalid_image_request',422)
        if self.active >= self.settings.max_in_flight_requests:
            raise WebError('image_input_busy',429)
        self.active += 1
        return ImageLease(self)


class ImageLease:
    def __init__(self, budget):
        self.budget, self.transferred, self.released = budget, False, False

    def release(self):
        if not self.released:
            self.released = True
            self.budget.active -= 1
