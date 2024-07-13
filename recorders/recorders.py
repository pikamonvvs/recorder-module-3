import io
import json
import os
import re
import sys
import time
from enum import Enum, IntEnum

import ffmpeg
import requests
from bs4 import BeautifulSoup

from utils.utils import logutil

# import bot_utils
# import errors

# from enums import ErrorMsg, LiveStatus, StatusCode, WaitTime

DEFAULT_INTERVAL = 10
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://www.tiktok.com/",
}
DEFAULT_OUTPUT = "output"
DEFAULT_FORMAT = "ts"
DEFAULT_PROXY = None
DEFAULT_COOKIES = None
DEFAULT_NAME = None


class TikTok:
    def __init__(self, user: dict):
        self.platform = user["platform"]
        self.id = user["id"]

        self.name = user.get("name", DEFAULT_NAME)
        self.interval = user.get("interval", DEFAULT_INTERVAL)
        self.headers = user.get("headers", DEFAULT_HEADERS)
        self.cookies = user.get("cookies", DEFAULT_COOKIES)
        self.format = user.get("format", DEFAULT_FORMAT)
        self.proxy = user.get("proxy", DEFAULT_PROXY)
        self.output = user.get("output", DEFAULT_OUTPUT)

        self.flag = f"[{self.platform}][{self.id}]"

        self.room_id = None

        self.req = requests
        if self.proxy:
            self.req = get_proxy_session(self.proxy)

        self.status = LiveStatus.BOT_INIT
        self.out_file = None
        self.video_list = [str]

    def run(self):
        if not os.path.exists(self.output):
            os.makedirs(self.output)

        while True:
            try:
                if self.status == LiveStatus.LAGGING:
                    retry_wait(WaitTime.LAG, False)
                if not self.room_id:
                    self.room_id = self.test_get_room_id_from_user()
                if not self.room_id:
                    self.room_id = self.get_room_id_from_user()
                if not self.name:
                    self.name = self.get_user_from_room_id()
                if self.status == LiveStatus.BOT_INIT:
                    logutil.info(self.flag, f"Username: {self.name}")
                    logutil.info(self.flag, f"Room ID: {self.room_id}")

                self.status = self.is_user_live()

                if self.status == LiveStatus.OFFLINE:
                    logutil.info(self.flag, f"{self.name} is offline")
                    self.room_id = None
                    if self.out_file:
                        self.finish_recording()
                    else:
                        retry_wait(self.interval, False)
                elif self.status == LiveStatus.LAGGING:
                    live_url = self.get_live_url()
                    self.start_recording(live_url)
                elif self.status == LiveStatus.LIVE:
                    logutil.info(self.flag, f"{self.name} is live")
                    live_url = self.get_live_url()
                    logutil.info(self.flag, f"Live URL: {live_url}")
                    self.start_recording(live_url)

            except (GenericReq, ValueError, requests.HTTPError, BrowserExtractor, ConnectionClosed, UserNotFound) as e:
                if "room_id not found" in str(e):
                    logutil.info(self.flag, e)
                else:
                    logutil.error(self.flag, e)
                self.room_id = None
                retry_wait(self.interval)
            except Blacklisted as e:
                logutil.error(self.flag, ErrorMsg.BLKLSTD_AUTO_MODE_ERROR)
                raise e
            except KeyboardInterrupt:
                logutil.warning(self.flag, "Stopped by keyboard interrupt.")
                sys.exit(0)

    def start_recording(self, live_url):
        """Start recording live"""
        should_exit = False
        # current_date = time.strftime("%Y.%m.%d_%H-%M-%S", time.localtime())
        # suffix = ""
        # self.out_file = f"{self.output}{self.name}_{current_date}{suffix}.mp4"

        title = self.get_title(self.room_id)
        output_file = self.get_filename(self.flag, title, self.format)
        self.out_file = os.path.join(self.output, output_file)

        if self.status is not LiveStatus.LAGGING:
            logutil.info(self.flag, f"Output directory: {self.output}")
        try:
            self.handle_recording_ffmpeg(live_url)

        except StreamLagging:
            logutil.info(self.flag, "Stream lagging")
        except FFmpeg as e:
            logutil.error(self.flag, f"FFmpeg error: {e}")
        except FileNotFoundError as e:
            logutil.error(self.flag, "FFmpeg is not installed.")
            raise e
        except KeyboardInterrupt:
            logutil.info(self.flag, "Recording stopped by keyboard interrupt")
            should_exit = True
        except Exception as e:
            logutil.error(self.flag, f"Recording error: {e}")

        self.status = LiveStatus.LAGGING

        try:
            if os.path.getsize(self.out_file) < 1048576:
                os.remove(self.out_file)
                # logutil.info(self.flag, "removed file < 1MB")
            else:
                self.video_list.append(self.out_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            logutil.error(self.flag, e)

        if should_exit:
            self.finish_recording()
            sys.exit(0)

    def handle_recording_ffmpeg(self, live_url):
        """Show real-time stats and raise ffmpeg errors"""
        stream = ffmpeg.input(
            live_url, **{"loglevel": "error"}, **{"reconnect": 1}, **{"reconnect_streamed": 1}, **{"reconnect_at_eof": 1}, **{"reconnect_delay_max": 5}, **{"timeout": 10000000}, stats=None
        )
        stats_shown = False
        stream = ffmpeg.output(stream, self.out_file, c="copy")
        try:
            proc = ffmpeg.run_async(stream, pipe_stderr=True)
            ffmpeg_err = ""
            last_stats = ""
            text_stream = io.TextIOWrapper(proc.stderr, encoding="utf-8")
            while True:
                if proc.poll() is not None:
                    break
                for line in text_stream:
                    line = line.strip()
                    if "frame=" in line:
                        last_stats = line
                        if not stats_shown:
                            logutil.info(self.flag, "Started recording")
                            logutil.info(self.flag, "Press 'q' to re-start recording, CTRL + C to stop")
                            self.status = LiveStatus.LIVE
                        # logutil.info(self.flag, last_stats, end="\r")
                        logutil.info(self.flag, last_stats)
                        stats_shown = True
                    else:
                        ffmpeg_err = ffmpeg_err + "".join(line)
            if ffmpeg_err:
                if lag_error(ffmpeg_err):
                    raise StreamLagging
                else:
                    raise FFmpeg(ffmpeg_err.strip())
        except KeyboardInterrupt as i:
            raise i
        except ValueError as e:
            logutil.error(self.flag, e)
        finally:
            if stats_shown:
                logutil.info(self.flag, last_stats)

    def finish_recording(self):
        """Combine multiple videos into one if needed"""
        try:
            current_date = time.strftime("%Y.%m.%d_%H-%M-%S", time.localtime())
            ffmpeg_concat_list = f"{self.name}_{current_date}_concat_list.txt"
            if len(self.video_list) > 1:
                title = self.get_title(self.room_id) + "_concat"
                output_file = self.get_filename(self.flag, title, self.format)
                self.out_file = os.path.join(self.output, output_file)
                logutil.info(self.flag, f"Concatenating {len(self.video_list)} video files")
                with open(ffmpeg_concat_list, "w") as file:
                    for v in self.video_list:
                        file.write(f"file '{v}'")
                proc = ffmpeg.input(ffmpeg_concat_list, **{"f": "concat"}, **{"safe": 0}, **{"loglevel": "error"}).output(self.out_file, c="copy").run_async(pipe_stderr=True)
                text_stream = io.TextIOWrapper(proc.stderr, encoding="utf-8")
                ffmpeg_err = ""
                while True:
                    if proc.poll() is not None:
                        break
                    for line in text_stream:
                        ffmpeg_err = ffmpeg_err + "".join(line)
                if ffmpeg_err:
                    raise FFmpeg(ffmpeg_err.strip())
                logutil.info(self.flag, "Concat finished")
                for v in self.video_list:
                    os.remove(v)
                logutil.info(self.flag, f"Deleted {len(self.video_list)} video files")
            if os.path.isfile(self.out_file):
                logutil.info(self.flag, f"Recording finished: {self.out_file}")
            if os.path.exists(ffmpeg_concat_list):
                os.remove(ffmpeg_concat_list)
        except FFmpeg as e:
            logutil.error(self.flag, "FFmpeg concat error:")
            logutil.error(self.flag, e)
        except Exception as ex:
            logutil.error(self.flag, ex)
        self.video_list = []
        self.out_file = None

    def is_user_live(self):
        try:
            url = f"https://www.tiktok.com/api/live/detail/?aid=1988&roomID={self.room_id}"
            json = self.req.get(url, headers=self.headers).json()
            # logutil.info(self.flag, f"is_user_live response {json}")
            if not check_exists(json, ["LiveRoomInfo", "status"]):
                raise ValueError(f"LiveRoomInfo.status not found in json: {json}")
            live_status_code = json["LiveRoomInfo"]["status"]
            if live_status_code != 4:
                return LiveStatus.LAGGING if self.status == LiveStatus.LAGGING else LiveStatus.LIVE
            else:
                return LiveStatus.OFFLINE

        except ConnectionAbortedError:
            raise ConnectionClosed(ErrorMsg.CONNECTION_CLOSED)
        except ValueError as e:
            raise e
        except Exception as ex:
            raise GenericReq(ex)

    def get_live_url(self) -> str:
        """Get the cdn (flv or m3u8) of the stream"""
        try:
            if self.status is not LiveStatus.LAGGING:
                logutil.info(self.flag, f"Getting live url for room ID {self.room_id}")
            url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={self.room_id}"
            json = self.req.get(url, headers=self.headers).json()
            if login_required(json):
                raise LoginRequired("Login required")
            if not check_exists(json, ["data", "stream_url", "rtmp_pull_url"]):
                raise ValueError(f"rtmp_pull_url not in response: {json}")
            return json["data"]["stream_url"]["rtmp_pull_url"]
        except ValueError as e:
            raise e
        except LoginRequired as e:
            raise e
        except AgeRestricted as e:
            raise e
        except BrowserExtractor as e:
            raise e
        except Exception as ex:
            raise GenericReq(ex)

    def get_room_id_from_user(self) -> str:
        try:
            response = self.req.get(f"https://www.tiktok.com/@{self.id}/live", allow_redirects=False, headers=self.headers)
            # logutil.info(self.flag, f'get_room_id_from_user response: {response.text}')
            if response.status_code == StatusCode.REDIRECT:
                raise Blacklisted("Redirect")
            match = re.search(r"room_id=(\d+)", response.text)
            if not match:
                raise ValueError("room_id not found")
            return match.group(1)

        except (requests.HTTPError, Blacklisted) as e:
            raise Blacklisted(e)
        except AttributeError as e:
            raise UserNotFound(f"{ErrorMsg.USERNAME_ERROR}\n{e}")
        except ValueError as e:
            raise e
        except Exception as ex:
            raise GenericReq(ex)

    def get_user_from_room_id(self) -> str:
        try:
            url = f"https://www.tiktok.com/api/live/detail/?aid=1988&roomID={self.room_id}"
            json = requests.get(url, headers=self.headers).json()
            if not check_exists(json, ["LiveRoomInfo", "ownerInfo", "uniqueId"]):
                logutil.error(self.flag, f"LiveRoomInfo.uniqueId not found in json: {json}")
                raise UserNotFound(ErrorMsg.USERNAME_ERROR)
            return json["LiveRoomInfo"]["ownerInfo"]["uniqueId"]

        except ConnectionAbortedError:
            raise ConnectionClosed(ErrorMsg.CONNECTION_CLOSED)
        except UserNotFound as e:
            raise e
        except Exception as ex:
            raise GenericReq(ex)

    ##################################################################################################

    def test_get_room_id_from_user(self):
        url = f"https://www.tiktok.com/@{self.id}"

        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                logutil.error(f"Failed to load the page. Status code: {response.status_code}")
                return None

            soup = BeautifulSoup(response.text, "html.parser")
            # logutil.debug(self.flag, soup.prettify())

            script_tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
            # logutil.debug(self.flag, f"Script tag: {script_tag}")

            if not script_tag:
                logutil.error(self.flag, "Cannot find script tag for this ID.")
                return None

            json_data = json.loads(script_tag.string)
            # logutil.debug(self.flag, f"JSON data: {json.dumps(json_data, indent=2)}")
            if not json_data:
                logutil.error(self.flag, "Failed to load JSON data.")
                return None

            default_scope = json_data.get("__DEFAULT_SCOPE__")
            # logutil.debug(self.flag, f"Default scope: {json.dumps(default_scope, indent=2)}")
            if not default_scope:
                logutil.error(self.flag, "Cannot find default scope.")
                return None
            # with open("default_scope.json", "w") as f:
            #     json.dump(default_scope, f, indent=2)

            user_detail = default_scope.get("webapp.user-detail")
            # logutil.debug(self.flag, f"User detail: {json.dumps(user_detail, indent=2)}")
            if not user_detail:
                logutil.error(self.flag, "Cannot find user detail.")
                return None

            user_info = user_detail.get("userInfo")
            # logutil.debug(self.flag, f"User info: {json.dumps(user_info, indent=2)}")
            if not user_info:
                logutil.error(self.flag, "Cannot find user info.")
                return None

            user = user_info.get("user")
            # logutil.debug(self.flag, f"User: {json.dumps(user, indent=2)}")
            if not user:
                logutil.error(self.flag, "Cannot find user.")
                return None

            room_id = user.get("roomId")
            # nickname = user.get("nickname")
            # unique_id = user.get("uniqueId")
            # logutil.debug(self.flag, f"Room ID: {room_id}")
            # logutil.debug(self.flag, f"Nickname: {nickname}")
            # logutil.debug(self.flag, f"Unique ID: {unique_id}")
            if not room_id:
                logutil.info(self.flag, "Cannot find Room ID.")
                return None

            # if not nickname:
            #     logutil.error(self.flag, "Cannot find nickname.")
            #     return None

            # if not unique_id:
            #     logutil.error(self.flag, "Cannot find unique ID.")
            #     return None

            return room_id
        except Exception as e:
            logutil.error(self.flag, f"Exception occurred: {e}")
            raise e

    def get_status(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/check_alive/?aid=1988&room_ids={room_id}"

        try:
            response = requests.get(url, headers=self.headers)
            if response.status_code != 200:
                logutil.error(self.flag, f"Failed to load the page. Status code: {response.status_code}")
                return None
            # logutil.debug(self.flag, f"Response: {response.text}")

            json_data = response.json()
            # logutil.debug(self.flag, f"JSON data: {json.dumps(json_data, indent=2)}")

            status_code = json_data.get("status_code")
            # logutil.debug(self.flag, f"Status code: {status_code}")
            if status_code != 0:
                logutil.error(self.flag, "Invalid status code")
                return None

            data = json_data.get("data")[0]
            # logutil.debug(self.flag, f"Data: {json.dumps(data, indent=2)}")
            if not data:
                logutil.error(self.flag, "Cannot find data.")
                return None

            alive = data.get("alive")
            # logutil.debug(self.flag, f"Alive: {alive}")
            if alive is None:
                logutil.error(self.flag, "Cannot find alive status.")
                return None

            return alive
        except Exception as e:
            logutil.error(self.flag, f"Exception occurred: {e}")
            raise e

    def get_title(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

        try:
            response = requests.get(url, headers=self.headers)
            # logutil.debug(self.flag, f"Response: {response.text}")
            if response.status_code != 200:
                logutil.error(f"Failed to load the page. Status code: {response.status_code}")
                return ""

            json_data = response.json()
            # logutil.debug(self.flag, f"JSON data: {json.dumps(json_data, indent=2)}")

            data = json_data.get("data")
            # logutil.debug(self.flag, f"Data: {json.dumps(data, indent=2)}")
            if not data:
                logutil.error(self.flag, "Cannot find data.")
                return ""

            title = data.get("title")
            logutil.debug(self.flag, f"Title: {title}")
            if not title:
                logutil.error(self.flag, "Cannot find title.")
                return ""

            return title
        except Exception as e:
            logutil.error(self.flag, f"Exception occurred: {e}")
            raise e

    def test_get_live_url(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

        try:
            response = requests.get(url, headers=self.headers)
            # logutil.debug(self.flag, f"Response: {response.text}")
            if response.status_code != 200:
                logutil.error(self.flag, f"Failed to load the page. Status code: {response.status_code}")
                return None

            json_data = response.json()
            # logutil.debug(self.flag, f"JSON data: {json.dumps(json_data, indent=2)}")

            data = json_data.get("data")
            # logutil.debug(self.flag, f"Data: {json.dumps(data, indent=2)}")
            if not data:
                logutil.error(self.flag, "Cannot find data.")
                return None

            stream_url = data.get("stream_url")
            # logutil.debug(self.flag, f"Stream URL: {stream_url}")
            if not stream_url:
                logutil.error(self.flag, "Cannot find stream URL.")
                return None

            rtmp_pull_url = stream_url.get("rtmp_pull_url")
            logutil.debug(f"RTMP Pull URL: {rtmp_pull_url}")
            if not rtmp_pull_url:
                logutil.error(self.flag, "Cannot find RTMP Pull URL.")
                return None

            return rtmp_pull_url
        except Exception as e:
            logutil.error(self.flag, f"Exception occurred: {e}")
            raise e

    def get_filename(self, flag, title, file_format):
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

        try:
            for half, full in char_dict.items():
                title = title.replace(half, full)

            filename = f"[{live_time}]{flag}{title[:50]}.{file_format}"
            return filename
        except Exception as e:
            logutil.error(self.flag, f"Exception occurred: {e}")
            return ""

    def test_handle_recording_ffmpeg(self, live_url, out_file):
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
        except Exception as e:
            logutil.error(self.flag, f"Exception occurred: {e}")
            raise e


def lag_error(err_str) -> bool:
    """Check if ffmpeg output indicates that the stream is lagging"""
    lag_errors = ["Server returned 404 Not Found", "Stream ends prematurely", "Error in the pull function"]
    return any(err in err_str for err in lag_errors)


def retry_wait(seconds=60, print_msg=True):
    """Sleep for the specified number of seconds"""
    if print_msg:
        if seconds < 60:
            logutil.info(f"Waiting {seconds} seconds")
        else:
            logutil.info(f"Waiting {'%g' % (seconds / 60)} minute{'s' if seconds > 60 else ''}")
    time.sleep(seconds)


def check_exists(exp, value):
    """Check if a nested json key exists"""
    # For the case that we have an empty element
    if exp is None:
        return False
    # Check existence of the first key
    if value[0] in exp:
        # if this is the last key in the list, then no need to look further
        if len(value) == 1:
            return True
        else:
            next_value = value[1 : len(value)]
            return check_exists(exp[value[0]], next_value)
    else:
        return False


def get_proxy_session(proxy_url):
    """Request with TOR or other proxy.
    TOR uses 9050 as the default socks port.
    To (hopefully) prevent getting home IP blacklisted for bot activity.
    """
    try:
        logutil.info(f"Using proxy: {proxy_url}")
        session = requests.session()
        session.proxies = {"http": proxy_url, "https": proxy_url}
        # logutil.info("regular ip:")
        # logutil.info(req.get("http://httpbin.org/ip").text)
        # logutil.info("proxy ip:")
        # logutil.info(session.get("http://httpbin.org/ip").text)
        return session
    except Exception as ex:
        logutil.error(ex)
        return requests


def login_required(json) -> bool:
    # logutil.info(json)
    if check_exists(json, ["data", "prompts"]) and "This account is private" in json["data"]["prompts"]:
        logutil.info("Account is private")
        return True
    elif check_exists(json, ["status_code"]) and json["status_code"] == 4003110:
        raise AgeRestricted("Account is age restricted")
    else:
        return False


class LiveStatus(IntEnum):
    """Enumeration that defines potential states of the live stream"""

    BOT_INIT = 0
    LAGGING = 1
    LIVE = 2
    OFFLINE = 3


class WaitTime(IntEnum):
    """Enumeration that defines wait times in seconds."""

    LONG = 120
    SHORT = 10
    LAG = 5


class StatusCode(IntEnum):
    """Enumeration that defines HTTP status codes."""

    OK = 200
    REDIRECT = 302
    BAD_REQUEST = 400


class Mode(IntEnum):
    """Enumeration that represents the recording modes."""

    MANUAL = 0
    AUTOMATIC = 1


class ErrorMsg(Enum):
    """Enumeration of error messages"""

    def __str__(self):
        return str(self.value)

    BLKLSTD_AUTO_MODE_ERROR: str = (
        "Automatic mode can be used only in unblacklisted country. Use a VPN\n[*] "
        "Unrestricted country list: "
        "https://github.com/Michele0303/TikTok-Live-Recorder/edit/main/GUIDE.md#unrestricted"
        "-country"
    )
    BLKLSTD_ERROR = (
        "Captcha required or country blocked. Use a vpn or room_id."
        "\nTo get room id: https://github.com/Michele0303/TikTok-Live-Recorder/blob/main/GUIDE.md#how-to-get-room_id"
        "\nUnrestricted country list: https://github.com/Michele0303/TikTok-Live-Recorder/edit/main/GUIDE"
        ".md#unrestricted-country"
    )
    USERNAME_ERROR = "Error: Username/Room_id not found or the user has never been in live"
    CONNECTION_CLOSED = "Connection broken by the server."


class Info(Enum):
    """Enumeration that defines the version number and the banner message."""

    def __str__(self):
        return str(self.value)

    VERSION = 4.2
    BANNER = f"Tiktok Live Recorder v{VERSION}"


class ConnectionClosed(Exception):
    pass


class UserNotFound(Exception):
    pass


class LoginRequired(Exception):
    pass


class AgeRestricted(Exception):
    pass


class Blacklisted(Exception):
    pass


class Recording(Exception):
    pass


class BrowserExtractor(Exception):
    pass


class GenericReq(Exception):
    pass


class FFmpeg(Exception):
    pass


class StreamLagging(Exception):
    pass
