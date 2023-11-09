from unittest.mock import MagicMock, call

from pytest import MonkeyPatch

from tests.fixtures.controller import ControllerSetupFixture
from util.xray import PalaceXrayMiddleware, PalaceXrayUtils


class TestPalaceXrayUtils:
    def test_put_annotations_none(self):
        # If no segment is passed in nothing is returned
        value = PalaceXrayUtils.put_annotations(None)
        assert value is None

    def test_put_annotations(self):
        # Type annotation set based on seg_type passed into put_annotation
        segment = MagicMock()
        PalaceXrayUtils.put_annotations(segment, "test")
        segment.put_annotation.assert_called_once_with("type", "test")

    def test_put_annotations_env(self, monkeypatch):
        # Annotations are made based on environment variables
        segment = MagicMock()
        monkeypatch.setenv(f"{PalaceXrayUtils.XRAY_ENV_ANNOTATE}TEST", "test")
        monkeypatch.setenv(
            f"{PalaceXrayUtils.XRAY_ENV_ANNOTATE}ANOTHER_TEST", "test123"
        )
        PalaceXrayUtils.put_annotations(segment)
        assert segment.put_annotation.called is True
        assert segment.put_annotation.call_count == 2
        assert segment.put_annotation.call_args_list == [
            call("test", "test"),
            call("another_test", "test123"),
        ]

    def test_configure_app(self, monkeypatch):
        mock_app = MagicMock()
        mock_middleware = MagicMock()

        monkeypatch.setattr(PalaceXrayUtils, "setup_xray", MagicMock())
        monkeypatch.setattr("util.xray.PalaceXrayMiddleware", mock_middleware)

        # Nothing happens if env isn't set
        monkeypatch.delenv(PalaceXrayUtils.XRAY_ENV_ENABLE, raising=False)
        PalaceXrayUtils.configure_app(mock_app)
        assert PalaceXrayUtils.setup_xray.called is False
        assert mock_middleware.called is False

        # Xray not setup is env isn't true
        monkeypatch.setenv(PalaceXrayUtils.XRAY_ENV_ENABLE, "false")
        PalaceXrayUtils.configure_app(mock_app)
        assert PalaceXrayUtils.setup_xray.called is False
        assert mock_middleware.called is False

        # Xray is setup is env is true
        monkeypatch.setenv(PalaceXrayUtils.XRAY_ENV_ENABLE, "true")
        PalaceXrayUtils.configure_app(mock_app)
        assert PalaceXrayUtils.setup_xray.called is True
        assert mock_middleware.called is True


class TestPalaceXrayMiddleware:
    def test_before_request(
        self, monkeypatch: MonkeyPatch, controller_setup_fixture: ControllerSetupFixture
    ):
        mock_app = MagicMock()
        mock_app._xray_first_request_done = None
        mock_recorder = MagicMock()
        xray = PalaceXrayMiddleware(mock_app, mock_recorder)

        with controller_setup_fixture.setup() as fixture:
            # First request does additional setup
            with fixture.app.test_request_context("/") as ctx:
                xray._before_request()
                assert mock_app._xray_first_request_done == True
                assert mock_recorder.current_segment().put_annotation.call_count == 2
                assert ctx.request._palace_first_request == True

            # Second request only calls put_annotation once
            with fixture.app.test_request_context("/") as ctx:
                xray._before_request()
                assert getattr(ctx.request, "_palace_first_request", None) is None
                assert mock_recorder.current_segment().put_annotation.call_count == 3
