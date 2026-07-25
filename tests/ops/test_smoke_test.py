"""Tests for smoke_test.py -- all external calls mocked."""
import json
import unittest.mock as mock
import urllib.error

from scripts.ops import smoke_test


class TestGetHealth:
    def test_health_success(self, sample_health_response):
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.status = 200
            mock_resp.read.return_value = json.dumps(sample_health_response).encode()
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            result = smoke_test.get_health("https://example.com/health")
            assert result["passed"] is True
            assert result["status_code"] == 200
            assert result["body"] == sample_health_response

    def test_health_wrong_status(self):
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.status = 500
            mock_resp.read.return_value = b'{"error": "Internal Error"}'
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            result = smoke_test.get_health("https://example.com/health")
            assert result["passed"] is False
            assert result["status_code"] == 500

    def test_health_http_error(self):
        http_error = urllib.error.HTTPError(
            "https://example.com/health", 503, "Unavailable", {}, None
        )
        http_error.read.return_value = b"Service Unavailable"
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            result = smoke_test.get_health("https://example.com/health")
            assert result["passed"] is False
            assert result["status_code"] == 503


class TestAnonymousPost:
    def test_anonymous_post_403(self):
        http_error = urllib.error.HTTPError(
            "https://example.com/invoke", 403, "Forbidden", {}, None
        )
        with mock.patch("urllib.request.urlopen", side_effect=http_error):
            result = smoke_test.post_anonymous("https://example.com/invoke")
            assert result["passed"] is True
            assert result["status_code"] == 403

    def test_anonymous_post_200(self):
        with mock.patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = mock.MagicMock()
            mock_resp.status = 200
            mock_resp.__enter__.return_value = mock_resp
            mock_urlopen.return_value = mock_resp

            result = smoke_test.post_anonymous("https://example.com/invoke")
            assert result["passed"] is False
            assert result["status_code"] == 200


class TestSigV4Signed:
    def _setup_sigv4_mocks(self, response_json):
        mock_creds = mock.MagicMock()
        mock_creds.get_frozen_credentials.return_value = mock.MagicMock(
            access_key="AKID", secret_key="SECRET", token=None
        )
        mock_session = mock.MagicMock()
        mock_session.get_credentials.return_value = mock_creds

        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(response_json).encode()
        mock_resp.__enter__.return_value = mock_resp

        return mock_session, mock_resp

    def test_signed_success(self, sample_agent_response):
        mock_session, mock_resp = self._setup_sigv4_mocks(sample_agent_response)
        with mock.patch("botocore.session.Session", return_value=mock_session), \
             mock.patch("botocore.auth.SigV4Auth"), \
             mock.patch("urllib.request.urlopen", return_value=mock_resp):

            result = smoke_test.post_signed(
                "https://example.com/invoke", "ap-southeast-2", "test-profile"
            )
            assert result["passed"] is True
            assert result["status_code"] == 200
            assert result["response_status"] == "success"

    def test_signed_schema_validation_fails(self):
        mock_session, mock_resp = self._setup_sigv4_mocks({"invalid": "json"})
        with mock.patch("botocore.session.Session", return_value=mock_session), \
             mock.patch("botocore.auth.SigV4Auth"), \
             mock.patch("urllib.request.urlopen", return_value=mock_resp):

            result = smoke_test.post_signed(
                "https://example.com/invoke", "ap-southeast-2", "test-profile"
            )
            assert result["passed"] is False

    def test_signed_timeout(self):
        mock_creds = mock.MagicMock()
        mock_creds.get_frozen_credentials.return_value = mock.MagicMock(
            access_key="AKID", secret_key="SECRET", token=None
        )
        mock_session = mock.MagicMock()
        mock_session.get_credentials.return_value = mock_creds

        with mock.patch("botocore.session.Session", return_value=mock_session), \
             mock.patch("botocore.auth.SigV4Auth"), \
             mock.patch("urllib.request.urlopen", side_effect=TimeoutError("Connection timed out")):

            result = smoke_test.post_signed(
                "https://example.com/invoke", "ap-southeast-2", "test-profile"
            )
            assert result["passed"] is False
            assert result["error"] == "TimeoutError"


class TestOutputStructure:
    def test_no_answer_in_output(self, sample_agent_response):
        mock_creds = mock.MagicMock()
        mock_creds.get_frozen_credentials.return_value = mock.MagicMock(
            access_key="AKID", secret_key="SECRET", token=None
        )
        mock_session = mock.MagicMock()
        mock_session.get_credentials.return_value = mock_creds

        mock_resp = mock.MagicMock()
        mock_resp.status = 200
        mock_resp.read.return_value = json.dumps(sample_agent_response).encode()
        mock_resp.__enter__.return_value = mock_resp

        with mock.patch("botocore.session.Session", return_value=mock_session), \
             mock.patch("botocore.auth.SigV4Auth"), \
             mock.patch("urllib.request.urlopen", return_value=mock_resp):

            result = smoke_test.post_signed(
                "https://example.com/invoke", "ap-southeast-2", "test-profile"
            )
            assert result["passed"] is True
            assert "answer" not in result
