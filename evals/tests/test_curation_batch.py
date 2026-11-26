import json

import pytest

from evals.curate.__main__ import main


@pytest.mark.parametrize("contents", [None, {"name": "an unrelated project manifest"}])
def test_review_rejects_wrong_directory_without_leaving_a_lock(tmp_path, contents):
    if contents is not None:
        (tmp_path / "manifest.json").write_text(json.dumps(contents), encoding="utf-8")
    before = set(tmp_path.iterdir())
    with pytest.raises(SystemExit) as error:
        main(["review", "--batch", str(tmp_path)])
    assert error.value.code == 2
    assert set(tmp_path.iterdir()) == before
