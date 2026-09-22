import base64
from pathlib import Path

import pytest
from acp.schema import (
    BlobResourceContents,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    ResourceContentBlock,
    TextContentBlock,
    TextResourceContents,
)

from acp_adapter.server import (
    HermesACPAgent,
    _content_blocks_to_openai_user_content,
    _path_from_file_uri,
)


def test_acp_image_blocks_convert_to_openai_multimodal_content():
    content = _content_blocks_to_openai_user_content([
        TextContentBlock(type="text", text="What is in this image?"),
        ImageContentBlock(type="image", data="aGVsbG8=", mimeType="image/png"),
    ])

    assert content == [
        {"type": "text", "text": "What is in this image?"},
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,aGVsbG8="},
        },
    ]


def test_text_only_acp_blocks_stay_string_for_legacy_prompt_path():
    content = _content_blocks_to_openai_user_content([
        TextContentBlock(type="text", text="/help"),
    ])

    assert content == "/help"


def test_acp_resource_link_file_is_inlined_as_text(tmp_path):
    attached = tmp_path / "notes.md"
    attached.write_text(
        "# Notes\n\nAttached file body", encoding="utf-8", newline=""
    )

    content = _content_blocks_to_openai_user_content([
        TextContentBlock(type="text", text="Please read this file"),
        ResourceContentBlock(
            type="resource_link",
            name="notes.md",
            title="Project notes",
            uri=attached.as_uri(),
            mimeType="text/markdown",
        ),
    ])

    assert content == (
        "Please read this file\n"
        "[Attached file: Project notes (notes.md)]\n"
        f"URI: {attached.as_uri()}\n\n"
        "# Notes\n\nAttached file body"
    )


@pytest.mark.windows_only
def test_native_windows_path_is_not_treated_as_a_uri_scheme(tmp_path):
    attached = tmp_path / "notes.md"

    assert _path_from_file_uri(str(attached)) == attached


@pytest.mark.windows_only
def test_native_windows_path_preserves_literal_percent_sequences(tmp_path):
    attached = tmp_path / "notes%20literal.md"

    assert _path_from_file_uri(str(attached)) == attached


def test_raw_unc_path_preserves_literal_percent_sequences():
    raw = r"\\server\share\notes%20literal.md"

    assert _path_from_file_uri(raw) == Path(raw)


def test_raw_posix_path_preserves_literal_percent_sequences():
    raw = "/workspace/notes%20literal.md"

    assert _path_from_file_uri(raw) == Path(raw)




@pytest.mark.asyncio
async def test_initialize_advertises_image_prompt_capability():
    response = await HermesACPAgent().initialize()

    assert response.agent_capabilities is not None
    assert response.agent_capabilities.prompt_capabilities is not None
    assert response.agent_capabilities.prompt_capabilities.image is True


# 1x1 transparent PNG — smallest valid image payload for inlining tests.
_ONE_PX_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6300010000000500010d0a2db40000000049454e44ae426082"
)

