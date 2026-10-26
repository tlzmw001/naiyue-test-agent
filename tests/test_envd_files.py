"""The actual container-side algorithms can be exercised without a Docker fake."""
from pathlib import Path
import pytest
from naiyue_test_agent.contracts.environment import EnvironmentRef


def test_envd_file_algorithms_preserve_feedback_and_confine_links(tmp_path):
    from naiyue_test_agent.envd.files import EnvdFiles
    from naiyue_test_agent.tools.settings import WriteSettings, ReadSettings, EditSettings
    from naiyue_test_agent.tools.edit import EditReplacement
    environment=EnvironmentRef('B','sandbox','P',str(tmp_path),'linux')
    files=EnvdFiles(environment,'S')
    subject=files.prepare_target('a.txt','write')
    subject.update(args={'path':'a.txt'},path_field='path',tool_name='write')
    text, artifact=files.write(subject,'before\n',settings=WriteSettings(),control=None,progress=None,read_allowed=True)
    assert 'created' in text
    subject=files.prepare_target('a.txt','read')
    assert 'before' in files.read(subject,None,None,settings=ReadSettings(),control=None,progress=None)
    subject=files.prepare_target('a.txt','write')
    subject.update(args={'path':'a.txt'},path_field='path',tool_name='edit')
    text,artifact=files.edit(subject,[EditReplacement(oldText='before',newText='after')],settings=EditSettings(),control=None,progress=None)
    assert (tmp_path/'a.txt').read_text()=='after\n' and artifact
    outside=tmp_path.parent/'outside';outside.write_text('private')
    (tmp_path/'escape').symlink_to(outside)
    with pytest.raises(Exception,match='outside|unavailable'):
        files.prepare_target('escape','read')
    with pytest.raises(Exception,match='outside|unavailable'):
        files.read_resource(str(outside),100)
