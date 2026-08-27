"""模拟播放会话模块.

把 Emby.play 的 296 行状态机收拢为单一深层模块 PlaybackSession: 会话参数、
初始上报、节流进度循环、最终 Pause+Stopped 与媒体源解析。接口窄 (run),
传输经由 client 协约注入, 便于用假实现测试整个会话 (接口即测试面)。

为什么拆出来: 播放会话过去是 Emby 类上的一个带闭包的巨型方法, 定位
「一次播放的完整生命周期」需要在大类里来回跳。此处是该概念的单一落点。
"""

import asyncio
import random
import string
from datetime import datetime
from typing import Union

from embykeeper.utils import show_exception, truncate_str

from embykeeper.emby.errors import (
    EmbyPlayError,
    EmbyRequestError,
    EmbyStatusError,
    EmbyStoppedReportError,
)


class PlaybackClient:
    """PlaybackSession 需要的最小协约 (port). Emby(emby/api.py) 实现之."""

    user_id: str
    token: str
    useragent: str
    env: object
    log: object

    async def _request(self, method, path, *args, **kwargs): ...

    def _resolve_stream_url(self, url: str) -> str: ...

    async def _stream_media(self, url: str, play_session_id: str): ...


def playback_info_body() -> dict:
    """POST /Items/{id}/PlaybackInfo 的请求体 (DeviceProfile)."""
    return {
        "DeviceProfile": {
            "MaxStaticBitrate": 200000000,
            "MaxStreamingBitrate": 200000000,
            "MusicStreamingTranscodingBitrate": 200000000,
            "DirectPlayProfiles": [{"Type": "Video"}, {"Type": "Audio"}],
            "TranscodingProfiles": [
                {
                    "Container": "ts",
                    "Type": "Video",
                    "AudioCodec": "aac,mp3,wav,ac3,eac3,flac,opus",
                    "VideoCodec": "hevc,h264,h265,mpeg4",
                    "Context": "Streaming",
                    "Protocol": "hls",
                    "MaxAudioChannels": "6",
                    "MinSegments": "1",
                    "BreakOnNonKeyFrames": True,
                    "ManifestSubtitles": "vtt",
                }
            ],
            "ContainerProfiles": [],
            "SubtitleProfiles": [
                {"Format": "vtt", "Method": "External"},
                {"Format": "ass", "Method": "External"},
                {"Format": "ssa", "Method": "External"},
                {"Format": "srt", "Method": "External"},
                {"Format": "sub", "Method": "External"},
                {"Format": "subrip", "Method": "External"},
                {"Format": "smi", "Method": "External"},
                {"Format": "ttml", "Method": "External"},
                {"Format": "webvtt", "Method": "External"},
                {"Format": "dvdsub", "Method": "External"},
                {"Format": "sup", "Method": "External"},
                {"Format": "dvdsub", "Method": "Embed"},
                {"Format": "vobsub", "Method": "Embed"},
                {"Format": "vtt", "Method": "Embed"},
                {"Format": "ass", "Method": "Embed"},
                {"Format": "ssa", "Method": "Embed"},
                {"Format": "srt", "Method": "Embed"},
                {"Format": "sub", "Method": "Embed"},
                {"Format": "pgssub", "Method": "Embed"},
                {"Format": "pgs", "Method": "Embed"},
                {"Format": "subrip", "Method": "Embed"},
                {"Format": "smi", "Method": "Embed"},
                {"Format": "ttml", "Method": "Embed"},
                {"Format": "webvtt", "Method": "Embed"},
                {"Format": "mov_text", "Method": "Embed"},
                {"Format": "dvb_teletext", "Method": "Embed"},
                {"Format": "dvb_subtitle", "Method": "Embed"},
                {"Format": "dvbsub", "Method": "Embed"},
                {"Format": "idx", "Method": "Embed"},
                {"Format": "sup", "Method": "Embed"},
                {"Format": "vtt", "Method": "Hls"},
                {"Format": "vtt", "Method": "Hls"},
            ],
        }
    }


