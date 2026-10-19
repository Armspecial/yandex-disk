"""Диспетчер просмотра: чем открывать ресурс."""

from __future__ import annotations

from ydx.core.models import Resource
from ydx.ui.views.viewer import viewer_kind


def _res(media_type=None, is_dir=False, name="f"):
    return Resource(name=name, type="dir" if is_dir else "file",
                    path="disk:/f", media_type=media_type)


def test_viewer_kind_dispatch():
    assert viewer_kind(_res("image")) == "photo"
    assert viewer_kind(_res("video")) == "video"
    assert viewer_kind(_res("document")) == "external"
    assert viewer_kind(_res()) == "external"
    assert viewer_kind(_res(None, is_dir=True)) == "external"
