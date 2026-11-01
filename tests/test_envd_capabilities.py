"""Docker capability differences must be normalized before any task can start."""
import errno

import pytest

from naiyue_test_agent.contracts.sandbox import SandboxError
from naiyue_test_agent.envd import identity


def status(inherited=0, **changes):
    return dict(CapEff=0xeb, CapPrm=0xeb, CapBnd=0xeb, CapInh=inherited,
        CapAmb=0, NoNewPrivs=1, **changes)


@pytest.mark.parametrize('inherited', [0, 1, 0xeb])
def test_supported_inheritance_is_cleared_and_rechecked(monkeypatch, inherited):
    states = iter([status(inherited), status()])
    cleared = []
    monkeypatch.setattr(identity, '_capability_status', lambda: next(states), raising=False)
    monkeypatch.setattr(identity, '_clear_inheritable', lambda: cleared.append(True), raising=False)
    identity.initialize_capabilities()
    assert cleared == ([True] if inherited else [])


@pytest.mark.parametrize(('field', 'value'), [('CapEff', 0xff), ('CapPrm', 0xff),
    ('CapBnd', 0xff), ('CapAmb', 1), ('CapInh', 0x100), ('NoNewPrivs', 0)])
def test_unapproved_privilege_is_rejected_before_normalization(monkeypatch, field, value, caplog):
    current = status(); current[field] = value
    cleared = []
    monkeypatch.setattr(identity, '_capability_status', lambda: current, raising=False)
    monkeypatch.setattr(identity, '_clear_inheritable', lambda: cleared.append(True), raising=False)
    with pytest.raises(SandboxError, match='env_policy_mismatch'):
        identity.initialize_capabilities()
    assert not cleared
    assert 'envd_identity_rejected' in caplog.text


def test_normalization_failure_preserves_os_cause_and_logs_no_private_text(monkeypatch, caplog):
    original = PermissionError(errno.EPERM, 'PRIVATE_KERNEL_DETAILS')
    monkeypatch.setattr(identity, '_capability_status', lambda: status(0xeb), raising=False)
    def fail(): raise original
    monkeypatch.setattr(identity, '_clear_inheritable', fail, raising=False)
    with pytest.raises(SandboxError) as caught:
        identity.initialize_capabilities()
    assert caught.value.__cause__ is original
    assert 'errno=1' in caplog.text and 'PRIVATE' not in caplog.text


def test_post_normalization_state_is_not_trusted_without_recheck(monkeypatch, caplog):
    monkeypatch.setattr(identity, '_capability_status', lambda: status(0xeb), raising=False)
    monkeypatch.setattr(identity, '_clear_inheritable', lambda: None, raising=False)
    with pytest.raises(SandboxError, match='env_policy_mismatch'):
        identity.initialize_capabilities()
    assert 'phase=normalized' in caplog.text


def test_capset_preserves_both_words_of_effective_and_permitted(monkeypatch):
    import ctypes
    seen = []
    class Function:
        def __init__(self, call): self.call = call
        def __call__(self, *args): return self.call(*args)
    def get(header, data):
        assert header._obj.version == 0x20080522 and header._obj.pid == 0
        for index in range(2):
            data[index].effective = 0xeb + index
            data[index].permitted = 0xeb + index
            data[index].inheritable = 0xeb
        return 0
    def set_(header, data):
        seen.extend((part.effective, part.permitted, part.inheritable) for part in data)
        return 0
    library = type('Library', (), {'capget': Function(get), 'capset': Function(set_)})()
    monkeypatch.setattr(ctypes, 'CDLL', lambda *args, **kwargs: library)
    identity._clear_inheritable()
    assert seen == [(0xeb, 0xeb, 0), (0xec, 0xec, 0)]
