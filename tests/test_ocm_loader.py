"""Tests for the OpenChargeMap loader: key handling and station parsing."""
import pytest
from shapely.geometry import box

from src.data.ocm_loader import OCMDataLoader


@pytest.fixture
def loader(tmp_path):
    ocm = OCMDataLoader(api_key="test-key-123")
    ocm.output_dir = tmp_path  # never overwrite the real cache
    return ocm


def _station(sid, lat, lon, is_operational):
    return {
        "ID": sid,
        "UUID": f"uuid-{sid}",
        "AddressInfo": {"Title": f"Station {sid}", "Town": "Vijayawada", "Latitude": lat,
                        "Longitude": lon, "Country": None},
        "OperatorInfo": None,  # OpenChargeMap returns null for unknown operators
        "UsageType": None,
        "StatusType": None if is_operational is None else {"Title": "x", "IsOperational": is_operational},
        "NumberOfPoints": 1,
        "Connections": [{"PowerKW": 30, "ConnectionType": None}],
    }


def test_key_is_sent_as_header_not_in_url(loader):
    assert "key" not in loader._build_params()
    assert loader._build_headers()["X-API-Key"] == "test-key-123"


def test_parse_keeps_unknown_status_and_drops_non_operational(loader):
    data = [
        _station(1, 16.50, 80.50, True),
        _station(2, 16.51, 80.51, None),
        _station(3, 16.52, 80.52, False),
    ]
    gdf = loader._parse_stations(data)
    assert sorted(gdf["station_id"]) == ["1", "2"]
    assert (gdf["distance_to_campus_km"] >= 0).all()


def test_parse_applies_search_radius_only_when_set(loader, monkeypatch):
    far = _station(4, 20.0, 80.5, True)  # roughly 390 km north of campus
    monkeypatch.setattr(OCMDataLoader, "region", property(lambda self: None))
    monkeypatch.setattr(OCMDataLoader, "search_radius_km", property(lambda self: None))
    assert len(loader._parse_stations([far])) == 1
    assert "distance" not in loader._build_params()
    monkeypatch.setattr(OCMDataLoader, "search_radius_km", property(lambda self: 50.0))
    assert loader._parse_stations([far]).empty
    assert loader._build_params()["distance"] == 50.0


def test_region_boundary_filters_by_location_not_state_label(loader, monkeypatch):
    square = box(80.0, 16.0, 81.0, 17.0)
    monkeypatch.setattr(OCMDataLoader, "region", property(lambda self: square))
    loader._region_name = "Test Region"
    inside = _station(5, 16.5, 80.5, True)
    inside["AddressInfo"]["StateOrProvince"] = "Odisha"  # wrong label, as in real OCM data
    outside = _station(6, 18.0, 83.0, True)
    gdf = loader._parse_stations([inside, outside])
    assert list(gdf["station_id"]) == ["5"]
    assert loader._build_params()["boundingbox"] == "(17.0,80.0),(16.0,81.0)"
    assert "distance" not in loader._build_params()


def test_parse_empty_response_returns_empty_frame(loader):
    assert loader._parse_stations([]).empty
