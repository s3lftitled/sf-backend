import base64

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import Address
from app.schemas import MAX_PHOTO_BYTES

BASE = "/api/v1/contacts"
# 1x1 transparent GIF, the smallest thing that survives the data-URL validator.
PHOTO = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "sqlite"


def test_create_contact(client, payload):
    response = client.post(BASE, json=payload)
    assert response.status_code == 201
    body = response.json()
    assert body["id"] > 0
    assert body["email"] == "ada@example.com"
    assert body["full_name"] == "Ada Lovelace"
    assert body["created_at"] and body["updated_at"]


def test_create_requires_valid_email(client, payload):
    response = client.post(BASE, json={**payload, "email": "not-an-email"})
    assert response.status_code == 422


def test_create_requires_names(client, payload):
    response = client.post(BASE, json={**payload, "first_name": ""})
    assert response.status_code == 422


def test_duplicate_email_conflicts(client, payload):
    assert client.post(BASE, json=payload).status_code == 201
    response = client.post(BASE, json={**payload, "email": "ADA@example.com"})
    assert response.status_code == 409


def test_get_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.get(f"{BASE}/{contact_id}")
    assert response.status_code == 200
    assert response.json()["id"] == contact_id


def test_get_missing_contact_returns_404(client):
    assert client.get(f"{BASE}/9999").status_code == 404


def test_list_pagination_and_total(client, payload):
    for index in range(5):
        client.post(BASE, json={**payload, "email": f"user{index}@example.com"})

    response = client.get(BASE, params={"limit": 2, "offset": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert len(body["items"]) == 2
    assert body["limit"] == 2 and body["offset"] == 2


def test_list_search(client, payload):
    client.post(BASE, json=payload)
    client.post(
        BASE,
        json={**payload, "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com", "company": "US Navy"},
    )

    hits = client.get(BASE, params={"search": "hopper"}).json()
    assert hits["total"] == 1
    assert hits["items"][0]["last_name"] == "Hopper"

    by_company = client.get(BASE, params={"search": "navy"}).json()
    assert by_company["total"] == 1

    misses = client.get(BASE, params={"search": "nobody"}).json()
    assert misses["total"] == 0


def test_list_sorting(client, payload):
    client.post(BASE, json={**payload, "last_name": "Zhang", "email": "z@example.com"})
    client.post(BASE, json={**payload, "last_name": "Adams", "email": "a@example.com"})

    names = [
        item["last_name"]
        for item in client.get(BASE, params={"sort_by": "last_name", "order": "asc"}).json()["items"]
    ]
    assert names == ["Adams", "Zhang"]


def test_list_rejects_bad_sort_field(client):
    assert client.get(BASE, params={"sort_by": "; DROP TABLE contacts"}).status_code == 422


def test_patch_updates_only_sent_fields(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"phone": "+1-000-000-0000"})
    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+1-000-000-0000"
    assert body["first_name"] == "Ada"
    assert body["company"] == "Analytical Engines"


def test_patch_duplicate_email_conflicts(client, payload):
    first = client.post(BASE, json=payload).json()["id"]
    client.post(BASE, json={**payload, "email": "grace@example.com"})
    response = client.patch(f"{BASE}/{first}", json={"email": "grace@example.com"})
    assert response.status_code == 409


def test_patch_same_email_is_allowed(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"email": payload["email"]})
    assert response.status_code == 200


