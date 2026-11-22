# -*- coding: utf-8 -*-
"""comfy_mcp.editing（ffmpeg 剪辑模块）单元测试。

运行：
    & 'K:\\CONFI\\mian\\ComfyUI_windows_portable\\python_embeded\\python.exe' ^
        -m unittest discover -s K:\\CONFI\\comfy_mcp\\tests -v

测试使用 ``artifacts/sample.mp4``（320x180@24fps 2s，含 aac）与
``artifacts/sample2.mp4``（无声 2s）。所有产物写到独立 tempfile 目录，测试结束清理。
整套测试只做 320x180 的小片段，目标 < 60s。
"""

import os
import shutil
import sys
import tempfile
import unittest

# 让 `python -m unittest discover -s comfy_mcp/tests` 能 import 到 comfy_mcp 包
_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_TESTS_DIR)
_PARENT_DIR = os.path.dirname(_PROJECT_DIR)
if _PARENT_DIR not in sys.path:
    sys.path.insert(0, _PARENT_DIR)

from comfy_mcp import editing  # noqa: E402

ARTIFACTS = os.path.join(_PROJECT_DIR, "artifacts")
SAMPLE = os.path.join(ARTIFACTS, "sample.mp4")       # 320x180 24fps 2s + aac
SAMPLE2 = os.path.join(ARTIFACTS, "sample2.mp4")     # 320x180 24fps 2s 无声
_HAS_ASSETS = os.path.isfile(SAMPLE) and os.path.isfile(SAMPLE2)

# 时长断言容差（重编码 + aac 编码器 priming 会带来几十毫秒误差）
TOL = 0.2


def _approx(actual, expected, tol=TOL):
    return actual is not None and abs(float(actual) - float(expected)) <= tol


