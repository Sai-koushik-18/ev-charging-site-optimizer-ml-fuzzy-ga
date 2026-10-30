import pytest
from shapely.geometry import Point, LineString, Polygon
import geopandas as gpd

from src.data.osm_loader import OSMLoader


class MockConfig:
    """Minimal mock config for OSMLoader."""
    class project:
        class study_area:
            class bbox:
                min_lon = 0.0
                min_lat = 0.0
                max_lon = 1.0
                max_lat = 1.0

    class paths:
        raw = "/tmp"


def test_elements_to_geodataframe_nodes_only():
    """Test that nodes are converted to Point geometries."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {"highway": "traffic_signals"}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 1.0, "tags": {"highway": "stop"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)  # Create instance without __init__
    loader.config = MockConfig()  # Assign a mock config to avoid __init__ issues
    gdf = loader._elements_to_geodataframe(elements, "node")
    assert len(gdf) == 2
    assert all(gdf.geometry.type == "Point")
    assert gdf.iloc[0]["osm_id"] == 1
    assert gdf.iloc[0]["highway"] == "traffic_signals"
    assert gdf.iloc[1]["osm_id"] == 2
    assert gdf.iloc[1]["highway"] == "stop"


def test_elements_to_geodataframe_ways_linestring():
    """Test that ways with at least two nodes become LineString."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 1.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "residential"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")
    # Expect 1 feature: the way only
    assert len(gdf) == 1
    # Check that we have 1 linestring and no points
    geom_types = gdf.geometry.type.value_counts()
    assert geom_types.get("Point", 0) == 0
    assert geom_types.get("LineString", 0) == 1
    # Check the way's properties
    way_row = gdf[gdf["osm_id"] == 10].iloc[0]
    assert way_row["osm_type"] == "way"
    assert way_row["highway"] == "residential"
    assert isinstance(way_row.geometry, LineString)
    assert len(way_row.geometry.coords) == 2
    # Check coordinates
    coords = list(way_row.geometry.coords)
    assert coords[0] == (0.0, 0.0)
    assert coords[1] == (1.0, 1.0)


def test_elements_to_geodataframe_way_insufficient_nodes():
    """Test that ways with fewer than two valid nodes are skipped."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1], "tags": {"highway": "residential"}},  # Only one node
        {"type": "way", "id": 20, "nodes": [3, 4], "tags": {"highway": "primary"}},  # Nodes 3,4 don't exist
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")
    # Should have 0 rows because both ways are skipped and no nodes are returned
    assert len(gdf) == 0


def test_elements_to_geodataframe_mixed():
    """Test mixed nodes and ways."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {"amenity": "parking"}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 1.0, "lat": 1.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3], "tags": {"highway": "service"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "node,way")
    # Expect 4 features: 3 nodes + 1 way
    assert len(gdf) == 4
    # Check that we have 3 points and 1 linestring
    geom_types = gdf.geometry.type.value_counts()
    assert geom_types.get("Point", 0) == 3
    assert geom_types.get("LineString", 0) == 1
    # Check the way's properties
    way_row = gdf[gdf["osm_id"] == 10].iloc[0]
    assert way_row["osm_type"] == "way"
    assert way_row["highway"] == "service"
    assert isinstance(way_row.geometry, LineString)
    assert len(way_row.geometry.coords) == 3


def test_elements_to_geodataframe_empty():
    """Test empty elements list returns empty GeoDataFrame."""
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe([], "node")
    assert len(gdf) == 0
    assert gdf.crs == "EPSG:4326"


