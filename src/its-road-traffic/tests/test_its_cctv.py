"""Tests for CCTV: model, API client, grouping, service cache, web API, HLS proxy, MCP tool."""

import httpx
import pytest
from datetime import datetime
from starlette.testclient import TestClient
from urllib.parse import quote, unquote

from data_go_mcp.its_road_traffic import server, web
from data_go_mcp.its_road_traffic.api_client import ItsRoadTrafficAPIClient
from data_go_mcp.its_road_traffic.demo import DemoTrafficSource, render_cctv_svg
from data_go_mcp.its_road_traffic.models import (
    CctvCamera,
    CctvResponse,
    EventResponse,
    RoadType,
    TrafficResponse,
    cctv_media_type,
)
from data_go_mcp.its_road_traffic.roads import (
    CATALOG,
    UNCLASSIFIED_ROAD,
    cctv_road,
    describe_cctv,
    group_cctv,
    path_fraction,
    point_on_path,
)
from data_go_mcp.its_road_traffic.service import TrafficService


STREAM_HOST = "cctvsec.example.com"


def cam(name, lat=37.0, lon=127.0, url=None, road_type="ex", fmt="HLS"):
    """테스트용 CCTV."""
    camera = CctvCamera.model_validate(
        {
            "cctvname": name,
            "cctvurl": url or f"https://{STREAM_HOST}/live/{abs(hash(name))}/playlist.m3u8?key=abc",
            "coordx": lon,
            "coordy": lat,
            "cctvformat": fmt,
        }
    )
    camera.road_type = road_type
    return camera


class FakeCctvSource:
    """CCTV를 제공하는 가짜 데이터 소스."""

    def __init__(self, cameras=None):
        self.cameras = cameras or {
            "ex": [cam("[경부선] 양재", 37.46, 127.04), cam("[경부선] 천안", 36.8, 127.15)],
            "its": [cam("[국도1호선] 오산", 37.15, 127.07, road_type="its")],
        }
        self.calls = 0
        self.fail = False

    async def get_traffic_info(self, road_type=RoadType.EXPRESSWAY, **kwargs):
        return TrafficResponse(items=[])

    async def get_events(self, **kwargs):
        return EventResponse(items=[])

    async def get_cctv(self, road_type=RoadType.EXPRESSWAY, **kwargs):
        self.calls += 1
        if self.fail:
            raise RuntimeError("cctv down")
        items = self.cameras[RoadType(road_type).value]
        return CctvResponse(items=items, total_count=len(items))

    async def close(self):
        pass


class Clock:
    """조작 가능한 시계."""

    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


# ------------------------------------------------------------------ 모델


class TestCctvModel:
    """CCTV 모델 테스트."""

    def test_lowercase_keys(self):
        camera = CctvCamera.model_validate(
            {
                "roadsectionid": "",
                "coordx": "127.1",
                "coordy": "37.5",
                "cctvresolution": "",
                "filecreatetime": "",
                "cctvtype": 4,
                "cctvformat": "HLS",
                "cctvname": "[경부선] 양재",
                "cctvurl": "https://x/y.m3u8",
            }
        )
        assert camera.name == "[경부선] 양재"
        assert camera.coord_x == 127.1
        assert camera.cctv_type == "4"
        assert camera.resolution is None
        assert camera.media == "hls"

    def test_camelcase_keys(self):
        camera = CctvCamera.model_validate(
            {"cctvName": "A", "cctvUrl": "http://x/a.mp4", "coordX": 1, "coordY": 2}
        )
        assert (camera.name, camera.url, camera.coord_x, camera.coord_y) == ("A", "http://x/a.mp4", 1, 2)
        assert camera.media == "video"

    def test_media_type(self):
        assert cctv_media_type("http://x/live.m3u8?t=1", None) == "hls"
        assert cctv_media_type("http://x/a", "HLS") == "hls"
        assert cctv_media_type("http://x/a.jpg", None) == "image"
        assert cctv_media_type("http://x/a", "JPEG") == "image"
        assert cctv_media_type("api/demo/cctv/a.svg", "IMAGE") == "image"
        assert cctv_media_type("http://x/a.mp4", None) == "video"
        assert cctv_media_type("http://x/stream", None) == "hls"

    def test_id_ignores_url_token(self):
        a = cam("[경부선] 양재", url="https://h/a.m3u8?key=1")
        b = cam("[경부선] 양재", url="https://h/a.m3u8?key=2")
        c = cam("[경부선] 양재", road_type="its")
        assert a.cctv_id == b.cctv_id
        assert a.cctv_id != c.cctv_id
        assert a.to_dict()["id"] == a.cctv_id


