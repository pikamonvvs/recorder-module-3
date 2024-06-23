import argparse
import json
import os
import time

import ffmpeg
import requests
from bs4 import BeautifulSoup

# List of valid platform names
PLATFORM_CHOICES = [
    "Afreeca",
    "Chzzk",
    "TikTok",
]
FORMAT_CHOICES = ["mp4", "ts", "flv"]

DEFAULT_HEADERS = {"User-Agent": "Chrome"}


def get_ids(user_id):
    url = f"https://www.tiktok.com/@{user_id}"

    response = requests.get(url, headers=DEFAULT_HEADERS)
    if response.status_code != 200:
        print(f"페이지를 불러오는데 실패했습니다. 상태 코드: {response.status_code}")
        return None

    soup = BeautifulSoup(response.text, "html.parser")
    # print(soup.prettify())

    script_tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
    # print(f"script 태그: {script_tag}")

    if not script_tag:
        print("해당 ID의 script 태그를 찾을 수 없습니다.")
        return None

    json_data = json.loads(script_tag.string)
    # print(f"JSON 데이터: {json.dumps(json_data, indent=2)}")
    if not json_data:
        print("JSON 데이터를 불러올 수 없습니다.")
        return None

    default_scope = json_data.get("__DEFAULT_SCOPE__")
    # print(f"Default scope: {json.dumps(default_scope, indent=2)}")
    if not default_scope:
        print("Default scope를 찾을 수 없습니다.")
        return None
    with open("default_scope.json", "w") as f:
        json.dump(default_scope, f, indent=2)

    user_detail = default_scope.get("webapp.user-detail")
    # print(f"User detail: {json.dumps(user_detail, indent=2)}")
    if not user_detail:
        print("User detail을 찾을 수 없습니다.")
        return None

    user_info = user_detail.get("userInfo")
    # print(f"User info: {json.dumps(user_info, indent=2)}")
    if not user_info:
        print("User info를 찾을 수 없습니다.")
        return None

    user = user_info.get("user")
    # print(f"User: {json.dumps(user, indent=2)}")
    if not user:
        print("User를 찾을 수 없습니다.")
        return None

    room_id = user.get("roomId")
    nickname = user.get("nickname")
    unique_id = user.get("uniqueId")
    # print(f"Room ID: {room_id}")
    # print(f"닉네임: {nickname}")
    # print(f"유니크 ID: {unique_id}")
    if not room_id:
        print("Room ID를 찾을 수 없습니다.")
        return None

    if not nickname:
        print("닉네임을 찾을 수 없습니다.")
        return None

    if not unique_id:
        print("유니크 ID를 찾을 수 없습니다.")
        return None

    return room_id, nickname, unique_id


def get_status(room_id):
    url = f"https://webcast.tiktok.com/webcast/room/check_alive/?aid=1988&room_ids={room_id}"

    response = requests.get(url, headers=DEFAULT_HEADERS)
    if response.status_code != 200:
        print(f"페이지를 불러오는데 실패했습니다. 상태 코드: {response.status_code}")
        return None
    # print(f"Response: {response.text}")

    json_data = response.json()
    # print(f"JSON 데이터: {json.dumps(json, indent=2)}")

    status_code = json_data.get("status_code")
    # print(f"Status code: {status_code}")
    if status_code != 0:
        print("Invalid status code")
        return None

    data = json_data.get("data")[0]
    # print(f"Data: {json.dumps(data, indent=2)}")
    if not data:
        print("Data를 찾을 수 없습니다.")
        return None

    alive = data.get("alive")
    # print(f"Alive: {alive}")
    if alive is None:
        print("Alive를 찾을 수 없습니다.")
        return None

    return alive


