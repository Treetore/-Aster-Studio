"""Minimal i18n dictionary for the web shell and setup labels."""
from __future__ import annotations

STRINGS = {
    "zh": {
        "app.title": "Aster Studio · 本地生成工作台",
        "nav.create": "创作", "nav.image": "生图", "nav.film": "长片",
        "nav.tasks": "任务", "nav.canvas": "画布", "nav.gallery": "我的作品",
        "nav.models": "模型", "nav.projects": "项目", "nav.settings": "设置",
        "common.save": "保存", "common.cancel": "取消", "common.download": "下载",
        "setup.title": "首次启动", "setup.easy": "易懂版", "setup.pro": "专业版",
        "models.installed": "已安装", "models.missing": "未安装", "models.license": "许可",
        "page.create": "创作视频", "page.image": "生图", "page.film": "长片",
        "page.tasks": "任务控制台", "page.canvas": "节点画布", "page.gallery": "我的作品",
        "page.models": "模型", "page.projects": "项目", "page.settings": "设置",
    },
    "en": {
        "app.title": "Aster Studio · Local AI Workspace",
        "nav.create": "Create", "nav.image": "Image", "nav.film": "Film",
        "nav.tasks": "Tasks", "nav.canvas": "Canvas", "nav.gallery": "Gallery",
        "nav.models": "Models", "nav.projects": "Projects", "nav.settings": "Settings",
        "common.save": "Save", "common.cancel": "Cancel", "common.download": "Download",
        "setup.title": "First Run", "setup.easy": "Easy", "setup.pro": "Professional",
        "models.installed": "Installed", "models.missing": "Not installed", "models.license": "License",
        "page.create": "Create Video", "page.image": "Image", "page.film": "Film",
        "page.tasks": "Task Console", "page.canvas": "Node Canvas", "page.gallery": "Gallery",
        "page.models": "Models", "page.projects": "Projects", "page.settings": "Settings",
    },
}


def get(lang: str = "zh") -> dict:
    return STRINGS.get(lang if lang in STRINGS else "zh", STRINGS["zh"])