# ------------------------------------------------------------------ API 클라이언트


CCTV_PAYLOAD = {
    "response": {
        "coordtype": 1,
        "datacount": 2,
        "data": [
            {"cctvname": "[경부선] 양재", "cctvurl": "https://h/1.m3u8", "coordx": 127.04, "coordy": 37.46, "cctvformat": "HLS"},
            {"cctvname": "URL 없음", "cctvurl": "", "coordx": 127.0, "coordy": 37.0},
        ],
    }
}


class TestCctvClient:
    """CCTV API 클라이언트 테스트."""

    async def test_params_and_parsing(self, monkeypatch):
        monkeypatch.delenv("ITS_CCTV_TYPE", raising=False)
        seen = {}

        def handler(request):
            seen["path"] = request.url.path
            seen.update(request.url.params)
            return httpx.Response(200, json=CCTV_PAYLOAD)

        client = ItsRoadTrafficAPIClient(api_key="k", transport=httpx.MockTransport(handler))
        async with client:
            result = await client.get_cctv(RoadType.NATIONAL)
        assert seen["path"] == "/cctvInfo"
        assert seen["type"] == "its"
        assert seen["cctvType"] == "4"
        assert seen["getType"] == "json"
        assert result.total_count == 2
        assert [c.name for c in result.items] == ["[경부선] 양재"]  # URL 없는 CCTV 제외
        assert result.items[0].road_type == "its"

    async def test_cctv_type_from_env_and_arg(self, monkeypatch):
        seen = []

        def handler(request):
            seen.append(request.url.params["cctvType"])
            return httpx.Response(200, json=CCTV_PAYLOAD)

        monkeypatch.setenv("ITS_CCTV_TYPE", "1")
        client = ItsRoadTrafficAPIClient(api_key="k", transport=httpx.MockTransport(handler))
        async with client:
            await client.get_cctv()
            await client.get_cctv(cctv_type="3")
            with pytest.raises(ValueError):
                await client.get_cctv(cctv_type="9")
            with pytest.raises(ValueError):
                await client.get_cctv(RoadType.ALL)
        assert seen == ["1", "3"]


# ------------------------------------------------------------------ 노선별 묶기


class TestCctvGrouping:
    """CCTV 노선 매칭·정렬 테스트."""

    def test_cctv_road(self):
        assert cctv_road("ex", "[경부선] 양재") == ("경부고속도로", "1", "양재")
        assert cctv_road("ex", "(영동선) 마성터널") == ("영동고속도로", "50", "마성터널")
        assert cctv_road("its", "[국도3호선] 광주") == ("국도3호선", "3", "광주")
        assert cctv_road("ex", "경부선 양재") == ("경부고속도로", "1", "양재")
        assert cctv_road("its", "세종대로 광화문") == (UNCLASSIFIED_ROAD, None, "세종대로 광화문")
        assert cctv_road("ex", "[새고속도로] A") == ("새고속도로", None, "A")
        assert cctv_road("ex", "[경부선]") == ("경부고속도로", "1", "[경부선]")

    def test_path_fraction(self):
        path = CATALOG[0].path
        assert path_fraction(path, *path[0]) == 0.0
        assert path_fraction(path, *path[-1]) == pytest.approx(1.0)
        middle = point_on_path(path, 0.5)
        assert path_fraction(path, *middle) == pytest.approx(0.5, abs=0.01)
        assert path_fraction(((37.0, 127.0),), 37, 127) == 0.0

    def test_describe_cctv_position(self):
        path = CATALOG[0].path  # 경부고속도로
        lat, lon = point_on_path(path, 0.25)
        data = describe_cctv(cam("[경부선] 어딘가", lat, lon))
        assert data["road_key"] == "ex:경부고속도로"
        assert data["location"] == "어딘가"
        assert data["position"] == pytest.approx(0.25, abs=0.01)
        assert describe_cctv(cam("무명", road_type="its"))["position"] is None

    def test_group_sorting(self):
        path = CATALOG[0].path
        cameras = [
            describe_cctv(cam("[경부선] 부산", *point_on_path(path, 0.9))),
            describe_cctv(cam("[경부선] 서울", *point_on_path(path, 0.1))),
            describe_cctv(cam("[국도1호선] 오산", road_type="its")),
            describe_cctv(cam("광화문", road_type="its")),
            describe_cctv(cam("[영동선] 여주")),
            describe_cctv(cam("[새고속도로] 신규")),
        ]
        groups = group_cctv(cameras)
        assert [g["name"] for g in groups] == [
            "경부고속도로",
            "영동고속도로",
            "새고속도로",
            "국도1호선",
            UNCLASSIFIED_ROAD,
        ]
        assert [c["location"] for c in groups[0]["items"]] == ["서울", "부산"]
        assert groups[0]["count"] == 2
        assert [g["name"] for g in group_cctv(cameras, road_type="its")] == ["국도1호선", UNCLASSIFIED_ROAD]


