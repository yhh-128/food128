"""啟動入口：`uv run gift-agent`"""

import logging
import sys
from logging.handlers import RotatingFileHandler

import discord

from gift_agent.bot import GiftBot
from gift_agent.config import Config, ConfigError, load_config


def _setup_logging(cfg: Config) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    handlers = [
        logging.StreamHandler(),
        RotatingFileHandler(cfg.logs_dir / "agent.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"),
    ]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def main() -> None:
    try:
        cfg = load_config()
        cfg.photos_dir.mkdir(parents=True, exist_ok=True)
        cfg.inbox_dir.mkdir(parents=True, exist_ok=True)
    except ConfigError as exc:
        print(f"設定有問題：{exc}", file=sys.stderr)
        raise SystemExit(1) from None
    except OSError as exc:
        print(f"無法建立 DATA_DIR 底下的資料夾：{exc}", file=sys.stderr)
        raise SystemExit(1) from None

    _setup_logging(cfg)
    try:
        GiftBot(cfg).run(cfg.discord_token, log_handler=None)
    except discord.LoginFailure:
        print("登入失敗：DISCORD_TOKEN 不正確（可以到 Developer Portal 重設 token 後更新 .env）", file=sys.stderr)
        raise SystemExit(1) from None
    except discord.PrivilegedIntentsRequired:
        print("Developer Portal 還沒打開 Message Content Intent（見 docs/discord-bot-setup.md 第二節）", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