def test_put_replaces_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={"first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Grace Hopper"
    assert body["company"] is None  # omitted fields are cleared by PUT


def test_put_missing_contact_returns_404(client):
    response = client.put(
        f"{BASE}/9999",
        json={"first_name": "A", "last_name": "B", "email": "ab@example.com"},
    )
    assert response.status_code == 404


def test_delete_contact(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204
    assert client.get(f"{BASE}/{contact_id}").status_code == 404
    assert client.delete(f"{BASE}/{contact_id}").status_code == 404


def test_create_contact_with_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": PHOTO})
    assert response.status_code == 201
    assert response.json()["photo"] == PHOTO


def test_photo_defaults_to_none(client, payload):
    assert client.post(BASE, json=payload).json()["photo"] is None


def test_create_rejects_non_image_photo(client, payload):
    response = client.post(BASE, json={**payload, "photo": "data:application/pdf;base64,Zm9v"})
    assert response.status_code == 422


def test_create_rejects_oversized_photo(client, payload):
    oversized = "data:image/png;base64," + "A" * (2 * 1024 * 1024)
    response = client.post(BASE, json={**payload, "photo": oversized})
    assert response.status_code == 422


def test_accepts_photo_at_the_size_limit(client, payload):
    image = b"GIF89a" + bytes(MAX_PHOTO_BYTES - 6)
    photo = "data:image/gif;base64," + base64.b64encode(image).decode()
    assert client.post(BASE, json={**payload, "photo": photo}).status_code == 201


def test_rejects_bytes_that_are_not_an_image(client, payload):
    photo = "data:image/png;base64," + base64.b64encode(b"not an image").decode()
    response = client.post(BASE, json={**payload, "photo": photo})
    assert response.status_code == 422


def test_rejects_photo_whose_bytes_contradict_its_media_type(client, payload):
    gif_bytes = PHOTO.split(",", 1)[1]
    response = client.post(BASE, json={**payload, "photo": f"data:image/png;base64,{gif_bytes}"})
    assert response.status_code == 422


def test_put_without_photo_clears_it(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PHOTO}).json()["id"]
    response = client.put(f"{BASE}/{contact_id}", json=payload)
    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_patch_preserves_photo(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PHOTO}).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"job_title": "Chief Engineer"})
    assert response.status_code == 200
    assert response.json()["photo"] == PHOTO


def test_patch_can_remove_photo(client, payload):
    contact_id = client.post(BASE, json={**payload, "photo": PHOTO}).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"photo": None})
    assert response.status_code == 200
    assert response.json()["photo"] is None


def test_create_contact_with_many_addresses(client, payload):
    body = {
        **payload,
        "addresses": [
            {"type": "Home", "street": "1 Home St", "city": "London", "country": "UK"},
            {"type": "Work", "street": "1 Market St", "city": "San Francisco", "country": "USA"},
            {"type": "Other", "city": "Paris", "country": "France"},
        ],
    }
    response = client.post(BASE, json=body)
    assert response.status_code == 201

    addresses = response.json()["addresses"]
    assert [address["type"] for address in addresses] == ["Home", "Work", "Other"]
    assert all(address["id"] > 0 for address in addresses)


def test_addresses_default_to_empty(client, payload):
    body = {key: value for key, value in payload.items() if key != "addresses"}
    assert client.post(BASE, json=body).json()["addresses"] == []


def test_rejects_unknown_address_type(client, payload):
    response = client.post(BASE, json={**payload, "addresses": [{"type": "Holiday"}]})
    assert response.status_code == 422


def test_put_replaces_the_whole_address_set(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.put(
        f"{BASE}/{contact_id}",
        json={**payload, "addresses": [{"type": "Home", "city": "Berlin"}]},
    )
    assert response.status_code == 200

    addresses = response.json()["addresses"]
    assert len(addresses) == 1
    assert addresses[0]["city"] == "Berlin"


def test_patch_without_addresses_keeps_them(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"job_title": "Chief Engineer"})
    assert len(response.json()["addresses"]) == 1


def test_patch_with_empty_list_clears_them(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    response = client.patch(f"{BASE}/{contact_id}", json={"addresses": []})
    assert response.json()["addresses"] == []


def test_deleting_a_contact_deletes_its_addresses(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    assert client.delete(f"{BASE}/{contact_id}").status_code == 204

    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(Address)).scalar_one() == 0


def test_replacing_addresses_leaves_no_orphans(client, payload):
    contact_id = client.post(BASE, json=payload).json()["id"]
    client.put(f"{BASE}/{contact_id}", json={**payload, "addresses": [{"type": "Home"}]})

    with SessionLocal() as db:
        assert db.execute(select(func.count()).select_from(Address)).scalar_one() == 1


def test_root_lists_entrypoints(client):
    body = client.get("/").json()
    assert body["contacts"] == BASE
