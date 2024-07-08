import traceback

import utils.utils as utils
from recorders.recorders import *
from utils.utils import logutil


def main():
    try:
        args = utils.parse_args()
        platform = globals()[args.get("platform")]
        platform(args).run()
    except Exception as ex:
        logutil.error("Exception caught in main:")
        logutil.error(f"{ex}\n")
        traceback.print_exc()


if __name__ == "__main__":
    main()
