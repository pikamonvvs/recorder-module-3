import traceback

import test_utils
from loguru import logger
from test_recorders import *


def main():
    logger.add(
        sink="logs/log_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="3 days",
        level="INFO",
        encoding="utf-8",
        format="[{time:YYYY-MM-DD HH:mm:ss}][{level}][{name}][{function}:{line}]{message}",
    )

    try:
        args = test_utils.parse_args()
        platform = globals()[args.get("platform")]
        platform(args).run()
    except Exception as ex:
        logger.error("Exception caught in main:")
        logger.error(f"{ex}\n")
        traceback.print_exc()


if __name__ == "__main__":
    main()
