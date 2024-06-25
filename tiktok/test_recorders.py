import io
import json
import logging
import os
import re
import shutil
import sys
import time

import ffmpeg
import requests
from bs4 import BeautifulSoup

import bot_utils
import errors
from browser import BrowserExtractor
from enums import ErrorMsg, LiveStatus, Mode, StatusCode, WaitTime

DEFAULT_INTERVAL = 10
DEFAULT_HEADERS = {"User-Agent": "Chrome"}
DEFAULT_OUTPUT = "output"
DEFAULT_FORMAT = "ts"


class TikTok:
    def __init__(self, user: dict):
        self.platform = user["platform"]
        self.id = user["id"]

        self.name = user.get("name", self.id)
        self.interval = user.get("interval", DEFAULT_INTERVAL)
        self.headers = user.get("headers", DEFAULT_HEADERS)
        self.cookies = user.get("cookies")
        self.format = user.get("format", DEFAULT_FORMAT)
        self.proxy = user.get("proxy")
        self.output = user.get("output", DEFAULT_OUTPUT)

        self.flag = f"[{self.platform}][{self.name}]"

        self.room_id = self.get_room_id_from_user()
        self.name = self.get_user_from_room_id()
        self.proxy = None
        self.use_ffmpeg = True
        self.mode = Mode.AUTOMATIC
        self.duration = 0
        self.browser_exec = False
        self.combine = True
        self.delete_segments = True

        self.req = requests
        if self.proxy:
            self.req = bot_utils.get_proxy_session(self.proxy)

        self.status = LiveStatus.BOT_INIT
        self.out_file = None
        self.video_list = [str]

    def run(self):
        """Runs the program in the selected mode.

        If the mode is MANUAL, it checks if the user is currently live and if so, starts recording.
        If the mode is AUTOMATIC, it continuously checks if the user is live and if not, waits for the specified timeout before rechecking.
        If the user is live, it starts recording.
        """
        while True:
            try:
                if self.status == LiveStatus.LAGGING:
                    bot_utils.retry_wait(WaitTime.LAG, False)
                if self.room_id is None:
                    self.room_id = self.get_room_id_from_user()
                if self.name is None:
                    self.name = self.get_user_from_room_id()
                if self.status == LiveStatus.BOT_INIT:
                    logging.info(f"Username: {self.name}")
                    logging.info(f"Room ID: {self.room_id}")

                self.status = self.is_user_live()

                if self.status == LiveStatus.OFFLINE:
                    logging.info(f"{self.name} is offline")
                    self.room_id = None
                    if self.out_file:
                        self.finish_recording()
                    if self.mode == Mode.MANUAL:
                        exit(0)
                    else:
                        bot_utils.retry_wait(WaitTime.LONG, False)
                elif self.status == LiveStatus.LAGGING:
                    live_url = self.get_live_url()
                    self.start_recording(live_url)
                elif self.status == LiveStatus.LIVE:
                    logging.info(f"{self.name} is live")
                    live_url = self.get_live_url()
                    logging.info(f"Live URL: {live_url}")
                    self.start_recording(live_url)

            except (errors.GenericReq, ValueError, requests.HTTPError, errors.BrowserExtractor, errors.ConnectionClosed, errors.UserNotFound) as e:
                if self.mode == Mode.MANUAL:
                    raise e
                else:
                    logging.error(e)
                    self.room_id = None
                    bot_utils.retry_wait(WaitTime.SHORT)
            except errors.Blacklisted as e:
                if Mode == Mode.AUTOMATIC:
                    logging.error(ErrorMsg.BLKLSTD_AUTO_MODE_ERROR)
                else:
                    logging.error(ErrorMsg.BLKLSTD_ERROR)
                raise e
            except KeyboardInterrupt:
                logging.info("Stopped by keyboard interrupt\n")
                sys.exit(0)

    def start_recording(self, live_url):
        """Start recording live"""
        should_exit = False
        current_date = time.strftime("%Y.%m.%d_%H-%M-%S", time.localtime())
        suffix = "" if self.use_ffmpeg else "_flv"
        self.out_file = f"{self.output}{self.name}_{current_date}{suffix}.mp4"
        if self.status is not LiveStatus.LAGGING:
            logging.info(f"Output directory: {self.output}")
        try:
            if self.use_ffmpeg:
                self.handle_recording_ffmpeg(live_url)
                if self.duration is not None:
                    should_exit = True
            else:
                response = requests.get(live_url, stream=True)
                with open(self.out_file, "wb") as file:
                    start_time = time.time()
                    rec_started = False
                    for chunk in response.iter_content(chunk_size=4096):
                        file.write(chunk)
                        if not rec_started:
                            rec_started = True
                            self.status = LiveStatus.LIVE
                            logging.info(f"Started recording{f' for {self.duration} seconds' if self.duration else ''}")
                            print("Press CTRL + C to stop")
                        elapsed_time = time.time() - start_time
                        if self.duration is not None and elapsed_time >= self.duration:
                            should_exit = True
                            break
                if not should_exit:
                    raise errors.StreamLagging

        except errors.StreamLagging:
            logging.info("Stream lagging")
        except errors.FFmpeg as e:
            logging.error("FFmpeg error:")
            logging.error(e)
        except FileNotFoundError as e:
            logging.error("FFmpeg is not installed.")
            raise e
        except KeyboardInterrupt:
            logging.info("Recording stopped by keyboard interrupt")
            should_exit = True
        except Exception as e:
            logging.error(f"Recording error: {e}")

        self.status = LiveStatus.LAGGING

        try:
            if os.path.getsize(self.out_file) < 1000000:
                os.remove(self.out_file)
                # logging.info('removed file < 1MB')
            else:
                self.video_list.append(self.out_file)
        except FileNotFoundError:
            pass
        except Exception as e:
            logging.error(e)

        if should_exit:
            self.finish_recording()
            sys.exit(0)

    def handle_recording_ffmpeg(self, live_url):
        """Show real-time stats and raise ffmpeg errors"""
        stream = ffmpeg.input(
            live_url, **{"loglevel": "error"}, **{"reconnect": 1}, **{"reconnect_streamed": 1}, **{"reconnect_at_eof": 1}, **{"reconnect_delay_max": 5}, **{"timeout": 10000000}, stats=None
        )
        stats_shown = False
        if self.duration is not None:
            stream = ffmpeg.output(stream, self.out_file, c="copy", t=self.duration)
        else:
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
                            logging.info(f"Started recording{f' for {self.duration} seconds' if self.duration else ''}")
                            print("Press 'q' to re-start recording, CTRL + C to stop")
                            self.status = LiveStatus.LIVE
                        print(last_stats, end="\r")
                        stats_shown = True
                    else:
                        ffmpeg_err = ffmpeg_err + "".join(line)
            if ffmpeg_err:
                if bot_utils.lag_error(ffmpeg_err):
                    raise errors.StreamLagging
                else:
                    raise errors.FFmpeg(ffmpeg_err.strip())
        except KeyboardInterrupt as i:
            raise i
        except ValueError as e:
            logging.error(e)
        finally:
            if stats_shown:
                logging.info(last_stats)

    def finish_recording(self):
        """Combine multiple videos into one if needed"""
        try:
            current_date = time.strftime("%Y.%m.%d_%H-%M-%S", time.localtime())
            ffmpeg_concat_list = f"{self.name}_{current_date}_concat_list.txt"
            if self.combine and len(self.video_list) > 1:
                self.out_file = f"{self.output}{self.name}_{current_date}_concat.mp4"
                logging.info(f"Concatenating {len(self.video_list)} video files")
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
                    raise errors.FFmpeg(ffmpeg_err.strip())
                logging.info("Concat finished")
                if self.delete_segments:
                    for v in self.video_list:
                        os.remove(v)
                    logging.info(f"Deleted {len(self.video_list)} video files")
                else:
                    videos_dir = os.path.join(self.output, f"{self.name}_{current_date}_segments", "")
                    os.makedirs(videos_dir)
                    for v in self.video_list:
                        shutil.move(v, videos_dir)
                    logging.info(f"Moved recorded segments to directory: {videos_dir}")
            if os.path.isfile(self.out_file):
                logging.info(f"Recording finished: {self.out_file}\n")
            if os.path.isfile(ffmpeg_concat_list):
                os.remove(ffmpeg_concat_list)
        except errors.FFmpeg as e:
            logging.error("FFmpeg concat error:")
            logging.error(e)
        except Exception as ex:
            logging.error(ex)
        self.video_list = []
        self.out_file = None

    def is_user_live(self) -> LiveStatus:
        """Check whether the user is live"""
        try:
            url = f"https://www.tiktok.com/api/live/detail/?aid=1988&roomID={self.room_id}"
            json = self.req.get(url, headers=bot_utils.headers).json()
            # logging.info(f'is_user_live response {json}')
            if not bot_utils.check_exists(json, ["LiveRoomInfo", "status"]):
                raise ValueError(f"LiveRoomInfo.status not found in json: {json}")
            live_status_code = json["LiveRoomInfo"]["status"]
            if live_status_code != 4:
                return LiveStatus.LAGGING if self.status == LiveStatus.LAGGING else LiveStatus.LIVE
            else:
                return LiveStatus.OFFLINE

        except ConnectionAbortedError:
            raise errors.ConnectionClosed(ErrorMsg.CONNECTION_CLOSED)
        except ValueError as e:
            raise e
        except Exception as ex:
            raise errors.GenericReq(ex)

    def get_live_url(self) -> str:
        """Get the cdn (flv or m3u8) of the stream"""
        try:
            if self.status is not LiveStatus.LAGGING:
                logging.info(f"Getting live url for room ID {self.room_id}")
            url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={self.room_id}"
            json = self.req.get(url, headers=bot_utils.headers).json()
            if bot_utils.login_required(json):
                if not self.browser_exec:
                    raise errors.LoginRequired("Login required")
                else:
                    logging.info("Login required")
                    browser_extractor = BrowserExtractor()
                    return browser_extractor.get_live_url(self.room_id, self.browser_exec)
            if not bot_utils.check_exists(json, ["data", "stream_url", "rtmp_pull_url"]):
                raise ValueError(f"rtmp_pull_url not in response: {json}")
            return json["data"]["stream_url"]["rtmp_pull_url"]
        except ValueError as e:
            raise e
        except errors.LoginRequired as e:
            raise e
        except errors.AgeRestricted as e:
            raise e
        except errors.BrowserExtractor as e:
            raise e
        except Exception as ex:
            raise errors.GenericReq(ex)

    def get_room_id_from_user(self) -> str:
        """Given a username, get the room_id"""
        try:
            response = self.req.get(f"https://www.tiktok.com/@{self.id}/live", allow_redirects=False, headers=bot_utils.headers)
            # logging.info(f'get_room_id_from_user response: {response.text}')
            if response.status_code == StatusCode.REDIRECT:
                raise errors.Blacklisted("Redirect")
            match = re.search(r"room_id=(\d+)", response.text)
            if not match:
                raise ValueError("room_id not found")
            return match.group(1)

        except (requests.HTTPError, errors.Blacklisted) as e:
            raise errors.Blacklisted(e)
        except AttributeError as e:
            raise errors.UserNotFound(f"{ErrorMsg.USERNAME_ERROR}\n{e}")
        except ValueError as e:
            raise e
        except Exception as ex:
            raise errors.GenericReq(ex)

    def get_user_from_room_id(self) -> str:
        """Given a room_id, get the username"""
        try:
            url = f"https://www.tiktok.com/api/live/detail/?aid=1988&roomID={self.room_id}"
            json = requests.get(url, headers=bot_utils.headers).json()
            if not bot_utils.check_exists(json, ["LiveRoomInfo", "ownerInfo", "uniqueId"]):
                logging.error(f"LiveRoomInfo.uniqueId not found in json: {json}")
                raise errors.UserNotFound(ErrorMsg.USERNAME_ERROR)
            return json["LiveRoomInfo"]["ownerInfo"]["uniqueId"]

        except ConnectionAbortedError:
            raise errors.ConnectionClosed(ErrorMsg.CONNECTION_CLOSED)
        except errors.UserNotFound as e:
            raise e
        except Exception as ex:
            raise errors.GenericReq(ex)

    ##################################################################################################

    def get_ids(self, user_id):
        url = f"https://www.tiktok.com/@{user_id}"

        response = requests.get(url, headers=self.headers)
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

    def get_status(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/check_alive/?aid=1988&room_ids={room_id}"

        response = requests.get(url, headers=self.headers)
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

    def get_title(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

        response = requests.get(url, headers=self.headers)
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

    def test_get_live_url(self, room_id):
        url = f"https://webcast.tiktok.com/webcast/room/info/?aid=1988&room_id={room_id}"

        response = requests.get(url, headers=self.headers)
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

    def get_filename(self, channel_name, flag, title, file_format):
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
