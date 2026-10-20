"""讀取 .env 設定。缺什麼就用白話告訴使用者，不把 token 印出來。"""

from dataclasses import dataclass
from pathlib import Path

from dotenv import dotenv_values

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    discord_token: str
    channel_id: int
    claude_model: str
    data_dir: Path

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def inbox_dir(self) -> Path:
        return self.data_dir / "inbox"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def excel_path(self) -> Path:
        return self.data_dir / "records.xlsx"


def load_config(env_path: Path | None = None) -> Config:
    path = env_path or PROJECT_ROOT / ".env"
    if not path.exists():
        raise ConfigError(f"找不到設定檔 {path}。請參考 docs/discord-bot-setup.md 建立 .env。")
    values = dotenv_values(path)

    missing = [k for k in ("DISCORD_TOKEN", "CHANNEL_ID", "CLAUDE_MODEL", "DATA_DIR") if not (values.get(k) or "").strip()]
    if missing:
        raise ConfigError(f".env 缺少這些項目或是空白：{', '.join(missing)}")

    try:
        channel_id = int(values["CHANNEL_ID"].strip())
    except ValueError:
        raise ConfigError("CHANNEL_ID 必須是純數字（右鍵頻道 → Copy Channel ID）") from None

    data_dir = Path(values["DATA_DIR"].strip().strip('"')).expanduser()
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir  # 相對路徑（例如 ./data）以專案資料夾為準，不受從哪裡啟動影響

    return Config(
        discord_token=values["DISCORD_TOKEN"].strip(),
        channel_id=channel_id,
        claude_model=values["CLAUDE_MODEL"].strip(),
        data_dir=data_dir,
    )
