import pytest

from gift_agent.config import Config


@pytest.fixture
def cfg(tmp_path):
    return Config(discord_token="x", channel_id=1, claude_model="haiku", data_dir=tmp_path / "data")
