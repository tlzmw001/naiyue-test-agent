from pathlib import Path
from threading import RLock
from types import SimpleNamespace
import pytest
from naiyue_test_agent.application.sessions.run_admission import RunAdmission
from naiyue_test_agent.application.sessions.background_tasks import BackgroundProcessManager
from naiyue_test_agent.application.errors import SessionBusyError


class Service:
    def __init__(self,project,busy=False):
        self._project=SimpleNamespace(project_id=project);self._lock=RLock();self._closing=False
        self._sessions={'S':SimpleNamespace(is_busy=lambda:busy)};self._environment_management=set()
        self.background_tasks=BackgroundProcessManager()


def test_project_delivery_prevents_new_admission_but_other_projects_continue():
    admission=RunAdmission(10);a,b=Service('A'),Service('B');admission.services.update((a,b))
    with admission.manage_project('A'):
        with pytest.raises(SessionBusyError):admission.check_project('A')
        admission.check_project('B')
        assert 'S' in a.background_tasks._deleting and 'S' not in b.background_tasks._deleting
    admission.check_project('A');assert not a.background_tasks._deleting


def test_active_actor_or_unconfirmed_background_rejects_before_reservation():
    admission=RunAdmission(10);service=Service('A',busy=True);admission.services.add(service)
    with pytest.raises(SessionBusyError):
        with admission.manage_project('A'):pytest.fail('reserved while running')
    service._sessions['S']=SimpleNamespace(is_busy=lambda:False)
    service.background_tasks._entries['T']=SimpleNamespace(session_id='S',cleanup_confirmed=False)
    with pytest.raises(SessionBusyError):
        with admission.manage_project('A'):pytest.fail('reserved with live process')
    assert not service.background_tasks._deleting
