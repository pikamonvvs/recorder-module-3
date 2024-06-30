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
from loguru import logger

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
                    logger.info(f"Username: {self.name}")
                    logger.info(f"Room ID: {self.room_id}")

                self.status = self.is_user_live()

                if self.status == LiveStatus.OFFLINE:
                    logger.info(f"{self.name} is offline")
                    self.room_id = None
                    if self.out_file:
                        self.finish_recording()
                    else:
                        retry_wait(self.interval, False)
                elif self.status == LiveStatus.LAGGING:
                    live_url = self.get_live_url()
                    self.start_recording(live_url)
                elif self.status == LiveStatus.LIVE:
                    logger.info(f"{self.name} is live")
                    live_url = self.get_live_url()
                    logger.info(f"Live URL: {live_url}")
                    self.start_recording(live_url)

            except (GenericReq, ValueError, requests.HTTPError, BrowserExtractor, ConnectionClosed, UserNotFound) as e:
                logger.error(e)
                self.room_id = None
                retry_wait(self.interval)
            except Blacklisted as e:
                logger.error(ErrorMsg.BLKLSTD_AUTO_MODE_ERROR)
                raise e
            except KeyboardInterrupt:
                logger.info("Stopped by keyboard interrupt\n")
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
            logger.info(f"Output directory: {self.output}")
        try:
            self.handle_recording_ffmpeg(live_url)

        except StreamLagging:
            logger.info("Stream lagging")
        except FFmpeg as e:
            logger.error("FFmpeg error:")
            logger.error(e)
        except FileNotFoundError as e:
            logger.error("FFmpeg is not installed.")
            raise e
        except KeyboardInterrupt:
            logger.info("Recording stopped by keyboard interrupt")
            should_exit = True
        except Exception as e:
            logger.error(f"Recording error: {e}")

        self.status = LiveStatus.LAGGING

        try:
            if os.path.getsize(self.out_file) < 1048576:
                os.remove(self.out_file)
                # logger.info('removed file < 1MB')
            else:
                self.video_list.append(self.out_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.error(e)

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
                            logger.info("Started recording")
                            print("Press 'q' to re-start recording, CTRL + C to stop")
                            self.status = LiveStatus.LIVE
                        print(last_stats, end="\r")
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
            logger.error(e)
        finally:
            if stats_shown:
                logger.info(last_stats)

    def finish_recording(self):
        """Combine multiple videos into one if needed"""
        try:
            current_date = time.strftime("%Y.%m.%d_%H-%M-%S", time.localtime())
            ffmpeg_concat_list = f"{self.name}_{current_date}_concat_list.txt"
            if len(self.video_list) > 1:
                title = self.get_title(self.room_id) + "_concat"
                output_file = self.get_filename(self.flag, title, self.format)
                self.out_file = os.path.join(self.output, output_file)
                logger.info(f"Concatenating {len(self.video_list)} video files")
                with open(ffmpeg_concat_list, "w") as file:
                    for v in self.video_list:
                        file.write(f"file '{v}'\n")
                stream = ffmpeg.input(ffmpeg_concat_list, **{"f": "concat"}, **{"safe": 0}, **{"loglevel": "error"})
                stream = ffmpeg.output(stream, self.out_file, c="copy")
                proc = ffmpeg.run_async(stream, pipe_stderr=True)
                text_stream = io.TextIOWrapper(proc.stderr, encoding="utf-8")
                ffmpeg_err = ""
                while True:
                    if proc.poll() is not None:
                        break
                    for line in text_stream:
                        ffmpeg_err = ffmpeg_err + "".join(line)
                if ffmpeg_err:
                    raise FFmpeg(ffmpeg_err.strip())
                logger.info("Concat finished")
                for v in self.video_list:
                    os.remove(v)
                logger.info(f"Deleted {len(self.video_list)} video files")
            if os.path.isfile(self.out_file):
                logger.info(f"Recording finished: {self.out_file}\n")
            if os.path.exists(ffmpeg_concat_list):
                os.remove(ffmpeg_concat_list)
        except FFmpeg as e:
            logger.error("FFmpeg concat error:")
            logger.error(e)
        except Exception as ex:
            logger.error(ex)
        self.video_list = []
        self.out_file = None

    def is_user_live(self):
        try:
            url = f"https://www.tiktok.com/api/live/detail/?aid=1988&roomID={self.room_id}"
            json = self.req.get(url, headers=self.headers).json()
            # logger.info(f'is_user_live response {json}')
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
                logger.info(f"Getting live url for room ID {self.room_id}")
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
            # logger.info(f'get_room_id_from_user response: {response.text}')
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
                logger.error(f"LiveRoomInfo.uniqueId not found in json: {json}")
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

        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            logger.error(f"Failed to load the page. Status code: {response.status_code}")
            return None

        soup = BeautifulSoup(response.text, "html.parser")
        # logger.debug(soup.prettify())

        script_tag = soup.find("script", id="__UNIVERSAL_DATA_FOR_REHYDRATION__")
        # logger.debug(f"Script tag: {script_tag}")

        if not script_tag:
            logger.error("Cannot find script tag for this ID.")
            return None

        json_data = json.loads(script_tag.string)
        # logger.debug(f"JSON data: {json.dumps(json_data, indent=2)}")
        if not json_data:
            logger.error("Failed to load JSON data.")
            return None

        default_scope = json_data.get("__DEFAULT_SCOPE__")
        # logger.debug(f"Default scope: {json.dumps(default_scope, indent=2)}")
        if not default_scope:
            logger.error("Cannot find default scope.")
            return None
        # with open("default_scope.json", "w") as f:
        #     json.dump(default_scope, f, indent=2)

        user_detail = default_scope.get("webapp.user-detail")
        # logger.debug(f"User detail: {json.dumps(user_detail, indent=2)}")
        if not user_detail:
            logger.error("Cannot find user detail.")
            return None

        user_info = user_detail.get("userInfo")
        # logger.debug(f"User info: {json.dumps(user_info, indent=2)}")
        if not user_info:
            logger.error("Cannot find user info.")
            return None

        user = user_info.get("user")
        # logger.debug(f"User: {json.dumps(user, indent=2)}")
        if not user:
            logger.error("Cannot find user.")
            return None

        room_id = user.get("roomId")
        # nickname = user.get("nickname")
        # unique_id = user.get("uniqueId")
        # logger.debug(f"Room ID: {room_id}")
        # logger.debug(f"Nickname: {nickname}")
        # logger.debug(f"Unique ID: {unique_id}")
        if not room_id:
            logger.error("Cannot find Room ID.")
            return None

        # if not nickname:
        #     logger.error("Cannot find nickname.")
        #     return None

        # if not unique_id:
        #     logger.error("Cannot find unique ID.")
        #     return None

        return room_id

    def get_status(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/check_alive/?aid=1988&room_ids={room_id}"

        response = requests.get(url, headers=self.headers)
        if response.status_code != 200:
            logger.error(f"Failed to load the page. Status code: {response.status_code}")
            return None
        # logger.debug(f"Response: {response.text}")

        json_data = response.json()
        # logger.debug(f"JSON data: {json.dumps(json_data, indent=2)}")

        status_code = json_data.get("status_code")
        # logger.debug(f"Status code: {status_code}")
        if status_code != 0:
            logger.error("Invalid status code")
            return None

        data = json_data.get("data")[0]
        # logger.debug(f"Data: {json.dumps(data, indent=2)}")
        if not data:
            logger.error("Cannot find data.")
            return None

        alive = data.get("alive")
        # logger.debug(f"Alive: {alive}")
        if alive is None:
            logger.error("Cannot find alive status.")
            return None

        return alive

    def get_title(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

        response = requests.get(url, headers=self.headers)
        # logger.debug(f"Response: {response.text}")
        if response.status_code != 200:
            logger.error(f"Failed to load the page. Status code: {response.status_code}")
            return None

        json_data = response.json()
        # logger.debug(f"JSON data: {json.dumps(json_data, indent=2)}")

        data = json_data.get("data")
        # logger.debug(f"Data: {json.dumps(data, indent=2)}")
        if not data:
            logger.error("Cannot find data.")
            return None

        title = data.get("title")
        logger.debug(f"Title: {title}")
        if not title:
            logger.error("Cannot find title.")
            return None

        return title

    def test_get_live_url(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

        response = requests.get(url, headers=self.headers)
        # logger.debug(f"Response: {response.text}")
        if response.status_code != 200:
            logger.error(f"Failed to load the page. Status code: {response.status_code}")
            return None

        json_data = response.json()
        # logger.debug(f"JSON data: {json.dumps(json_data, indent=2)}")

        data = json_data.get("data")
        # logger.debug(f"Data: {json.dumps(data, indent=2)}")
        if not data:
            logger.error("Cannot find data.")
            return None

        stream_url = data.get("stream_url")
        # logger.debug(f"Stream URL: {stream_url}")
        if not stream_url:
            logger.error("Cannot find stream URL.")
            return None

        rtmp_pull_url = stream_url.get("rtmp_pull_url")
        logger.debug(f"RTMP Pull URL: {rtmp_pull_url}")
        if not rtmp_pull_url:
            logger.error("Cannot find RTMP Pull URL.")
            return None

        return rtmp_pull_url

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
        for half, full in char_dict.items():
            title = title.replace(half, full)

        filename = f"[{live_time}]{flag}{title[:50]}.{file_format}"
        return filename

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


def lag_error(err_str) -> bool:
    """Check if ffmpeg output indicates that the stream is lagging"""
    lag_errors = ["Server returned 404 Not Found", "Stream ends prematurely", "Error in the pull function"]
    return any(err in err_str for err in lag_errors)


def retry_wait(seconds=60, print_msg=True):
    """Sleep for the specified number of seconds"""
    if print_msg:
        if seconds < 60:
            logger.info(f"Waiting {seconds} seconds")
        else:
            logger.info(f"Waiting {'%g' % (seconds / 60)} minute{'s' if seconds > 60 else ''}")
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
        logger.info(f"Using proxy: {proxy_url}")
        session = requests.session()
        session.proxies = {"http": proxy_url, "https": proxy_url}
        # logger.info("regular ip:")
        # logger.info(req.get("http://httpbin.org/ip").text)
        # logger.info("proxy ip:")
        # logger.info(session.get("http://httpbin.org/ip").text)
        return session
    except Exception as ex:
        logger.error(ex)
        return requests


def login_required(json) -> bool:
    # logger.info(json)
    if check_exists(json, ["data", "prompts"]) and "This account is private" in json["data"]["prompts"]:
        logger.info("Account is private")
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
