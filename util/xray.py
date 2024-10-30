import logging
import os

from aws_xray_sdk.core import AWSXRayRecorder
from aws_xray_sdk.core import patch as xray_patch
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core.models.segment import Segment
from aws_xray_sdk.ext.flask.middleware import XRayMiddleware
from aws_xray_sdk.ext.httplib import add_ignored as httplib_add_ignored
from flask import Flask


class PalaceXrayUtils:
    XRAY_ENV_ENABLE = "PALACE_XRAY"
    XRAY_ENV_NAME = "PALACE_XRAY_NAME"
    XRAY_ENV_ANNOTATE = "PALACE_XRAY_ANNOTATE_"

    @classmethod
    def put_annotations(cls, segment: Segment | None, seg_type: str | None = None):
        if seg_type is not None:
            segment.put_annotation("type", seg_type)

        for env, value in os.environ.items():
            if env.startswith(cls.XRAY_ENV_ANNOTATE):
                name = env.replace(cls.XRAY_ENV_ANNOTATE, "").lower()
                segment.put_annotation(name, value)

    @classmethod
    def setup_xray(cls):
        name = os.environ.get(cls.XRAY_ENV_NAME, "Palace")
        xray_recorder.configure(
            service=name,
            streaming_threshold=5,
            context_missing="LOG_ERROR",
            plugins=["EC2Plugin"],
        )
        xray_patch(("httplib", "sqlalchemy_core", "requests"))
        httplib_add_ignored(hostname="logs.*.amazonaws.com")

    @classmethod
    def enabled(cls) -> bool:
        enable_xray = os.environ.get(cls.XRAY_ENV_ENABLE)
        return enable_xray and enable_xray.lower() == "true"

    @classmethod
    def configure_app(cls, app: Flask):
        if cls.enabled():
            logging.getLogger().info("Configuring app with AWS XRAY.")
            cls.setup_xray()
            PalaceXrayMiddleware(app, xray_recorder)


class PalaceXrayMiddleware(XRayMiddleware):
    def __init__(self, app: Flask, recorder: AWSXRayRecorder):
        super().__init__(app, recorder)

    def _before_request(self):
        super()._before_request()
        PalaceXrayUtils.put_annotations(self._recorder.current_segment(), "registry")
