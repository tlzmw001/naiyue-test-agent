"""Execute the summary IO requested by the shared compaction algorithm."""

from .stream_events import SUMMARY_TAG


async def arun_summary_steps(steps):
    """Keep model waiting asynchronous; the deterministic policy is identical."""
    try:
        pending = next(steps)
        while True:
            model, messages = pending
            try:
                response = await model.ainvoke(messages, config={'tags': [SUMMARY_TAG]})
            except BaseException as error:
                pending = steps.throw(error)
            else:
                pending = steps.send(response)
    except StopIteration as completed:
        return completed.value
    finally:
        steps.close()
