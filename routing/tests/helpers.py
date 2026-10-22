def make_straight_line_coordinates(start_lon, start_lat, end_lon, end_lat, num_points):
    """Generate evenly spaced [lon, lat] points along a straight line, used to
    build synthetic OSRM-style route geometry for tests."""
    coords = []
    for i in range(num_points):
        t = i / (num_points - 1)
        lon = start_lon + (end_lon - start_lon) * t
        lat = start_lat + (end_lat - start_lat) * t
        coords.append([lon, lat])
    return coords
