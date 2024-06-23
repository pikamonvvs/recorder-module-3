import re
from enum import Enum, IntEnum

import requests


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


class LiveStatus(IntEnum):
    """Enumeration that defines potential states of the live stream"""

    BOT_INIT = 0
    LAGGING = 1
    LIVE = 2
    OFFLINE = 3


class WaitTime(IntEnum):
    """Enumeration that defines wait times in seconds."""

    LONG = 120
    SHORT = 60
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


############################################################################################################

DEFAULT_INTERVAL = 10
DEFAULT_HEADERS = {"User-Agent": "Chrome"}
DEFAULT_OUTPUT = "output"
DEFAULT_FORMAT = "ts"

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Referer": "https://www.tiktok.com/",
}


class Tiktok:
    def __init__(self, user: dict):
        self.platform = user["platform"]
        self.id = user["id"]

        self.name = user.get("name", self.id)
        self.interval = user.get("interval", DEFAULT_INTERVAL)
        self.headers = user.get("headers", DEFAULT_HEADERS)
        self.cookies = user.get("cookies")
        self.format = user.get("format", DEFAULT_FORMAT)
        self.proxy = user.get("proxy")
        self.output = user.get("output", DEFAULT_OUTPUT)  # out_dir

        self.flag = f"[{self.platform}][{self.name}]"

        # self.mode = mode                          # automatic
        # self.browser_exec = browser_exec
        # self.combine = combine
        # self.delete_segments = delete_segments
        # self.use_ffmpeg = use_ffmpeg
        # self.duration = duration                  # interval

        # if proxy:
        #     self.req = bot_utils.get_proxy_session(proxy)
        # else:
        #     self.req = req
        # self.status = LiveStatus.BOT_INIT
        # self.out_file = None
        # self.video_list = []

        self.room_id = None

    def get_room_id_from_user(self):
        """Given a username, get the room_id"""
        try:
            print(f"self.id: {self.id}")
            response = requests.get(f"https://www.tiktok.com/@{self.id}/live", allow_redirects=False, headers=self.headers)
            # logging.info(f'get_room_id_from_user response: {response.text}')
            print(f"get_room_id_from_user response: {response.text}")
            print(f"get_room_id_from_user response.status_code: {response.status_code}")
            if response.status_code == 404:
                raise UserNotFound(ErrorMsg.USERNAME_ERROR)
            if response.status_code == 302:
                raise Blacklisted("Redirect")
            match = re.search(r"room_id=(\d+)", response.text)
            if not match:
                raise ValueError("room_id not found")
            self.room_id = match.group(1)
            print(f"{self.flag} Room ID: {self.room_id}")

        except (requests.HTTPError, Blacklisted) as e:
            raise Blacklisted(e)
        except AttributeError as e:
            raise UserNotFound(f"{ErrorMsg.USERNAME_ERROR}\n{e}")
        except ValueError as e:
            raise e
        except Exception as ex:
            raise GenericReq(ex)

    def get_user_from_room_id(self) -> str:
        """Given a room_id, get the username"""
        try:
            url = f"https://www.tiktok.com/api/live/detail/?aid=1988&roomID={self.room_id}"
            json = requests.get(url, headers=self.headers).json()

            live_room_info = json.get("LiveRoomInfo")
            if not live_room_info:
                print(f"LiveRoomInfo not found in json: {json}")
                raise UserNotFound(ErrorMsg.USERNAME_ERROR)

            owner_info = live_room_info.get("ownerInfo")
            if not owner_info:
                print(f"ownerInfo not found in json: {json}")
                raise UserNotFound(ErrorMsg.USERNAME_ERROR)

            unique_id = owner_info.get("uniqueId")
            if not unique_id:
                print(f"uniqueId not found in json: {json}")
                raise UserNotFound(ErrorMsg.USERNAME_ERROR)

            return json["LiveRoomInfo"]["ownerInfo"]["uniqueId"]

        except ConnectionAbortedError:
            raise ConnectionClosed(ErrorMsg.CONNECTION_CLOSED)
        except UserNotFound as e:
            raise e
        except Exception as ex:
            raise GenericReq(ex)


if __name__ == "__main__":
    user = {
        "platform": "Tiktok",
        "id": "zizizixizizizi",
        "name": "두더지",
        "interval": 10,
        "format": "flv",
        "output": "output",
    }
    tiktok = Tiktok(user)
    print(tiktok.get_room_id_from_user())
    # print(tiktok.get_user_from_room_id())