class PlaybackSession:
    """一次模拟播放会话: 获取播放信息 → 启动流 → 上报进度 → 停止."""

    def __init__(self, client: PlaybackClient, item: Union[dict, int], time: float = 10):
        self.client = client
        self.item = item
        self.time = time

    def _resolve_item(self):
        item = self.item
        if isinstance(item, dict):
            try:
                iid = item["Id"]
                iname = item["Name"]
            except KeyError:
                raise EmbyPlayError("无法解析视频信息")
        else:
            iid = item
            iname = "(请求播放的视频)"
        return iid, iname

    async def _fetch_playback_info(self, iid: str) -> dict:
        """读取媒体源并解析会话所需字段, 空媒体源时回退为随机假 id."""
        client = self.client
        try:
            await client._request(
                method="GET",
                path=f"/Videos/{iid}/AdditionalParts",
                params=dict(
                    Fields="PrimaryImageAspectRatio,UserData,CanDelete",
                    IncludeItemTypes="Playlist,BoxSet",
                    Recursive=True,
                    SortBy="SortName",
                ),
            )
        except EmbyStatusError as e:
            if "异常 HTTP 代码 404" not in str(e):
                raise
            client.log.debug(f"附加分段信息不可用, 跳过: {e}")

        playback_info_params = {
            "UserId": client.user_id,
            "IsPlayback": "true",
            "X-Emby-Authorization": (
                f'Emby Client="{client.env.client}", Device="{client.env.device}", '
                f'DeviceId="{client.env.device_id}", Version="{client.env.client_version}"'
            ),
            "X-Emby-Client": client.env.client,
            "X-Emby-Device-Name": client.env.device,
            "X-Emby-Device-Id": client.env.device_id,
            "X-Emby-Client-Version": client.env.client_version,
            "X-Emby-Language": "zh-cn",
            "X-Emby-Token": client.token,
        }
        resp = await client._request(
            method="POST",
            path=f"/Items/{iid}/PlaybackInfo",
            params=playback_info_params,
            json=playback_info_body(),
        )
        playback_info = resp.json()

        media_sources = playback_info.get("MediaSources") or []
        if media_sources:
            media_source = media_sources[0]
            media_source_id = media_source["Id"]
            direct_stream_url = media_source.get("DirectStreamUrl")
            audio_stream_index = media_source.get("DefaultAudioStreamIndex")
            if audio_stream_index is None:
                audio_stream_index = media_source.get("AudioStreamIndex", 0)
            subtitle_stream_index = media_source.get("DefaultSubtitleStreamIndex")
            if subtitle_stream_index is None:
                subtitle_stream_index = media_source.get("SubtitleStreamIndex")
            if subtitle_stream_index is None:
                subtitle_stream_index = -1
        else:
            media_source_id = "".join(
                random.choice(string.ascii_lowercase + string.digits) for _ in range(32)
            )
            direct_stream_url = None
            audio_stream_index = 0
            subtitle_stream_index = -1
        play_session_id = playback_info.get("PlaySessionId", "")
        return {
            "params": playback_info_params,
            "media_source_id": media_source_id,
            "direct_stream_url": direct_stream_url,
            "audio_stream_index": audio_stream_index,
            "subtitle_stream_index": subtitle_stream_index,
            "play_session_id": play_session_id,
        }

    async def run(self) -> bool:
        client = self.client
        time = self.time

        iid, iname = self._resolve_item()
        info = await self._fetch_playback_info(iid)
        session_params = {
            "reqformat": "json",
            "UserId": client.user_id,
            "X-Emby-Authorization": info["params"]["X-Emby-Authorization"],
            "X-Emby-Client": client.env.client,
            "X-Emby-Device-Name": client.env.device,
            "X-Emby-Device-Id": client.env.device_id,
            "X-Emby-Client-Version": client.env.client_version,
            "X-Emby-Language": "zh-cn",
            "X-Emby-Token": client.token,
        }
        session_headers = {"Content-Type": "text/plain"}
        media_source_id = info["media_source_id"]
        audio_stream_index = info["audio_stream_index"]
        subtitle_stream_index = info["subtitle_stream_index"]
        play_session_id = info["play_session_id"]

        start_tick = 0
        if isinstance(self.item, dict):
            start_tick = self.item.get("UserData", {}).get("PlaybackPositionTicks") or 0
        playback_start_time_ticks = int(datetime.now().timestamp() // 10 * 10 * 10000000)

        def get_playing_data(tick, event_name=None, paused=False):
            data = {
                "SubtitleOffset": 0,
                "MaxStreamingBitrate": 140000000,
                "MediaSourceId": str(media_source_id),
                "SubtitleStreamIndex": subtitle_stream_index,
                "VolumeLevel": 100,
                "PlaybackRate": 1.25,
                "PlaybackStartTimeTicks": playback_start_time_ticks,
                "PositionTicks": tick,
                "PlaySessionId": play_session_id,
                "PlaylistLength": 1,
                "NowPlayingQueue": [],
                "IsMuted": False,
                "PlaylistIndex": 0,
                "ItemId": str(iid),
                "RepeatMode": "RepeatNone",
                "AudioStreamIndex": audio_stream_index,
                "PlayMethod": "DirectStream",
                "CanSeek": True,
                "IsPaused": paused,
                "Shuffle": False,
            }
            if event_name:
                data["EventName"] = event_name
            return data

        await asyncio.sleep(random.uniform(1, 3))

        stream_url = (
            client._resolve_stream_url(info["direct_stream_url"])
            if info["direct_stream_url"]
            else f"/Videos/{iid}/stream"
        )
        stream_task = asyncio.create_task(client._stream_media(stream_url, play_session_id))
        rt = random.uniform(5, 10)
        client.log.info(f'开始模拟加载视频 "{truncate_str(iname, 10)}" ({rt:.0f} 秒).')
        await asyncio.sleep(rt)
        client.log.info(f'开始发送视频 "{truncate_str(iname, 10)}" 发送进度.')
        type(client).playing_count += 1
        try:
            await asyncio.sleep(random.uniform(1, 3))
            try:
                await client._request(
                    method="POST",
                    path="/Sessions/Playing/Progress",
                    params=session_params,
                    headers=session_headers,
                    json=get_playing_data(start_tick, event_name="TimeUpdate"),
                )
                await client._request(
                    method="POST",
                    path="/Sessions/Playing",
                    params=session_params,
                    headers=session_headers,
                    json=get_playing_data(start_tick),
                )
                await client._request(
                    method="POST",
                    path="/Sessions/Playing/Progress",
                    params=session_params,
                    headers=session_headers,
                    json=get_playing_data(start_tick, event_name="Pause", paused=True),
                )
                await client._request(
                    method="POST",
                    path="/Sessions/Playing/Progress",
                    params=session_params,
                    headers=session_headers,
                    json=get_playing_data(start_tick, event_name="Unpause"),
                )
            except EmbyRequestError as e:
                raise EmbyPlayError(f"无法开始播放: {e}")
            t = time

            last_tick = start_tick
            last_report_t = t
            progress_errors = 0
            report_interval = 5  # Start with 5 seconds
            report_count = 0
            max_interval = 300  # 5 minutes in seconds
            while t > 0:
                if progress_errors > 12:
                    raise EmbyPlayError("播放状态设定错误次数过多")
                if last_report_t and last_report_t - t > report_interval:
                    client.log.info(f'正在播放: "{truncate_str(iname, 10)}" (还剩 {t:.0f} 秒).')
                    last_report_t = t
                    report_count += 1
                    # After 3 reports at current interval, double the interval
                    if report_count >= 3:
                        report_count = 0
                        report_interval = min(report_interval * 2, max_interval)
                st = min(10, t)
                await asyncio.sleep(st)
                t -= st
                tick = start_tick + int((time - t) * 10000000)
                last_tick = tick
                payload = get_playing_data(tick, event_name="TimeUpdate")
                try:
                    resp = await asyncio.wait_for(
                        client._request(
                            method="POST",
                            path="/Sessions/Playing/Progress",
                            params=session_params,
                            headers=session_headers,
                            json=payload,
                        ),
                        30,
                    )
                except Exception as e:
                    detail = str(e).strip()
                    if detail:
                        client.log.debug(f"播放状态设定错误: {type(e).__name__}: {detail}")
                    else:
                        client.log.debug(f"播放状态设定错误: {type(e).__name__}")
                    progress_errors += 1
            await asyncio.sleep(random.uniform(1, 3))
        finally:
            type(client).playing_count -= 1
            stream_task.cancel()
            try:
                await stream_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                client.log.warning(f"模拟播放时, 访问流媒体文件失败.")
                show_exception(e)

        final_percentage = random.uniform(0.95, 1.0)
        final_tick = max(last_tick, start_tick + int(time * final_percentage * 10000000))
        try:
            await client._request(
                method="POST",
                path="/Sessions/Playing/Progress",
                params=session_params,
                headers=session_headers,
                json=get_playing_data(final_tick, event_name="Pause", paused=True),
            )
        except Exception as e:
            raise EmbyPlayError(f"由于连接错误或服务器错误无法停止播放: {e}")
        try:
            await client._request(
                method="POST",
                path="/Sessions/Playing/Stopped",
                params=session_params,
                headers=session_headers,
                json=get_playing_data(final_tick, paused=True),
            )
        except Exception as e:
            raise EmbyStoppedReportError(f"由于连接错误或服务器错误无法停止播放: {e}")
        client.log.info(f"播放完成, 共 {time:.0f} 秒.")
        return True