@unittest.skipUnless(_HAS_ASSETS, "缺少 artifacts/sample.mp4 或 sample2.mp4 测试素材")
class EditingTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="comfy_edit_test_")
        cls.info = editing.toolchain()
        if not cls.info.get("ok"):
            shutil.rmtree(cls.tmp, ignore_errors=True)
            raise unittest.SkipTest("ffmpeg/ffprobe 不可用: %r" % (cls.info,))
        cls._audio_src = None

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(getattr(cls, "tmp", ""), ignore_errors=True)

    # ------------------------------------------------------------------ 工具
    def path(self, *names):
        return os.path.join(self.tmp, *names)

    def audio_source(self):
        """抽取一次 sample.mp4 的音轨，供 mux_audio 测试复用。"""
        if EditingTest._audio_src is None:
            dst = self.path("audio_src.m4a")
            editing.extract_audio(SAMPLE, dst)
            EditingTest._audio_src = dst
        return EditingTest._audio_src

    def assert_ok(self, result, dst=None, *, expect_audio=None):
        self.assertIsInstance(result, dict)
        self.assertTrue(result.get("ok"), result)
        self.assertIsInstance(result.get("cmd"), list)
        self.assertTrue(result["cmd"], "cmd 不应为空")
        self.assertIn("-hide_banner", result["cmd"])
        self.assertIn("-nostdin", result["cmd"])
        self.assertIn("-loglevel", result["cmd"])
        self.assertIn("-threads", result["cmd"])
        self.assertIsInstance(result.get("probe"), dict)
        target = dst or result.get("dst")
        self.assertTrue(os.path.isfile(target), "输出文件不存在: %s" % target)
        self.assertGreater(os.path.getsize(target), 0)
        if expect_audio is not None:
            self.assertEqual(bool(result["probe"].get("has_audio")), expect_audio)
        return result

    def assert_no_part_files(self, directory=None):
        root = directory or self.tmp
        stale = []
        for base, _dirs, files in os.walk(root):
            for name in files:
                if name.endswith(".part"):
                    stale.append(os.path.join(base, name))
        self.assertEqual(stale, [], "存在未清理的 .part 文件: %s" % stale)

    # ------------------------------------------------------------- 工具链/probe
    def test_01_toolchain(self):
        info = self.info
        self.assertTrue(info.get("ok"), info)
        for key in ("ffmpeg", "ffprobe"):
            value = info[key]
            self.assertTrue(value)
            self.assertTrue(os.path.isfile(value) or shutil.which(value), "%s 不可执行: %s" % (key, value))
        self.assertIsInstance(info.get("version"), str)
        self.assertTrue(info["version"], "应能读取 ffmpeg 版本")

    def test_02_probe_sample(self):
        info = editing.probe(SAMPLE)
        self.assertEqual(info["width"], 320)
        self.assertEqual(info["height"], 180)
        self.assertAlmostEqual(info["fps"], 24.0, places=1)
        self.assertTrue(_approx(info["duration_s"], 2.0))
        self.assertEqual(info["video_codec"], "h264")
        self.assertEqual(info["audio_codec"], "aac")
        self.assertTrue(info["has_audio"])
        self.assertGreater(info["bitrate"], 0)
        self.assertGreater(info["size_mb"], 0)
        self.assertEqual(info["rotation"], 0)

    def test_03_probe_silent(self):
        info = editing.probe(SAMPLE2)
        self.assertEqual(info["width"], 320)
        self.assertEqual(info["height"], 180)
        self.assertFalse(info["has_audio"])
        self.assertIsNone(info["audio_codec"])
        self.assertTrue(_approx(info["duration_s"], 2.0))
        self.assertEqual(info["rotation"], 0)

    def test_04_probe_missing_file(self):
        with self.assertRaises(editing.EditError):
            editing.probe(self.path("不存在.mp4"))

    def test_05_src_alias_and_signatures(self):
        # SPEC 函数签名必须精确：首参是 video 的几个函数不能被改名
        import inspect

        params = inspect.signature(editing.thumbnail).parameters
        self.assertIn("video", params)
        self.assertEqual(list(inspect.signature(editing.run_ffmpeg).parameters), ["args", "dst", "timeout_s"])
        # MCP edit 工具统一用 src 字段透传，6 个 video 首参函数也应接受 src=
        dst = self.path("alias_thumb.jpg")
        result = editing.thumbnail(src=SAMPLE, dst=dst, at_s=0.2, width=32)
        self.assert_ok(result, dst)
        self.assertEqual(result["probe"]["width"], 32)
        audio = self.path("alias_audio.m4a")
        result = editing.extract_audio(src=SAMPLE, dst=audio)
        self.assert_ok(result, audio, expect_audio=True)
        self.assert_no_part_files()

    # ------------------------------------------------------------------- trim
    def test_10_trim_reencode(self):
        dst = self.path("trim.mp4")
        result = editing.trim(SAMPLE, dst, start_s=0.5, duration_s=1.0)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 1.0))
        self.assertEqual(result["probe"]["width"], 320)
        self.assertEqual(result["probe"]["height"], 180)
        self.assertTrue(_approx(result["probe"]["fps"], 24.0, 0.1))
        self.assert_no_part_files()

    def test_11_trim_copy(self):
        dst = self.path("trim_copy.mp4")
        result = editing.trim(SAMPLE, dst, start_s=0.5, duration_s=1.0, reencode=False)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 1.0, 0.25))

    def test_12_trim_chinese_space_path(self):
        target_dir = self.path("中文 目录")
        os.makedirs(target_dir, exist_ok=True)
        dst = os.path.join(target_dir, "片段 输出（测试）.mp4")
        result = editing.trim(SAMPLE, dst, start_s=0.2, duration_s=0.6)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 0.6))
        self.assertIn("中文 目录", result["dst"])
        self.assert_no_part_files()

    # ----------------------------------------------------------------- concat
    def test_20_concat_reencode(self):
        dst = self.path("concat2.mp4")
        result = editing.concat([SAMPLE, SAMPLE], dst)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 4.0))
        self.assertEqual(result["probe"]["width"], 320)
        self.assertEqual(result["probe"]["height"], 180)

    def test_21_concat_silent_inputs(self):
        dst = self.path("concat_silent.mp4")
        result = editing.concat([SAMPLE2, SAMPLE2], dst)
        self.assert_ok(result, dst, expect_audio=False)
        self.assertTrue(_approx(result["probe"]["duration_s"], 4.0))

    def test_22_concat_mixed_audio(self):
        # sample 有音轨、sample2 无音轨：缺音轨段自动补静音
        dst = self.path("concat_mixed.mp4")
        result = editing.concat([SAMPLE, SAMPLE2, SAMPLE], dst)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 6.0))

    def test_23_concat_crossfade(self):
        dst = self.path("concat_xfade.mp4")
        result = editing.concat([SAMPLE, SAMPLE], dst, crossfade_s=0.5)
        self.assert_ok(result, dst, expect_audio=True)
        # 2 + 2 - 0.5 = 3.5s
        self.assertTrue(_approx(result["probe"]["duration_s"], 3.5))
        self.assertIn("xfade", " ".join(result["cmd"]))
        self.assertIn("acrossfade", " ".join(result["cmd"]))

    def test_24_concat_crossfade_three(self):
        dst = self.path("concat_xfade3.mp4")
        result = editing.concat([SAMPLE, SAMPLE, SAMPLE], dst, crossfade_s=0.4)
        self.assert_ok(result, dst, expect_audio=True)
        # 3 * 2 - 2 * 0.4 = 5.2s
        self.assertTrue(_approx(result["probe"]["duration_s"], 5.2))

    def test_25_concat_copy(self):
        dst = self.path("concat_copy.mp4")
        result = editing.concat([SAMPLE, SAMPLE], dst, reencode=False)
        self.assert_ok(result, dst, expect_audio=True)
        # stream copy 时 aac priming 会让时长略长
        self.assertTrue(_approx(result["probe"]["duration_s"], 4.0, 0.3))
        self.assertFalse(result.get("warnings"), result.get("warnings"))

    def test_26_concat_crossfade_too_long(self):
        with self.assertRaises(editing.EditError):
            editing.concat([SAMPLE, SAMPLE], self.path("bad_xfade.mp4"), crossfade_s=5.0)

    # -------------------------------------------------------------- transform
    def test_30_transform_scale_pad(self):
        dst = self.path("transform_pad.mp4")
        result = editing.transform(SAMPLE, dst, width=240, height=240, pad=True)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertEqual(result["probe"]["width"], 240)
        self.assertEqual(result["probe"]["height"], 240)
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0))

    def test_31_transform_fps(self):
        dst = self.path("transform_fps.mp4")
        result = editing.transform(SAMPLE, dst, fps=12)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertEqual(result["probe"]["width"], 320)
        self.assertTrue(_approx(result["probe"]["fps"], 12.0, 0.2))

    def test_32_transform_width_only(self):
        dst = self.path("transform_w.mp4")
        result = editing.transform(SAMPLE, dst, width=160)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertEqual(result["probe"]["width"], 160)
        self.assertEqual(result["probe"]["height"], 90)

    def test_33_transform_copy_rejects_scale(self):
        with self.assertRaises(editing.EditError):
            editing.transform(SAMPLE, self.path("bad_copy.mp4"), width=160, reencode=False)

    # -------------------------------------------------------------- mux_audio
    def test_40_mux_audio_replace_onto_silent(self):
        dst = self.path("mux_replace.mp4")
        result = editing.mux_audio(SAMPLE2, dst, audio=self.audio_source(), volume=0.8, offset_s=0.1)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0))

    def test_41_mux_audio_mix(self):
        dst = self.path("mux_mix.mp4")
        result = editing.mux_audio(
            SAMPLE, dst, audio=self.audio_source(), volume=0.5, offset_s=0.2, replace=False
        )
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0))
        self.assertEqual(result["probe"]["width"], 320)

    def test_42_mux_audio_normalize_own_track(self):
        dst = self.path("mux_norm.mp4")
        result = editing.mux_audio(SAMPLE, dst, audio=None, volume=0.9, normalize=True)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0))

    def test_43_mux_audio_requires_audio(self):
        with self.assertRaises(editing.EditError):
            editing.mux_audio(SAMPLE2, self.path("bad_mux.mp4"), audio=None)

    # ---------------------------------------------------------- burn_subtitles
    def test_50_burn_subtitles(self):
        srt = self.path("字幕 测试.srt")
        with open(srt, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(
                "1\n"
                "00:00:00,200 --> 00:00:01,200\n"
                "Hello ComfyUI\n"
                "\n"
                "2\n"
                "00:00:01,300 --> 00:00:01,900\n"
                "你好，视频剪辑\n"
            )
        dst = self.path("burn.mp4")
        result = editing.burn_subtitles(SAMPLE, dst, srt, font_size=28, margin_v=30)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0))
        self.assertIn("subtitles=", " ".join(result["cmd"]))
        self.assertEqual(result["probe"]["width"], 320)

    def test_51_burn_subtitles_gbk_srt(self):
        # GBK 编码的 srt 也应被转成 UTF-8 后烧录（中文 Windows 常见坑）
        srt = self.path("gbk.srt")
        content = "1\n00:00:00,200 --> 00:00:01,500\n中文字幕 GBK\n"
        with open(srt, "wb") as handle:
            handle.write(content.encode("gb18030"))
        dst = self.path("burn_gbk.mp4")
        result = editing.burn_subtitles(SAMPLE, dst, srt, font_size=24)
        self.assert_ok(result, dst, expect_audio=True)

    # ---------------------------------------------------------------- overlay
    def test_60_overlay_png(self):
        overlay_png = self.path("overlay.png")
        editing.thumbnail(SAMPLE, overlay_png, at_s=0.4, width=64)
        self.assertTrue(os.path.isfile(overlay_png))
        dst = self.path("overlay.mp4")
        result = editing.overlay(
            SAMPLE, dst, overlay_png, position="top-right", margin=10, opacity=0.7, scale=0.2
        )
        self.assert_ok(result, dst, expect_audio=True)
        self.assertEqual(result["probe"]["width"], 320)
        self.assertEqual(result["probe"]["height"], 180)
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0, 0.3))

    def test_61_overlay_bad_position(self):
        with self.assertRaises(editing.EditError):
            editing.overlay(SAMPLE, self.path("bad_overlay.mp4"), self.audio_source(), position="nowhere")

    # ------------------------------------------------------------------ speed
    def test_70_speed_up(self):
        dst = self.path("speed2x.mp4")
        result = editing.speed(SAMPLE, dst, factor=2.0)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 1.0))
        self.assertIn("atempo", " ".join(result["cmd"]))

    def test_71_speed_down(self):
        dst = self.path("speed0_5x.mp4")
        result = editing.speed(SAMPLE, dst, factor=0.5)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertTrue(_approx(result["probe"]["duration_s"], 4.0))

    def test_72_speed_no_audio(self):
        dst = self.path("speed_noaudio.mp4")
        result = editing.speed(SAMPLE, dst, factor=2.0, keep_audio=False)
        self.assert_ok(result, dst, expect_audio=False)
        self.assertTrue(_approx(result["probe"]["duration_s"], 1.0))

    # ---------------------------------------------------------- extract_frames
    def test_80_extract_frames(self):
        out_dir = self.path("frames")
        result = editing.extract_frames(SAMPLE, out_dir, fps=6)
        self.assertTrue(result.get("ok"), result)
        self.assertIsInstance(result.get("cmd"), list)
        count = result["probe"]["count"]
        self.assertIn(count, (11, 12, 13), "2s@6fps 应约 12 帧，实际 %s" % count)
        self.assertEqual(len(result["probe"]["files"]), count)
        first = os.path.join(out_dir, result["probe"]["files"][0])
        self.assertTrue(os.path.isfile(first))
        frame_info = editing.probe(first)
        self.assertEqual(frame_info["width"], 320)
        self.assertEqual(frame_info["height"], 180)

    def test_81_extract_frames_range(self):
        out_dir = self.path("frames_range")
        result = editing.extract_frames(SAMPLE, out_dir, fps=4, start_s=0.5, end_s=1.5)
        self.assertTrue(result.get("ok"), result)
        self.assertIn(result["probe"]["count"], (3, 4, 5))
        self.assertEqual(result["probe"]["width"], 320)

    def test_82_extract_frames_out_of_range(self):
        with self.assertRaises(editing.EditError):
            editing.extract_frames(SAMPLE, self.path("frames_bad"), fps=4, start_s=10.0, end_s=11.0)

    # ----------------------------------------------------------- extract_audio
    def test_85_extract_audio(self):
        dst = self.path("audio_out.m4a")
        result = editing.extract_audio(SAMPLE, dst)
        self.assert_ok(result, dst, expect_audio=True)
        self.assertEqual(result["probe"]["audio_codec"], "aac")
        self.assertIsNone(result["probe"]["video_codec"])
        self.assertTrue(_approx(result["probe"]["duration_s"], 2.0))

    def test_86_extract_audio_no_track(self):
        with self.assertRaises(editing.EditError):
            editing.extract_audio(SAMPLE2, self.path("bad_audio.m4a"))

    # --------------------------------------------------------------- thumbnail
    def test_90_thumbnail(self):
        dst = self.path("thumb.jpg")
        result = editing.thumbnail(SAMPLE, dst, at_s=1.0, width=160)
        self.assert_ok(result, dst)
        self.assertEqual(result["probe"]["width"], 160)
        self.assertEqual(result["probe"]["height"], 90)
        self.assertFalse(result["probe"]["has_audio"])
        self.assert_no_part_files()

    def test_91_thumbnail_png_after_end(self):
        # at_s 超过时长时自动回退到末尾附近，不应报错
        dst = self.path("thumb.png")
        result = editing.thumbnail(SAMPLE, dst, at_s=99.0, width=64)
        self.assert_ok(result, dst)
        self.assertEqual(result["probe"]["width"], 64)

    # ------------------------------------------------------------------ to_gif
    def test_95_to_gif(self):
        dst = self.path("out.gif")
        result = editing.to_gif(SAMPLE, dst, fps=8, width=160, start_s=0.2, duration_s=1.0)
        self.assert_ok(result, dst, expect_audio=False)
        self.assertEqual(result["probe"]["width"], 160)
        self.assertGreater(result["probe"]["size_mb"], 0)
        self.assertTrue(_approx(result["probe"]["duration_s"], 1.0, 0.25))
        # 第二遍命令里应有 paletteuse；palettegen 在第一遍（结果只回传最终编码命令）
        self.assertIn("paletteuse", " ".join(result["cmd"]))
        # 调色板临时文件应被清理
        leftovers = [name for name in os.listdir(self.tmp) if "palette" in name]
        self.assertEqual(leftovers, [])
        self.assert_no_part_files()

    # -------------------------------------------------------------- run_ffmpeg
    def test_97_run_ffmpeg_direct(self):
        dst = self.path("direct.mp4")
        # 末尾故意带上 dst，验证 run_ffmpeg 会识别并替换为 .part 原子写
        result = editing.run_ffmpeg(
            ["-i", SAMPLE, "-t", "0.5", "-c", "copy", dst],
            dst,
        )
        self.assertTrue(result.get("ok"), result)
        self.assertTrue(os.path.isfile(dst))
        self.assertTrue(_approx(result["duration_s"], 0.5, 0.15))
        self.assertIn("-nostdin", result["cmd"])
        self.assertIsInstance(result["stderr_tail"], str)
        self.assert_no_part_files()

    def test_98_run_ffmpeg_failure(self):
        dst = self.path("should_not_exist.mp4")
        with self.assertRaises(editing.EditError) as ctx:
            editing.run_ffmpeg(["-i", self.path("missing_input.mp4")], dst)
        message = str(ctx.exception)
        self.assertIn("ffmpeg", message)
        self.assertFalse(os.path.isfile(dst))
        self.assertFalse(os.path.isfile(dst + ".part"))
        self.assertLessEqual(len(message), 2000 + 200)

    def test_99_run_ffmpeg_timeout(self):
        dst = self.path("timeout.mp4")
        with self.assertRaises(editing.EditError) as ctx:
            editing.run_ffmpeg(
                ["-i", SAMPLE, "-c:v", "libx264", "-preset", "veryfast"],
                dst,
                timeout_s=0.001,
            )
        self.assertIn("超时", str(ctx.exception))
        self.assertFalse(os.path.isfile(dst + ".part"))

    def test_96_run_ffmpeg_rejects_string_args(self):
        with self.assertRaises(editing.EditError):
            editing.run_ffmpeg("-i %s -c copy out.mp4" % SAMPLE, self.path("x.mp4"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