# ------------------------------------------------------------------ 서비스


class TestCctvService:
    """CCTV 캐시 테스트."""

    async def test_cache_ttl_and_all(self):
        source, clock = FakeCctvSource(), Clock()
        service = TrafficService(source, clock=clock, cctv_ttl=600)
        assert len(await service.get_cctv("all")) == 3
        await service.get_cctv("ex")
        assert source.calls == 2
        clock.now += 601
        await service.get_cctv("ex")
        assert source.calls == 3
        assert "ex" in service.status()["cctv_updated"]

    async def test_stale_on_error_and_raise_without_cache(self):
        source, clock = FakeCctvSource(), Clock()
        service = TrafficService(source, clock=clock, cctv_ttl=10)
        await service.get_cctv("ex")
        source.fail = True
        clock.now += 11
        assert len(await service.get_cctv("ex")) == 2
        assert "cctv down" in service.last_error
        with pytest.raises(RuntimeError):
            await service.get_cctv("its")

    async def test_allowed_hosts(self):
        service = TrafficService(FakeCctvSource())
        assert not service.is_allowed_stream(f"https://{STREAM_HOST}/x.m3u8")
        await service.get_cctv("ex")
        assert service.is_allowed_stream(f"https://{STREAM_HOST}/x.m3u8")
        assert service.is_allowed_stream(f"http://{STREAM_HOST.upper()}/seg.ts")
        assert not service.is_allowed_stream("https://evil.example.com/x.m3u8")
        assert not service.is_allowed_stream(f"file://{STREAM_HOST}/etc/passwd")
        assert not service.is_allowed_stream("")
        service.allow_stream_host("cdn.example.com")
        assert service.is_allowed_stream("https://cdn.example.com/a.ts")

    async def test_groups_query(self):
        service = TrafficService(FakeCctvSource())
        assert [g["name"] for g in await service.cctv_groups("all")] == ["경부고속도로", "국도1호선"]
        assert [c["location"] for g in await service.cctv_groups("ex", "천안") for c in g["items"]] == ["천안"]
        assert len((await service.cctv_groups("all", "경부"))[0]["items"]) == 2
        # 노선번호 1은 경부고속도로와 국도1호선 모두 해당한다
        assert [g["name"] for g in await service.cctv_groups("all", "1")] == ["경부고속도로", "국도1호선"]
        assert [g["name"] for g in await service.cctv_groups("ex", "1")] == ["경부고속도로"]

    async def test_road_detail_includes_cctv(self):
        source = FakeCctvSource()
        service = TrafficService(source)
        detail = await service.road("ex:경부고속도로")
        assert [c["location"] for c in detail["cctv"]] == ["양재", "천안"]
        source.fail = True
        service._cctv.clear()
        service._cctv_at.clear()
        assert (await service.road("ex:경부고속도로"))["cctv"] == []

    async def test_source_without_cctv(self):
        class NoCctvSource:
            async def close(self):
                pass

        service = TrafficService(NoCctvSource())
        assert not service.supports_cctv
        assert await service.get_cctv("ex") == []


class TestDemoCctv:
    """데모 CCTV 테스트."""

    async def test_cameras_and_render(self):
        demo = DemoTrafficSource(seed=1)
        ex = await demo.get_cctv(RoadType.EXPRESSWAY)
        its = await demo.get_cctv(RoadType.NATIONAL)
        assert ex.items and all(c.road_type == "ex" for c in ex.items)
        assert any(not c.name.startswith("[") for c in its.items)
        assert all(c.media == "image" for c in ex.items)
        slug = ex.items[0].url.rsplit("/", 1)[-1][: -len(".svg")]
        svg = demo.render_cctv(slug)
        assert svg.startswith("<svg") and "DEMO" in svg
        assert demo.render_cctv("nope") is None

    def test_render_changes_every_5s(self):
        a = render_cctv_svg("[경부선] <A&B>", datetime(2026, 9, 23, 12, 0, 1), seed="s")
        b = render_cctv_svg("[경부선] <A&B>", datetime(2026, 9, 23, 12, 0, 3), seed="s")
        c = render_cctv_svg("[경부선] <A&B>", datetime(2026, 9, 23, 12, 0, 6), seed="s")
        assert "&lt;A&amp;B&gt;" in a
        assert a.replace("12:00:01", "") == b.replace("12:00:03", "")
        assert a.replace("12:00:01", "") != c.replace("12:00:06", "")


