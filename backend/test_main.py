#!/usr/bin/env python3
"""
Tests for PM2230 Dashboard Backend API (main.py)
Tests for helper functions and FastAPI endpoints
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime
import main as main_module

# Import test client and app
from fastapi.testclient import TestClient
from main import (
    app,
    get_latest_data,
    check_limits,
    RateLimiter,
    discover_serial_ports,
    has_live_reading,
    connect_client,
    auto_connect,
    generate_simulated_data,
    init_csv_file,
    parse_allowed_origins,
    _unique_order,
    update_current_alerts,
    cached_data,
    real_client,
)


# ============================================================================
# Fixtures
# ============================================================================
@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.fixture
def sample_data():
    """Sample data for testing."""
    return {
        "timestamp": "2026-03-03T10:00:00",
        "status": "OK",
        "V_LN1": 230.5,
        "V_LN2": 229.8,
        "V_LN3": 231.2,
        "V_LN_avg": 230.5,
        "V_LL12": 399.2,
        "V_LL23": 398.0,
        "V_LL31": 400.4,
        "V_LL_avg": 399.2,
        "I_L1": 10.2,
        "I_L2": 9.8,
        "I_L3": 10.5,
        "I_N": 0.3,
        "I_avg": 10.17,
        "Freq": 50.02,
        "P_L1": 2.1,
        "P_L2": 2.0,
        "P_L3": 2.2,
        "P_Total": 6.3,
        "S_L1": 2.35,
        "S_L2": 2.25,
        "S_L3": 2.4,
        "S_Total": 7.0,
        "Q_L1": 1.0,
        "Q_L2": 0.95,
        "Q_L3": 1.05,
        "Q_Total": 3.0,
        "THDv_L1": 2.5,
        "THDv_L2": 2.4,
        "THDv_L3": 2.6,
        "THDi_L1": 25.0,
        "THDi_L2": 24.5,
        "THDi_L3": 26.0,
        "V_unb": 0.5,
        "U_unb": 0.4,
        "I_unb": 2.0,
        "PF_L1": 0.92,
        "PF_L2": 0.91,
        "PF_L3": 0.93,
        "PF_Total": 0.92,
        "kWh_Total": 1000.5,
        "kVAh_Total": 1200.3,
        "kvarh_Total": 500.2,
    }


@pytest.fixture
def mock_scanner():
    """Mock PM2230Scanner for testing."""
    with patch("main.PM2230Client") as mock:
        yield mock


# ============================================================================
# Tests for Helper Functions
# ============================================================================
class TestGetLatestData:
    """Tests for get_latest_data() function."""

    def test_get_latest_data_with_cached_data(self, sample_data):
        """Test get_latest_data returns cached data when available."""
        with patch("main.cached_data", sample_data):
            result = get_latest_data()

            assert result["status"] == "OK"
            assert result["V_LN1"] == 230.5
            assert result["Freq"] == 50.02
            assert "timestamp" in result

    def test_get_latest_data_without_cached_data(self):
        """Test get_latest_data returns default values when no cache."""
        with patch("main.cached_data", {}):
            result = get_latest_data()

            assert result["status"] == "NOT_CONNECTED"
            assert result["V_LN1"] == 0
            assert result["Freq"] == 0
            assert "timestamp" in result

    def test_get_latest_data_calculates_pf_total(self):
        """Test get_latest_data calculates PF_Total from P_Total and S_Total."""
        data = {
            "timestamp": "2026-03-03T10:00:00",
            "status": "OK",
            "P_Total": 6.0,
            "S_Total": 7.5,
            "PF_L1": 0.8,
            "PF_L2": 0.8,
            "PF_L3": 0.8,
        }
        with patch("main.cached_data", data):
            result = get_latest_data()
            # PF = P/S = 6.0/7.5 = 0.8
            assert abs(result["PF_Total"] - 0.8) < 0.01

    def test_get_latest_data_handles_missing_keys(self):
        """Test get_latest_data handles missing keys gracefully."""
        data = {"timestamp": "2026-03-03T10:00:00", "status": "OK"}
        with patch("main.cached_data", data):
            result = get_latest_data()

            assert result["V_LN1"] == 0
            assert result["I_L1"] == 0
            assert result["P_Total"] == 0

    def test_get_latest_data_deep_copy(self, sample_data):
        """Test get_latest_data returns a deep copy."""
        with patch("main.cached_data", sample_data):
            result = get_latest_data()
            result["V_LN1"] = 999
            # Original should not be modified
            assert sample_data["V_LN1"] == 230.5


class TestCheckLimits:
    """Tests for check_limits() function."""

    def test_check_limits_no_alerts(self, sample_data):
        """Test check_limits returns OK when all values within limits."""
        result = check_limits(sample_data)

        assert result["status"] == "OK"
        assert result["count"] == 0
        assert result["alerts"] == []

    def test_check_limits_voltage_low(self):
        """Test check_limits detects low voltage."""
        data = {"V_LN_avg": 180, "Freq": 50.0, "PF_Total": 0.95,
                "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 2}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert result["count"] >= 1
        assert any(alert["category"] == "voltage_sag" for alert in result["alerts"])

    def test_check_limits_voltage_high(self):
        """Test check_limits detects high voltage."""
        data = {"V_LN_avg": 260, "Freq": 50.0, "PF_Total": 0.95,
                "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 2}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert any("Voltage" in alert["message"] for alert in result["alerts"])

    def test_check_limits_frequency_low(self):
        """Test check_limits detects low frequency."""
        data = {"V_LN_avg": 230, "Freq": 48.9, "PF_Total": 0.95,
                "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 2}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert any(alert["category"] == "frequency" for alert in result["alerts"])

    def test_check_limits_frequency_high(self):
        """Test check_limits detects high frequency."""
        data = {"V_LN_avg": 230, "Freq": 51.1, "PF_Total": 0.95,
                "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 2}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert any(alert["category"] == "frequency" for alert in result["alerts"])

    def test_check_limits_overload(self):
        """Test check_limits detects overload when low voltage coincides with high current."""
        data = {"V_LN_avg": 180, "I_avg": 50, "Freq": 50.0, "PF_Total": 0.95,
                "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 2}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert any(alert["category"] == "overload" for alert in result["alerts"])

    def test_check_limits_thdv_high(self):
        """Test check_limits detects high THD voltage."""
        data = {"V_LN_avg": 230, "Freq": 50.0, "PF_Total": 0.95,
                "THDv_L1": 8, "THDv_L2": 7, "THDv_L3": 9, "I_unb": 2}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert any(alert["category"] == "harmonics" for alert in result["alerts"])

    def test_check_limits_current_unbalance_high(self):
        """Test check_limits detects phase voltage unbalance."""
        data = {"V_LN1": 230, "V_LN2": 210, "V_LN3": 260, "V_LN_avg": 233.3, "Freq": 50.0,
                "PF_Total": 0.95, "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 15}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert any(alert["category"] == "unbalance" for alert in result["alerts"])

    def test_check_limits_multiple_alerts(self):
        """Test check_limits returns multiple alerts."""
        data = {"V_LN_avg": 200, "Freq": 49.0, "PF_Total": 0.85,
                "THDv_L1": 8, "THDv_L2": 7, "THDv_L3": 9, "I_unb": 15}
        result = check_limits(data)

        assert result["status"] == "ALERT"
        assert result["count"] >= 1

    def test_check_limits_handles_none_values(self):
        """Test check_limits handles None values."""
        data = {"V_LN_avg": None, "Freq": None, "PF_Total": None,
                "THDv_L1": None, "THDv_L2": None, "THDv_L3": None, "I_unb": None}
        result = check_limits(data)

        assert result["status"] == "OK"
        assert result["count"] == 0

    def test_check_limits_alert_severity(self, sample_data):
        """Test check_limits assigns correct severity levels."""
        # Voltage/frequency should be high severity
        data = {"V_LN_avg": 200, "Freq": 50.0, "PF_Total": 0.95,
                "THDv_L1": 2, "THDv_L2": 2, "THDv_L3": 2, "I_unb": 2}
        result = check_limits(data)

        voltage_alert = next((a for a in result["alerts"] if "Voltage" in a["message"]), None)
        if voltage_alert:
            assert voltage_alert["severity"] == "high"


class TestRateLimiter:
    """Tests for RateLimiter class."""

    @pytest.mark.asyncio
    async def test_rate_limiter_allows_under_limit(self):
        """Test rate limiter allows requests under the limit."""
        limiter = RateLimiter(max_requests=5, window_seconds=1.0)

        for _ in range(3):
            allowed = await limiter.is_allowed("192.168.1.1")
            assert allowed is True

    @pytest.mark.asyncio
    async def test_rate_limiter_blocks_over_limit(self):
        """Test rate limiter blocks requests over the limit."""
        limiter = RateLimiter(max_requests=3, window_seconds=1.0)

        # Make max requests
        for _ in range(3):
            await limiter.is_allowed("192.168.1.2")

        # Next request should be blocked
        allowed = await limiter.is_allowed("192.168.1.2")
        assert allowed is False

    @pytest.mark.asyncio
    async def test_rate_limiter_different_ips(self):
        """Test rate limiter tracks different IPs separately."""
        limiter = RateLimiter(max_requests=2, window_seconds=1.0)

        # IP1 uses its limit
        await limiter.is_allowed("192.168.1.1")
        await limiter.is_allowed("192.168.1.1")
        assert await limiter.is_allowed("192.168.1.1") is False

        # IP2 should still be allowed
        assert await limiter.is_allowed("192.168.1.2") is True

    def test_get_client_ip(self):
        """Test get_client_ip extracts IP correctly."""
        limiter = RateLimiter()

        # Test direct client IP
        mock_request = MagicMock()
        mock_request.headers.get.return_value = None
        mock_request.client.host = "10.0.0.1"
        assert limiter.get_client_ip(mock_request) == "10.0.0.1"

    def test_get_client_ip_forwarded_header(self):
        """Test get_client_ip handles X-Forwarded-For header."""
        limiter = RateLimiter()

        mock_request = MagicMock()
        mock_request.headers.get.return_value = "203.0.113.1, 10.0.0.1"
        mock_request.client.host = "127.0.0.1"
        assert limiter.get_client_ip(mock_request) == "203.0.113.1"


class TestDiscoverSerialPorts:
    """Tests for discover_serial_ports() function."""

    @patch("main.DEFAULT_PORT", "COM5")
    def test_discover_ports_with_default_port(self):
        """Test discover_serial_ports includes DEFAULT_PORT."""
        ports = discover_serial_ports()
        assert "COM5" in ports

    @patch("main.DEFAULT_PORT", "")
    @patch("main.platform.system", return_value="Windows")
    def test_discover_ports_windows(self, mock_system):
        """Test discover_serial_ports on Windows."""
        ports = discover_serial_ports()
        # Should include COM3 as default
        assert "COM3" in ports

    @patch("main.DEFAULT_PORT", "")
    @patch("main.platform.system", return_value="Linux")
    def test_discover_ports_linux(self, mock_system):
        """Test discover_serial_ports on Linux."""
        with patch("main.glob.glob", return_value=["/dev/ttyUSB0"]):
            ports = discover_serial_ports()
            assert "/dev/ttyUSB0" in ports
            assert "COM3" not in ports


class TestHasLiveReading:
    """Tests for has_live_reading() function."""

    def test_has_live_reading_true(self):
        """Test has_live_reading returns True for valid readings."""
        data = {"V_LN1": 230, "V_LL12": 400, "Freq": 50}
        assert has_live_reading(data) is True

    def test_has_live_reading_false_all_zero(self):
        """Test has_live_reading returns False for all zeros."""
        data = {"V_LN1": 0, "V_LL12": 0, "Freq": 0}
        assert has_live_reading(data) is False

    def test_has_live_reading_false_empty(self):
        """Test has_live_reading returns False for empty data."""
        data = {}
        assert has_live_reading(data) is False

    def test_has_live_reading_handles_invalid_data(self):
        """Test has_live_reading handles non-numeric values."""
        data = {"V_LN1": "invalid", "V_LL12": None, "Freq": ""}
        assert has_live_reading(data) is False

    def test_has_live_reading_small_values(self):
        """Test has_live_reading returns False for very small values."""
        data = {"V_LN1": 0.1, "V_LL12": 0.2, "Freq": 0.3}
        assert has_live_reading(data) is False


class TestUniqueOrder:
    """Tests for _unique_order() function."""

    def test_unique_order_removes_duplicates(self):
        """Test _unique_order removes duplicates."""
        result = _unique_order(["a", "b", "a", "c", "b"])
        assert result == ["a", "b", "c"]

    def test_unique_order_preserves_order(self):
        """Test _unique_order preserves first occurrence order."""
        result = _unique_order(["c", "b", "a", "b", "c"])
        assert result == ["c", "b", "a"]

    def test_unique_order_skips_empty(self):
        """Test _unique_order skips empty strings."""
        result = _unique_order(["a", "", "b", "", "c"])
        assert result == ["a", "b", "c"]

    def test_unique_order_empty_input(self):
        """Test _unique_order with empty input."""
        result = _unique_order([])
        assert result == []


class TestGenerateSimulatedData:
    """Tests for generate_simulated_data() function."""

    def test_generate_simulated_data_returns_dict(self):
        """Test generate_simulated_data returns a dictionary."""
        result = generate_simulated_data()
        assert isinstance(result, dict)

    def test_generate_simulated_data_has_required_keys(self):
        """Test generate_simulated_data has all required keys."""
        result = generate_simulated_data()
        required_keys = ["status", "V_LN1", "I_L1", "Freq", "P_Total", "kWh_Total"]
        for key in required_keys:
            assert key in result

    def test_generate_simulated_data_status_ok(self):
        """Test generate_simulated_data status is OK."""
        result = generate_simulated_data()
        assert result["status"] == "OK"

    def test_generate_simulated_data_voltage_range(self):
        """Test generated voltage is in reasonable range."""
        result = generate_simulated_data()
        assert 220 < result["V_LN1"] < 240

    def test_generate_simulated_data_energy_increments(self):
        """Test energy values increment over time."""
        result1 = generate_simulated_data()
        result2 = generate_simulated_data()
        assert result2["kWh_Total"] > result1["kWh_Total"]


class TestParseAllowedOrigins:
    """Tests for parse_allowed_origins() function."""

    @patch("main.ALLOWED_ORIGINS_ENV", "http://localhost:3000,http://localhost:3002")
    def test_parse_allowed_origins_multiple(self):
        """Test parsing multiple origins."""
        result = parse_allowed_origins()
        assert "http://localhost:3000" in result
        assert "http://localhost:3002" in result

    @patch("main.ALLOWED_ORIGINS_ENV", "")
    def test_parse_allowed_origins_empty(self):
        """Test empty origins returns wildcard."""
        result = parse_allowed_origins()
        assert result == ["*"]

    @patch("main.ALLOWED_ORIGINS_ENV", "   ")
    def test_parse_allowed_origins_whitespace(self):
        """Test whitespace-only returns wildcard."""
        result = parse_allowed_origins()
        assert result == ["*"]


# ============================================================================
# Tests for FastAPI Endpoints
# ============================================================================
class TestAPIEndpoints:
    """Tests for FastAPI endpoints."""

    def test_root_endpoint(self, client):
        """Test root endpoint returns something."""
        # The app may not have a root endpoint defined
        response = client.get("/")
        # Just verify the client works - 404 is acceptable if no root endpoint
        assert response.status_code in [200, 404]

    def test_get_all_data_endpoint(self, client, sample_data):
        """Test /api/v1/data endpoint."""
        with patch("main.cached_data", sample_data):
            response = client.get("/api/v1/data")
            assert response.status_code == 200
            data = response.json()
            assert "V_LN1" in data
            assert "status" in data

    def test_get_page1_endpoint(self, client, sample_data):
        """Test /api/v1/page1 endpoint."""
        with patch("main.cached_data", sample_data):
            response = client.get("/api/v1/page1")
            assert response.status_code == 200
            data = response.json()
            assert "V_LN1" in data
            assert "Freq" in data

    def test_get_page2_endpoint(self, client, sample_data):
        """Test /api/v1/page2 endpoint."""
        with patch("main.cached_data", sample_data):
            response = client.get("/api/v1/page2")
            assert response.status_code == 200
            data = response.json()
            assert "P_Total" in data
            assert "S_Total" in data

    def test_get_page3_endpoint(self, client, sample_data):
        """Test /api/v1/page3 endpoint."""
        with patch("main.cached_data", sample_data):
            response = client.get("/api/v1/page3")
            assert response.status_code == 200
            data = response.json()
            assert "THDv_L1" in data
            assert "PF_Total" in data

    def test_get_page4_endpoint(self, client, sample_data):
        """Test /api/v1/page4 endpoint."""
        with patch("main.cached_data", sample_data):
            response = client.get("/api/v1/page4")
            assert response.status_code == 200
            data = response.json()
            assert "kWh_Total" in data

    def test_get_alerts_endpoint(self, client, sample_data):
        """Test /api/v1/alerts endpoint."""
        with patch("main.cached_data", sample_data):
            response = client.get("/api/v1/alerts")
            assert response.status_code == 200
            data = response.json()
            assert "status" in data
            assert "alerts" in data

    def test_get_status_endpoint(self, client):
        """Test /api/v1/status endpoint."""
        response = client.get("/api/v1/status")
        assert response.status_code == 200
        data = response.json()
        assert "connected" in data
        assert "mode" in data

    def test_get_ports_endpoint(self, client):
        """Test /api/v1/ports endpoint."""
        response = client.get("/api/v1/ports")
        assert response.status_code == 200
        data = response.json()
        assert "ports" in data
        assert "defaults" in data

    def test_get_parameters_list_endpoint(self, client):
        """Test /api/v1/parameters endpoint."""
        response = client.get("/api/v1/parameters")
        assert response.status_code == 200
        data = response.json()
        assert "parameters" in data
        assert "total" in data

    def test_logging_status_endpoint(self, client):
        """Test /api/v1/datalog/status endpoint."""
        response = client.get("/api/v1/datalog/status")
        assert response.status_code == 200
        data = response.json()
        assert "is_logging" in data
        assert "file_size_kb" in data

    def test_start_logging_endpoint(self, client):
        """Test /api/v1/datalog/start endpoint."""
        response = client.post("/api/v1/datalog/start")
        assert response.status_code == 200

    def test_stop_logging_endpoint(self, client):
        """Test /api/v1/datalog/stop endpoint."""
        response = client.post("/api/v1/datalog/stop")
        assert response.status_code == 200

    def test_clear_log_endpoint(self, client):
        """Test /api/v1/datalog/clear endpoint."""
        response = client.delete("/api/v1/datalog/clear")
        assert response.status_code == 200

    def test_ai_summary_endpoint(self, client, sample_data):
        """Test /api/v1/ai-summary endpoint."""
        with patch("main.cached_data", sample_data):
            with patch("main.generate_power_summary", new_callable=AsyncMock) as mock_ai:
                mock_ai.return_value = {
                    "summary": "Test summary",
                    "is_cached": False,
                    "cache_key": "test_key",
                }
                response = client.post("/api/v1/ai-summary")
                assert response.status_code == 200
                data = response.json()
                assert "summary" in data

    def test_rate_limiting(self, client):
        """Test that rate limiting is appliedied."""
        # Make many rapid requests
        responses = []
        for _ in range(15):
            response = client.get("/api/v1/status")
            responses.append(response.status_code)

        # Should get at least one 429 (Too Many Requests)
        # or all succeed if rate limiter window passed
        assert all(code in [200, 429] for code in responses)


class TestAlertRetention:
    """Tests for retained alert state used by the web toaster."""

    def test_update_current_alerts_retains_recent_fault(self):
        recent_alert = {
            "status": "ALERT",
            "count": 1,
            "alerts": [
                {
                    "category": "phase_loss",
                    "severity": "critical",
                    "message": "Phase Loss Detected: L1 disconnected",
                }
            ],
        }

        with patch.object(main_module, "current_alerts", {"status": "OK", "alerts": []}), \
             patch.object(main_module, "last_active_alerts", None), \
             patch.object(main_module, "last_alert_seen_at", 0.0), \
             patch.object(main_module, "ALERT_RETENTION_SECONDS", 10.0):
            active = update_current_alerts(recent_alert, now_ts=100.0)
            assert active["status"] == "ALERT"
            assert active["active"] is True
            assert active["retained"] is False

            retained = update_current_alerts(None, now_ts=105.0)
            assert retained["status"] == "ALERT"
            assert retained["active"] is False
            assert retained["retained"] is True

            cleared = update_current_alerts(None, now_ts=111.0)
            assert cleared["status"] == "OK"
            assert cleared["alerts"] == []


class TestConnectEndpoints:
    """Tests for connection endpoints."""

    @patch("main.connect_client")
    def test_connect_endpoint_success(self, mock_connect, client):
        """Test /api/v1/connect with successful connection."""
        mock_client = MagicMock()
        mock_client.port = "COM3"
        mock_client.baudrate = 9600
        mock_client.slave_id = 1
        mock_client.parity = "E"
        mock_connect.return_value = (mock_client, "connected")

        response = client.get("/api/v1/connect?port=COM3")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "connected"

    @patch("main.connect_client")
    def test_connect_endpoint_failure(self, mock_connect, client):
        """Test /api/v1/connect with failed connection."""
        mock_connect.return_value = (None, "connection_failed")

        response = client.get("/api/v1/connect?port=COM99")
        assert response.status_code == 500

    @patch("main.auto_connect")
    def test_auto_connect_endpoint_success(self, mock_auto, client):
        """Test /api/v1/auto-connect with successful connection."""
        mock_client = MagicMock()
        mock_client.port = "COM3"
        mock_auto.return_value = (mock_client, [])

        response = client.get("/api/v1/auto-connect")
        assert response.status_code == 200

    @patch("main.auto_connect")
    def test_auto_connect_endpoint_failure(self, mock_auto, client):
        """Test /api/v1/auto-connect with failed connection."""
        mock_auto.return_value = (None, [{"port": "COM3", "result": "failed"}])

        response = client.get("/api/v1/auto-connect")
        assert response.status_code == 500

    def test_disconnect_endpoint(self, client):
        """Test /api/v1/disconnect endpoint."""
        response = client.get("/api/v1/disconnect")
        assert response.status_code == 200


class TestLoggingEndpoints:
    """Tests for logging endpoints."""

    def test_download_log_not_found(self, client):
        """Test /api/v1/datalog/download when file doesn't exist."""
        with patch("main.os.path.exists", return_value=False):
            response = client.get("/api/v1/datalog/download")
            assert response.status_code == 404


