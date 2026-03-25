import webbrowser
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def mock_webbrowser_open():
    """Prevent tests from opening browser windows."""
    with patch.object(webbrowser, "open") as mock_open:
        yield mock_open