# ------------------------------------------------------------------ 웹 API · 프록시


class NoPush:
    """비활성 알림기."""

    enabled = False
    public_key = None
    subscription_count = 0


PLAYLIST = "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-KEY:METHOD=AES-128,URI=\"key.bin\"\n#EXTINF:2.0,\nseg-1.ts?t=1\n#EXTINF:2.0,\nhttps://other.example.com/seg-2.ts\n"


def upstream(request: httpx.Request) -> httpx.Response:
    """가짜 CCTV 스트리밍 서버."""
    path = request.url.path
    if request.url.host == "redirect.example.com":
        return httpx.Response(302, headers={"location": f"https://{STREAM_HOST}/live/playlist.m3u8"})
    if path.endswith(".m3u8"):
        return httpx.Response(200, text=PLAYLIST, headers={"content-type": "application/vnd.apple.mpegurl"})
    if path.endswith(".ts"):
        return httpx.Response(200, content=b"\x47" * 188, headers={"content-type": "video/mp2t"})
    return httpx.Response(404)


@pytest.fixture
def cctv_client():
    service = TrafficService(FakeCctvSource())
    app = web.create_app(service, notifier=NoPush(), start_polling=False, stream_transport=httpx.MockTransport(upstream))
    with TestClient(app) as client:
        client.get("/api/cctv")  # 허용 호스트 등록
        yield client, service