def get_title(room_id):
    url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

    response = requests.get(url, headers=DEFAULT_HEADERS)
    # print(f"Response: {response.text}")
    if response.status_code != 200:
        print(f"페이지를 불러오는데 실패했습니다. 상태 코드: {response.status_code}")
        return None

    json_data = response.json()
    # print(f"JSON 데이터: {json.dumps(json, indent=2)}")

    data = json_data.get("data")
    # print(f"Data: {json.dumps(data, indent=2)}")
    if not data:
        print("Data를 찾을 수 없습니다.")
        return None

    title = data.get("title")
    print(f"Title: {title}")
    if not title:
        print("Title을 찾을 수 없습니다.")
        return None

    return title


def get_live_url(room_id):
    url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

    response = requests.get(url, headers=DEFAULT_HEADERS)
    # print(f"Response: {response.text}")
    if response.status_code != 200:
        print(f"페이지를 불러오는데 실패했습니다. 상태 코드: {response.status_code}")
        return None

    json_data = response.json()
    # print(f"JSON 데이터: {json.dumps(json, indent=2)}")

    data = json_data.get("data")
    # print(f"Data: {json.dumps(data, indent=2)}")
    if not data:
        print("Data를 찾을 수 없습니다.")
        return None

    stream_url = data.get("stream_url")
    # print(f"Stream URL: {stream_url}")
    if not stream_url:
        print("Stream URL을 찾을 수 없습니다.")
        return None

    rtmp_pull_url = stream_url.get("rtmp_pull_url")
    print(f"RTMP Pull URL: {rtmp_pull_url}")
    if not rtmp_pull_url:
        print("RTMP Pull URL을 찾을 수 없습니다.")
        return None

    return rtmp_pull_url


def get_filename(channel_name, flag, title, file_format):
    live_time = time.strftime("%Y.%m.%d %H.%M.%S")
    # Convert special characters in the filename to full-width characters
    char_dict = {
        '"': "＂",
        "*": "＊",
        ":": "：",
        "<": "＜",
        ">": "＞",
        "?": "？",
        "/": "／",
        "\\": "＼",
        "|": "｜",
    }
    for half, full in char_dict.items():
        title = title.replace(half, full)

    filename = f"[{live_time}]{flag}{title[:50]}.{file_format}"
    return filename


def handle_recording_ffmpeg(live_url, out_file):
    try:
        proc = (
            ffmpeg.input(
                live_url, **{"loglevel": "error"}, **{"reconnect": 1}, **{"reconnect_streamed": 1}, **{"reconnect_at_eof": 1}, **{"reconnect_delay_max": 5}, **{"timeout": 10000000}, stats=None
            )
            .output(out_file, c="copy")
            .run_async(pipe_stderr=True)
        )
        while True:
            if proc.poll() is not None:
                break
    except KeyboardInterrupt as e:
        raise e
    except ValueError as e:
        raise e


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


def main():
    args = parse_args()

    platform = args.get("platform", "TikTok")
    user_id = args.get("id", "havivivi8")
    file_format = args.get("format", "ts")
    out_dir = args.get("output", "output")

    room_id, nickname, unique_id = get_ids(user_id)
    if not room_id:
        print("Room ID를 찾을 수 없습니다.")
        return
    print(f"Room ID: {room_id}")

    status = get_status(room_id)
    if status is None:
        print("상태를 찾을 수 없습니다.")
        return

    if status is True:
        print(f"방송 중: {status}")
    elif status is False:
        print("방송 중이 아닙니다.")
    elif status is None:
        print("상태를 찾을 수 없습니다.")

    live_url = get_live_url(room_id)
    if not live_url:
        print("라이브 URL을 찾을 수 없습니다.")
        return

    title = get_title(room_id)
    if not title:
        print("제목을 찾을 수 없습니다.")
        return

    flag = f"[{platform}][{user_id}]"
    file_name = get_filename(nickname, flag, title, file_format)
    print(f"파일 이름: {file_name}")
    output_path = os.path.join(out_dir, file_name)
    print(f"Output path: {output_path}")

    if not os.path.exists(out_dir):
        os.makedirs(out_dir)

    handle_recording_ffmpeg(live_url, output_path)


if __name__ == "__main__":
    main()
