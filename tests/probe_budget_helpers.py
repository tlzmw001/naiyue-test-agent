"""Offline probe adapters count generation starts instead of nonexistent HTTP."""

from langchain_core.callbacks import BaseCallbackHandler


def guard_fake_catalog(catalog, budget):
    class Reserve(BaseCallbackHandler):
        raise_error = True

        def __init__(self, model_id):
            self.model_id = model_id

        def on_chat_model_start(self, serialized, messages, **kwargs):
            budget.reserve(self.model_id)

    for model_id in catalog.model_ids:
        model = catalog.get(model_id)
        model.callbacks = [*(model.callbacks or []), Reserve(model_id)]