class TestCctvWeb:
    """CCTV 웹 API 테스트."""

    def test_cctv_endpoint(self, cctv_client):
        client, _ = cctv_client
        data = client.get("/api/cctv").json()
        assert data["total"] == 3
        assert [g["name"] for g in data["groups"]] == ["경부고속도로", "국도1호선"]
        assert data["updated_at"]
        assert client.get("/api/cctv?type=its").json()["total"] == 1
        assert client.get("/api/cctv?q=양재").json()["total"] == 1
        assert client.get("/api/cctv?type=bad").status_code == 400

    def test_cctv_upstream_error(self):
        source = FakeCctvSource()
        source.fail = True
        app = web.create_app(TrafficService(source), notifier=NoPush(), start_polling=False)
        with TestClient(app) as client:
            assert client.get("/api/cctv").status_code == 502

    def test_status_has_map_and_cctv(self, cctv_client, monkeypatch):
        client, _ = cctv_client
        status = client.get("/api/status").json()
        assert status["map"]["style"].startswith("https://")
        assert status["cctv"] == {"enabled": True, "proxy": True}

    def test_custom_map_style(self, monkeypatch):
        monkeypatch.setenv("ITS_MAP_STYLE_URL", "https://maps.example.com/style.json")
        monkeypatch.delenv("ITS_MAP_STYLE_DARK_URL", raising=False)
        app = web.create_app(TrafficService(FakeCctvSource()), notifier=NoPush(), start_polling=False)
        with TestClient(app) as client:
            assert client.get("/api/status").json()["map"] == {
                "style": "https://maps.example.com/style.json",
                "style_dark": "https://maps.example.com/style.json",
            }

    def test_proxy_rewrites_playlist(self, cctv_client):
        client, service = cctv_client
        url = f"https://{STREAM_HOST}/live/a/playlist.m3u8?key=abc"
        res = client.get(f"/api/cctv/proxy?u={quote(url, safe='')}")
        assert res.status_code == 200
        assert "mpegurl" in res.headers["content-type"]
        lines = res.text.splitlines()
        segment = next(line for line in lines if line.startswith("proxy?u=") and "seg-1" in line)
        assert unquote(segment[len("proxy?u="):]) == f"https://{STREAM_HOST}/live/a/seg-1.ts?t=1"
        key_line = next(line for line in lines if line.startswith("#EXT-X-KEY"))
        assert f'URI="proxy?u={quote(f"https://{STREAM_HOST}/live/a/key.bin", safe="")}"' in key_line
        # 다른 호스트의 세그먼트는 목록에 없으므로 프록시가 거부한다
        other = next(line for line in lines if "other.example.com" in line)
        assert client.get(f"/api/cctv/{other}").status_code == 403

    def test_proxy_passes_segments(self, cctv_client):
        client, _ = cctv_client
        url = f"https://{STREAM_HOST}/live/a/seg-1.ts"
        res = client.get(f"/api/cctv/proxy?u={quote(url, safe='')}")
        assert res.status_code == 200
        assert res.headers["content-type"] == "video/mp2t"
        assert len(res.content) == 188

    def test_proxy_rejects_unknown_hosts(self, cctv_client):
        client, _ = cctv_client
        for url in ("https://evil.example.com/a.m3u8", "http://127.0.0.1:22/", "", "ftp://x/y"):
            assert client.get(f"/api/cctv/proxy?u={quote(url, safe='')}").status_code == 403

    def test_proxy_follows_redirect_and_allows_final_host(self, cctv_client):
        client, service = cctv_client
        service.allow_stream_host("redirect.example.com")
        res = client.get(f"/api/cctv/proxy?u={quote('https://redirect.example.com/x.m3u8', safe='')}")
        assert res.status_code == 200
        assert f"https%3A%2F%2F{STREAM_HOST}%2Flive%2Fseg-1.ts" in res.text

    def test_proxy_upstream_404(self, cctv_client):
        client, _ = cctv_client
        url = f"https://{STREAM_HOST}/missing"
        assert client.get(f"/api/cctv/proxy?u={quote(url, safe='')}").status_code == 502

    def test_proxy_disabled(self, monkeypatch):
        monkeypatch.setenv("ITS_CCTV_PROXY", "false")
        app = web.create_app(TrafficService(FakeCctvSource()), notifier=NoPush(), start_polling=False)
        with TestClient(app) as client:
            client.get("/api/cctv")
            assert client.get("/api/status").json()["cctv"]["proxy"] is False
            url = quote(f"https://{STREAM_HOST}/a.m3u8", safe="")
            assert client.get(f"/api/cctv/proxy?u={url}").status_code == 403

    def test_rewrite_playlist_relative_and_absolute(self):
        text = web.rewrite_playlist("#EXTM3U\n\nchunk.ts\n/abs/path.ts\n", "https://h.example.com/a/b/list.m3u8")
        lines = text.splitlines()
        assert unquote(lines[2][len("proxy?u="):]) == "https://h.example.com/a/b/chunk.ts"
        assert unquote(lines[3][len("proxy?u="):]) == "https://h.example.com/abs/path.ts"
        assert lines[1] == ""

    def test_demo_cctv_image(self):
        service = TrafficService(DemoTrafficSource(seed=2), demo=True)
        app = web.create_app(service, notifier=NoPush(), start_polling=False)
        with TestClient(app) as client:
            group = client.get("/api/cctv").json()["groups"][0]
            url = group["items"][0]["url"]
            res = client.get("/" + url)
            assert res.status_code == 200
            assert res.headers["content-type"].startswith("image/svg+xml")
            assert client.get("/api/demo/cctv/none.svg").status_code == 404
        service.demo = False
        app = web.create_app(service, notifier=NoPush(), start_polling=False)
        with TestClient(app) as client:
            assert client.get("/api/demo/cctv/ex-1-0.svg").status_code == 403


# ------------------------------------------------------------------ MCP 도구


class TestListCctvTool:
    """list_cctv MCP 도구 테스트."""

    @pytest.fixture(autouse=True)
    def fake_service(self, monkeypatch):
        monkeypatch.setattr(server, "_service", TrafficService(FakeCctvSource()))

    async def test_default_expressways(self):
        result = await server.list_cctv()
        assert result["total_count"] == 2
        road = result["roads"][0]
        assert road["name"] == "경부고속도로" and road["count"] == 2
        assert set(road["cameras"][0]) == {"name", "location", "url", "media", "lat", "lon"}

    async def test_filters_and_limit(self):
        result = await server.list_cctv("양재", limit=1)
        assert [c["location"] for c in result["roads"][0]["cameras"]] == ["양재"]
        assert (await server.list_cctv(road_type="국도"))["roads"][0]["name"] == "국도1호선"
        assert (await server.list_cctv(road_type="all"))["total_count"] == 3
        assert "error" in await server.list_cctv(road_type="bad")

    async def test_error(self, monkeypatch):
        source = FakeCctvSource()
        source.fail = True
        monkeypatch.setattr(server, "_service", TrafficService(source))
        assert (await server.list_cctv())["error"] == "cctv down"
