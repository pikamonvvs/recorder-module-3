import argparse

PLATFORM_CHOICES = [
    "Afreeca",
    "Chzzk",
]
FORMAT_CHOICES = ["mp4", "ts", "flv"]


def parse_args():
    parser = argparse.ArgumentParser(description="Print a welcome message and accept various settings.")
    parser.add_argument("platform", type=str, choices=PLATFORM_CHOICES, help="Name of the platform")
    parser.add_argument("id", type=str, help="ID of the user")
    parser.add_argument("-n", "--name", type=str, help="Specify a name")
    parser.add_argument("-i", "--interval", type=int, help="Set interval time in seconds")
    parser.add_argument("-f", "--format", type=str, choices=FORMAT_CHOICES, help="Set the output format")
    parser.add_argument("-o", "--output", type=str, help="Specify the output file path")
    parser.add_argument("-p", "--proxy", type=str, help="Set the proxy server")
    parser.add_argument("-c", "--cookies", type=str, help="Set the cookies file path")
    parser.add_argument("-H", "--headers", type=str, help="Set the headers")
    parser.add_argument("-l", "--log-level", type=str, help="Set the logging level")

    args = parser.parse_args()

    # Create a dictionary from the arguments and filter out None values
    args_dict = {key: value for key, value in vars(args).items() if value is not None}

    return args_dict
