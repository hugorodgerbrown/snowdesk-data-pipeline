"""Tests for bin/record-3d-flyby — the 3D fly-past promo recorder (SNOW-1466).

The browser half needs a running dev server and a GPU, so it is exercised by
hand (docs/map-3d-flyby.md). What is pinned here is everything that decides
the video without one: the defaults a bare run records with, how a route
file becomes GeoJSON (GPX through the app's own parser), the ffmpeg
commands the frames are encoded with (built, never run) and the exit on a
missing ffmpeg.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RECORDER = REPO_ROOT / "bin" / "record-3d-flyby"


def _load() -> ModuleType:
    """Import the extensionless recorder as a module."""
    spec = importlib.util.spec_from_loader(
        "record_3d_flyby",
        importlib.machinery.SourceFileLoader("record_3d_flyby", str(RECORDER)),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["record_3d_flyby"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


rec = _load()


def test_a_bare_run_records_the_breithorn_at_1080p_30fps_for_20s() -> None:
    """No arguments: the bundled route, 1920×1080, 30 fps, 20 s, build/flyby/."""
    settings = rec.parse_args([])
    assert settings.route == REPO_ROOT / "bin" / "flyby" / "breithorn.geojson"
    assert (settings.width, settings.height) == (1920, 1080)
    assert settings.fps == 30
    assert settings.seconds == 20
    assert settings.frame_count == 600
    assert settings.out == REPO_ROOT / "build" / "flyby"
    assert settings.url == "http://127.0.0.1:8000"
    assert not settings.resume
    assert not settings.software_gl


def test_the_flags_override_the_defaults() -> None:
    """Every flag lands in the settings, and the URL loses a trailing slash."""
    settings = rec.parse_args(
        [
            "--size", "960x540", "--fps", "24", "--seconds", "5",
            "--url", "http://localhost:9000/", "--resume", "--software-gl",
        ]
    )  # fmt: skip
    assert (settings.width, settings.height) == (960, 540)
    assert settings.frame_count == 120
    assert settings.url == "http://localhost:9000"
    assert settings.resume
    assert settings.software_gl
    assert "--use-angle=swiftshader" in rec.chromium_args(settings)
    assert "--use-angle=swiftshader" not in rec.chromium_args(rec.parse_args([]))
    # SwiftShader gets ten minutes a frame; a GPU keeps the page's own cap.
    assert settings.frame_cap_ms == 600_000
    assert rec.parse_args([]).frame_cap_ms is None


@pytest.mark.parametrize("size", ["1920", "1920x", "axb", "1921x1080", "0x0"])
def test_a_bad_size_is_refused(size: str) -> None:
    """--size wants two even numbers: H.264 at yuv420p needs them."""
    with pytest.raises(SystemExit):
        rec.parse_args(["--size", size])


def test_a_clip_no_longer_than_the_end_card_is_refused() -> None:
    """The end card fades in over the last 2.5 s, so the clip must be longer."""
    with pytest.raises(SystemExit):
        rec.parse_args(["--seconds", "2.5"])


def test_the_bundled_route_is_swisstopos_breithorn_line() -> None:
    """The route file is the swisstopo tour, credited, with its reveal bearing."""
    route = rec.load_route(rec.DEFAULT_ROUTE)
    assert route["type"] == "Feature"
    assert route["geometry"]["type"] == "MultiLineString"
    assert len(route["geometry"]["coordinates"][0]) > 10
    assert route["properties"]["attribution"] == "© swisstopo"
    assert route["properties"]["flight"] == {"reveal": {"bearingDeg": 15}}


def test_a_gpx_goes_through_the_apps_parser() -> None:
    """A .gpx becomes a LineString Feature through parse_gpx, as an upload is."""
    gpx = REPO_ROOT / "apps" / "routes" / "fixtures" / "canonical" / "hidden-valley.gpx"
    route = rec.load_route(gpx)
    assert route["type"] == "Feature"
    assert route["properties"]["name"] == "Hidden Valley"
    coords = route["geometry"]["coordinates"]
    assert route["geometry"]["type"] == "LineString"
    assert len(coords) > 100
    assert all(len(point) == 2 for point in coords)


def test_an_unreadable_route_is_a_record_error(tmp_path: Path) -> None:
    """A missing file, bad JSON or JSON that is not GeoJSON each fail cleanly."""
    with pytest.raises(rec.RecordError, match="no route"):
        rec.load_route(tmp_path / "nope.geojson")
    bad = tmp_path / "bad.geojson"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(rec.RecordError, match="not readable GeoJSON"):
        rec.load_route(bad)
    odd = tmp_path / "odd.geojson"
    odd.write_text(json.dumps([1, 2]), encoding="utf-8")
    with pytest.raises(rec.RecordError, match="not GeoJSON"):
        rec.load_route(odd)


def test_the_mp4_overlays_the_credit_and_fades_in_the_end_card(tmp_path: Path) -> None:
    """H.264 at CRF 20 with +faststart; the credit on every frame; a 2.5 s fade."""
    settings = rec.parse_args(["--out", str(tmp_path)])
    credit, card, out = (
        tmp_path / "credit.png",
        tmp_path / "card.png",
        tmp_path / "f.mp4",
    )
    argv = rec.mp4_command("ffmpeg", settings, credit, card, out)
    assert argv[0] == "ffmpeg"
    assert argv[-1] == str(out)
    assert str(settings.frames_dir / "f%05d.png") in argv
    # The credit and the card are both inputs, the credit over the card.
    assert argv.index(str(credit)) < argv.index(str(card))
    graph = argv[argv.index("-filter_complex") + 1]
    assert "fade=t=in:st=17.500:d=2.5:alpha=1" in graph
    assert graph.index("[card]") < graph.index("[1:v]overlay")
    for pair in (["-c:v", "libx264"], ["-crf", "20"], ["-pix_fmt", "yuv420p"]):
        i = argv.index(pair[0])
        assert argv[i : i + 2] == pair
    assert argv[argv.index("-movflags") + 1] == "+faststart"
    assert argv.count("-framerate") == 3
    assert "-an" in argv


def test_the_webm_is_vp9_from_the_mp4(tmp_path: Path) -> None:
    """VP9 at CRF 32, constant quality, from the MP4 already made."""
    mp4, webm = tmp_path / "f.mp4", tmp_path / "f.webm"
    argv = rec.webm_command("ffmpeg", mp4, webm)
    assert argv[argv.index("-i") + 1] == str(mp4)
    assert argv[argv.index("-c:v") + 1] == "libvpx-vp9"
    assert argv[argv.index("-crf") + 1] == "32"
    assert argv[argv.index("-b:v") + 1] == "0"
    assert argv[-1] == str(webm)


def test_the_poster_is_the_frame_before_the_end_card(tmp_path: Path) -> None:
    """One JPG frame, a frame before the card starts to fade in."""
    settings = rec.parse_args(["--out", str(tmp_path)])
    argv = rec.poster_command(
        "ffmpeg", settings, tmp_path / "f.mp4", tmp_path / "p.jpg"
    )
    assert argv[argv.index("-ss") + 1] == f"{20 - 2.5 - 1 / 30:.3f}"
    assert argv[argv.index("-frames:v") + 1] == "1"
    assert argv[-1] == str(tmp_path / "p.jpg")


def test_the_credit_is_swisstopos() -> None:
    """The credit burned into every frame names swisstopo (Hugo, 7 Nov 2026)."""
    assert rec.CREDIT == "Map © swisstopo"
    assert "{credit}" in rec.CREDIT_HTML


def test_a_missing_ffmpeg_exits_non_zero_with_its_message(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No ffmpeg on PATH: exit 1, saying how to install it, before any browser."""
    monkeypatch.setattr(rec.shutil, "which", lambda _name: None)
    called = []
    monkeypatch.setattr(rec, "record_frames", lambda *a: called.append(a))
    assert rec.main([]) == 1
    assert not called
    assert "ffmpeg is not on PATH" in capsys.readouterr().err


def test_a_failed_frame_exits_non_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A RecordError from the browser half is one line on stderr and exit 1."""
    monkeypatch.setattr(rec.shutil, "which", lambda _name: "/usr/bin/ffmpeg")

    def fail(*_args: object) -> None:
        raise rec.RecordError("frame 3/600 failed: not settled (re-run with --resume)")

    monkeypatch.setattr(rec, "record_frames", fail)
    assert rec.main([]) == 1
    assert "frame 3/600 failed" in capsys.readouterr().err
