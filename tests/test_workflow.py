"""workflow 模板层单元测试。

运行：
    & 'K:\\CONFI\\mian\\ComfyUI_windows_portable\\python_embeded\\python.exe' -m unittest discover -s K:\\CONFI\\comfy_mcp\\tests -v
"""

from __future__ import annotations

import copy
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
PKG_DIR = os.path.dirname(HERE)
COMFY_ROOT = os.environ.get(
    "COMFY_MCP_COMFY_ROOT",
    r"K:\CONFI\mian\ComfyUI_windows_portable\ComfyUI",
)
WORKFLOW_DIR = os.path.join(COMFY_ROOT, "user", "default", "workflows")
OBJECT_INFO_PATH = os.path.join(HERE, "fixtures", "object_info.json")
PROJECT_DIR = os.path.dirname(PKG_DIR)

for path in (PKG_DIR, PROJECT_DIR):
    if path not in sys.path:
        sys.path.insert(0, path)

import workflow as wf  # noqa: E402


def load_object_info() -> dict:
    with open(OBJECT_INFO_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_ui_workflow(filename: str) -> dict:
    with open(os.path.join(WORKFLOW_DIR, filename), "r", encoding="utf-8") as f:
        return json.load(f)


def node_of_class(api: dict, class_type: str) -> dict:
    for node in api.values():
        if node.get("class_type") == class_type:
            return node
    raise AssertionError(f"api 中没有 {class_type}")


def ids_of_class(api: dict, class_type: str) -> list[str]:
    return [node_id for node_id, node in api.items() if node.get("class_type") == class_type]


def _norm(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
        return None  # 连线不参与字面量比较
    return value


def api_signature(api: dict) -> dict:
    """与节点 id 无关的结构签名：类多重集 + 类间连线 + 字面量。"""
    class_of = {node_id: node.get("class_type") for node_id, node in api.items()}
    classes = sorted(class_of.values())
    edges = sorted(
        (class_of[value[0]], class_of[node_id], input_name)
        for node_id, node in api.items()
        for input_name, value in (node.get("inputs") or {}).items()
        if isinstance(value, list) and len(value) == 2 and value[0] in class_of
    )
    literals = sorted(
        (class_of[node_id], input_name, repr(_norm(value)))
        for node_id, node in api.items()
        for input_name, value in (node.get("inputs") or {}).items()
        if _norm(value) is not None
    )
    return {"classes": classes, "edges": edges, "literals": literals}


def canonicalize_dims(api: dict) -> dict:
    """把 H3 条件节点的宽高对齐到 32（UI 工作流可能给 36 对齐的旧值）。"""
    api = copy.deepcopy(api)
    for node in api.values():
        if node.get("class_type") in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo"):
            inputs = node["inputs"]
            if isinstance(inputs.get("width"), int):
                inputs["width"] = wf.align_dim(inputs["width"])
            if isinstance(inputs.get("height"), int):
                inputs["height"] = wf.align_dim(inputs["height"])
    return api


def params_from_api(api: dict) -> dict:
    """把（已折叠辅助节点的）API prompt 反向提取回 resolve() 参数。"""
    cond = None
    for node in api.values():
        if node.get("class_type") in ("MiniMaxH3ReferenceToVideo", "MiniMaxH3ImageToVideo"):
            cond = node
            break
    assert cond is not None, "没有 MiniMaxH3 条件节点"
    inputs = cond["inputs"]
    params = {
        "prompt": inputs["prompt"],
        "width": inputs.get("width"),
        "height": inputs.get("height"),
        "length": inputs.get("length"),
    }
    if cond["class_type"] == "MiniMaxH3ReferenceToVideo":
        params["ref_image_size"] = inputs.get("ref_image_size", "match")
        refs = []
        for key in sorted(k for k in inputs if k.startswith("ref_images.")):
            refs.append(api[inputs[key][0]]["inputs"]["image"])
        params["ref_images"] = refs
    else:
        if "first_frame" in inputs:
            params["first_frame"] = api[inputs["first_frame"][0]]["inputs"]["image"]
        if "last_frame" in inputs:
            params["last_frame"] = api[inputs["last_frame"][0]]["inputs"]["image"]
    noise = node_of_class(api, "RandomNoise")["inputs"]
    params["seed"] = noise["noise_seed"]
    scheduler = node_of_class(api, "BasicScheduler")["inputs"]
    params["steps"] = scheduler["steps"]
    params["scheduler"] = scheduler["scheduler"]
    params["sampler"] = node_of_class(api, "KSamplerSelect")["inputs"]["sampler_name"]
    params["output_prefix"] = node_of_class(api, "SaveVideo")["inputs"]["filename_prefix"]
    loras = ids_of_class(api, "LoraLoaderModelOnly")
    if loras:
        lora_inputs = api[loras[0]]["inputs"]
        params["lora_name"] = lora_inputs["lora_name"]
        params["lora_strength"] = lora_inputs["strength_model"]
    if params.get("width") is None or params.get("height") is None:
        params.pop("width", None)
        params.pop("height", None)
    return params


class TemplateFileTests(unittest.TestCase):
    def setUp(self):
        self.object_info = load_object_info()

    def test_three_template_files_load(self):
        self.assertEqual(len(wf.TEMPLATES), 9)
        for name, meta in wf.TEMPLATES.items():
            path = os.path.join(PKG_DIR, "templates", meta["template_file"])
            self.assertTrue(os.path.isfile(path), path)
            api = wf.load_template(name)
            self.assertGreaterEqual(len(api), 10)
            for node_id, node in api.items():
                self.assertIsInstance(node_id, str)
                self.assertIn("class_type", node)
                self.assertIsInstance(node.get("inputs"), dict)

    def test_templates_validate_clean(self):
        # fixture 只包含 H3 节点 schema；Qwen/MOSS 模板由 workflow 自身单测覆盖
        for name in ("minimax_h3_t2v", "minimax_h3_fl2v", "minimax_h3_r2v", "minimax_h3_image"):
            result = wf.validate(wf.load_template(name), self.object_info)
            self.assertTrue(result["ok"], f"{name}: {result['errors']}")
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["warnings"], [])

    def test_models_match_spec(self):
        r2v = wf.load_template("minimax_h3_r2v")
        self.assertEqual(
            node_of_class(r2v, "UNETLoader")["inputs"]["unet_name"],
            "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        )
        for name in ("minimax_h3_fl2v", "minimax_h3_t2v"):
            api = wf.load_template(name)
            self.assertEqual(
                node_of_class(api, "UNETLoader")["inputs"]["unet_name"],
                "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
            )
            self.assertEqual(node_of_class(api, "CLIPLoader")["inputs"]["type"], "minimax")
            save = node_of_class(api, "SaveVideo")["inputs"]
            self.assertEqual(save["format"], "auto")
            self.assertEqual(save["codec"], "auto")
            create = node_of_class(api, "CreateVideo")["inputs"]
            self.assertEqual(create["fps"], 24.0)
            self.assertEqual(create["bit_depth"], 8)

    def test_list_templates_metadata(self):
        entries = wf.list_templates()
        self.assertEqual({entry["name"] for entry in entries}, set(wf.TEMPLATES))
        for entry in entries:
            self.assertIn("title", entry)
            self.assertIn("description", entry)
            self.assertIn("model", entry)
            parameters = entry["parameters"]
            for required in ("prompt", "profile", "width", "height", "duration_s", "length", "seed"):
                self.assertIn(required, parameters)
                self.assertIn("type", parameters[required])
            self.assertIn("help", parameters["prompt"])

    def test_utf8_template_files(self):
        for meta in wf.TEMPLATES.values():
            path = os.path.join(PKG_DIR, "templates", meta["template_file"])
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            self.assertGreater(len(content), 100)


class ValidatorTests(unittest.TestCase):
    def setUp(self):
        self.object_info = load_object_info()
        self.api = wf.load_template("minimax_h3_r2v")

    def test_unknown_class_type(self):
        api = copy.deepcopy(self.api)
        api["1"]["class_type"] = "NotARealNode"
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("unknown class_type" in error for error in result["errors"]))

    def test_missing_required_input(self):
        api = copy.deepcopy(self.api)
        del api["10"]["inputs"]["prompt"]
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("缺少必需输入 prompt" in error for error in result["errors"]))

    def test_dangling_link(self):
        api = copy.deepcopy(self.api)
        api["10"]["inputs"]["clip"] = ["999", 0]
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("不存在的节点 999" in error for error in result["errors"]))

    def test_link_slot_out_of_range(self):
        api = copy.deepcopy(self.api)
        api["8"]["inputs"]["model"] = ["13", 3]  # CreateVideo 只有 1 个输出
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("槽位 3" in error for error in result["errors"]))

    def test_silent_node(self):
        api = copy.deepcopy(self.api)
        api["50"] = {"class_type": "LoadImage", "inputs": {"image": "example.png"}}
        result = wf.validate(api, self.object_info)
        self.assertTrue(any("静默节点" in warning for warning in result["warnings"]))

    def test_no_output_node(self):
        api = copy.deepcopy(self.api)
        del api["14"]
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("输出节点" in error for error in result["errors"]))

    def test_autogrow_container_key_is_error(self):
        api = copy.deepcopy(self.api)
        api["10"]["inputs"]["ref_images"] = {"ref_image_0": ["20", 0]}
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("点号键" in error for error in result["errors"]))

    def test_autogrow_dotted_key_ok_and_out_of_range_fails(self):
        api = copy.deepcopy(self.api)
        api["20"] = {"class_type": "LoadImage", "inputs": {"image": "example.png"}}
        api["10"]["inputs"]["ref_images.ref_image_0"] = ["20", 0]
        self.assertTrue(wf.validate(api, self.object_info)["ok"])

        bad = copy.deepcopy(api)
        del bad["10"]["inputs"]["ref_images.ref_image_0"]
        bad["10"]["inputs"]["ref_images.ref_image_9"] = ["20", 0]
        result = wf.validate(bad, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("autogrow 槽位" in error for error in result["errors"]))

    def test_unknown_input_warns(self):
        api = copy.deepcopy(self.api)
        api["10"]["inputs"]["bogus_input"] = 1
        result = wf.validate(api, self.object_info)
        self.assertTrue(any("未知输入 bogus_input" in warning for warning in result["warnings"]))

    def test_combo_value_not_in_list(self):
        api = copy.deepcopy(self.api)
        api["6"]["inputs"]["sampler_name"] = "not_a_sampler"
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("不在可选列表" in error for error in result["errors"]))

    def test_codec_value_must_be_option_key(self):
        api = copy.deepcopy(self.api)
        api["14"]["inputs"]["codec"] = "h265"
        result = wf.validate(api, self.object_info)
        self.assertFalse(result["ok"])
        self.assertTrue(any("codec" in error for error in result["errors"]))

    def test_validate_without_object_info_still_checks_graph(self):
        api = copy.deepcopy(self.api)
        api["10"]["inputs"]["clip"] = ["999", 0]
        result = wf.validate(api, {})
        self.assertFalse(result["ok"])
        self.assertTrue(result["warnings"])

    def test_local_absolute_image_path_allowed_for_upload(self):
        result = wf.resolve(
            "minimax_h3_fl2v",
            {"prompt": "x", "first_frame": os.path.abspath(__file__)},
        )
        validation = wf.validate(result["api"], self.object_info)
        self.assertTrue(validation["ok"], validation["errors"])

    def test_missing_local_image_path_warns(self):
        result = wf.resolve(
            "minimax_h3_fl2v",
            {"prompt": "x", "first_frame": r"K:\CONFI\no_such_file_12345.png"},
        )
        validation = wf.validate(result["api"], self.object_info)
        self.assertTrue(validation["ok"], validation["errors"])
        self.assertTrue(any("不存在" in warning for warning in validation["warnings"]))


