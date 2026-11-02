"""Read-only capacity and shared soft disk admission for managed allocations."""
import logging
from threading import Lock
import time

from naiyue_test_agent.contracts.cloud_tasks import CloudError
from . import resource_disk

logger = logging.getLogger(__name__)


class CloudResources:
    def __init__(self, runtime):
        self.runtime = runtime
        self.limits = runtime.settings.resource_limits
        self._lock = Lock()
        self._usage = None
        self._measured = 0.0

    def disk(self, *, refresh=False):
        with self._lock:
            if (refresh or self._usage is None or
                    time.monotonic() - self._measured >= self.limits.scan_cache_seconds):
                self._usage = resource_disk.measure(self.runtime.settings.data_dir, self.limits)
                self._measured = time.monotonic()
                if self._usage.status == 'unknown':
                    logger.warning('cloud_resource_measurement_unknown code=%s', self._usage.reason)
            usage = self._usage
        # Volume free space is always fresh, including while logical usage is cached.
        try:
            available = resource_disk.free_bytes(self.runtime.settings.data_dir)
        except OSError:
            logger.error('cloud_resource_free_space_failed')
            return usage.model_copy(update={'status': 'unknown', 'free_bytes': None,
                'reason': 'resource_scan_unavailable'})
        return usage.model_copy(update={'free_bytes': available})

    def _reason(self, usage, additional_bytes):
        if usage.status != 'known' or usage.free_bytes is None:
            return 'resource_disk_unknown'
        if (usage.free_bytes < self.limits.min_free_bytes + additional_bytes or
                self.limits.max_logical_bytes is not None and
                usage.logical_bytes + additional_bytes > self.limits.max_logical_bytes):
            return 'resource_disk_capacity'
        return None

    def ensure(self, *, additional_bytes=0):
        if type(additional_bytes) is not int or additional_bytes < 0:
            raise CloudError('invalid_resource_allocation', 422)
        reason = self._reason(self.disk(), additional_bytes)
        if reason:
            raise CloudError(reason, 507 if reason == 'resource_disk_capacity' else 503)

    def capacity(self):
        counts = self.runtime.registry.resource_counts()
        usage = self.disk()
        reasons = []
        settings = self.runtime.settings
        if counts['active'] >= settings.max_active_environments:
            reasons.append('environment_capacity')
        if counts['retained'] >= settings.max_retained_environments:
            reasons.append('environment_retained_capacity')
        reason = self._reason(usage, 0)
        if reason:
            reasons.append(reason)
        return dict(instance_id=self.runtime.instance_id, counts=counts,
            limits=dict(active=settings.max_active_environments, retained=settings.max_retained_environments),
            disk=usage.model_dump(), can_create=not reasons, reasons=reasons)
