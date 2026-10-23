from gift_agent.config import PROJECT_ROOT, load_config


def test_relative_data_dir_is_relative_to_project(tmp_path):
    env = tmp_path / ".env"
    env.write_text("DISCORD_TOKEN=abc\nCHANNEL_ID=123\nCLAUDE_MODEL=sonnet\nDATA_DIR=./data\n", encoding="utf-8")
    assert load_config(env).data_dir == PROJECT_ROOT / "data"