class RuleTests(unittest.TestCase):
    def test_duration_grid(self):
        expected = {1: 39, 2: 56, 5: 124, 10: 243, 15: 362}
        for seconds, frames in expected.items():
            self.assertEqual(wf.duration_to_length(seconds), frames, seconds)
        for frames in (39, 56, 124, 243, 362):
            self.assertEqual(frames % 17, 5)

    def test_align_frame_count(self):
        self.assertEqual(wf.align_frame_count(5), 5)
        self.assertEqual(wf.align_frame_count(56), 56)
        self.assertEqual(wf.align_frame_count(124), 124)
        self.assertEqual(wf.align_frame_count(125), 141)
        self.assertEqual(wf.align_frame_count(362), 362)
        self.assertEqual(wf.align_frame_count(0), 5)

    def test_resolve_size(self):
        self.assertEqual(wf.resolve_size("16:9", 0.2), (608, 352))
        self.assertEqual(wf.resolve_size("16:9", 0.4), (864, 480))
        self.assertEqual(wf.resolve_size("16:9", 0.8), (1216, 672))
        self.assertEqual(wf.resolve_size("16:9", 2.0), (1920, 1088))
        self.assertEqual(wf.resolve_size("9:16", 0.4), (480, 864))
        self.assertEqual(wf.resolve_size("1:1", 1.0), (1024, 1024))
        self.assertEqual(wf.resolve_size("16:9 (Widescreen)", 0.4), (864, 480))
        for aspect in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9", "3:2", "2:3"):
            width, height = wf.resolve_size(aspect, 0.4)
            self.assertEqual(width % 32, 0)
            self.assertEqual(height % 32, 0)
        with self.assertRaises(ValueError):
            wf.resolve_size("bogus", 0.4)


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.object_info = load_object_info()

    def assert_valid(self, api):
        result = wf.validate(api, self.object_info)
        self.assertTrue(result["ok"], result["errors"])
        self.assertEqual(result["errors"], [])

    def test_profiles(self):
        cases = {
            "draft": (608, 352, 56, 4),
            "standard": (608, 352, 124, 4),
            "high": (608, 352, 124, 8),
        }
        for profile, (width, height, length, steps) in cases.items():
            result = wf.resolve("minimax_h3_r2v", {"prompt": "cat", "profile": profile})
            resolved = result["resolved"]
            self.assertEqual((resolved["width"], resolved["height"]), (width, height), profile)
            self.assertEqual(resolved["length"], length, profile)
            self.assertEqual(resolved["steps"], steps, profile)
            self.assertEqual(resolved["profile"], profile)
            self.assertEqual(resolved["duration_s"], round(length / 24, 3))
            self.assert_valid(result["api"])

    def test_explicit_duration_and_length(self):
        result = wf.resolve("minimax_h3_t2v", {"prompt": "x", "duration_s": 5})
        self.assertEqual(result["resolved"]["length"], 124)
        result = wf.resolve("minimax_h3_t2v", {"prompt": "x", "length": 120})
        self.assertEqual(result["resolved"]["length"], 124)
        self.assertTrue(any("snap" in warning for warning in result["warnings"]))

    def test_length_clamped_to_max(self):
        result = wf.resolve("minimax_h3_t2v", {"prompt": "x", "length": 1000})
        self.assertEqual(result["resolved"]["length"], 362)
        self.assertTrue(any("max_length" in warning for warning in result["warnings"]))

    def test_explicit_size_and_alignment(self):
        result = wf.resolve(
            "minimax_h3_t2v", {"prompt": "x", "width": 865, "height": 481, "profile": "draft"}
        )
        self.assertEqual((result["resolved"]["width"], result["resolved"]["height"]), (864, 480))
        self.assertTrue(any("对齐" in warning for warning in result["warnings"]))
        self.assert_valid(result["api"])

    def test_megapixels_and_aspect(self):
        result = wf.resolve(
            "minimax_h3_t2v", {"prompt": "x", "megapixels": 0.4, "aspect": "9:16"}
        )
        self.assertEqual((result["resolved"]["width"], result["resolved"]["height"]), (480, 864))

    def test_seed_explicit_and_random(self):
        explicit = wf.resolve("minimax_h3_t2v", {"prompt": "x", "seed": 42})
        self.assertEqual(explicit["resolved"]["seed"], 42)
        self.assertEqual(node_of_class(explicit["api"], "RandomNoise")["inputs"]["noise_seed"], 42)
        first = wf.resolve("minimax_h3_t2v", {"prompt": "x"})["resolved"]["seed"]
        second = wf.resolve("minimax_h3_t2v", {"prompt": "x"})["resolved"]["seed"]
        self.assertGreaterEqual(first, 0)
        self.assertLessEqual(first, wf.SEED_MAX)
        self.assertNotEqual(first, second)

    def test_sampler_and_scheduler_overrides(self):
        result = wf.resolve(
            "minimax_h3_t2v",
            {"prompt": "x", "sampler": "uni_pc", "scheduler": "karras", "steps": 12},
        )
        self.assertEqual(node_of_class(result["api"], "KSamplerSelect")["inputs"]["sampler_name"], "uni_pc")
        scheduler = node_of_class(result["api"], "BasicScheduler")["inputs"]
        self.assertEqual(scheduler["scheduler"], "karras")
        self.assertEqual(scheduler["steps"], 12)
        self.assert_valid(result["api"])

    def test_negative_prompt_ignored_with_warning(self):
        result = wf.resolve("minimax_h3_t2v", {"prompt": "x", "negative_prompt": "blurry"})
        self.assertTrue(any("negative_prompt" in warning for warning in result["warnings"]))

    def test_prompt_required(self):
        with self.assertRaises(ValueError):
            wf.resolve("minimax_h3_t2v", {"prompt": "  "})
        with self.assertRaises(ValueError):
            wf.resolve("nope", {"prompt": "x"})

    def test_output_prefix(self):
        result = wf.resolve(
            "minimax_h3_t2v", {"prompt": "x", "output_prefix": "video/custom"}
        )
        self.assertEqual(
            node_of_class(result["api"], "SaveVideo")["inputs"]["filename_prefix"], "video/custom"
        )
        self.assertEqual(result["resolved"]["output_prefix"], "video/custom")

    def test_r2v_ref_images(self):
        refs = ["屏幕截图 2026-08-12 101426.png", "example.png"]
        result = wf.resolve(
            "minimax_h3_r2v", {"prompt": "x", "ref_images": refs, "ref_image_size": "max"}
        )
        api = result["api"]
        cond_inputs = node_of_class(api, "MiniMaxH3ReferenceToVideo")["inputs"]
        self.assertEqual(cond_inputs["ref_image_size"], "max")
        self.assertEqual(result["resolved"]["ref_images"], refs)
        for index, filename in enumerate(refs):
            key = f"ref_images.ref_image_{index}"
            self.assertIn(key, cond_inputs)
            node_id, slot = cond_inputs[key]
            self.assertEqual(slot, 0)
            self.assertEqual(api[node_id]["class_type"], "LoadImage")
            self.assertEqual(api[node_id]["inputs"]["image"], filename)
        self.assertEqual(len(ids_of_class(api, "LoadImage")), 2)
        self.assert_valid(api)

    def test_r2v_too_many_ref_images(self):
        with self.assertRaises(ValueError):
            wf.resolve("minimax_h3_r2v", {"prompt": "x", "ref_images": ["a.png"] * 10})

    def test_r2v_ignores_frames_and_lora(self):
        result = wf.resolve(
            "minimax_h3_r2v",
            {"prompt": "x", "first_frame": "a.png", "lora_name": wf.TURBO_LORA_4STEP},
        )
        self.assertTrue(any("first_frame/last_frame" in warning for warning in result["warnings"]))
        self.assertTrue(any("LoRA" in warning for warning in result["warnings"]))
        self.assertEqual(ids_of_class(result["api"], "LoadImage"), [])
        self.assertEqual(ids_of_class(result["api"], "LoraLoaderModelOnly"), [])

    def test_fl2v_frames_and_lora(self):
        result = wf.resolve(
            "minimax_h3_fl2v",
            {
                "prompt": "x",
                "first_frame": "example.png",
                "last_frame": "屏幕截图 2026-08-12 101426.png",
                "lora_name": wf.TURBO_LORA_4STEP,
                "lora_strength": 0.8,
                "steps": 6,
            },
        )
        api = result["api"]
        self.assert_valid(api)
        cond_inputs = node_of_class(api, "MiniMaxH3ImageToVideo")["inputs"]
        self.assertEqual(api[cond_inputs["first_frame"][0]]["inputs"]["image"], "example.png")
        self.assertEqual(
            api[cond_inputs["last_frame"][0]]["inputs"]["image"], "屏幕截图 2026-08-12 101426.png"
        )
        lora_id = ids_of_class(api, "LoraLoaderModelOnly")[0]
        lora_inputs = api[lora_id]["inputs"]
        self.assertEqual(lora_inputs["lora_name"], wf.TURBO_LORA_4STEP)
        self.assertEqual(lora_inputs["strength_model"], 0.8)
        self.assertEqual(lora_inputs["model"], [ids_of_class(api, "UNETLoader")[0], 0])
        unet_id = ids_of_class(api, "UNETLoader")[0]
        for class_type in ("BasicScheduler", "BasicGuider"):
            self.assertEqual(node_of_class(api, class_type)["inputs"]["model"], [lora_id, 0])
        self.assertEqual(node_of_class(api, "BasicScheduler")["inputs"]["steps"], 6)
        self.assertNotEqual(unet_id, lora_id)

    def test_t2v_no_frames(self):
        result = wf.resolve("minimax_h3_t2v", {"prompt": "x"})
        api = result["api"]
        self.assert_valid(api)
        cond_inputs = node_of_class(api, "MiniMaxH3ImageToVideo")["inputs"]
        self.assertNotIn("first_frame", cond_inputs)
        self.assertNotIn("last_frame", cond_inputs)
        self.assertNotIn("ref_images.ref_image_0", cond_inputs)
        self.assertEqual(len(ids_of_class(api, "LoadImage")), 0)

    def test_t2v_rejects_frames_with_warning(self):
        result = wf.resolve("minimax_h3_t2v", {"prompt": "x", "first_frame": "example.png"})
        self.assertEqual(len(ids_of_class(result["api"], "LoadImage")), 0)
        self.assertTrue(any("不支持 first_frame" in warning for warning in result["warnings"]))

    def test_unknown_profile(self):
        with self.assertRaises(ValueError):
            wf.resolve("minimax_h3_t2v", {"prompt": "x", "profile": "ultra"})


class UIToAPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.object_info = load_object_info()
        for name in ("video_minimax_h3_r2v.json", "VIDOH3.json"):
            if not os.path.isfile(os.path.join(WORKFLOW_DIR, name)):
                raise unittest.SkipTest("ComfyUI UI workflow fixtures not installed")
        cls.ui_r2v = load_ui_workflow("video_minimax_h3_r2v.json")
        cls.ui_fl2v = load_ui_workflow("VIDOH3.json")

    def test_r2v_conversion(self):
        api = wf.ui_to_api(self.ui_r2v, self.object_info)
        result = wf.validate(api, self.object_info)
        self.assertTrue(result["ok"], result["errors"])
        classes = [node["class_type"] for node in api.values()]
        self.assertIn("MiniMaxH3ReferenceToVideo", classes)
        self.assertIn("SaveVideo", classes)
        # 辅助节点被折叠/丢弃
        for dropped in ("MarkdownNote", "ResolutionSelector", "ComfyMathExpression",
                        "PrimitiveFloat", "PrimitiveStringMultiline", "TESpeedMiniMaxH3"):
            self.assertNotIn(dropped, classes)
        # 两个 mode=4 的 LoadImage 被 bypass 掉，只剩 ref_image_0
        self.assertEqual(len(ids_of_class(api, "LoadImage")), 1)
        cond = node_of_class(api, "MiniMaxH3ReferenceToVideo")["inputs"]
        self.assertTrue(cond["prompt"].startswith("新海诚风格"))
        self.assertEqual(cond["width"], 864)
        self.assertEqual(cond["height"], 468)  # UI 里 ResolutionSelector multiple=36 的旧值
        self.assertEqual(cond["length"], 362)
        self.assertEqual(cond["ref_images.ref_image_0"][1], 0)
        self.assertEqual(node_of_class(api, "SaveVideo")["inputs"]["codec"], "auto")
        self.assertEqual(node_of_class(api, "RandomNoise")["inputs"]["noise_seed"], 140506114053066)

    def test_r2v_equivalent_to_handwritten_template(self):
        converted = wf.ui_to_api(self.ui_r2v, self.object_info)
        params = params_from_api(converted)
        canonical = canonicalize_dims(converted)
        resolved = wf.resolve("minimax_h3_r2v", params)["api"]
        self.assertEqual(api_signature(canonical), api_signature(resolved))

    def test_vidoh3_conversion(self):
        api = wf.ui_to_api(self.ui_fl2v, self.object_info)
        result = wf.validate(api, self.object_info)
        self.assertTrue(result["ok"], result["errors"])
        classes = [node["class_type"] for node in api.values()]
        self.assertIn("MiniMaxH3ImageToVideo", classes)
        for dropped in ("ResolutionSelector", "ComfyMathExpression", "PrimitiveFloat"):
            self.assertNotIn(dropped, classes)
        # subgraph 被展开，节点 id 带实例前缀
        self.assertTrue(any(":" in node_id for node_id in api))
        cond = node_of_class(api, "MiniMaxH3ImageToVideo")["inputs"]
        self.assertNotIn("first_frame", cond)
        self.assertNotIn("last_frame", cond)
        self.assertEqual((cond["width"], cond["height"]), (864, 480))
        self.assertEqual(cond["length"], 56)
        self.assertEqual(
            node_of_class(api, "UNETLoader")["inputs"]["unet_name"],
            "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        )
        self.assertEqual(node_of_class(api, "RandomNoise")["inputs"]["noise_seed"], 615078928902715)

    def test_vidoh3_equivalent_to_fl2v_and_t2v_templates(self):
        converted = wf.ui_to_api(self.ui_fl2v, self.object_info)
        params = params_from_api(converted)
        for template in ("minimax_h3_fl2v", "minimax_h3_t2v"):
            resolved = wf.resolve(template, params)["api"]
            self.assertEqual(api_signature(converted), api_signature(resolved), template)

    def test_mute_node_is_dropped(self):
        ui = copy.deepcopy(self.ui_r2v)
        for node in ui["nodes"]:
            if node["id"] == 137:
                node["mode"] = 2
        api = wf.ui_to_api(ui, self.object_info)
        cond = node_of_class(api, "MiniMaxH3ReferenceToVideo")["inputs"]
        self.assertFalse(any(key.startswith("ref_images.") for key in cond))
        self.assertEqual(len(ids_of_class(api, "LoadImage")), 0)

    def test_bypass_node_passthrough(self):
        # r2v 里 LoadImage(139/142) 与 TESpeedMiniMaxH3(141) 是 mode=4，全部被 bypass
        api = wf.ui_to_api(self.ui_r2v, self.object_info)
        self.assertNotIn("139", api)
        self.assertNotIn("141", api)
        self.assertNotIn("142", api)

    def test_math_expression_folding(self):
        self.assertEqual(wf._eval_math_expression("max(5, round(a * 24)) + (5 - (max(5, round(a * 24)) % 17)) % 17", {"a": 15}), 362)
        self.assertEqual(wf._eval_math_expression("a + b", {"a": 2, "b": 3}), 5)
        with self.assertRaises(ValueError):
            wf._eval_math_expression("__import__('os').system('echo hi')", {})

    def test_unknown_ui_type_is_kept(self):
        ui = copy.deepcopy(self.ui_r2v)
        ui["nodes"].append({
            "id": 999, "type": "FakeCustomNode", "mode": 0,
            "inputs": [{"name": "value", "type": "INT", "link": None, "widget": {"name": "value"}}],
            "outputs": [{"name": "INT", "type": "INT", "links": []}],
            "widgets_values": [1],
        })
        api = wf.ui_to_api(ui, self.object_info)
        # 没有连到输出节点，会被剪掉；但不应该崩溃
        self.assertNotIn("999", api)


class DocsTests(unittest.TestCase):
    def test_workflows_doc_exists_and_is_utf8(self):
        path = os.path.join(PKG_DIR, "docs", "WORKFLOWS.md")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("ref_images.ref_image_0", content)
        self.assertIn("codec", content)
        self.assertIn("duration_to_length", content)


if __name__ == "__main__":
    unittest.main(verbosity=2)
