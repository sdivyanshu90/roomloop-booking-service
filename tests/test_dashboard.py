def test_dashboard_and_assets(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Room for your next idea." in response.text
    assert "Europe/Berlin" in response.text
    assert "America/Denver" in response.text
    assert client.get("/static/dashboard.css").status_code == 200
    assert client.get("/static/dashboard.js").status_code == 200


def test_office_filters(client, booking, series):
    client.post("/bookings", json=booking)
    client.post("/bookings", json={**booking, "room_id": 9})
    rows = client.get("/bookings?timezone=America/Denver").json()
    assert len(rows) == 1 and rows[0]["room_id"] == 9
    client.post("/series", json=series)
    client.post("/series", json={**series, "room_id": 17})
    rows = client.get("/series?timezone=Europe/Berlin").json()
    assert len(rows) == 1 and rows[0]["room_id"] == 3
