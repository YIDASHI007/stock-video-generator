from __future__ import annotations

import pytest
from stock_video_generator.douyin_publisher import DouyinBrowserPublisher


class _FakeInput:
    def __init__(self, *, classes: str, context: str) -> None:
        self.attributes = {
            "accept": "image/png,image/jpeg,image/jpg",
            "class": classes,
        }
        self.context = context

    async def get_attribute(self, name: str) -> str | None:
        return self.attributes.get(name)

    async def evaluate(self, _expression: str) -> str:
        return self.context


class _FakeInputs:
    def __init__(self, items: list[_FakeInput]) -> None:
        self.items = items

    async def count(self) -> int:
        return len(self.items)

    def nth(self, index: int) -> _FakeInput:
        return self.items[index]


class _FakeContainer:
    def __init__(self, items: list[_FakeInput]) -> None:
        self.inputs = _FakeInputs(items)

    def locator(self, selector: str) -> _FakeInputs:
        assert selector == 'input[type="file"]'
        return self.inputs


@pytest.mark.asyncio
async def test_image_input_prefers_initial_custom_cover_input() -> None:
    ai_reference = _FakeInput(
        classes="semi-upload-hidden-input",
        context="生成参考图 智能参考 AI生成封面",
    )
    custom_replace = _FakeInput(
        classes="semi-upload-hidden-input-replace",
        context="上传封面 点击上传文件或拖拽文件到这里",
    )
    custom_initial = _FakeInput(
        classes="semi-upload-hidden-input",
        context="上传封面 点击上传文件或拖拽文件到这里",
    )
    publisher = object.__new__(DouyinBrowserPublisher)

    selected = await publisher._image_input(  # noqa: SLF001
        _FakeContainer([ai_reference, custom_replace, custom_initial])
    )

    assert selected is custom_initial
