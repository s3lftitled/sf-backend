from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Address, Contact, utcnow
from app.schemas import AddressCreate, ContactCreate, ContactReplace, ContactUpdate

SORTABLE_FIELDS = ("id", "first_name", "last_name", "email", "company", "created_at", "updated_at")


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _replace_addresses(contact: Contact, addresses: list[AddressCreate]) -> None:
    """
    Swap in a fresh set of addresses.

    Assigning the whole collection lets delete-orphan remove the rows that are
    no longer referenced, so a contact never accumulates addresses it dropped.
    """
    contact.addresses = [Address(**address.model_dump()) for address in addresses]


def _touch(contact: Contact) -> None:
    """
    Advance the last-modified timestamp after an edit.

    Replacing only the addresses leaves the parent row clean, so SQLAlchemy
    emits no UPDATE and `onupdate` never fires — but an address change is still
    a change to the contact. Creation is left alone: the column default is
    evaluated at INSERT, and sampling the clock earlier would date the edit
    before the record exists.
    """
    contact.updated_at = utcnow()


def get_contact(db: Session, contact_id: int) -> Contact | None:
    return db.get(Contact, contact_id)


def get_contact_by_email(db: Session, email: str) -> Contact | None:
    stmt = select(Contact).where(func.lower(Contact.email) == _normalize_email(email))
    return db.execute(stmt).scalar_one_or_none()


def count_contacts(db: Session) -> int:
    return db.execute(select(func.count()).select_from(Contact)).scalar_one()


def list_contacts(
    db: Session,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    sort_by: str = "id",
    order: str = "asc",
) -> tuple[list[Contact], int]:
    """Return (page of contacts, total matching count)."""
    stmt = select(Contact)

    if search:
        pattern = f"%{search.strip().lower()}%"
        stmt = stmt.where(
            or_(
                func.lower(Contact.first_name).like(pattern),
                func.lower(Contact.last_name).like(pattern),
                func.lower(Contact.email).like(pattern),
                func.lower(func.coalesce(Contact.company, "")).like(pattern),
                func.lower(func.coalesce(Contact.phone, "")).like(pattern),
            )
        )

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()

    if sort_by not in SORTABLE_FIELDS:
        sort_by = "id"
    column = getattr(Contact, sort_by)
    stmt = stmt.order_by(column.desc() if order == "desc" else column.asc())

    items = db.execute(stmt.limit(limit).offset(offset)).scalars().all()
    return list(items), total


def create_contact(db: Session, payload: ContactCreate) -> Contact:
    data = payload.model_dump()
    data.pop("addresses")
    data["email"] = _normalize_email(data["email"])
    contact = Contact(**data)
    _replace_addresses(contact, payload.addresses)
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def replace_contact(db: Session, contact: Contact, payload: ContactReplace) -> Contact:
    data = payload.model_dump()
    data.pop("addresses")
    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)
    _replace_addresses(contact, payload.addresses)
    _touch(contact)
    db.commit()
    db.refresh(contact)
    return contact


def update_contact(db: Session, contact: Contact, payload: ContactUpdate) -> Contact:
    data = payload.model_dump(exclude_unset=True)
    # Presence, not value: an omitted `addresses` leaves the rows alone, while
    # anything actually sent replaces them — `null` and `[]` alike clear the set.
    replaces_addresses = "addresses" in data
    data.pop("addresses", None)
    for field, value in data.items():
        setattr(contact, field, _normalize_email(value) if field == "email" else value)
    if replaces_addresses:
        _replace_addresses(contact, payload.addresses or [])
        _touch(contact)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, contact: Contact) -> None:
    db.delete(contact)
    db.commit()