def test_elements_to_geodataframe_valid_closed_building_way_polygon():
    """Test that a valid closed building way becomes a Polygon with positive area."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 1.0, "lat": 1.0, "tags": {}},
        {"type": "node", "id": 4, "lon": 0.0, "lat": 1.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 1],  # Closed way (1=1)
         "tags": {"building": "yes"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 1 feature (the building way)
    assert len(gdf) == 1
    assert gdf.iloc[0]["osm_id"] == 10
    assert gdf.iloc[0]["osm_type"] == "way"
    assert gdf.iloc[0]["building"] == "yes"
    assert isinstance(gdf.iloc[0].geometry, Polygon)
    # Validate it's a proper polygon with positive area
    assert gdf.iloc[0].geometry.is_valid
    assert gdf.iloc[0].geometry.area > 0  # Positive area assertion


def test_elements_to_geodataframe_building_way_insufficient_coords_skipped():
    """Test that a building way with genuinely insufficient coordinates is skipped."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        # Only two nodes - need at least three unique points for polygon [A,B,C,A]
        {"type": "way", "id": 10, "nodes": [1, 2, 1],  # Closed way but only 2 unique nodes
         "tags": {"building": "yes"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 0 features (insufficient coordinates for polygon)
    assert len(gdf) == 0


def test_elements_to_geodataframe_building_way_missing_node_skipped():
    """Test that a building way with missing nodes is skipped entirely."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        # Node 3 is missing
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 2, 1],  # Claims to be closed but node 3 missing
         "tags": {"building": "yes"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 0 features (building way skipped due to missing node)
    assert len(gdf) == 0


def test_elements_to_geodataframe_building_way_not_closed_skipped():
    """Test that an open building way (not closed) is skipped entirely."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 1.0, "lat": 1.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3],  # Open way (1≠3)
         "tags": {"building": "yes"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 0 features (not closed)
    assert len(gdf) == 0


def test_elements_to_geodataframe_building_way_invalid_polygon_skipped():
    """Test that a building way creating an invalid polygon is skipped."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 0.0, "lat": 1.0, "tags": {}},
        {"type": "node", "id": 4, "lon": 1.0, "lat": 1.0, "tags": {}},
        # Self-intersecting way: [1,2,3,4,2,3,1] - crosses itself
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 2, 3, 1],
         "tags": {"building": "yes"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 0 features (invalid polygon)
    assert len(gdf) == 0


def test_elements_to_geodataframe_valid_open_road_way_linestring():
    """Test that a valid open road way remains a LineString."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 2.0, "lat": 0.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3],  # Open way (1≠3)
         "tags": {"highway": "residential"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 1 feature (the road way)
    assert len(gdf) == 1
    assert gdf.iloc[0]["osm_id"] == 10
    assert gdf.iloc[0]["osm_type"] == "way"
    assert gdf.iloc[0]["highway"] == "residential"
    assert isinstance(gdf.iloc[0].geometry, LineString)
    assert len(gdf.iloc[0].geometry.coords) == 3  # Three coordinates


def test_elements_to_geodataframe_closed_road_way_linestring():
    """Test that a closed road way remains a LineString (preserving road behavior)."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 1.0, "lat": 1.0, "tags": {}},
        {"type": "node", "id": 4, "lon": 0.0, "lat": 1.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 1],  # Closed way (1=1)
         "tags": {"highway": "primary"}},  # Road way, not building
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 1 feature (the road way)
    assert len(gdf) == 1
    assert gdf.iloc[0]["osm_id"] == 10
    assert gdf.iloc[0]["osm_type"] == "way"
    assert gdf.iloc[0]["highway"] == "primary"
    assert isinstance(gdf.iloc[0].geometry, LineString)
    assert len(gdf.iloc[0].geometry.coords) == 5  # Five coordinates


def test_elements_to_geodataframe_road_way_insufficient_coords_skipped():
    """Test that a road way with insufficient coordinates is skipped."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1],  # Only one node
         "tags": {"highway": "residential"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    # Should have 0 features (insufficient coordinates)
    assert len(gdf) == 0


def test_elements_to_geodataframe_building_tags_preserved():
    """Test that building tags are preserved in the output records."""
    elements = [
        {"type": "node", "id": 1, "lon": 0.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 2, "lon": 1.0, "lat": 0.0, "tags": {}},
        {"type": "node", "id": 3, "lon": 1.0, "lat": 1.0, "tags": {}},
        {"type": "node", "id": 4, "lon": 0.0, "lat": 1.0, "tags": {}},
        {"type": "way", "id": 10, "nodes": [1, 2, 3, 4, 1],
         "tags": {"building": "yes", "name": "Test Building", "levels": "2"}},
    ]
    loader = OSMLoader.__new__(OSMLoader)
    loader.config = MockConfig()
    gdf = loader._elements_to_geodataframe(elements, "way")

    assert len(gdf) == 1
    assert gdf.iloc[0]["building"] == "yes"
    assert gdf.iloc[0]["name"] == "Test Building"
    assert gdf.iloc[0]["levels"] == "2"
    assert isinstance(gdf.iloc[0].geometry, Polygon)


if __name__ == "__main__":
    pytest.main([__file__])