def test_app_imports(flask_app):
    assert flask_app is not None

def test_home_page_loads(client):
    response = client.get("/")
    assert response.status_code == 200