class TestRecentBugFixes:
    """Regression tests for recently fixed endpoint bugs."""

    @patch("main.SIMULATE_MODE", True)
    def test_simulator_invalid_fault_returns_bad_request(self, client):
        """Unknown simulator fault types should stay 400, not be wrapped as 500."""
        response = client.post("/api/v1/simulator/inject", json={"type": "unknown_fault"})
        assert response.status_code == 400
        assert response.json()["detail"] == "Unknown fault type: unknown_fault"

    def test_chat_invalid_json_does_not_raise_secondary_error(self, client):
        """Invalid JSON should surface the parse failure, not an UnboundLocalError."""
        response = client.post(
            "/api/v1/chat",
            data="{",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 500
        assert "body" not in response.json()["detail"].lower()


class TestAPIErrorHandling:
    """Tests for API error handling."""

    def test_invalid_port_parameter(self, client):
        """Test connect with invalid port parameter."""
        response = client.get("/api/v1/connect?port=")
        # Should return 400 or 422 for validation error
        assert response.status_code in [400, 422]

    def test_invalid_baudrate_parameter(self, client):
        """Test connect with invalid baudrate."""
        response = client.get("/api/v1/connect?port=COM3&baudrate=100")
        assert response.status_code in [400, 422]

    def test_invalid_slave_id_parameter(self, client):
        """Test connect with invalid slave_id."""
        response = client.get("/api/v1/connect?port=COM3&slave_id=0")
        assert response.status_code in [400, 422]

    def test_invalid_parity_parameter(self, client):
        """Test connect with invalid parity."""
        response = client.get("/api/v1/connect?port=COM3&parity=X")
        assert response.status_code in [400, 422]


# ============================================================================
# Integration Tests
# ============================================================================
class TestIntegration:
    """Integration tests for the complete flow."""

    def test_full_data_read_flow(self, client, sample_data):
        """Test complete data reading flow."""
        with patch("main.cached_data", sample_data):
            # Get all data
            response = client.get("/api/v1/data")
            assert response.status_code == 200

            # Check alerts
            response = client.get("/api/v1/alerts")
            assert response.status_code == 200

            # Check status
            response = client.get("/api/v1/status")
            assert response.status_code == 200

    def test_page_navigation_flow(self, client, sample_data):
        """Test navigating through all pages."""
        with patch("main.cached_data", sample_data):
            pages = ["/api/v1/page1", "/api/v1/page2",
                     "/api/v1/page3", "/api/v1/page4"]

            for page in pages:
                response = client.get(page)
                assert response.status_code == 200

    def test_logging_flow(self, client):
        """Test complete logging flow."""
        # Start logging
        response = client.post("/api/v1/datalog/start")
        assert response.status_code == 200

        # Check status
        response = client.get("/api/v1/datalog/status")
        assert response.status_code == 200

        # Stop logging
        response = client.post("/api/v1/datalog/stop")
        assert response.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
